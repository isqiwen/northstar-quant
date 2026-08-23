# Northstar Quant 备份策略目录

这里只保存备份策略、恢复演练说明和无秘密示例。真实 PostgreSQL 转储、WAL、行情文件、报告、
账户/券商状态、对象存储清单、密钥和恢复凭据一律不得进入版本库。

P6-WP08 提供了受限的显式维护入口：

```text
scripts/maintenance/backup_bundle.py create|verify
scripts/maintenance/restore_drill.py
```

`backup_bundle.py create` 需要双重 `YES` 确认，并且固定的
`northstar-quant.service` 必须被 systemd 确认为 `inactive`；它不会自动停止服务。它以 PostgreSQL
自定义格式转储和 versioned SHA-256 manifest 封装活动非秘密 `app.yaml`、ontology、正式回测
manifest、Paper/ctp_sim 状态以及 release/systemd metadata。`.env`、DSN、令牌和私钥都会被拒绝。
输出父目录必须预先存在、私有且不与任何输入重叠，因而应由运维人员挂载到仓库外的备份介质。
服务状态会在采集开始前和 staging 完成、原子发布前各检查一次；操作者仍必须在整个维护窗口内保持服务
停止，若任一检查失败则不会发布最终包。

`restore_drill.py` 只接受 loopback 上精确的 `northstar_test`，使用真实
`pg_dump → pg_restore → psql BEGIN/ROLLBACK` 验证受控 schema；它不会接收运行时数据库 URL，也不会
清理 source schema 或 archive。生产恢复入口仍然失败关闭，必须使用独立、已审批的 runbook。

备份包本身不是异机灾备、加密、WAL 归档或 PITR。真实备份仍必须存放在独立故障域，按生产运维流程
加密、限制访问、导出后独立校验，并定期执行隔离恢复演练。
