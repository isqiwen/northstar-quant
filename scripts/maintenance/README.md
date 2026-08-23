# 维护脚本

此目录只放需要明确人工确认的受审阅维护入口；它们不会由 `just ops-*`、部署或调度自动调用。

- `backup_bundle.py create`：仅在传入 `--confirm-create YES` 和
  `--confirm-runtime-quiesced YES`、且固定 `northstar-quant.service` 已确认为 `inactive` 时，创建
  不可覆盖的逻辑备份包。它不自动停止服务，不读取/复制 `.env`，且输出目录必须位于输入之外。
  服务会在采集前及最终无覆盖发布前再次检查；操作者必须在整个维护窗口内保持服务停止。
- `backup_bundle.py verify`：重新验证 manifest、文件集合、SHA-256、秘密边界和 PostgreSQL archive 格式；
  不连接数据库。
- `restore_drill.py`：仅接受 `NORTHSTAR_TEST_DATABASE_URL` 指向 loopback 的 `northstar_test`，且必须
  传入 `--confirm-test-drill YES`。它通过事务回滚验证真实 PostgreSQL 客户端工具链，绝不接收生产 URL。

真实备份、恢复凭据、数据库转储、市场数据和券商状态不能提交，也不能由脚本默认删除。生产恢复仍需独立、
已审批的 runbook；同机目录、单个逻辑包或 CI 演练都不构成 PITR/灾备承诺。
