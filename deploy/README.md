# 跨主机部署

| 主机 | 服务与存储 |
|---|---|
| `core.local` | Data Hub 前端、API、同步 worker；独立 PostgreSQL 服务，活跃数据放本机 SSD |
| `nas.local` | NFS 文件共享与备份；不运行 Northstar PostgreSQL |
| `research.local` | Research 前端、API、本机 DuckDB 计算；本机 SQLite 保存研究任务与结果元数据 |
| Live 独立主机 | 自己的前端、API、内核及本地存储，不依赖上述三台主机 |

## 数据访问

Research 用 `NORTHSTAR_DATA_HUB_URL` 访问 Data Hub，只取得固定版本和文件清单；
`NORTHSTAR_PUBLICATION_TOKEN` 是两个后端共享的只读接口凭据，至少 32 字符，与 Tushare token 无关。
前端不持有这项凭据。独立只读网关默认监听 core 私网 `19083`，只允许清单 GET；管理 API `19082` 仍仅绑定本机。Data API 只把 `/api/publications` 及具体版本的 GET 开放给该身份，
管理操作仍受原有本机/同源保护。使用可信私网或加密隧道，勿向公网直接暴露明文接口。

清单包含存储 UUID、相对路径、文件长度及 SHA-256，不保存 core 的绝对路径。
Research 将 UUID 映射到自己的 `NORTHSTAR_MARKET_DIR`，只读指定文件，以本机 DuckDB 查询 Parquet；
不扫描目录选择最新版本、不复制行情进 PostgreSQL、不在 NAS 共享可写 `.duckdb`。
已装载的固定输入不随新版本改变；目录查询需要 Data API，缺失或损坏文件明确失败。
当前尚未实现跨重启的本地 SSD 行情缓存，不能承诺 NAS 失联后新任务仍可启动。
Tushare 原始响应快照到完整跨日研究输入的语义映射仍见架构中的发布边界。

## 配置

仓库维护 `deploy/hosts.toml` 和各目录的 `.env`。`database` 的默认目标是 `core.local`。
填写 SSH 用户，将各 `.env` 的运行副本放到目标主机 `env_file`，权限 600；真实口令不提交到 Git。
`northstarctl` 更新代码不会覆盖该运行副本。

- `database/.env`：core 本机 `NORTHSTAR_DATABASE_PGDATA`、管理口令、Data Hub 应用口令、共享路径/UUID。
- `data_hub/.env`：相同应用口令及 `NORTHSTAR_DATA_DATABASE_NETWORK`、只读发布入口私网绑定地址、只读接口 token、NAS 挂载。
- `research/.env`：Data Hub URL/token、市场只读挂载、研究产物挂载。
- `live/.env`：保持独立运行配置。

core PostgreSQL 不发布宿主机端口，通过独立内部 Docker 网络供 Data Hub 使用。
Data Hub 启停不包含数据库服务；单独停止 database 会影响数据管理操作，但不会操作 Research/Live 的本地数据库。
Research 的 SQLite 文件使用本机 `state` 持久卷，不部署 PostgreSQL 容器。
SQLite 使用 WAL、FULL 同步及事务串行写入；DuckDB 只承担行情查询。
应用账号没有超级用户、建库、建角色或 DDL 权限；管理凭据仅交给初始化/维护容器。

## 暂无 NAS：core 单机测试

同一套 Compose 支持 `NORTHSTAR_STORAGE_MODE=local`；默认仍为 `nfs`。
将 `deploy/database/.env`、`deploy/data_hub/.env` 复制到 core 的
`/etc/northstar/database.env`、`/etc/northstar/data-hub.env`，权限 600。
在这两份运行配置中设置相同的本机路径：

```dotenv
NORTHSTAR_STORAGE_MODE=local
NORTHSTAR_SOURCE_MOUNT=/var/lib/northstar/files/source
NORTHSTAR_MARKET_MOUNT=/var/lib/northstar/files/market
NORTHSTAR_RESEARCH_MOUNT=/var/lib/northstar/files/research
NORTHSTAR_BACKUP_MOUNT=/var/lib/northstar/files/backup
```

首次在 core 准备空目录：

```sh
sudo mkdir -p /var/lib/northstar/files/{source,market,research,backup}
sudo mkdir -p /var/lib/northstar/postgresql
```

目录必须在 core 的 ext4/xfs/btrfs/zfs 持久文件系统上，不能是符号链接、NFS、SMB、tmpfs 或容器临时层。
按容器运行身份准备读写权限；不要使用 777。各目录不能相同或互相包含。
数据库 `PGDATA` 仍为 `/var/lib/northstar/postgresql`，与文件目录分离。

四个 `NORTHSTAR_*_STORAGE_ID` 各生成一个 UUID，并将同一组值填入两份运行配置；例如在自己的终端运行：

```sh
python3 -c 'from uuid import uuid4; [print(f"NORTHSTAR_{name}_STORAGE_ID={uuid4()}") for name in ("SOURCE", "MARKET", "RESEARCH", "BACKUP")]'
```

数据库口令、Docker 数据库网络和发布 token 按上节填写；仅本机测试时，
`NORTHSTAR_PUBLICATION_BIND_ADDRESS=127.0.0.1` 即可。NFS 导出路径可以留空，NAS 地址不使用。
`local` 模式只安装本地检查所需工具，不要求 NFS 客户端。然后从本机仓库执行：

```sh
./scripts/northstarctl.py deploy database --config ~/.config/northstar/hosts.toml
./scripts/northstarctl.py deploy data-hub --config ~/.config/northstar/hosts.toml
ssh -N -L 18082:127.0.0.1:18082 用户名@core.local
```

打开 <http://127.0.0.1:18082>，在历史同步页设置 Tushare token 并启动全量同步。
来源、行情和备份此时都在 core；本机备份不能抵御 core 磁盘损坏。
Research 暂不需要部署；以后若也在 core 测试，可选择相同本地目录，市场仍以只读方式挂入其容器。
另一台主机不能直接读取 core 本地路径。

购买 NAS 后需显式迁移：停止数据写入，备份数据库与文件，将完整文件和身份标记迁移到已核验的 NAS 共享，
保留对应存储 UUID，再把运行配置切换为 `nfs` 并填写挂载/导出信息。脚本不自动搬迁或覆盖数据。
`nfs` 模式缺失挂载仍报错，绝不自动回退到 `local`。

## NAS 挂载

| 共享 | 挂载点示例 | core | research |
|---|---|---|---|
| 来源 | `/mnt/northstar/source` | 读写 | 不挂载 |
| 市场发布 | `/mnt/northstar/market` | 读写 | 只读 |
| 研究产物 | `/mnt/northstar/research` | 只读使用引用 | 读写 |
| 备份 | `/mnt/northstar/backup` | 维护时读写 | 维护时读写 |

QNAP 共享名称不等于实际导出路径；在 `.env` 填写已核验的 `NORTHSTAR_*_NFS_EXPORT`。
四个共享使用不同 UUID，同一共享在两台机器上的 UUID 一致，挂载点可以不同。
默认 `NORTHSTAR_NAS_ADDRESS=nas.local`、NFSv4；容器无法解析 mDNS 时填写固定 IP 或私有 DNS。

```sh
sudo mkdir -p /mnt/northstar/market
# research 使用 ro，core 发布挂载使用 rw；替换实际地址和导出路径
sudo mount -t nfs -o vers=4,hard,ro NAS_ADDRESS:VERIFIED_EXPORT /mnt/northstar/market
```

管理员先创建目录并配置受限 UID/GID 权限，不使用 chmod 777。主机检查 NFS 地址、导出、版本和模式，
容器检查存储 UUID；缺失挂载明确拒绝，不静默写到同名空目录。
首次数据库初始化在 core 为配置的空共享写入身份标记，需要这些目录的初始化写权限。
PGDATA 必须是 core 本机持久文件系统，不能是 NFS/SMB、临时文件系统或容器临时层。

## 启动和维护

```sh
# core：先准备 NAS 挂载和本机 PGDATA，再分别启动数据库与 Data Hub
make up-database ENV_FILE=/etc/northstar/database.env
make up-data ENV_FILE=/etc/northstar/data-hub.env
# research：只读挂载市场，准备自己的产物挂载
make up-research ENV_FILE=/etc/northstar/research.env
```

远程统一入口：本机需要 Python 3.11+/Git/SSH；目标主机先具备 SSH、Python 3.11+。
`deploy` 在传送代码前自动检测并安装 Git、uv、Docker Engine、Compose 和 Buildx；
非 Live 对象另安装 NFS 客户端与 findmnt。自动安装支持 Ubuntu/Debian amd64，
需要 root 或免交互 `sudo -n`，以及软件源网络访问。已有可用工具直接复用，不主动升级或重启已有 Docker。
Docker 使用官方 apt 源，uv 0.11.6 安装到部署用户的独立工具环境。
首次安装 Docker 自动加入部署用户组，再重新连接 SSH 生效。
已有 Docker 不可访问或发行版运行时缺少插件时明确报错，不自动替换运行时。
其他系统须预装依赖；`start/restart/stop/status/logs` 不安装软件。

私有 env 文件、NAS 共享与挂载、PGDATA 仍需提前准备；脚本不生成口令或猜测挂载路径。
部署目录不存在时自动创建并交给部署用户。`--dry-run` 不连接主机，也不安装依赖。
本机 Make 入口不执行主机自动安装，首次远程部署使用下列命令：

```sh
./scripts/northstarctl.py deploy database
./scripts/northstarctl.py deploy data-hub
./scripts/northstarctl.py deploy research
./scripts/northstarctl.py status data-hub
./scripts/northstarctl.py restart research
```

Makefile 快捷命令与远程入口共用 `scripts/operations/compose.py` 的本机执行规则。
支持 deploy/start/restart/stop/status/logs、`--config`、`--dry-run`、`--help`。
部署传送干净 Git HEAD；start/restart 使用已部署镜像；停止保留数据卷。
Live 的整套启停包含内核，但不代表撤单、平仓或完成核对，启动也不自动连接柜台或授予交易权。
浏览器仍可用 SSH 隧道访问各前端本机端口，见根 README。

## 备份与恢复

在 core 执行 `make backup-database ENV_FILE=/etc/northstar/database.env`，
保存 Data Hub 数据库、角色和固定文件到 NAS 的新时间/UUID 目录，`complete.json` 表示完整完成。
Research 用自己的维护容器执行 `northstar maintenance backup <新的备份绝对目录>`，
保存本机研究数据库及引用的市场 Parquet、清单和结果文件。
正常 API/worker 不挂载备份共享。可由各主机定时任务调用，仓库不修改个人调度器。

恢复只接受空数据库和全新目录，核验内容与存储身份后再启动；不覆盖个人数据。
旧 NAS 三库部署不会自动迁移或删除，实际迁移需要先备份并明确现场操作范围。
发布格式已改为清单与 Parquet；旧发布目录须从保留的数据证据重建到新目录，不覆盖旧快照。
NAS 备份仍需另存外接盘/异地副本；NFS、权限映射、断电和三物理主机故障必须现场验收。
本地容器模拟共享的检查不代表 QNAP 现场已经部署。
