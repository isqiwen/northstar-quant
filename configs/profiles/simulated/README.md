# 模拟账户交易画像

此目录仅用于连接券商模拟账户的交易画像。画像必须使用
`lifecycle.role: simulated`，并通过与真实账户相同的实时数据、风控、订单状态恢复和
对账流程。

当前仓库没有可运行的模拟账户画像。`cn_etf_daily_stateful_offline` 是离线日频撮合模拟，
其角色为 `research`，因此位于 `../offline/`。
