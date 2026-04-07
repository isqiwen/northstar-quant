#!/usr/bin/env bash
set -e

echo "初始化数据库..."
northstar init-db

echo "生成示例数据..."
northstar sample-data

echo "运行研究..."
northstar research momentum

echo "运行轻量事件回测..."
northstar backtest event momentum

echo "运行 Backtrader 回测..."
northstar backtest bt momentum

echo "运行纸面交易主流程..."
northstar live run
