#!/usr/bin/env bash
set -e

echo "下载国内期货连续合约示例数据..."
northstar data download --profile cn_futures_daily_trend_offline

echo "运行期货趋势研究..."
northstar research futures-trend --profile cn_futures_daily_trend_offline

echo "连续合约只用于研究；当前不提供自动下单演示。"
