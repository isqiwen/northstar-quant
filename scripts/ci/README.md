# 本地质量门禁脚本

此目录放置仓库本地质量门禁脚本。GitHub Actions 或其他 hosted CI 不受支持；质量验证通过
`python scripts/dev/run_just.py check` 与相关显式本地命令执行。`check_mypy_baseline.py` 校验版本化类型债务基线；
更新基线仍必须显式执行 `emit`、审阅输出并提交结果。

质量门禁脚本不得写入凭据、真实数据、数据库转储或交易状态。
