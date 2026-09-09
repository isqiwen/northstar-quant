# 部署

| 主机 | 服务 |
|---|---|
| `core.local` | Data Hub 前端、API、同步 worker；独立 PostgreSQL |
| `research.local` | Research 前端、API、worker；本机 SQLite 和 DuckDB |
| Live 独立主机 | 自己的前端、API、内核和本地存储 |

应用只读写固定的目录，不感知存储设备、服务器、协议或挂载方式。
部署脚本创建缺失的 `files/` 和本机运行目录；路径有挂载就使用挂载存储，没有挂载就使用本地磁盘。

## 主机与凭据

仓库维护 `deploy/hosts.toml` 和各目录的 `.env`。`database` 默认部署到 `core.local`。
主机配置接受 `host/user/port`，不填写路径。`user` 仅用于 `init-host` 登录和提权；其他命令固定使用 northstar。
`user = "root"` 时直接初始化；普通用户须有 sudo 权限。同一地址和端口的各应用填写相同初始化用户。
`northstarctl deploy` 自动将本次 Git 提交中对应应用的 `.env` 上传到
`/opt/northstar/config/{database,data-hub,research,live}.env`，归 northstar 用户所有，权限 600。
首次部署无需手工复制配置；未指定 `--env-file` 时已有运行配置保留原内容。
`deploy --env-file <本地文件>` 使用该文件完整更新所选应用的远程配置，权限仍为 600，不合并默认值。
自定义文件可以位于仓库外，无需提交；内容通过 SSH 上传，不进入源码包或命令参数。
配置通过独立的无终端 SSH 输入流传输，不写入命令参数或日志；真实凭据仍仅保存在目标主机运行副本。

- `database/.env`：管理员口令、Data Hub 应用口令。
- `data_hub/.env`：与数据库配置相同的应用口令。
- `research/.env`：Data Hub URL。
- `live/.env`：独立运行配置，见 [Live 部署](live/README.md)。

数据库密码默认均为 `123456`，不限制长度，也不要求账号使用不同密码。core PostgreSQL 不发布宿主机端口，
通过固定的内部 Docker 网络 `northstar-data-storage` 供 Data Hub 使用，无需配置网络名。Research 不连接此数据库。
应用账号没有超级用户、建库、建角色或 DDL 权限，管理员凭据仅交给初始化/维护容器。

## 存储目录

所有主机统一使用 `/opt/northstar`，路径写在程序和 Compose 中，不提供目录环境变量或命令行覆盖：

```text
/opt/northstar/
├── apps/{database,data-hub,research,live}/  # releases、current 与部署记录
├── config/{database,data-hub,research,live}.env
├── files/{source,market,research,backup}/
├── state/data-hub/postgresql/
├── state/research/                        # SQLite
├── state/live/{postgresql,sources}/       # Live 本地数据库与持久材料
├── credentials/{data-hub,live}/
├── logs/{data-hub,research,live}/
└── work/research/
```

各主机只准备自己需要的部分。程序部署不覆盖状态、凭据和文件。
所有应用持久文件使用明确的主机目录映射，不使用 Docker 命名卷；Docker 自己的镜像、容器元数据和容器标准输出仍由 Docker 管理。
容器内部路径由 Compose 固定，无需用户配置。

| 文件目录 | Data Hub | Research |
|---|---|---|
| `files/source` | 读写 | 不访问 |
| `files/market` | 读写 | 只读 |
| `files/research` | 只读引用 | 读写 |
| `files/backup` | 维护时使用 | 维护时使用 |

无需手工创建存储目录。`northstarctl deploy` 自动准备所选应用需要的目录。
`files/` 可以使用本地磁盘，也可以提前挂载其他存储；脚本不要求挂载存在。
Live 不使用 Data Hub/Research 的文件目录。

| 本机目录 | 所有者 | 权限 |
|---|---|---|
| 新建 `files/<用途>` | northstar:northstar | `750` |
| `apps/<应用>` | northstar:northstar | `755` |
| `config`、`logs/<应用>` | 同上 | `750` |
| `state/<应用>`、`bindings`、`credentials/<应用>`、`work/research` | 同上 | `700` |
| 各应用 `.env` | 同上，保留内容 | `600` |
| PostgreSQL 数据目录 | 新建时为部署用户，初始化后由 PostgreSQL 管理 | 新建 `700`，已有目录不改权限/所有者 |

所有主机统一使用 northstar 部署用户。脚本只管理目录本身，不递归改动现有文件权限。
Python 容器当前以 root 运行；Next.js 以 node 运行且不挂载业务数据和凭据，前端日志由 Docker 收集。
已有文件目录保留原权限：部署用户需要读取身份标记，后端需要对应读写权限；不使用 777。
脚本记录已准备的持久目录；后续目录丢失时要求恢复，不创建空状态替代。日志和临时目录可以重建。
本机 Make 不执行远程准备阶段，使用前须具备相同目录和权限。

文件目录缺失时自动创建；路径须非符号链接、互不包含。已有绑定对应的空目录自动初始化为可写的本地存储。
PostgreSQL、SQLite、Live 状态和凭据在各自主机本地持久存储，Live 不挂载 Data Hub/Research 的文件目录。

存储编号由程序自动管理，无需在 `.env` 填写 UUID。
首次 `deploy database` 为四个空目录自动分配身份，数据库初始化将标记写入各目录。
Data Hub 使用同一份绑定；Research 首次部署读取共享目录标记并保存自己的绑定，
读取行情时还会与 Data Hub 的固定发布清单核对。

绑定保存在 `/opt/northstar/state/{data-hub,research}/bindings/storage.json`，目录由远程部署脚本准备，
文件为程序状态，不是用户配置。重试、重新部署和空目录本地存储复用已保存编号；已有数据库缺少绑定、非空目录缺少标记或编号改变仍报错。
使用本地空目录不会复制原存储的数据，读取旧快照仍检查所引用的真实文件。目录标记可读，绑定状态仅部署用户可读写。
直接使用本机 Make 前需确保对应 `bindings/` 目录由执行用户拥有。

容器另外验证实际文件读写、同步和身份；底层文件系统须支持应用使用的 POSIX 文件操作。

跨主机 Research 必须看到相同发布文件和对应存储 UUID，路径固定相同，实际目录内容须一致。
怎样把这些文件提供给两台主机、设置挂载与开机顺序，由主机管理负责。
更换文件存储时先停止相关写入、备份数据库与文件，迁移完整内容和身份标记，再恢复运行；
保持原路径和 UUID 时不需要修改应用配置。脚本不搬迁或覆盖现有数据。

## 部署与访问

远程入口本机需要 Python 3.11+/Git/OpenSSH；目标主机先具备 SSH、Python 3.11+，并允许配置的 user 登录；该用户为 root 或具有 sudo 权限。
`deploy` 自动安装 Ubuntu/Debian amd64 上缺失的 Git、uv、Docker Engine、Compose、Buildx；主机需要软件源和镜像网络访问。
Ubuntu 首次安装 Docker 使用已配置的系统 APT 源（需提供 universe 中的 docker.io、docker-compose-v2、docker-buildx），不另行下载 Docker 官方源公钥。
Debian 或已有 Docker CE 使用 Docker 官方软件源；公钥下载有超时和重试。已有可用运行时不自动替换。

在 `deploy/hosts.toml` 填好主机地址、初始化 user 和端口，然后首次初始化：

```sh
# 初始化全部已配置主机，相同地址和端口只执行一次
python3 scripts/northstarctl.py init-host
# 或者只初始化 core
python3 scripts/northstarctl.py init-host database
```

交互执行时由 OpenSSH 提示确认主机指纹、按需输入配置用户的 SSH 登录密码；普通用户由 sudo 提示提权密码。
无终端时需要配置用户可用的 SSH 密钥，普通用户还需要免密码 sudo。
脚本创建 northstar 普通用户，安装公钥并配置免密码 sudo，重复执行保留已有公钥。
自动优先使用本机 `~/.ssh/id_ed25519.pub`，不存在时使用 `~/.ssh/id_rsa.pub`，无需公钥参数。

公钥对应的私钥须可通过本机 SSH 默认身份、配置或 agent 使用；脚本不会上传私钥或保存登录及提权密码。
没有 SSH 密钥时可先执行 `ssh-keygen -t ed25519` 创建。初始化最后验证 northstar 密钥登录和 `sudo -n`。
northstar 获得免密码管理员权限以安装依赖和准备目录；Docker 已安装时加入现有 docker 组，否则首次安装时加入。
已有依赖直接复用，不主动升级或重启 Docker。存储检查与版本读取直接使用 Python，不在主机安装后端业务依赖。
SSH 连接关闭或取消部署时清理本次部署子进程并释放锁，已启动容器和持久数据保留；可重新执行 `deploy`。
挂载存储由主机管理员管理。

初始化完成并提交代码后部署（固定使用 northstar，无需手工复制 `.env`）：

```sh
python3 scripts/northstarctl.py deploy database
python3 scripts/northstarctl.py deploy data-hub
python3 scripts/northstarctl.py status data-hub
ssh -N -L 18082:127.0.0.1:18082 northstar@core.local
```

四个应用均可在部署时指定自己的配置文件，例如：

```sh
python3 scripts/northstarctl.py deploy database --env-file ~/.config/northstar/database.env
python3 scripts/northstarctl.py deploy data-hub --env-file ~/.config/northstar/data-hub.env
python3 scripts/northstarctl.py deploy research --env-file ~/.config/northstar/research.env
python3 scripts/northstarctl.py deploy live --env-file ~/.config/northstar/live.env
```

`--config` 指定主机清单，`--env-file` 指定本地应用配置，两者用途不同。
`--env-file` 仅用于 `deploy`；启停、状态和日志命令使用远程已部署配置。
更换配置文件不等于轮换已初始化 PostgreSQL 的管理员密码，数据库实际密码须保持一致。

打开 <http://127.0.0.1:18082>，在历史同步页设置 Tushare token 并启动同步。
仅测试 Data Hub 时无需部署 Research/Live。
前端/API 与同步 worker 独立，关闭管理界面不停止已提交任务。

支持 `init-host/deploy/start/restart/stop/status/logs`、`--config`、`--dry-run`、`--help`。
`deploy` 传送干净 Git HEAD；`start/restart` 使用已部署镜像；停止保留主机数据目录。
`--dry-run` 不连接主机或安装软件。其他管理命令也不安装软件。
Live 整套启停包含内核，但不代表撤单、平仓或完成核对，也不自动授予交易权。

也可在目标主机仓库根目录执行（依赖已准备）：

```sh
make up-database
make up-data
make up-research
```

## Research 数据访问

Research 用 `NORTHSTAR_DATA_HUB_URL` 获取固定版本清单，用配置的市场目录读取对应 Parquet。
发布清单接口无需 token，使用 Protobuf 消息；Tushare token 仍在 Data Hub 网页设置。
独立只读网关固定监听 `0.0.0.0`，端口固定为 `19090`，只允许清单 GET；管理 API `19082` 仅绑定本机。
Research 的 `NORTHSTAR_DATA_HUB_URL` 填写可访问的 core 地址，例如 `http://core.local:19090`。
跨主机使用可信私网或加密隧道，不向公网暴露明文接口。

清单记录存储 UUID、相对路径、文件长度和 SHA-256，不记录 core 绝对路径。
Research 使用本机 DuckDB 计算，不扫描目录追踪最新文件、不共享可写 DuckDB。
缺失或损坏文件明确失败；跨重启 SSD 缓存尚未实现。固定供应商响应到完整跨日研究输入仍有语义边界。

## 备份

在 core 执行 `make backup-database`，
把 Data Hub 数据库、角色和固定文件保存到备份目录的新时间/UUID 子目录，`complete.json` 表示完成。
Research 在目标主机运行 `python3 scripts/operations/compose.py backup research`，
自动加载存储绑定并在维护容器内写入新的备份子目录。
正常 API/worker 不挂载备份目录；定时备份由各主机调度器调用。

数据库与引用文件必须联合备份，恢复仅接受空数据库和全新目录，不覆盖原有数据。
core 联合备份包含 `storage-bindings.json`；恢复 core 时连同目录标记一起还原到上述绑定状态位置。
Research 备份也保存绑定，恢复时自动还原，不需要手工生成新 UUID。
备份与原数据放在同一块磁盘不能抵御磁盘故障，应另存独立副本。
