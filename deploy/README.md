# NAS 与三应用部署

| 主机 | 运行内容 | 配置 |
|---|---|---|
| `nas.local` / Qu605 | 一个 PostgreSQL 服务、共享文件、集中备份 | `nas/compose.yaml` |
| `core.local` | Data Hub 前端、API、独立加工 worker | `data_hub/compose.yaml` |
| `research.local` | Research 前端、API、本机临时计算 | `research/compose.yaml` |
| Live 独立主机 | 前端、API、交易内核与当前本地存储 | `live/compose.yaml` |

NAS、core、research 故障不得成为 Live 交易和本地恢复的依赖。NAS 的 Live 库仅用于异步归档；
当前内核仍使用自己的本地 PostgreSQL，恢复日志和独立上传器见 #54，不能提前删除本地数据库。

## 数据库与账号

| NAS 数据库 | 日常账号 | 保存内容 |
|---|---|---|
| `northstar_data_hub` | `northstar_data_hub_app` | 来源、加工、质量、目录和快照 |
| `northstar_research` | `northstar_research_app` | 配置、因子、实验、回测、Paper 与候选 |
| `northstar_live` | `northstar_live_app` | 按写者和序号去重的不可变归档记录 |

三个账号不具备超级用户、建库、建角色或绕过行安全权限；不能连接另外两个业务库或建表。
`northstar_admin` 只在 NAS 初始化和集中备份时使用，不分发到应用容器。
各库的 `*_owner` 是无登录管理角色，运行账号只能操作已授权业务表，不能修改数据库归属标记。
NAS 初始化会检查归属并设置配置中的应用口令；更改口令后须同步所属主机配置并重建容器。

Data Hub 发布校验过的固定市场文件；Research 从只读市场共享读取，不访问 Data Hub 的 SQL 表或来源目录。
研究结果保存到自己的数据库与产物目录，Data Hub 通过公开引用记录展示使用情况。
发布文件与报告均校验内容身份；数据库已提交而文件导出中断时，Data worker 在下一轮修复市场发布。
来源与已发布事实没有删除入口，未同步的使用记录不能使它们被清理。

## 创建共享并填写参数

当前已确认 `nas.local` 可访问，共享尚未创建。先复制三个模板到各自主机的私有环境文件：
[nas.env.example](examples/nas.env.example)、[core.env.example](examples/core.env.example)、
[research.env.example](examples/research.env.example)。限制配置文件权限，不提交真实口令。

| QNAP 规划共享 | Linux 挂载点 | core | research |
|---|---|---|---|
| `northstar-source` | `/mnt/northstar/source` | 读写 | 不挂载 |
| `northstar-market` | `/mnt/northstar/market` | 读写发布新版本 | 只读 |
| `northstar-research` | `/mnt/northstar/research` | 只读引用信息 | 读写 |
| `backups` | `/mnt/northstar/backup` | 仅维护容器使用 | 仅维护容器使用 |
| `appdata` | 不通过 NFS/SMB 分发 PGDATA | 无 | 无 |
| `northstar-live` | 归档文件后续按实际需要配置 | 默认无 | 默认无 |

每个共享的实际 NFS 导出路径和 NAS 本地路径分别填写，**共享名称不是导出路径**：

- NAS：`NORTHSTAR_NAS_PGDATA`、`NORTHSTAR_NAS_SOURCE_DIR`、`...MARKET_DIR`、`...RESEARCH_DIR`、`...BACKUP_DIR`。
- Linux：各自的 `NORTHSTAR_SOURCE_NFS_EXPORT` / `...MARKET_NFS_EXPORT` 等，以及 `..._MOUNT`。
- 四个共享分别使用不同的 `..._STORAGE_ID` UUID；同一共享在不同主机上使用相同 UUID。
- `NORTHSTAR_NFS_VERSION=4` 优先使用 NFSv4，也可填写已核验的 `4.1`、`4.2`。
- `NORTHSTAR_NAS_ADDRESS=nas.local` 要求容器也能解析；不支持容器 mDNS 时填写 NAS 固定 IP 或配置私有 DNS。
- 数据库默认端口 `15432`；`NORTHSTAR_NAS_BIND_ADDRESS` 必须填写 NAS 的私网接口 IP。

可用 `python3 -c 'import uuid; print(uuid.uuid4())'` 分别生成 UUID，口令使用不同的 URL-safe 随机值且至少 16 字符。
PGDATA 是 NAS **本地服务卷中的专用目录**，不是 NFS 客户端挂载；首次为空，持久化配置保持开启。
应用日志和 Research `work`/`TMPDIR` 保留在各自主机本地。

QNAP 需按主机、UID/GID、共享权限实际配置 NFS 访问。当前容器默认用户为 root，须采用受限的身份映射和对应目录所有权，
不能通过 `chmod 777` 或把所有远端用户映射为 NAS 管理员解决权限。挂载成功不代表容器可以安全读写。
Linux 使用 NFS；Mac 日常文件访问可由管理员单独配置 SMB 权限，不开放 PGDATA。

## 初始化和启动

NAS 原生共享目录及 PGDATA 目录必须已由管理员创建；程序拒绝接管无存储标记的非空目录。
旧的共用业务数据库不能直接改名使用，必须保留备份并单独安排数据迁移；本项目不维护兼容初始化路径。

```sh
# nas.local
make up-nas ENV_FILE=/absolute/private/nas.env
```

在 Linux 主机安装 NFS 客户端后，按每个共享的权限挂载。下列为占位示例，须替换实际导出路径：

```sh
sudo mkdir -p /mnt/northstar/market
# core 用 rw；research 用 ro
sudo mount -t nfs -o vers=4,hard,ro NAS_ADDRESS:VERIFIED_MARKET_EXPORT /mnt/northstar/market
```

应用须在网络和挂载就绪后启动；开机挂载由管理员写入主机挂载管理配置。不要使用 soft 挂载。
缺失、错误的服务器/导出路径、NFS 版本、读写模式或存储 UUID 均拒绝启动，不静默写入同名本地空目录。

```sh
# core.local
make up-data ENV_FILE=/absolute/private/core.env
# research.local
make up-research ENV_FILE=/absolute/private/research.env
# Live 主机
make up-live
```

`make` 先运行 Linux `findmnt` 检查；容器再检查每个共享的 UUID，并对可写共享运行临时读写/刷盘探针。
手动执行 Compose 前同样要用 `uv run --project backend python scripts/check_nfs_mount.py --app data_hub --env-file ...`，
Research 将 `--app` 改为 `research`。维护共享检查另加 `--maintenance`。

应用分别使用 `ps-data` / `down-data`、`ps-research` / `down-research`、`ps-live` / `down-live`；NAS 使用 `ps-nas` / `down-nas`。
传入相同 `ENV_FILE`，所有日常停止操作都保留存储，不使用 `down -v`。个人已有容器不会自动迁移。

浏览器通过 SSH 隧道访问，保留当前本机 Host/Origin 保护：

```sh
ssh -N -L 18082:127.0.0.1:18082 core.local
ssh -N -L 18084:127.0.0.1:18084 research.local
```

随后访问 `http://127.0.0.1:18082` / `http://127.0.0.1:18084`。Live 访问方式见 [Live 部署](live/README.md)。

## 备份与恢复

NAS 集中备份会保存三个库、数据库角色，以及 Data Hub 来源和 Research 引用的固定输入与产物：

```sh
make backup-nas ENV_FILE=/absolute/private/nas.env
```

每次写入新的 UTC 时间/UUID 目录，只有存在 `complete.json` 才表示所有步骤完成。
角色备份含敏感认证信息，须保持私有。可在 NAS 任务调度中每天调用此命令，工作目录必须是仓库根目录；
本仓库不自动修改 QNAP 调度器、删除历史备份或配置 HBS。

单应用维护使用单独的临时容器；正常 API/worker 不挂载备份共享：

```sh
docker compose --env-file /absolute/private/core.env -f deploy/data_hub/compose.yaml run --rm --no-deps maintenance northstar maintenance backup /var/lib/northstar/backup/data-manual-001
docker compose --env-file /absolute/private/research.env -f deploy/research/compose.yaml run --rm --no-deps maintenance northstar maintenance backup /var/lib/northstar/backup/research-manual-001
```

恢复必须指向**空数据库和全新目录**，由管理员使用恢复凭据执行 `northstar maintenance restore <backup-directory>`：

- Data Hub：配置新的 `NORTHSTAR_DATA_DIR` / `NORTHSTAR_STORAGE_ID`；数据库与来源一起恢复，固定市场文件由 Data worker 从已提交事实重新发布。
- Research：配置新的 `NORTHSTAR_MARKET_DIR` / `...MARKET_STORAGE_ID` 和 `NORTHSTAR_RESEARCH_DIR` / `...RESEARCH_STORAGE_ID`，并设置 `NORTHSTAR_DATABASE_OWNER=research`。
  恢复同时验证研究记录、市场输入、报告校验和 Paper 状态。临时恢复的市场目录是独立副本，不给 Research 正常运行的市场挂载增加写权限。
- NAS Live 归档：仅是归档库的 `pg_restore`，不恢复交易执行权，也不替代 Live 本地恢复和柜台核对。

恢复目标目录未通过验证时保留 `.restore-incomplete`，应用拒绝启用。确认目标身份和目录后，管理员重新应用对应库的权限再启动。
NAS 上的 `backups` 只是暂存，仍需管理员配置 NAS 外的 HBS/外接盘/异地副本及版本保留，不能用 RAID 或同池快照替代。

## QNAP 现场待办与验收边界

QTS/QuTS hero、盘内数据、容量尚未确认；不自动切系统、初始化硬盘、建 RAID 或调整卷。
按两盘镜像起步的规划核验系统支持，再配置服务卷、文件卷、快照与增长余量；具体容量不写死。
UPS 应覆盖 NAS 和相关网络设备，并现场验证断电检测、安全关机及恢复；NAS 之外还需可用性告警。
WAL/PITR、HBS 目的地、快照保留策略和硬件故障恢复尚未配置或验收，不能宣称已有零丢失或高可用能力。

`scripts/check_application_deployment.py` 验证独立 PostgreSQL 端点、三库账号权限、固定文件研究、备份恢复与错误挂载拒绝。
它使用临时 bind 目录，**不代表真实 QNAP NFS、权限映射、断电持久性或三物理主机验收**。
`scripts/check_live_deployment.py` 验证当前独立 Live 生命周期，不连接柜台；完整柜台 Sim 故障隔离另行验收。
