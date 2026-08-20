# 真实账户交易画像

此目录中的画像代表真实账户交易主线，必须使用 `lifecycle.role: production`。
即使位于此目录，仍须同时满足真实数据资格、券商身份映射、预交易风控和全局安全开关，
否则系统必须拒绝下单。

当前仓库**没有** production YAML，也没有真实 CTP 报单适配器；不要在此目录创建占位画像
来试图启动 `live scheduler`。真实账户画像只能在
[`docs/03_执行与安全边界.md`](../../../docs/03_执行与安全边界.md) 和
[`docs/08_项目主规划与实施状态.md`](../../../docs/08_项目主规划与实施状态.md) 的全部前置条件
完成且账户持有人明确授权后创建。

届时画像还必须提供按交易所配置的 `futures.calendar_artifact_snapshot_hashes`。每个值都是已验证
normalized calendar payload 的不可变 ArtifactSnapshot hash；运行时不会读取项目日历 YAML。`XSHG`、
工作日推断或测试 fixture 都不能替代中国商品期货的夜盘/休市事实。
