# Linux 目标操作层

此目录只在 Linux 服务器的受限临时目录中执行。Windows 与 Linux 开发机均通过
`scripts/deploy/deploy.py` 的 Python + OpenSSH 控制面进行本地编排，不需要本地 Bash，
也不能直接以此目录替代控制面。

- `install.sh`：首次安装包装器，强制 `SETUP_SERVER=1` 后交给既有 `provision.sh`。
- `upgrade.sh`：常规发布包装器，强制 `SETUP_SERVER=0`，保留制品校验、健康检查和失败自动回滚。
- `restart.sh`：必须显式设置 `CONFIRM_SERVICE_RESTART=YES`。
- `rollback.sh`：只保留发布失败时的自动回滚；手动回滚目前失败关闭。
- `uninstall.sh`：涉及持久化数据与审计记录，目前失败关闭。

所有真实部署仍由 `provision.sh`、`install-runtime.sh`、`install-release.sh` 负责，并会
重新校验 production `.env`、broker、真实交易确认、路径、权限和 systemd 配置。
