# 发布脚本（预留）

此目录预留给版本标记、变更记录和发布验证辅助脚本。公开入口是
`just deploy-prod /secure/operator/northstar-release-signing-key` 或
`scripts/deploy/deploy.py`；实际 `--apply` 必须显式提供未跟踪的 `--signing-key`，由服务器 root 管理的
`release-allowed-signers` 验证。部署 SSH 身份只能传输已签名提交，不能授予自身 root 执行权。

Linux 目标只接受固定 root release gate 的 `identity` 与 `submit` 动作。gate 在验证 canonical manifest、
signature、runtime/control bundle 的 SHA-256 与完整成员索引后，才从 root-owned transaction 中执行固定控制
入口。root gate bootstrap、签名 authority 变更和 migration 后的恢复都属于服务器管理员的带外、人工审批流程；
它们不能由常规 release 脚本或 CI 自动触发。

私有 ntfy 也不属于签名 release：`NTFY_DEPLOY_ENABLED=1` 会被拒绝，`--upload-ntfy-bootstrap` 不是有效
release 参数。其身份 bootstrap 与基础设施变更只能走独立 root-operated runbook。
