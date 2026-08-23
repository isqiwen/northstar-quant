# Linux 一键部署

## 适用范围

`just deploy-prod`（或 `scripts/deploy/deploy.py`）可从 Windows 或 Linux 开发机向 Ubuntu/Debian
服务器发布 Northstar Quant。Just 只路由命令，Python 控制面负责本地预检、构建源码制品与 SSH 编排；
Linux 目标端才负责运行时安装、依赖同步、数据库迁移、systemd 配置、健康检查和应用版本回退。Windows
不需要也不应运行 systemd、服务、scheduler 或未来 live trading。

`infra/systemd/` 是发布制品的一部分。目标端只会从已校验、已冻结的 release 渲染 systemd 单元，并将
渲染结果保存为该 release 的 `.northstar/systemd/` 快照；`/etc/systemd/system/` 中的活动单元只是
该快照的 root 管理副本。正常发布将已签名 manifest、runtime bundle、control bundle 和可选环境输入通过
SSH stdin 提交给固定 root release gate；root 不会从部署 SSH 身份可写的临时目录执行控制脚本。运行时状态、
真实数据、密钥和备份绝不进入制品或 manifest。

私有 ntfy 是可选的即时告警基础设施，不参与下单、撤单、风控判断或 kill switch；告警服务不可用
不能改变交易或风控的既有结果。签名 root release gate 明确拒绝 `NTFY_DEPLOY_ENABLED=1`：ntfy 的首次
身份 bootstrap 含有不能安全绑定到普通 release manifest 的秘密。因此 Northstar 常规发布不会部署、升级或
初始化 ntfy/Caddy；这必须走独立的 root-operated 运维工作流。

它不负责创建生产 PostgreSQL、配置云防火墙、购买服务器或申请券商权限。生产数据库应
独立部署并具备自动备份，不能复用开发环境的 Docker 数据卷。它也**不会安装 Docker**；
启用私有 ntfy 前，远程服务器必须由运维人员预装 Docker Engine 与 Docker Compose。

当前仓库没有 production 画像，也没有完整 CTP 报单适配器。因此当前唯一可用的部署模式是
`SERVICE_MODE=health`，并且必须保持 `NORTHSTAR_BROKER=paper` 和
`NORTHSTAR_LIVE_TRADING_ENABLED=false`。

## 服务器要求

- Ubuntu 或 Debian 64 位服务器。
- 可通过 SSH 密钥登录。
- SSH 用户必须既非 `root` 也不同于 `SERVICE_USER`，并且只可无交互调用固定的
  `sudo -n /usr/local/libexec/northstar-quant/release-gate identity` 与
  `sudo -n /usr/local/libexec/northstar-quant/release-gate submit`；不授予 `sudo -n true`、任意 shell、
  任意脚本路径或服务账户 sudo。
- 服务器管理员已按下方“root release gate bootstrap”带外安装固定 gate，并已把 `northstar-release` 的
  OpenSSH 公钥安装到 `/etc/northstar/release-allowed-signers`。工作站持有对应的未跟踪发布签名私钥；
  该私钥不上传到服务器，也不写入 `deploy.env`、`.env`、制品或 CI 日志。
- 工作站的 SSH known-hosts 必须预先固定目标主机指纹；控制面强制 `StrictHostKeyChecking=yes`，
  不会在发布时接受新指纹。端口、跳板和密钥路径如有需要只能通过受管 SSH 配置声明，不能写入
  `DEPLOY_HOST`。
- 服务器可以访问 Python 包源、所需 Python 运行时来源和生产 PostgreSQL。
- 首次部署前必须通过受审计的系统供应流程预装 `/usr/local/bin/uv`。它必须是无符号链接、无 file
  capabilities 的普通文件，权限严格为 `root:root 0755`，并且其 `uv --version` 必须与部署控制端
  使用的版本完全一致。`--setup-server` 只会验证它，绝不会下载或以 root 身份执行 uv 安装脚本。
- PostgreSQL 已创建 `northstar` 数据库和最小权限应用用户。
- 系统启用 NTP 时间同步。
- 如计划由独立 root-operated 工作流启用私有 ntfy：远程服务器已由管理员安装 Docker Engine 与 Docker
  Compose，且部署 SSH 身份和 `northstar` 服务账户均没有 Docker 权限。
- 如计划启用私有 ntfy：`NTFY_PUBLIC_HOST` 的 A/AAAA 记录已解析到该服务器，TCP `80`/`443` 对公网开放，
  且没有被其他 Web 服务占用；Caddy 需要它们申请和续期 ACME TLS 证书。

部署前检查：

```bash
uv --version
ssh -o StrictHostKeyChecking=yes deploy@example.com \
  'uname -s && id -un && sudo -n /usr/local/libexec/northstar-quant/release-gate identity && stat -c "%U:%G %a" /usr/local/bin/uv && /usr/local/bin/uv --version'
```

### root release gate bootstrap（带外管理员步骤）

这不是常规部署的一部分，也不是 `--setup-server` 的职责。服务器管理员必须先在受审阅的 root 控制目录中
准备 `root_release_runner.py` 和仅含公钥的 OpenSSH `allowed_signers` 文件，手动核对 SHA-256，然后以 root
显式执行一次无覆盖 bootstrap。例如：

```bash
sudo /root/reviewed/release_gate_bootstrap.py \
  --gate-source /root/reviewed/root_release_runner.py \
  --expected-gate-sha256 "$(sha256sum /root/reviewed/root_release_runner.py | awk '{print $1}')" \
  --allowed-signers-source /root/reviewed/release-allowed-signers \
  --expected-allowed-signers-sha256 "$(sha256sum /root/reviewed/release-allowed-signers | awk '{print $1}')" \
  --apply \
  --confirm-root-gate-bootstrap INSTALL_ROOT_RELEASE_GATE
```

两个 source 都必须是绝对路径，gate source 及其目录链必须由 root 控制，且不得位于 SSH 暂存或临时目录。
bootstrap 将无覆盖地安装固定 gate wrapper、root runner 和公钥 authority；若目标文件已存在或留下部分证据，
流程会失败关闭并保留证据供人工审查。普通部署、CI 和部署 SSH 身份都不能 bootstrap、替换 gate 或修改
`release-allowed-signers`。

## 本地配置

部署目标使用不含密钥的 `deploy.env`：

```bash
cp deploy.env.example deploy.env
```

至少修改：

```text
DEPLOY_HOST=deploy@example.com
SERVICE_MODE=health
```

`DEPLOY_HOST` 只接受 SSH 别名、`user@host` 或 `user@[IPv6]`。不得写端口、路径、额外
SSH 选项或 `root`/`SERVICE_USER` 身份；需要端口、跳板或密钥时，使用工作站受管的 SSH 配置。

`deploy.env` 只描述非机密部署目标与运行时**叶子目录**。生产身份固定为
`APP_NAME=northstar-quant`、`SERVICE_USER=northstar` 和
`SYSTEMD_SERVICE_NAME=northstar-quant`；控制面会拒绝覆盖为其他账户或 systemd 服务。
`SERVICE_HOME` 不再是清单字段，也不能改写生产目录根。

### 固定 FHS 目录与权限边界

Linux 生产环境使用下面唯一的布局。代码、发布指针、部署元数据和密钥配置由 root 管理；服务账户只获得
实际运行所需的可写叶子目录，不能修改下次启动会执行的代码、`current` 指针、systemd 单元、密钥或部署状态。

| 用途 | 固定位置 | 所有权与访问边界 |
| --- | --- | --- |
| release 代码与原子指针 | `/opt/northstar/releases/`、`/opt/northstar/current` | root 管理；已验证 release 会冻结，`northstar` 无写权限。 |
| 规范环境指针 | `/etc/northstar/northstar-quant.env -> /opt/northstar/current/.env` | `/etc/northstar/` 为 `root:northstar 0750`；指针由 root 管理，链接目标必须精确为该路径；它不保存秘密，也不是可提升的全局配置文件。 |
| release 环境快照 | `/etc/northstar/releases/<release>.env`，以及 `/opt/northstar/releases/<release>/.env -> /etc/northstar/releases/<release>.env` | `releases/` 为 `root:northstar 0750`；快照是 `root:northstar 0640` 的普通文件，release 内链接由 root 创建；服务账户只读，不能改写任一 release 的下一次启动配置。 |
| 持久状态根 | `/var/lib/northstar/` | 父目录为 `root:northstar 0750`；`deploy-state/` 为 `root:root 0700`，`python/` 由 root 管理。 |
| 缓存根 | `/var/cache/northstar/` | 父目录为 `root:northstar 0750`；仅下列运行时缓存叶子可由服务写入。 |
| 日志根 | `/var/log/northstar/` | 父目录为 `root:northstar 0750`；仅 `app/` 叶子可由服务写入。 |
| 服务可写叶子 | `/var/lib/northstar/{storage,downloads,reports}`、`/var/cache/northstar/{runtime,matplotlib,uv-cache,dashboard,venv-build}`、`/var/log/northstar/app` | 每一个都是受 root 控制父目录的直接孩子，且为 `northstar:northstar 0750`；`dashboard/` 仅在显式启用私网 Dashboard 时使用，`venv-build/` 仅供受限构建过程使用。 |

这是一次干净的 breaking change：旧的
`/srv/northstar/northstar-quant/shared/` 布局没有兼容别名、软链接回退或自动迁移。不要把旧路径传入
`deploy.env`，也不要手工把它链接到上述根目录。需要保留旧主机上的数据时，先由人工按经批准的迁移/
备份流程处理；仓库自动化不会移动、删除或清空旧状态、数据库或卷。

运行时路径字段默认如下：

```text
RUNTIME_STORAGE_DIR=/var/lib/northstar/storage
RUNTIME_DOWNLOADS_DIR=/var/lib/northstar/downloads
RUNTIME_REPORTS_DIR=/var/lib/northstar/reports
RUNTIME_LOG_DIR=/var/log/northstar/app
RUNTIME_CACHE_DIR=/var/cache/northstar/runtime
RUNTIME_MATPLOTLIB_DIR=/var/cache/northstar/matplotlib
```

这些字段可显式改到独立数据盘，例如：

```text
RUNTIME_STORAGE_DIR=/mnt/northstar-quant/storage
RUNTIME_DOWNLOADS_DIR=/mnt/northstar-quant/downloads
RUNTIME_REPORTS_DIR=/mnt/northstar-quant/reports
RUNTIME_LOG_DIR=/mnt/northstar-quant/logs
RUNTIME_CACHE_DIR=/mnt/northstar-quant/cache
RUNTIME_MATPLOTLIB_DIR=/mnt/northstar-quant/matplotlib
```

清单只接受以下受 root 控制专属父目录的**直接孩子**：`/var/lib/northstar/<leaf>`、
`/var/cache/northstar/<leaf>`、`/var/log/northstar/<leaf>`、`/mnt/northstar-quant/<leaf>` 或
`/data/northstar-quant/<leaf>`。任意其他 `/var/lib`、`/var/cache`、`/var/log`、`/mnt` 或 `/data`
路径都会被拒绝；相对路径、包含 `.`、`..` 的路径段、或像
`/mnt/northstar-quant/storage/downloads` 这样嵌套在另一个服务可写叶子下的路径也会被拒绝。
运行时目录彼此不得重叠，不能覆盖 `/opt/northstar`、`/etc/northstar`、root 管理的
Python/uv/deploy-state 目录，Dashboard 的 HOME 也固定为缓存根的同级 `dashboard/` 叶子而非
`runtime/` 的子目录。清单字段也不得占用系统管理的 `dashboard/`、`venv-build/` 或 `uv-cache/`
叶子。首次安装只会安全创建缺失的受控父目录和缺失叶子；既有叶子必须已经是
`northstar:northstar 0750` 的普通目录且不为符号链接。既有受控父目录也必须已经是
`root:northstar 0750`，其上一级由 root 控制且不可被 group/other 写入；自动化绝不会对既有可写叶子
执行 chown 或 chmod。

在 root 封存 stage 或按 `KEEP_RELEASES` 回收旧 release 前，部署器还会读取 Linux mount table，拒绝目标树
自身或其任意后代为独立 mount/bind mount 的状态；这避免特权 `find`、封存或清理跨入服务可写数据、外部磁盘或
未知挂载点。发现该状态时一律失败关闭，不会尝试修复挂载或改变既有目录权限。

部署脚本会以制品内的 `configs/app.example.yaml` 为模板，将已校验的业务输出路径原子写入**待发布版本**
完整的 `configs/app.yaml`；发布前迁移和健康检查使用这份配置，失败时它会随 stage 清理，旧版本的配置
不会变化。应用通过该 YAML 读取 `storage`、`downloads`、`reports` 和 `logs`，而 systemd 仅用
`RUNTIME_*_DIR` 设置最小可写白名单和缓存目录。切换 `current` 前脚本会先停止旧服务，避免旧进程在
重载配置时读取新版本路径。不要手工替换受管 `.env` 符号链接，也不要在 release `.env` 重复设置同名
`NORTHSTAR_*_DIR` 绕过这一边界。

生成的 release 内 `app.yaml` 始终与示例具有完全相同的字段。当前部署只从 `deploy.env` 注入运行目录；其余非敏感
应用规则（例如日志轮转）随已审阅的 `configs/app.example.yaml` 版本冻结。不要在服务器上事后手改
release 内的 `app.yaml`；需要变更通用规则时，应修改模板、测试并重新发布。

生产密钥和数据库 URL 随 release 固化；不存在可直接替换的全局“活动 `.env`”普通文件。
`/etc/northstar/northstar-quant.env` 是 root 管理的规范指针，且必须**精确**指向
`/opt/northstar/current/.env`。被 `current` 选中的 release 其 `.env` 又是 root 创建的符号链接，指向
`/etc/northstar/releases/<release>.env`；后者才是 `root:northstar 0640` 的普通秘密快照文件。主服务和
Dashboard 的 systemd 模板都读取 `@CURRENT_LINK@/.env`，因此原子切换或回退 `current` 会同时选择匹配的
代码、`app.yaml`、systemd 快照与环境快照。

请在**独立的部署工作树**中创建上传源，不要把开发工作树中 `NORTHSTAR_ENV=dev` 的 `.env` 拿来上传。
上传源必须是名称为 `.env` 的普通文件，不能是符号链接：

```bash
cp .env.example .env
chmod 600 .env
```

必须把 `NORTHSTAR_ENV` 设为 `production`，并替换 `NORTHSTAR_DATABASE_URL` 中的 `CHANGE_ME`。
部署脚本会在上传前先校验 `.env.example` 的完整字段结构，再验证这两个条件；若缺字段、仍是开发值或
不安全的 broker/live 组合，都会失败关闭。`.env` 只保存数据库 URL、令牌等敏感运行时变量；业务输出目录以
`deploy.env` 为唯一部署来源，并由发布过程在每个新版本生成完整的 `configs/app.yaml`。
`configs/app.yaml`、`.env` 与 `deploy.env` 均被 Git 忽略；不要把密码、令牌或数据库 URL 写入
`deploy.env`。

显式使用 `--upload-env` 时，控制面会把已验证的源文件作为本次签名提交的可选输入，并生成与该 release ID
及原始环境字节精确绑定的 OpenSSH detached signature；canonical manifest 只记录该受保护输入是否存在，
不记录 `.env`、秘密内容或秘密哈希。root gate 在
`/var/lib/northstar/deploy-state/transactions/<release>/` 接收并绑定该输入，再创建仅属于该 release 的候选文件。
符号链接一律拒绝；待发布 stage 用它完成迁移和发布前健康检查。只有这些检查通过后，安装器才会原子地
生成 `/etc/northstar/releases/<release>.env` 快照，并把该 release 的 `.env` 链接到此快照；它**不会**把候选
提升为 `/etc/northstar/northstar-quant.env`，也不会创建或恢复全局活动配置的备份。

不上传环境文件的普通发布会解析现有规范指针所选的当前 release 快照，并为新 release 创建独立的同内容
快照。因此每个保留 release 都有自己的配置版本，即使该次发布没有改变环境变量。首次发布没有现有指针，
必须使用 `--upload-env`。`current` 首次成功切换后才会原子创建规范指针；后续发布要求该指针仍精确指向
`/opt/northstar/current/.env`。migration 尚未开始时，已知失败不会提升候选或替换现有规范指针；migration
一旦开始，事务必须保留给人工恢复，自动化不会通过切回旧 `current` 并重启服务来声称数据库已经恢复。
没有全局配置提升、回退副本或独立配置恢复步骤。
按 `KEEP_RELEASES` 清理旧版本时，安装器会一并清理同名环境快照；不得手工删除仍保留 release 的快照，否则
该版本不能作为安全回退目标。

### 签名制品与特权交接

发布控制面先查询固定 gate identity，再构造并签署 canonical manifest。manifest 绑定完整 Git revision、
gate identity、固定 `scripts/deploy/gate_release.sh` 入口、allowlisted 非机密 profile，以及 runtime/control
bundle 的 SHA-256、大小和完整 archive 成员索引。每次 `--apply` 都必须传入本机未跟踪的
`--signing-key /安全路径/release-signing-key`；部署 SSH 密钥不被视为 release authority。

应用制品和 control bundle 不会由 root 按部署 SSH 身份可改写的暂存路径重新打开。非特权控制面只把 manifest、
其 detached signature、应用/control bundle 与（如上传）独立环境 signature 的字节流写入固定 gate 的 stdin；gate 在
`/var/lib/northstar/deploy-state`（`root:root 0700`）中接收、验证签名与完整索引，以 `fsync` 和 `link(2)`
no-overwrite 发布 root-owned 候选和 transaction 证据。只有在 root-owned transaction 中解包并验证后，固定
控制入口才能以特权运行；安装器只接受该 release 的精确候选路径、所有者、模式和链接数。普通 SSH 用户没有
`/tmp`、`/var/tmp` 或自有工作目录内脚本的 root 执行路径。

所有受支持的特权部署与运维 shell 入口均使用固定的 `/bin/bash -p`、受限 `PATH`，并在解析脚本路径或
`source` 前清除 `BASH_ENV`、`ENV` 与 `CDPATH`；从部署流程启动的 root shell 使用显式 `env -i` 传递所需
配置。此约束消除继承的解释器环境注入；root release gate 额外保证特权控制脚本只从已验证的 root-owned
transaction 执行。

首次发布使用 Python 控制面的 `--upload-env` 上传部署工作树中的 `.env`；后续如需更新环境变量仍显式
使用该选项。自动化场景可用 `--env-file /安全路径/.env` 指定上传来源，但文件名与字段结构仍应与
`.env.example` 一致，且必须通过 `NORTHSTAR_ENV=production` 门禁。

无需为 Paper broker 设置固定状态文件路径：默认状态按账户写入
`<runtime.storage_dir>/brokers/paper/<NORTHSTAR_PAPER_ACCOUNT>/state.json`。这保证部署
切换到独立数据盘时，Paper 状态仍与数据目录一起迁移；只有明确的迁移或恢复场景才应在
对应 release 的上传源 `.env` 中取消注释 `NORTHSTAR_PAPER_STATE_PATH`，并通过受控发布生成新的快照。

## 私有 ntfy（可选，独立 root-operated 工作流）

私有 ntfy 是本项目唯一的外部即时告警服务。它与 Northstar 应用、报告邮件和本地日志职责不同，但其
首次身份初始化需要处理管理员与订阅者秘密；这些秘密不能安全地进入普通 release manifest 或 control bundle。
因此签名发布的 `deploy.env` 必须保持 `NTFY_DEPLOY_ENABLED=0`。设置为 `1` 会在控制端和 root release gate
均失败关闭；`deploy.py`、`just deploy-prod`、`release-gate submit` 与 CI 都不会 provision、升级或初始化
ntfy/Caddy，也不接受 `--upload-ntfy-bootstrap` 或 `--confirm-ntfy-bootstrap`。

启用或变更 ntfy 是独立的服务器管理员工作：管理员在目标机上使用经审阅、root 控制的 ntfy 运维代码和单独
批准的 runbook，显式核对 Docker/Compose、DNS、TLS、存储、用户、ACL、令牌和回退证据。仓库中的
`scripts/deploy/ntfy/provision-ntfy.sh` 只能作为该 root-operated 工作流的一部分从受信任位置运行，绝不能
由部署 SSH 身份、应用服务账户、常规 release gate 或临时目录调用。它不安装 Docker，也不授权 `northstar`
或部署 SSH 身份访问 Docker socket。

独立工作流的非秘密配置保持以下固定安全语义；这些值不是常规签名 release 的 `NTFY_DEPLOY_ENABLED=1`
配置来源：

| 字段 | 示例/默认值 | 含义 |
| --- | --- | --- |
| `NTFY_PUBLIC_HOST` | `ntfy.example.com` | 公网 HTTPS 域名；只能填写主机名，不含协议、端口或路径。 |
| `NTFY_ACME_EMAIL` | `ops@example.com` | 供 Caddy/ACME 证书通知使用。 |
| `NTFY_IMAGE` | `binwiederhier/ntfy:v2.27.0` | 固定 ntfy 镜像标签；升级前先在隔离环境验证，禁止 `latest`。 |
| `NTFY_CADDY_IMAGE` | `caddy:2.10.2-alpine` | 固定 Caddy 镜像标签；负责 TLS 终止与反向代理。 |
| `NTFY_CONFIG_DIR` | `/etc/northstar-ntfy` | 专用服务端配置目录；不得覆盖任意主机路径。 |
| `NTFY_DATA_DIR` | `/var/lib/northstar-ntfy` | 专用认证、消息缓存与证书数据；不属于 application release。 |
| `NTFY_CACHE_DURATION` | `24h` | 告警缓存保留期；按最小必要原则设置。 |

`NTFY_CONFIG_DIR` 和 `NTFY_DATA_DIR` 不能改为 `/etc/ssh`、`/var/lib/postgresql` 或其他主机目录。不要手工
删除 `NTFY_DATA_DIR`、容器卷或认证数据库；这会使现有 topic、用户和令牌失效。root-operated 工作流必须
保持严格私有模式：匿名访问默认拒绝，Caddy 提供 HTTPS，ntfy 仅在反向代理之后接受请求。

### 首次身份初始化与应用配置

管理员应从受版本控制模板创建 root-only 的 bootstrap 文件，填写独立高强度值：

```bash
cp ntfy.bootstrap.env.example /root/secure/northstar-ntfy-bootstrap.env
chmod 600 /root/secure/northstar-ntfy-bootstrap.env
```

该文件只含 `NTFY_ADMIN_USERNAME`、`NTFY_ADMIN_PASSWORD`、`NTFY_READER_USERNAME` 与
`NTFY_READER_PASSWORD`。它不得进入制品、manifest、工单、命令行历史、CI 日志或聊天记录；常规发布不读取它。
root runbook 还必须创建最小权限的 `northstar-publisher` 身份、随机不可猜测 topic 与仅可写该 topic 的发布令牌。

ntfy 服务通过独立工作流验收后，应用的 release `.env` 才可设置其发布端配置并使用普通签名
`--upload-env` 发布：

```dotenv
NORTHSTAR_ALERT_MODE=ntfy
NORTHSTAR_NTFY_BASE_URL=https://ntfy.example.com
NORTHSTAR_NTFY_TOPIC=
NORTHSTAR_NTFY_TOKEN=
```

应用 token 只属于非管理员发布身份；管理员和订阅者身份绝不提供给 Northstar 或手机。令牌、topic 或 ntfy
身份轮换仍须由独立 root runbook 和恢复计划执行，不能通过重新发布 application、删除数据目录或重新 bootstrap
普通 release 来完成。

身份和权限必须至少分为三类：

- 管理员：只用于人工维护 ntfy 用户、ACL 和令牌，绝不提供给 Northstar 或手机。
- 订阅者：手机使用的独立只读身份，只能读取指定告警 topic，不能伪造告警。
- 发布者：Northstar 专用的非管理员身份，只能向指定告警 topic 写入。应用只使用其发布令牌，令牌只保存在
  生产 `.env` 的 `NORTHSTAR_NTFY_TOKEN` 中。

ntfy token 的权限继承其所属用户账户；因此“发布令牌”不是管理员令牌的缩小版本。发布用户本身必须
仅拥有指定 topic 的写权限，不能读取消息、管理其他用户或访问其他 topic。

### 手机可达性与验收

服务部署成功不等于手机一定及时收到告警。Android 自建 ntfy 的即时送达依赖客户端保持前台订阅服务；
iOS 在不使用上游推送服务的严格私有配置下可能延迟数分钟甚至更久。默认部署不把消息转发到公共
`ntfy.sh`，也不承诺移动网络、厂商省电策略或推送系统的送达时效。不得把 ntfy 消息视为交易控制、
人工确认或唯一审计证据。

首次验收至少确认：Caddy 的 HTTPS 证书有效、`https://<NTFY_PUBLIC_HOST>/v1/health` 返回健康状态、
发布者可以发布、匿名访问被拒绝、订阅者不能发布，并在目标手机上完成实际订阅测试。健康接口只说明
服务状态，不证明 topic ACL、令牌或手机送达正常。

## 部署命令

先做不连接服务器的完整构建演练（这是默认行为）：

```bash
uv run --offline --no-sync python scripts/deploy/deploy.py --inventory deploy.env
```

首次部署需要安装服务器运行时并上传首个 release 的 `.env`：

```bash
uv run --offline --no-sync python scripts/deploy/deploy.py \
  --inventory deploy.env --apply --setup-server --upload-env \
  --signing-key /secure/operator/northstar-release-signing-key
```

`--setup-server` 不负责安装 uv：远端 `/usr/local/bin/uv` 必须已由系统供应流程以 `root:root 0755`
普通文件形式预装，且版本与本机 `uv --version` 相同；任何符号链接、可写权限、file capability 或版本
不一致都会使首次部署失败关闭。

后续普通发布同样需要操作者显式提供 release signing key：

```bash
just deploy-prod /secure/operator/northstar-release-signing-key
```

更新服务器环境变量：

```bash
just deploy-prod-with-env /secure/operator/northstar-release-signing-key
```

上述 recipe 的第一个位置参数就是未跟踪的 `signing_key`；也可以直接使用 Python 入口的
`--signing-key /安全路径/release-signing-key`。不得把私钥路径、私钥内容或签名材料写入 `deploy.env`、`.env`、
仓库文件或 CI 日志。

正常发布会拒绝未提交工作区，并在上传前执行：

```bash
uv run --offline --no-sync ruff check .
uv run --offline --no-sync python scripts/ci/check_secrets.py
uv run --offline --no-sync python scripts/ci/check_mypy_baseline.py check
uv run --offline --no-sync pytest
```

没有选项可以跳过干净工作区、Ruff、密钥扫描、mypy 基线、完整 pytest 或 release manifest 签名；每次
`--apply` 都必须在提交前通过这些门禁。`--apply` 前控制面只在本机构建输入；实际提交先查询固定 gate identity，
再通过 SSH stdin 传输签名 manifest、runtime/control bundle 和可选环境输入。目标 gate 会再次验证运行时与
交易安全门禁，且不会从部署 SSH 身份的 `/tmp`、`/var/tmp` 或工作目录运行特权脚本。

同一目标的发布由 root-only
`/var/lib/northstar/deploy-state/release-gate.lock` 与
`/var/lib/northstar/deploy-state/transactions/<release>/` 的持久事务证据协调。创建前 gate 验证从 `/` 到状态
目录的 root 控制祖先链；已有锁、重复 release ID、签名/索引不一致、未知子进程结果或 SSH 结果未知都会
fail closed 并保留证据。不要删除 transaction、候选或锁来“重试”：先由服务器管理员检查精确 transaction 记录，
再按已批准 runbook 决定恢复。

## 服务器目录与版本化 systemd

固定目录树如下；运行时叶子可按上节改到受允许的独立数据盘，但 release、配置、状态根、缓存根与日志根
不可重定位：

```text
/opt/northstar/
├── current -> releases/<revision-timestamp>
└── releases/
    ├── <old-release>/
    │   ├── .env -> /etc/northstar/releases/<old-release>.env
    │   └── .northstar/systemd/
    └── <current-release>/
        ├── .venv/
        ├── .env -> /etc/northstar/releases/<current-release>.env
        ├── configs/app.yaml
        └── .northstar/systemd/
            ├── northstar-quant.service
            └── northstar-quant-dashboard.service  # 仅启用 Dashboard 时

/etc/northstar/
├── northstar-quant.env -> /opt/northstar/current/.env
└── releases/
    ├── <old-release>.env      # root:northstar 0640 regular file
    └── <current-release>.env  # root:northstar 0640 regular file

/var/lib/northstar/
├── deploy-state/       # root:root 0700；含 release-gate.lock 与 transactions/<release>/ 证据
├── python/             # root 管理的 uv Python
├── storage/
├── downloads/
└── reports/

/var/cache/northstar/
├── runtime/
├── matplotlib/
├── uv-cache/
├── dashboard/          # 仅启用私网 Dashboard 时
└── venv-build/         # 仅供受限依赖构建过程使用

/var/log/northstar/
└── app/
```

每个 release 拥有独立 `.venv`，并在安装时生成自身完整的 `configs/app.yaml`；其程序文件和 systemd
快照会在 preflight 后冻结为 root 管理状态。每个 release 的 `.env` 仅是指向同名 `/etc/northstar/releases/`
快照的 root 创建链接；规范 `/etc/northstar/northstar-quant.env` 仅精确指向
`/opt/northstar/current/.env`。因此回退原子切换 `current` 后会自动获得旧版本匹配的配置，不存在可变的
全局环境文件、提升流程或回退备份。`storage`、
`reports` 和 `logs` 则以版本目录中的软链接指向对应的受限运行时叶子。依赖由已签名 runtime artifact
中的 `scripts/ci/bootstrap_pep517.py` 在 service identity 的全新 build directory 内安装：先从锁定 wheels
materialize，再逐字节核验唯一获准 source artifact 后以离线/no-index/no-isolation 边界构建。release 不会
在 sealed release tree 内直接运行 `uv sync`，也不会继承部署用户的 resolver 或 build 环境。

主服务和可选 Dashboard 的 systemd 单元不会只依赖仓库当前模板：每个通过验证的 release 都从制品内
`infra/systemd/` 渲染自己的 `.northstar/systemd/<unit>.service`，其中记录 release ID 与制品
SHA-256。活动的 `/etc/systemd/system/<unit>.service` 由 root 从当前 release 快照安装。切换前，
部署会核对现有单元与上一 release 快照的哈希完全一致；首次部署若发现未知同名单元、或后续发现手工
篡改，都会失败关闭而不会覆盖。回退则恢复上一 release 的同一快照，不读取可变的共享目录或临时备份。

## 服务模式

### health

默认模式。systemd 以 oneshot 服务运行：

```bash
northstar health
```

默认配置下，该模式会迁移和验证应用，但不会启动 Dashboard、调度器或任何交易执行流程。

### 私网 Dashboard（可选）

Dashboard 是只读观察界面，不是交易许可、鉴权门户或公网 Web 服务。默认
`DASHBOARD_DEPLOY_ENABLED=0`；只有在 `deploy.env` 显式设为 `1` 时，发布流程才会
管理独立的 `<SYSTEMD_SERVICE_NAME>-dashboard.service`。它不会改变
`SERVICE_MODE=health` 或 `SERVICE_MODE=scheduler` 的含义，也不会替换这两个主服务。

该服务固定监听 `127.0.0.1`，应用也会拒绝任何其他
`NORTHSTAR_DASHBOARD_HOST` 值；不配置 Caddy、Docker、反向代理或 `80`/`443` 端口。远程查看使用
SSH 隧道或受控 VPN，例如默认端口：

```bash
ssh -N -L 8501:127.0.0.1:8501 deploy@example.com
```

然后在本机浏览器打开 `http://127.0.0.1:8501`。如修改
`NORTHSTAR_DASHBOARD_PORT`，隧道两侧端口必须同步调整。验收时至少确认服务器监听地址仅为
loopback、Dashboard systemd 服务正常，以及公网防火墙没有放行该端口。

发布主服务成功后才会更新 Dashboard。若 Dashboard 自身启动失败，部署会强制关闭并移除该可选服务，
主服务仍保持本次已验证的发布版本，远端输出会明确为
`dashboard=disabled_after_failure`；若连“已关闭”都无法确认，部署会失败并要求立即人工检查。
当前 Dashboard 会显示账户、订单、成交、报告路径与行情摘要，且没有登录、角色控制或独立只读数据库
身份，因此绝不能面向公网或共享给不受信任的用户。

### scheduler

这是**未来** production 阶段的 `infra/systemd/scheduler.service.in` 模板，不是当前可启动服务。当前没有 production
画像，执行下列命令会在画像/preflight 阶段失败关闭：

```bash
northstar live scheduler
```

只有在生产画像、真实 CTP adapter、经授权数据、完整 preflight 和阶段审批全部完成后，
才可评估 scheduler 模式。届时 paper 调度器仍要求：

```text
NORTHSTAR_BROKER=paper
NORTHSTAR_LIVE_TRADING_ENABLED=false
```

非 paper 调度器除了生产画像、券商适配器和应用 preflight，还要求每次发布显式执行：

```bash
just deploy-prod-live /secure/operator/northstar-release-signing-key
```

这个确认只表示允许部署脚本启动非 paper 调度器，不替代交易系统自身的 preflight、
kill switch、账户核验和风控。

## 回退与数据库迁移

新版本先在 root-owned transaction 记录中接收、验签、stage 并完成 candidate health；只有通过这些步骤才会
进入 cutover。数据库 migration 尚未开始时，已知失败不会提升候选、切换 `current` 或覆盖当前环境快照。

一旦事务记录 migration 已开始，任何后续失败（包含 candidate health、cutover、systemd 启动或提交连接中断）
都会进入 `RECOVERY_REQUIRED`。自动化绝不自动 downgrade 数据库、删除或清空数据，也不会自动恢复旧
`current` 并重启服务来制造“已回退”的假象。服务器管理员必须审查该 release 的 root transaction、版本、
systemd 与数据库证据，并在经审批的恢复 runbook 中显式决定后续动作；这不会自动解除 HALT 或增加交易风险。

数据库迁移只允许前向、加法式升级；`downgrade()` 会失败关闭，部署绝不自动回退数据库 schema、
删除或清空数据。即使仍处于开发阶段，模型、迁移、测试与文档也必须作为同一变更整体更新，不能以
重建、清空或重新初始化本地数据库替代兼容性设计。正式发布前必须备份生产数据库，并定期演练恢复。
仓库自动化绝不删除或清空数据库、表、schema 或 Docker 数据卷；生产数据库的删除或清空只能由用户
在仓库自动化之外手动执行。仓库内的 `infra/backup/northstar-quant/` 只保存策略和演练说明，绝不保存
真实备份。

默认保留最近五个版本，且至少保留两个版本。

### PostgreSQL 备份包、恢复演练与证据

部署脚本和远程 `just ops-backup` **不会**创建 PostgreSQL 备份、安装对象存储客户端、配置 WAL
归档，也不会把数据库凭据交给远程控制面。生产 PostgreSQL 必须仍由独立、最小权限的备份系统负责；
开发 Docker 数据卷、同机 `pg_dump` 文件或“任务执行成功”不能视为可恢复灾备。

P6-WP08 另提供受审阅、非自动的 Linux 维护入口。它只能由操作者在固定服务已停止后显式调用：

```bash
# /mnt/northstar-backups 必须是预先挂载的私有外部目录，不能位于 release、reports 或 storage 内。
# 脚本会在采集前和最终无覆盖发布前确认 northstar-quant.service 为 inactive；不会停止或重启服务。
sudo -u northstar /opt/northstar/current/.venv/bin/python \
  /opt/northstar/current/scripts/maintenance/backup_bundle.py create \
  --output-parent /mnt/northstar-backups \
  --confirm-create YES \
  --confirm-runtime-quiesced YES

sudo -u northstar /opt/northstar/current/.venv/bin/python \
  /opt/northstar/current/scripts/maintenance/backup_bundle.py verify \
  --bundle-dir /mnt/northstar-backups/northstar-backup-<uuid>
```

创建路径从被调用的 `current` release 显式解析活动 `.env` 和 `configs/app.yaml`，不会依赖已安装 Python
包的位置或调用目录。它使用受限子进程环境运行 `pg_dump`，命令行、日志、manifest 都不含 DSN 或密码；包清单记录
PostgreSQL custom archive、活动非秘密 app config、ontology、正式 run manifest、Paper/`ctp_sim` state 和
release/systemd metadata 的 SHA-256。符号链接、特殊节点、路径逃逸、重复/额外条目（含空目录）、`.env`、数据库 URL
或秘密样式内容均会失败关闭。包创建不会自动更新 readiness 证据。

`configs/maintenance/database_backup_readiness.yaml` 仍只提供诚实的观测门禁。默认关闭；启用后，独立
备份运维流程须在以下位置写入**无秘密**证据：

```text
<runtime.storage_dir>/operations/database-backup/readiness.json
```

证据必须记录一个逻辑备份制品的 UUID、SHA-256、大小、完成 UTC 时间，以及对**同一制品**执行隔离
PostgreSQL 恢复演练的完成 UTC 时间、`passed` 状态和方法名。它不能包含 DSN、主机、路径、对象存储
桶、账户、令牌或密码。不要为了消除告警手工伪造该文件：应用只能验证证据结构和时效，无法独立验证
外部介质或恢复目标，所以完整证据仍只显示 `warn`，永远不会显示 `pass`。

```bash
# 仅查看证据，不会备份、恢复或修改任何文件。
sudo -u northstar /opt/northstar/current/.venv/bin/northstar \
  ops backup status

# 发布预检和 health systemd 服务均使用此门禁：只有 blocked 才返回退出码 2。
sudo -u northstar /opt/northstar/current/.venv/bin/northstar \
  health --fail-on-blocked
```

启用策略后，证据缺失、损坏、过期、指向不同制品或恢复演练失败会使 health 为 `blocked`，从而阻断
后续发布。策略关闭时结果为 `skipped`，明确表示“未检查”，而不是“备份正常”。P6-WP08 的包已覆盖
Paper/`ctp_sim` state 和正式回测 manifest，但市场数据的复制仍受数据授权约束，不会被本工具自动纳入。

仓库还提供 `scripts/maintenance/restore_drill.py`，供 Linux CI/本地明确地对受隔离、loopback 的
`northstar_test` 执行 `pg_dump → pg_restore --schema → psql BEGIN/ROLLBACK`，并在回滚后验证 source schema/table
OID、行数与 marker 保持不变。它要求
`--confirm-test-drill YES` 和受保护环境注入的测试 URL；不接受运行时 URL、不清理 source schema/archive，
也不能作为生产恢复命令。

在进入真实 CTP 或实盘前，必须升级为独立故障域的加密备份、PITR（基础备份加 WAL 归档）、书面
RPO/RTO、备份新鲜度告警和至少定期的隔离恢复演练。不能仅依赖本项目的证据检查作为灾难恢复方案。

## 运维命令

从 Windows 或 Linux 工作站读取状态：

```bash
just ops-health
```

读取日志（默认最后 200 行）：

```bash
just ops-logs
```

收集只读诊断或备份/恢复演练证据：

```bash
just ops-diagnose
just ops-backup
```

这些命令通过受限 SSH 调用 Linux 目标脚本；它们不上传 `.env`、不启动服务，也不创建数据库备份。
`backup` 只读取独立备份系统留下的无秘密就绪证据，而不是维护包创建入口。服务重启需要目标端
`CONFIRM_SERVICE_RESTART=YES`，且只会在 root 管理的 `current` release、受管 systemd 快照、fragment 与
drop-in 均匹配时重启固定的 `northstar-quant.service`；手动回退和卸载目前明确失败关闭；生产恢复必须走独立、已审批的
PostgreSQL runbook。
