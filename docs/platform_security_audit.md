# 平台安全与审计

## 机密边界

运行时机密只允许位于未跟踪的 `.env`、受保护的目标机环境文件或外部凭据系统中。仓库中的
`.env.example`、`deploy.env.example` 和 bootstrap 示例只能包含空值或明确占位符；`.pem`、`.key`
及其他私钥容器也被忽略。

`just check` 与 CI 对全部已跟踪的可解码文本（包括 `tests/`）执行密钥扫描，检查 token、credential、authorization、
DSN 用户信息、私钥块以及常见云或聊天令牌。已知二进制格式只能经文件魔数识别后跳过；未知二进制、不可读文件、
非 UTF-8 文本与符号链接一律失败关闭。只有带 `secret-scan: allow; reason: ...` 的受审计、一次性
test/CI fixture 才能跳过单行扫描；该例外不得用于业务源代码、配置、部署、文档或生产清单。

## P9-WP02：受锁定制品约束的 Hermetic PEP 517 bootstrap

`scripts/ci/check_dependency_policy.py` 先于任何依赖 materialization 运行；随后才允许显式离线的
`uv lock --check --offline`。`just env-bootstrap` 和两个 Tier-1 CI job 都先运行这两个只读门禁及机密扫描，
再调用标准库入口 `scripts/ci/bootstrap_pep517.py`。该 policy 解析已跟踪的 `pyproject.toml` / `uv.lock`，拒绝
项目与 root lock metadata 不一致、未 allowlist registry、direct URL/VCS/path source、非 root editable source、
缺失或格式无效的 SHA-256 artifact hash 与异常 artifact metadata。成功时只输出稳定的
package name/version/source/lock digest inventory，不访问网络、不输出凭据，也不改变依赖、数据库或运行环境。

PEP 517 builder 现在是锁定的 `build-bootstrap` group：`setuptools==80.9.0` 与 `wheel==0.45.1` 必须在
`pyproject.toml`、root lock metadata 和带 SHA-256 的 universal wheels 中完全一致。runner 创建一个不含
system-site-packages 的 non-seed fresh venv，先以 `uv sync --no-build` 仅 materialize lock 中的 wheel；
任何未来出现的未登记 source build 都会失败关闭。

当前唯一获准的 source-only 包为 `jsonpath==0.82.2`。policy 固定其 `files.pythonhosted.org` URL、大小、
SHA-256 和无额外依赖语义；runner 拒绝 redirect，并把下载流逐字节核对大小与 SHA-256。只有核对成功的
临时本地 sdist 才会在 `--offline --no-index --no-deps --no-build-isolation` 边界构建；首方包在同一边界安装。
Linux release 也从已签名 runtime artifact 中携带同一 runner 与 policy，在 service identity 的新 build directory
执行完全相同的 release profile。所有后续命令必须使用 `uv run --offline --no-sync`，因此不能在检查或运行时
隐式解析、下载或构建包。

runner 只把最小 OS 变量 allowlist 传给 resolver/build 子进程，去除 `NORTHSTAR_*`、proxy、cloud/credential、
`PIP_*`、`UV_*` 与 Python activation 环境；直接 source 下载禁用代理并仍以 hash 为最终完整性判定。development
先在同级 fresh staging venv 完成整个流程，只有成功后才切换到 `.venv`；被占用时保留原 `.venv` 和 staging
证据而失败，不会强制终止进程或先清空旧环境。该边界不会把任意 source build、ambient resolver 设置、
system site-packages 或既有虚拟环境带入结果；它确保已记录制品的完整性和 provenance，不声称 sandbox 恶意
构建代码、提供网络隔离、进行 CVE 扫描或替代操作系统级供应链控制。

这是一道可复现的**离线供应链完整性/来源策略**门禁，不是 CVE、许可证或实时 advisory 结论。未来若引入
外部漏洞情报，必须先获得数据授权，并将输入版本、可得时间、签名/哈希与失败关闭语义单独纳入工作包。

## 脱敏与导出

所有可观察输出都经过同一套脱敏边界：结构化日志的 message、exception、stack 和直接传入的
`extra` 字段，CLI JSON，部署审计事件，以及新生成的 Markdown/JSON 报告均递归处理敏感字段、
Bearer/Basic 凭据、URL 用户信息和 query token。

发现已有报告包含可脱敏的机密时，邮件导出会 fail closed，而不是发送原始 Markdown、HTML 或附件。
业务代码不得把机密放入报告、异常文本、日志字段、命令行参数或审计 subject。

## 审计事件

部署计划、安全拒绝和实际发布产生稳定 JSON `SecurityAuditEvent`，至少包含 `actor`、`action`、
`outcome`、`subject`、UTC `occurred_at` 与已脱敏 `details`。合法 outcome 仅为 `planned`、`success`、
`failed` 或 `denied`，因此日志采集和后续审计不依赖 Python 对象字符串。

## 最小权限

Linux 服务始终以 `SERVICE_USER` 运行，使用 `UMask=0077`、`NoNewPrivileges=true`、
`ProtectSystem=strict` 等 systemd 限制，并且没有 Docker socket 权限。SSH 部署身份必须由远端
`id -un` 证明其既非 `root` 也不同于 `SERVICE_USER`；这不会授予服务身份 sudo 或真实交易权限。

## P6-WP08：备份与恢复演练边界

`scripts/maintenance/backup_bundle.py` 不是常规运维控制面：它不在 `just ops-*`、部署或调度中调用，
创建动作同时要求精确的双重确认和 `northstar-quant.service` 的 `inactive` 状态，且绝不自动停止服务。
其输出父目录必须已存在、私有、位于 release/报告/状态输入之外；输入目录中任何符号链接、特殊文件、
路径逃逸、重复归档路径、异常文件类型或秘密样式内容都会失败关闭。最终包只有在私有 stage 内写入、
`fsync` 和自身验证成功后才以 no-overwrite 名称发布。

包 manifest 是 versioned、无源路径、无 DSN/令牌的 SHA-256 索引，覆盖 PostgreSQL 自定义格式转储、
活动非秘密配置、ontology、正式回测 manifest、Paper/ctp_sim state 与 release/systemd metadata。验证器
拒绝缺失、额外、被替换或哈希不符的条目；它也调用 `pg_restore --list`，但不会连接或恢复生产数据库。
同机包并不等价于异机加密备份、WAL/PITR 或已批准的恢复目标，health readiness 也不会因创建包而自动变为
`pass`。

真实恢复工具链演练仅接受 loopback 的 `northstar_test`，在单一 `BEGIN` 事务内暂时改名其新建 source
schema、把 `pg_restore` SQL 流送入 `psql`、验证 sentinel 后 `ROLLBACK`。它不接受运行时数据库 URL，
不包含清理、删除或生产恢复行为；source schema 和 archive 被保留供审计。

## P6-WP07：已建立的目标运行时边界

P6-WP07 的保证发生在受管 release、环境文件和 systemd 单元已经由 root 接管之后：

- 代码 release 和 `current` 位于 `/opt/northstar`，服务账户只能写入经过白名单约束的状态、缓存和日志
  叶子目录；它不能改写下次启动所使用的 release、`current`、活动环境文件或 systemd 单元。
- release 的源码与 systemd 模板在非特权依赖安装前由 root 封存；通过验证后，root 从该 release 渲染
  带 release ID 和制品 SHA-256 的 systemd 快照。活动单元必须与上一受管快照匹配，否则拒绝覆盖或回退。
- 生产环境文件是 `root:northstar 0640` 的普通文件。上传的新值先以绑定 release 的候选文件接受验证；
  通过 stage、迁移和发布前健康检查后复制为该 release 的独立快照，`current` 切换后才由规范指针选择它。
  不存在可替换的全局活动 `.env`、旧副本提升或独立配置回退步骤。
- 主服务以 `northstar` 运行，继续使用 `UMask=0077`、`NoNewPrivileges=true`、
  `ProtectSystem=strict` 和最小 `ReadWritePaths`；服务账户没有 sudo、Docker 或真实 broker 凭据权限。
- 应用制品不再由 root 按部署身份可改写的路径重新打开：非特权流程只打开 release-bound 上传文件一次，
  root receiver 在 `deploy-state` 的 `root:root 0700` 边复制边核对固定 SHA-256，以 `fsync` 与 `link(2)`
  no-overwrite 发布 `root:root 0600`、单链接候选。安装器只接受该 release 的精确候选路径及所有者/模式/链接数。
  普通已知失败清理候选；信号或未知中断保留证据，并由 P6-WP09 的持久事务记录关联后续人工恢复决定。
- root 在封存 stage、回收旧 release、或让 uv 写入/封存受管 Python 前检查 Linux mount table；若受管树自身或
  后代存在 mount/bind mount，则拒绝跨挂载执行写入、封存或删除。既有服务可写叶子不被自动 `chown` 或 `chmod`
  修复。
- 受支持的特权部署与运维 shell 入口使用 `/bin/bash -p`、固定 `PATH`，并在路径解析或 `source` 前清除
  `BASH_ENV`、`ENV`、`CDPATH`；部署流程的 root shell 以 `env -i` 明确传递必需配置。这只保护继承的
  解释器环境；它与 root-owned release gate 一起构成特权执行边界。

## P6-WP09：签名 root release gate 与可恢复发布事务

普通部署不再让 root 从部署 SSH 身份可写的暂存目录加载或执行 `provision.sh`、库或安装脚本。SSH 身份只可把
字节流提交给固定的 root gate；它既不是 release authority，也不能选择 root 要执行的路径或命令。

### 带外信任锚 bootstrap

首次安装 gate 是服务器管理员的带外、一次性信任启动步骤，使用
`scripts/deploy/release_gate_bootstrap.py`，而不是 `deploy.py`、`provision.sh` 或任何 SSH 暂存内容。管理员必须：

- 从受审阅、root 控制且不在 `/tmp`、`/var/tmp`、`/run/user` 或 `/dev/shm` 下的绝对路径提供
  `root_release_runner.py`，并显式核对其 SHA-256；
- 独立审阅只含 `northstar-release` 公钥记录的 OpenSSH `allowed_signers` 文件并显式核对其 SHA-256；
- 以 Linux root 明确确认后，无覆盖地发布固定的
  `/usr/local/libexec/northstar-quant/release-gate`、
  `/usr/local/libexec/northstar-quant/root_release_runner.py` 与
  `/etc/northstar/release-allowed-signers`。

私钥永不进入该流程、目标机或仓库。任何已有 trust-anchor 文件或残留的部分 bootstrap 证据都会失败关闭并保留，
由管理员审查；常规部署没有“更新 gate”或“重新 bootstrap”的 sudo 路径。

### 已签名的常规发布路径

控制面先查询固定 gate 的 identity，再构造 canonical release manifest。操作者必须显式提供本机、未跟踪的
OpenSSH 发布签名私钥（`--signing-key`）；部署 SSH 私钥不是发布授权。manifest 绑定完整 Git revision、目标
gate identity、固定入口 `scripts/deploy/gate_release.sh`、allowlisted 非机密部署 profile，以及 runtime 与 control
bundle 的 SHA-256、大小和完整成员索引。它不包含 `.env`、机密派生哈希、控制端路径或由 SSH 身份提供的执行命令。
由于该签名可授权 root 执行经过验证的 control bundle，release signing key 是高权限发布 authority：必须离线或
由受管硬件/审批流程保护，且绝不能与部署 SSH 私钥、CI 常规凭据或目标机共享。

签名 release profile 强制 `ntfy_deploy_enabled=0`。带管理员/订阅者 bootstrap 秘密的私有 ntfy 不能被普通
manifest 安全绑定，因此 `NTFY_DEPLOY_ENABLED=1` 在控制端和 root gate 都被拒绝；ntfy 只能经独立、root-operated
的审批工作流配置，不能通过 `submit`、`--upload-ntfy-bootstrap` 或 CI 传入。

`--upload-env` 的环境内容仍作为只针对该 release 的不透明候选传输；控制面使用独立 OpenSSH detached signature
将其原始字节和 release ID 精确绑定，而 manifest 只声明是否存在这一受保护输入，不记录秘密或秘密哈希。root 在
自己的 transaction 目录中接收并验签它，且只有通过同一发布验证后才可形成该 release 的环境快照。

常规提交通过 SSH 的 stdin 把 manifest、detach signature、runtime bundle、control bundle 和可选环境输入交给
固定 gate。gate 在任何候选、release、systemd 或数据库变更前验证：自身 root 控制布局、gate identity、canonical
manifest、`release-allowed-signers` 中的 OpenSSH 签名、bundle 的大小/SHA-256/完整成员索引、固定控制入口和
（如有）绑定 release ID/原始字节的环境 detached signature。随后它只在 `root:root 0700` 的 transaction 树中以 root-owned 文件解包 control bundle，
并从该树运行固定入口。正常路径没有可由 SSH 身份改写的远端临时目录执行，也不再把 `sudo` 授给任意暂存脚本。

### 持久事务与恢复边界

每个 release 在 `/var/lib/northstar/deploy-state/transactions/<release>/` 保留 root-owned 的请求证据、签名、
bundle、控制副本和 append-only 生命周期事件。事务在候选创建前记录接收与验证，随后记录 stage、migration、
candidate health、cutover、post-start health 与 promote/failed/recovery-required 等不可混淆状态；中断、重复 release
ID、未知子进程结果或证据不一致均 fail closed，而不是静默重试或覆盖已有证据。

数据库 migration 一旦开始，自动化绝不执行 schema downgrade、删除或清空数据，也不会自动把 `current` 切回旧
release 后重启服务来伪造恢复成功。此时事务进入 `RECOVERY_REQUIRED`，保留服务/版本/签名证据供管理员按已批准
runbook 审查，并由人显式决定后续服务、代码与数据库恢复动作。控制端 SSH 断开或提交结果未知时同样必须先检查
root 事务记录，不能重新提交、恢复 HALT 或增加任何交易风险。

部署 gate 的受限 sudo 授权只允许其固定 `identity` 与 `submit` 动作；服务账户和应用代码仍不得获得 sudo、
Docker 或真实 broker 凭据权限。
