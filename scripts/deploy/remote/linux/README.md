# Linux 签名发布控制层

此目录是签名 control bundle 的源代码，不是部署 SSH 身份可直接执行的远端临时目录。Windows 与 Linux
工作站通过 `scripts/deploy/deploy.py` 构建 runtime/control bundle、签署 canonical manifest，并把字节流
提交给固定的 `/usr/local/libexec/northstar-quant/release-gate`。普通发布不能以 `ssh sudo`、`/tmp`、
`/var/tmp` 或用户拥有的工作目录运行这里的任何脚本。

服务器管理员先带外 bootstrap root gate。gate 用其 root 管理的 `release-allowed-signers` 验证 manifest、
SHA-256 和完整 archive index，把 control bundle 解包到
`/var/lib/northstar/deploy-state/transactions/<release>/control/` 的 root-owned 目录后，才会调用固定的
`scripts/deploy/gate_release.sh`。该入口以受限环境调用 `install-runtime.sh`（只在签名 profile 请求首次
运行时安装时）和 `install-release.sh`，后者仍会重新验证 production `.env`、broker、真实交易确认、路径、
权限和 systemd 配置。

签名 profile 强制 `NTFY_DEPLOY_ENABLED=0`。私有 ntfy 的 bootstrap 秘密不能经普通 release 传输；它是独立的
root-operated 运维流程，不能由 `submit`、`--upload-ntfy-bootstrap`、部署 SSH 身份或临时目录触发。

- `install.sh`、`upgrade.sh`：保留为受审阅的目标端包装器，不能作为普通 sudo 或 SSH 发布入口。
- `restart.sh`：必须显式设置 `CONFIRM_SERVICE_RESTART=YES`；只能重启固定的
  `northstar-quant.service`，并会核对受管 unit、fragment 和 drop-in。
- `rollback.sh` 与 `uninstall.sh`：手动动作均失败关闭。尤其 migration 已开始的发布失败不会自动切回旧
  release、重启服务或 downgrade 数据库；必须根据 root transaction 证据使用已批准的人工恢复 runbook。

控制 bundle、manifest、signature、runtime 输入和生命周期事件均保留在 root-owned transaction 中。提交断开、
重复 release ID、未知子进程结果或任何证据不一致都必须人工检查该记录，不能通过重新上传或删除暂存内容“修复”。
