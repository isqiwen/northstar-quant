# 发布脚本（预留）

此目录预留给版本标记、变更记录和发布验证辅助脚本。公开入口是 `just deploy-prod`，它委托
`scripts/deploy/deploy.py` 的跨平台控制面；Linux 目标操作仍只可通过
`scripts/deploy/remote/linux/` 的受控层执行，安全门禁不得绕过。
