# 部署

| 主机 | 服务 |
|---|---|
| `core.local` | Data Hub 前端、API、同步 worker；独立 PostgreSQL |
| `research.local` | Research 前端、API、worker；本机 SQLite 和 DuckDB |
| Live 独立主机 | 自己的前端、API、内核和本地存储 |

应用只读写固定的目录，不感知存储设备、服务器、协议或挂载方式。
主机管理员提前准备持久目录；目录可以由本机磁盘或已挂载的共享提供，应用配置相同。

## 主机与凭据

仓库维护 `deploy/hosts.toml` 和各目录的 `.env`。`database` 默认部署到 `core.local`。
主机配置仅接受 `host/user/port`，不接受路径字段。
将各 `.env` 的运行副本放到 `/opt/northstar/config/{database,data-hub,research,live}.env`，
权限 600，所属用户为 SSH 部署用户；真实口令不提交到 Git。
`northstarctl` 更新代码不会覆盖运行副本。

- `database/.env`：管理员口令、Data Hub 应用口令。
- `data_hub/.env`：相同应用口令和数据库网络名称、发布接口地址与 token。
- `research/.env`：Data Hub URL/token、连接与凭据。
- `live/.env`：独立运行配置，见 [Live 部署](live/README.md)。

数据库管理员与应用口令至少 16 字符且不同。core PostgreSQL 不发布宿主机端口，
通过内部 Docker 网络供 Data Hub 使用。Research 不连接此数据库。
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

首次在对应主机准备目录，例如：

```sh
# core
sudo mkdir -p /opt/northstar/files/{source,market,research,backup}
sudo mkdir -p /opt/northstar/{config,state/data-hub/postgresql,credentials/data-hub,logs/data-hub}
# research
sudo mkdir -p /opt/northstar/files/{market,research,backup}
sudo mkdir -p /opt/northstar/{config,state/research,logs/research,work/research}
# Live 主机
sudo mkdir -p /opt/northstar/{config,state/live/postgresql,state/live/sources,credentials/live,logs/live}
```

部署用户需要遍历目录、读取身份标记，容器运行身份需要对应读写权限；当前 Python 容器以 root 运行。
不要使用 777。凭据目录应限制无关用户访问，配置文件为 600。
目录须已存在、非符号链接、互不包含；缺失时不自动创建替代目录。
PostgreSQL、SQLite、Live 状态和凭据在各自主机本地持久存储，Live 不挂载 Data Hub/Research 的文件目录。

存储编号由程序自动管理，无需在 `.env` 填写 UUID。
首次 `deploy database` 为四个空目录自动分配身份，数据库初始化将标记写入各目录。
Data Hub 使用同一份绑定；Research 首次部署读取共享目录标记并保存自己的绑定，
读取行情时还会与 Data Hub 的固定发布清单核对。

绑定保存在 `/opt/northstar/state/{data-hub,research}/bindings/storage.json`，目录由远程部署脚本准备，
文件为程序状态，不是用户配置。重试和重新部署复用已保存编号；已有数据库缺少绑定、目录标记缺失或编号改变都会报错，
不会自动换号或修复成新空目录。目录标记可读，绑定状态仅部署用户可读写。
直接使用本机 Make 前需确保对应 `bindings/` 目录由执行用户拥有。

容器另外验证实际文件读写、同步和身份；底层文件系统须支持应用使用的 POSIX 文件操作。

跨主机 Research 必须看到相同发布文件和对应存储 UUID，路径固定相同，实际目录内容须一致。
怎样把这些文件提供给两台主机、设置挂载与开机顺序，由主机管理负责。
更换文件存储时先停止相关写入、备份数据库与文件，迁移完整内容和身份标记，再恢复运行；
保持原路径和 UUID 时不需要修改应用配置。脚本不搬迁或覆盖现有数据。

## 部署与访问

远程入口本机需要 Python 3.11+/Git/SSH，目标主机先具备 SSH、Python 3.11+。
`deploy` 自动检测并安装 Git、uv、Docker Engine、Compose、Buildx；
支持 Ubuntu/Debian amd64，需要 root 或免交互 `sudo -n` 以及软件源网络访问。
已有可用工具直接复用，不主动升级或重启已有 Docker。存储挂载工具由主机管理员管理。
首次安装 Docker 后自动配置部署用户组并重新连接 SSH；其他系统须预装依赖。

先准备私有配置与上述目录，再执行：

```sh
./scripts/northstarctl.py deploy database --config ~/.config/northstar/hosts.toml
./scripts/northstarctl.py deploy data-hub --config ~/.config/northstar/hosts.toml
./scripts/northstarctl.py status data-hub --config ~/.config/northstar/hosts.toml
ssh -N -L 18082:127.0.0.1:18082 用户名@core.local
```

打开 <http://127.0.0.1:18082>，在历史同步页设置 Tushare token 并启动同步。
仅测试 Data Hub 时无需部署 Research/Live；发布接口绑定地址可设为 `127.0.0.1`。
前端/API 与同步 worker 独立，关闭管理界面不停止已提交任务。

支持 `deploy/start/restart/stop/status/logs`、`--config`、`--dry-run`、`--help`。
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
两端 `NORTHSTAR_PUBLICATION_TOKEN` 相同且至少 32 字符，与 Tushare token 无关，前端不持有它。
独立只读网关默认端口 `19083`，只允许清单 GET；管理 API `19082` 仅绑定本机。
跨主机填写 core 私网绑定地址，使用可信私网或加密隧道，不向公网暴露明文接口。

清单记录存储 UUID、相对路径、文件长度和 SHA-256，不记录 core 绝对路径。
Research 使用本机 DuckDB 计算，不扫描目录追踪最新文件、不共享可写 DuckDB。
缺失或损坏文件明确失败；跨重启 SSD 缓存尚未实现。固定供应商响应到完整跨日研究输入仍有语义边界。

## 备份

在 core 执行 `make backup-database`，
把 Data Hub 数据库、角色和固定文件保存到备份目录的新时间/UUID 子目录，`complete.json` 表示完成。
Research 在目标主机运行 `uv run --project backend python scripts/operations/compose.py backup research`，
自动加载存储绑定并在维护容器内写入新的备份子目录。
正常 API/worker 不挂载备份目录；定时备份由各主机调度器调用。

数据库与引用文件必须联合备份，恢复仅接受空数据库和全新目录，不覆盖原有数据。
core 联合备份包含 `storage-bindings.json`；恢复 core 时连同目录标记一起还原到上述绑定状态位置。
Research 备份也保存绑定，恢复时自动还原，不需要手工生成新 UUID。
备份与原数据放在同一块磁盘不能抵御磁盘故障，应另存独立副本。
