# 部署

| 主机 | 服务 |
|---|---|
| `core.local` | Data Hub 前端、API、同步 worker；独立 PostgreSQL |
| `research.local` | Research 前端、API、worker；本机 SQLite 和 DuckDB |
| Live 独立主机 | 自己的前端、API、内核和本地存储 |

应用只读写配置的目录，不感知存储设备、服务器、协议或挂载方式。
主机管理员提前准备持久目录；目录可以由本机磁盘或已挂载的共享提供，应用配置相同。

## 主机与凭据

仓库维护 `deploy/hosts.toml` 和各目录的 `.env`。`database` 默认部署到 `core.local`。
填写 SSH 用户，将各 `.env` 的运行副本放到目标主机 `env_file`，权限 600；真实口令不提交到 Git。
`northstarctl` 更新代码不会覆盖运行副本。

- `database/.env`：core 本机 PGDATA、管理员口令、Data Hub 应用口令、文件目录与存储 UUID。
- `data_hub/.env`：相同应用口令和数据库网络名称、文件目录与相同 UUID、发布接口地址与 token。
- `research/.env`：Data Hub URL/token、市场目录和研究产物目录。
- `live/.env`：独立运行配置，见 [Live 部署](live/README.md)。

数据库管理员与应用口令至少 16 字符且不同。core PostgreSQL 不发布宿主机端口，
通过内部 Docker 网络供 Data Hub 使用。Research 不连接此数据库。
应用账号没有超级用户、建库、建角色或 DDL 权限，管理员凭据仅交给初始化/维护容器。

## 存储目录

默认目录可以直接用于 core 单机部署：

| 配置 | 默认主机目录 | Data Hub | Research |
|---|---|---|---|
| `NORTHSTAR_SOURCE_MOUNT` | `/var/lib/northstar/files/source` | 读写 | 不访问 |
| `NORTHSTAR_MARKET_MOUNT` | `/var/lib/northstar/files/market` | 读写 | 只读 |
| `NORTHSTAR_RESEARCH_MOUNT` | `/var/lib/northstar/files/research` | 只读引用 | 读写 |
| `NORTHSTAR_BACKUP_MOUNT` | `/var/lib/northstar/files/backup` | 维护时使用 | 维护时使用 |

`*_MOUNT` 表示挂入容器的主机目录，不要求它本身是操作系统挂载点。
创建专用空目录并给部署用户及容器运行身份相应权限，不使用 777：

```sh
sudo mkdir -p /var/lib/northstar/files/{source,market,research,backup}
sudo mkdir -p /var/lib/northstar/postgresql
```

目录须已存在、非符号链接、互不包含。PostgreSQL 活跃目录使用 core 本地持久磁盘，
Research SQLite 使用 research 本地持久卷；临时计算目录也在所属主机本地。

四个存储目录各有一个 UUID。生成后，把同一组值填入 database 和 Data Hub 的运行配置：

```sh
python3 -c 'from uuid import uuid4; [print(f"NORTHSTAR_{name}_STORAGE_ID={uuid4()}") for name in ("SOURCE", "MARKET", "RESEARCH", "BACKUP")]'
```

首次部署在空数据库、空目录上初始化 `.northstar-storage-id`。以后启动或重新部署都必须匹配该身份；
目录缺失、身份不符或恢复未完成会报错，不自动创建替代目录、不重新初始化已使用的存储。
容器另外验证实际文件读写、同步和身份；底层文件系统须支持应用使用的 POSIX 文件操作。

跨主机 Research 必须看到相同发布文件和对应存储 UUID，各主机路径可以不同。
怎样把这些文件提供给两台主机、设置挂载与开机顺序，由主机管理负责。
更换存储时先停止相关写入、备份数据库与文件，迁移完整内容和身份标记，再恢复运行；
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
`deploy` 传送干净 Git HEAD；`start/restart` 使用已部署镜像；停止保留数据卷。
`--dry-run` 不连接主机或安装软件。其他管理命令也不安装软件。
Live 整套启停包含内核，但不代表撤单、平仓或完成核对，也不自动授予交易权。

也可在目标主机仓库根目录执行（依赖已准备）：

```sh
make up-database ENV_FILE=/etc/northstar/database.env
make up-data ENV_FILE=/etc/northstar/data-hub.env
make up-research ENV_FILE=/etc/northstar/research.env
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

在 core 执行 `make backup-database ENV_FILE=/etc/northstar/database.env`，
把 Data Hub 数据库、角色和固定文件保存到备份目录的新时间/UUID 子目录，`complete.json` 表示完成。
Research 用自己的维护容器执行 `northstar maintenance backup <新的备份绝对目录>`。
正常 API/worker 不挂载备份目录；定时备份由各主机调度器调用。

数据库与引用文件必须联合备份，恢复仅接受空数据库和全新目录，不覆盖原有数据。
备份与原数据放在同一块磁盘不能抵御磁盘故障，应另存独立副本。
