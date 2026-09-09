# 跨主机部署

| 主机 | 配置 | 运行内容 |
|---|---|---|
| `nas.local`（QNAP） | `nas/compose.yaml` | PostgreSQL、数据库与来源目录初始化 |
| `core.local`（Linux） | `data_hub/compose.yaml` | Data Hub 前端、API、持久 worker |
| `research.local`（Linux） | `research/compose.yaml` | Research 前端、API、本机临时计算目录 |
| Live 独立主机 | `live/compose.yaml` | 独立前端、API、内核及当前本地数据库 |

三个应用各自启停，前后端分容器。Data Hub/Research 通过 NAS 的网络 PostgreSQL 端点和 NFS 共享访问当前数据，
不再共享 Docker 网络或本机命名卷。Data Hub 管理和写入来源；Research 的来源挂载只读，业务接口只提供已发布且校验通过的数据。
Research 可独立于 Data Hub 进程运行，但仍依赖 NAS。跨主机共享存储不等于离线快照复制。

PostgreSQL 的 `database` 卷在 **NAS 自己的本地磁盘**，不把 PGDATA 放在 NFS 客户端挂载目录。
Research 的 `work` 卷和 `TMPDIR` 在 **research.local 本地**；各应用运行日志也留在本机。
Live 不挂载家庭 NAS，也不连接 NAS 数据库完成实时交易。NAS、core、research 故障不得阻塞 Live 的交易和本地恢复；
固定运行材料保存在 Live 本地，远端仅可异步归档。其“最小本地恢复日志＋异步上传 NAS”是下一项改造，当前仍保留已验证的本地 PostgreSQL。

## 1. 确认 NAS 参数

QNAP 共享计划名称为 `northstar`，QTS / QuTS hero 版本、共享是否已创建、实际 NFS 导出路径仍待现场确认。
**共享名称不能直接当作导出路径。** 本仓库不猜测 `/share/...` 等路径。

复制 [nas.env.example](examples/nas.env.example)、[core.env.example](examples/core.env.example)、
[research.env.example](examples/research.env.example) 到各自主机的私有配置文件，填写空值并限制文件权限。

| 参数 | 含义 |
|---|---|
| `NORTHSTAR_NAS_ADDRESS` | 从应用 Docker 主机可访问的 NAS IP；容器内显式映射为 `nas.local`，不依赖容器支持 mDNS |
| `NORTHSTAR_NAS_BIND_ADDRESS` | NAS 发布数据库的私网接口地址 |
| `NORTHSTAR_NAS_DATABASE_PORT` | NAS PostgreSQL 端口，默认 `15432` |
| `NORTHSTAR_NAS_DATABASE_PASSWORD` | 三台主机一致的私有 URL-safe 数据库口令，无默认值 |
| `NORTHSTAR_NAS_SHARE_DIR` | QNAP 上共享的实际本地目录，仅 NAS 配置使用 |
| `NORTHSTAR_NFS_EXPORT` | 经 QNAP 确认的 NFS 导出路径，必填 |
| `NORTHSTAR_NFS_VERSION` | 默认 `4`，可明确指定 `4.1`、`4.2` 或经验证的 `3` |
| `NORTHSTAR_NAS_MOUNT` | Linux 挂载点，默认 `/mnt/northstar` |
| `NORTHSTAR_STORAGE_ID` | 同一份来源存储的固定 UUID，三台主机一致，不能每次启动重生成 |

首次可用 `python3 -c 'import uuid; print(uuid.uuid4())'` 生成存储 UUID。
在实际共享目录准备 `sources/` 和 `backups/`；首次初始化的 `sources/` 必须为空。
已有数据没有身份标记时拒绝自动接管，须单独安排迁移。修改口令配置不会轮换已有 PostgreSQL 的口令。

在 QNAP 配置 NFS 授权：`core.local` 可读写，`research.local` 只读；实际主机地址、UID/GID 映射和共享权限要一致。
来源目录包含私有证据，应用使用严格权限；必须实际确认容器用户可访问，不能只以挂载成功作为验收。
数据库仅允许所需私网客户端访问，不公开 NAS 管理端口或数据库到互联网。

## 2. NAS 初始化与 Linux 挂载

在 `nas.local` 上，以该主机的环境文件启动 PostgreSQL 并初始化数据库和来源目录：

```sh
make up-nas ENV_FILE=/absolute/private/nas.env
```

NAS 上使用 Docker 本地卷保存 PostgreSQL；初始化不会关闭持久化配置或覆盖已有来源。
QNAP 的共享创建、NFS 服务启用和客户端挂载由管理员明确完成，启动脚本不会自动改 NAS 设置或执行 sudo 挂载。

在两台 Linux 主机安装 NFS 客户端后，用核实的 NAS 地址和导出路径挂载。例如下列占位符必须替换：

```sh
# core.local：读写；research.local 将 rw 改为 ro
sudo mkdir -p /mnt/northstar
sudo mount -t nfs -o vers=4,hard,rw NAS_IP:VERIFIED_EXPORT /mnt/northstar
```

需要开机挂载时，将同样的已核实参数加入主机的挂载管理配置；应用应在网络和该挂载就绪后启动。
NFSv4 的实际导出路径与客户端版本必须以 QNAP 配置为准。不要使用 soft 挂载来规避存储故障。

## 3. 各主机启动应用

```sh
# core.local
make up-data ENV_FILE=/absolute/private/core.env
# research.local
make up-research ENV_FILE=/absolute/private/research.env
```

启动前检查 Linux 实际挂载点、NFS 类型、服务器/导出路径、协议版本、hard 及读写模式；
缺失或错误时明确退出，不创建本地替代目录。容器使用 `create_host_path: false`，再检查存储 UUID。
Data Hub 还通过临时探针验证写入、硬链接、刷盘和内容读取；探针不修改已留存数据。
这些检查不证明 NAS 硬件的断电持久性，也不能替代真实双主机 NFS 验收。

手动执行 `docker compose` 前也必须先运行 `scripts/check_nfs_mount.py --app data_hub|research --env-file ...`；
Compose 的身份检查不能替代主机 NFS 挂载检查。应用重启不会初始化或启动远端 NAS。

各应用使用对应的 `make ps-data` / `ps-research`、`make down-data` / `down-research`，并传相同 `ENV_FILE`。
NAS 独立使用 `make ps-nas` / `down-nas`；停止 NAS 会影响依赖它的数据和研究操作。
所有停止命令保留卷，不用 `down -v` 停止日常服务。已有个人容器、端口和持久数据不会自动迁移。

## 4. 网页访问和备份

当前 Web 继续仅绑定主机回环地址并校验本机 Host/Origin。从操作电脑建立 SSH 隧道：

```sh
ssh -N -L 18082:127.0.0.1:18082 core.local
ssh -N -L 18084:127.0.0.1:18084 research.local
```

浏览器访问 `http://127.0.0.1:18082` / `http://127.0.0.1:18084`。隧道本地端口已被占用时选空闲端口。
不直接把现有工作台开放到整个 LAN；需要直接域名访问时另行接入身份认证和 TLS。

Data Hub 容器的 `/var/lib/northstar/backups` 对应 NAS 共享中的 `backups/`，使用已有联合备份操作：

```sh
docker compose --env-file /absolute/private/core.env -f deploy/data_hub/compose.yaml exec data-api northstar maintenance backup /var/lib/northstar/backups/manual-001
```

目标必须尚不存在。备份包含同一快照的数据库和引用来源文件，不单独复制运行中的 PGDATA。
恢复仍要求空数据库和全新来源目录；显式设置 `NORTHSTAR_STORAGE_ID` 后，恢复同时初始化目标存储身份。
恢复失败保留禁止启用标记，不把恢复成功当成交易授权。

## 验收边界

`scripts/check_application_deployment.py` 使用独立 Docker 网络、已发布数据库端口和临时 bind 目录，
验证网络数据库、队列加工、固定数据回测、备份落点和错误存储身份拒绝。bind 目录模拟共享，**不是实际 NFS 或三台物理主机验收**。
真实 `core.local` / `research.local` / QNAP 的导出路径、挂载与断网行为须在参数确认后另行验收。
`scripts/check_live_deployment.py` 继续验证当前独立 Live 故障路径，不连接柜台。
