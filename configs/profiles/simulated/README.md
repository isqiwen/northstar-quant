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

uv run --offline --no-sync northstar data download \
  --profile cn_futures_daily_trend_simulated
uv run --offline --no-sync northstar live signal \
  --profile cn_futures_daily_trend_simulated
uv run --offline --no-sync northstar live sync
uv run --offline --no-sync northstar live risk-check \
  --profile cn_futures_daily_trend_simulated
uv run --offline --no-sync northstar live preflight \
  --profile cn_futures_daily_trend_simulated
uv run --offline --no-sync northstar live preview-rebalance \
  --profile cn_futures_daily_trend_simulated
```

当前内置画像没有经授权的 runtime Calendar Artifact，因此 `live execute` 会在最终订单提交前
以 `TRADING_CALENDAR_ARTIFACT_REQUIRED` 失败关闭，不会写入仿真订单。不要用 `XSHG`、工作日或
`tests/golden/` 合成 fixture 绕过该门禁。待日历来源制品、授权与配置发布链完成后，才可由账户
持有人按交易所显式配置 `futures.calendar_artifact_snapshot_hashes` 并演练订单写入；随后再用
`live poll` 或 `live sync` 取得异步成交并对账。

这条流程只会修改本地 `ctp_sim` 状态和开发数据库，绝不连接期货公司。完整能力边界见
[`docs/ARCHITECTURE.md`](../../../docs/ARCHITECTURE.md)。
