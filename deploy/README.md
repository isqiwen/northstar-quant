# 部署

| 主机 | 服务 |
|---|---|
| `core.local` | Data Hub 前端、API、同步 worker；独立 PostgreSQL |
| `research.local` | Research 前端、API、worker；本机 SQLite 和 DuckDB |
| Live 独立主机 | 自己的前端、API、内核和本地存储 |

应用只读写固定的目录，不感知存储设备、服务器、协议或挂载方式。
部署脚本按 `hosts.toml` 准备行情 NFS，其余缺失目录在本机创建。显式关闭 NFS 时可全部使用本地磁盘。

## 行情共享

默认配置：

```toml
[nfs]
server = "research" # core / research / external
enabled = true
```

| 服务端选择 | core | Research |
|---|---|---|
| `research`（默认） | NFS 读写挂载 | 本地提供共享，研究容器只读 |
| `core` | 本地写入并提供共享 | NFS 只读挂载 |
| `external` | NFS 读写挂载 | NFS 只读挂载 |

选择 `external` 时增加 `[nfs_server]`，填写 `host`、`user`、`port`，格式与应用主机一致。
自动管理的服务端和客户端要求 Ubuntu/Debian Linux；QNAP 等设备不能直接使用 Linux SSH 安装流程。
`init-host` 会同时准备所需 NFS 服务端账号；`deploy database/data-hub/research` 先准备服务端，
再准备当前应用挂载和依赖。Research 自己的 `deploy research` 同样自动安装 Docker、Compose、Buildx 和 uv。
服务端只需 NFS，不因仅提供文件共享而安装 Docker。

唯一共享路径为 `/opt/northstar/files/market`，NFSv4、TCP 2049。服务端按解析后的客户端 IPv4 地址
导出：core 可写、Research 只读；启用 UFW 时自动放行对应客户端的 2049/TCP。
其他防火墙需允许这些客户端访问该端口。主机地址应稳定，地址变化后重新部署刷新规则。
共享账号 `northstar-market` 由脚本创建，写请求映射到这个无登录账号，独立 Research 客户端映射到匿名只读身份，不授予远程 root 身份。
发布目录/文件使用 `755/644`，原始来源和凭据不共享；首次接管市场目录会调整其所有者和读取权限。

客户端用 systemd 持久挂载，Docker 启动前要求挂载就绪，使用 `hard` 避免把网络故障当成成功写入。
重复部署不会重复追加配置或重启 Docker。配置了 NFS 却挂载失败时停止部署，不能退回本地空目录。
Research 的 SQLite、计算临时目录、研究产物和备份目录仍在本机；Live 不配置 NFS，也不依赖该服务端。
默认方案下 Research 主机离线会影响 core 的行情写入，因此服务端应持续在线。

**切换已有部署**：先停止 Data Hub 写入和 Research 使用，联合备份数据库与文件；把完整市场目录
（包括 `.northstar-storage-id`）迁移到目标服务端并核对文件哈希。保留旧数据直至验证完成。
卸载旧客户端挂载并移除对应 `opt-northstar-files-market.mount`、Docker 的
`northstar-market.conf` 和 `state/nfs/client.json`；再修改 `server` 并重新部署受影响应用。
旧服务端停止使用后移除其 `/etc/exports.d/northstar.exports` 并执行 `exportfs -ra`。
脚本拒绝覆盖非空本地目录或替换不同挂载，不自动移动、删除业务数据。
`enabled = false` 适用于本地部署；它也不会自动卸载已有共享或删除其配置。

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
除已配置 NFS 的市场目录外，`files/` 默认使用本地磁盘。
Live 不使用 Data Hub/Research 的文件目录。

| 本机目录 | 所有者 | 权限 |
|---|---|---|
| 新建 `files/<用途>` | northstar:northstar | `750` |
| `apps/<应用>` | northstar:northstar | `755` |
| `config`、`logs/<应用>` | 同上 | `750` |
| `state/<应用>`、`bindings`、`credentials/<应用>`、`work/research` | 同上 | `700` |
| 各应用 `.env` | 同上，保留内容 | `600` |
| PostgreSQL 数据目录 | 新建时为部署用户，初始化后由 PostgreSQL 管理 | 新建 `700`，已有目录不改权限/所有者 |

所有主机统一使用 northstar 部署用户。普通应用目录只管理目录本身；NFS 首次接管市场目录时单独调整发布文件权限。
Python 容器当前以 root 运行；Next.js 以 node 运行且不挂载业务数据和凭据，前端日志由 Docker 收集。
原始来源、私有目录保留原权限：部署用户需要读取身份标记，后端需要对应读写权限；不使用 777。
脚本记录已准备的持久目录；后续目录丢失时要求恢复，不创建空状态替代。日志和临时目录可以重建。
本机 Make 不执行远程准备阶段，使用前须具备相同目录和权限。

文件目录缺失时自动创建；路径须非符号链接、互不包含。本地模式中已有绑定对应的空目录自动初始化；已配置 NFS 时先验证实际挂载。
PostgreSQL、SQLite、Live 状态和凭据在各自主机本地持久存储，Live 不挂载 Data Hub/Research 的文件目录。

存储编号由程序自动管理，无需在 `.env` 填写 UUID。
NFS 服务端为全新空市场目录初始化身份；已有市场保留身份。首次 `deploy database` 初始化其余空目录。
Data Hub 使用同一份绑定；Research 首次部署读取市场标记，初始化本机产物/备份目录并保存自己的绑定，
读取行情时还会与 Data Hub 的固定发布清单核对。

绑定保存在 `/opt/northstar/state/{data-hub,research}/bindings/storage.json`，目录由远程部署脚本准备，
文件为程序状态，不是用户配置。重试、重新部署和空目录本地存储复用已保存编号；已有数据库缺少绑定、非空目录缺少标记或编号改变仍报错。
使用本地空目录不会复制原存储的数据，读取旧快照仍检查所引用的真实文件。目录标记可读，绑定状态仅部署用户可读写。
直接使用本机 Make 前需确保对应 `bindings/` 目录由执行用户拥有。

容器另外验证实际文件读写、同步和身份；底层文件系统须支持应用使用的 POSIX 文件操作。

跨主机 Research 必须看到相同发布文件和对应存储 UUID，路径固定相同，实际目录内容须一致。
行情文件共享、挂载与开机顺序由上述部署配置管理。
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

打开 <http://core.local:18082>，在历史同步页设置 Tushare token 并启动同步。
Research 网页为 <http://research.local:18084>。也可使用所属主机的局域网 IPv4 地址（例如 `http://192.168.50.10:18082`）。
Live 公网网页为 <https://live.wangqiwen.me>，直连使用 Live 主机 IP:18080。三个应用不限制来源 IP，支持任意合法 IP 地址及默认主机名访问，无需配置 IP 白名单；保留同源和会话校验。
前端发布到 `0.0.0.0`，API 仍仅绑定本机。`deploy/start/restart` 自动开放对应前端端口，
通过 iptables 的 `INPUT` 和 `DOCKER-USER` 两个入口兼容 UFW 与 Docker 转发，只维护各应用自己的规则。
重复执行不追加重复规则，不修改 SSH、数据库或其他应用规则。
对应 `northstar-<应用>-firewall.service` 在开机/Docker 重启后重新应用。
要求 Docker 使用 iptables 防火墙后端（当前默认），不关闭或重置现有 UFW。
可用 `sudo unshare --net python3 scripts/acceptance/check_web_firewall.py` 在隔离网络中验证规则。
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

每次部署验证成功后，自动删除所属应用的旧源码版本和未被容器使用的旧镜像标签，只保留当前版本。部署失败不清理旧版本；仍被容器引用的旧版本会保留并明确报错。配置、业务数据、依赖与构建缓存不清理。

所属应用 `.env` 可保留未提交的本地凭据修改；部署捕获该文件并通过独立 SSH 流传输，不进入 Git 源码包。其他源码仍必须干净。默认仅首次安装配置，更新已有配置须显式传 `--env-file`。

### Caddy / FRP 访问三个应用

公网域名与所属 Next.js 前端对应如下；保留浏览器的 Host 和 Origin，不直接转发到 Python API 或发布清单端口。

| 域名 | 所属主机的前端端口 |
|---|---|
| datahub.wangqiwen.me | 18082 |
| research.wangqiwen.me | 18084 |
| live.wangqiwen.me | 18080 |

三个应用继续允许合法 IP；Data Hub/Research 也支持 core.local/research.local。
网页域名与 deploy/hosts.toml 的 SSH 部署地址是两种用途，不必相同。
Caddy 到 FRP 的 HTTP 上游须保留 `Host: datahub.wangqiwen.me`；若配置曾重写 Host，移除该重写，
或在 reverse_proxy 中使用 `header_up Host {http.request.hostport}`。FRP 也不要改写 Host。

前端先核验公网 Host/Origin，再按内部连接协议向 Python API 转发同源信息。
X-Forwarded-Host/For 不参与访问授权，也不传给 API；X-Forwarded-Proto=https 仅用于为浏览器 Cookie 增加 Secure。
HTTPS 页面经内部 HTTP 转发可以建立会话并提交带 CSRF 的操作，其他来源仍拒绝。
代理行为参考 [Caddy reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)。
