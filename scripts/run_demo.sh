#!/usr/bin/env bash
set -e

echo "初始化数据库..."
northstar init-db

echo "生成中国 A 股 ETF 示例数据..."
northstar sample-data --profile cn_etf_daily

echo "运行研究..."
northstar research momentum --profile cn_etf_daily

echo "运行轻量事件回测..."
northstar backtest event momentum --profile cn_etf_daily

echo "运行 Backtrader 回测..."
northstar backtest bt momentum --profile cn_etf_daily

echo "运行纸面交易主流程..."
northstar live run --profile cn_etf_daily
