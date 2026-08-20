# 交易日历制品

这里不再是运行时信任根。真实交易日历必须先由受控发布链写入不可变制品库，成为单一交易所、
单一 Calendar Snapshot 的 normalized payload ArtifactSnapshot；其 record 必须绑定 payload SHA-256、
日历语义 content hash 与直接 raw 来源 snapshot。画像只在
`futures.calendar_artifact_snapshot_hashes` 中按交易所引用该 hash，Application 会从制品库读取 bytes，
不会读取本目录中的 YAML。

当前仓库没有可运行的日历制品。`tests/golden/trading_calendar/` 中的合成 fixture 只用于测试，
加载器和 Application 都会拒绝把它当作运行时/真实交易配置。不要在这里填写公开网页摘录、
`XSHG` 占位数据、账号信息或未经授权的交易所资料。
