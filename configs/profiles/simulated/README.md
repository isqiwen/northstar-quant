# 模拟账户交易画像

此目录仅用于模拟账户和本地柜台语义演练。画像必须使用
`lifecycle.role: simulated`，并通过账户状态、风控、订单状态恢复和对账流程。

当前内置 `cn_futures_daily_trend_simulated`，只允许与
`NORTHSTAR_BROKER=ctp_sim` 一起使用。`ctp_sim` 是本地持久化的 CTP 语义仿真柜台，
不会连接期货公司前置，也不代表真实 CTP 适配器已经完成。

画像使用实际合约日线生成动态保证金规则，策略连续序列必须经
`configs/instruments/ctp_sim.yaml` 映射为具体合约。映射过期、数据过期、缺少今昨仓、
存在未完成订单或盘中风控失败时都会停止新订单。

基础演练顺序：

```bash
export NORTHSTAR_BROKER=ctp_sim
export NORTHSTAR_DEFAULT_PROFILE_ID=cn_futures_daily_trend_simulated

uv run northstar data download \
  --profile cn_futures_daily_trend_simulated
uv run northstar live signal \
  --profile cn_futures_daily_trend_simulated
uv run northstar live sync
uv run northstar live risk-check \
  --profile cn_futures_daily_trend_simulated
uv run northstar live preflight \
  --profile cn_futures_daily_trend_simulated
uv run northstar live preview-rebalance \
  --profile cn_futures_daily_trend_simulated
```

`live execute` 会写入仿真订单；随后使用 `live poll` 或 `live sync` 取得异步成交并对账。
首次演练建议先运行两次 `live sync`，建立账户快照和区间归因基线。

这条流程只会修改本地 `ctp_sim` 状态和开发数据库，绝不连接期货公司。完整能力边界见
[`docs/04_实盘执行现状与增强说明.md`](../../../docs/04_实盘执行现状与增强说明.md)。
