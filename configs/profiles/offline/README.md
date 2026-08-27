# 离线研究画像

此目录只放不连接券商账户的交易画像，适用于历史数据下载、指标研究、策略回测和本地撮合。
其中的 `lifecycle.role` 只能是 `research` 或 `experimental`，不能用于模拟账户或真实账户执行。

当前仓库内置三条离线研究路径：

```text
cn_futures_daily_trend_offline.yaml          # 连续合约快速研究
cn_futures_daily_actual_offline.yaml         # 实际合约日线验证
cn_futures_intraday_replay_offline.yaml      # 分钟执行专项回放
```

连续合约画像自动下载商品期货主力连续合约日线数据并运行趋势信号，且明确设置
`futures.symbols_are_continuous: true`、`data.live_trading_eligible: false`。连续合约只适合
研究和回测，不能代表实际可交易合约、保证金、手续费或成交能力。

数据来自 `akshare` 提供的新浪主力连续合约接口。画像在
`data.download.options.vendor_symbols` 中显式声明内部研究 symbol 与上游代码的映射；
运行 `python scripts/dev/run_uv.py run --offline --no-sync northstar data download --profile cn_futures_daily_trend_offline` 会自动下载并写入
`storage/downloads/akshare/` 与 `storage/market/`。公开接口可能限流或修订历史数据，因此每次
研究都应保留生成的 manifest，并且不得将这份数据用于实盘下单或结算。

## 创建自己的研究画像

以该文件为模板，在本目录中新建一个 YAML 文件，并同时修改：

1. 文件名与 `profile_id`，两者必须完全相同，且都以 `_offline` 结尾。
2. `name`、频率、期货交易日边界、货币和研究基准。
3. `futures`、`data` 和 `data.download` 的合约规格、数据源、品种池、路径和价格口径。
4. `strategies`、`backtest`、`risk` 与版本字段。

新建后先运行：

```bash
python scripts/dev/run_uv.py run --offline --no-sync northstar data profiles
python scripts/dev/run_uv.py run --offline --no-sync northstar data download --profile <你的期货画像_offline>
python scripts/dev/run_uv.py run --offline --no-sync northstar data validate --profile <你的期货画像_offline>
python scripts/dev/run_uv.py run --offline --no-sync northstar backtest run portfolio --profile <你的期货画像_offline>
```

如果画像使用新策略，必须使用唯一的 `dataset_id` 与 `data.path`，并在 `data.source_id`、
`universe_id` 和 `research_admission` 上保持与数据治理配置一致。完整示例见
[`docs/DEVELOPMENT.md`](../../../docs/DEVELOPMENT.md)。

连接券商模拟账户必须在 `../simulated/` 创建 `*_simulated` 画像；连接真实账户必须在
`../live/` 创建经过柜台、实际合约、保证金和开平仓规则核验的 `*_live` 画像。不得通过修改
offline 连续合约画像绕过这些边界。
