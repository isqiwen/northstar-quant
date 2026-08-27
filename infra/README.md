# 基础设施

这里保存可审阅的部署声明和运维模板，不保存环境状态。运行时容器卷、Terraform state、Kubernetes
Secret、监控运行数据、数据库转储、行情下载、账户快照和任何凭据均不得提交。

这些声明的目标平台是 Linux 生产服务器。Windows 与 Linux 开发机通过 `just` 和 Python 控制面
构建、预检并远程调用它们；开发机不应直接运行 systemd、production scheduler、worker 或 live
trading。当前尚无可运行 worker 命令，因此不得创建会伪装为已支持能力的 worker systemd 单元。

`backup/northstar-quant/` 仅保存备份策略、恢复演练模板和无秘密证据说明；真实备份必须位于
仓库外的独立、受控存储中。
