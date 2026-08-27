# 文档导航

这是 Northstar Quant 的唯一文档入口。每个主题只保留一个规范权威；根 README 只提供项目入口，
不重复配置、回测、执行或运维细节。

| 你要解决的问题 | 阅读文档 | 权威范围 |
|---|---|---|
| 理解系统边界、领域依赖和决策/订单链 | [架构设计](ARCHITECTURE.md) | 结构、语义、PIT、证据流、AI/执行边界和外部前提。 |
| 初始化环境、创建画像、开发策略、运行回测 | [开发与研究工作流](DEVELOPMENT.md) | 开发平台、质量门禁、研究路径和代码约定。 |
| 配置、运行画像、报告、部署、备份与故障处理 | [运行、配置与部署手册](OPERATIONS.md) | 配置事实来源、运行/生产边界和运维程序。 |
| 审核数据供应商、研究资格、AI 权限和安全控制 | [数据、研究、AI 与安全治理](GOVERNANCE.md) | 授权、PIT、准入、机密、审计与人工确认。 |
| 查看当前实施状态、下一个工作包或外部阻塞 | [规划与验收控制面](planning/README.md) | 主实施计划、P10 验收登记和交易故障矩阵。 |

## 关联工程文档

- [测试说明](../tests/README.md)：测试层级、隔离 PostgreSQL 和质量命令；
- [脚本说明](../scripts/README.md)：`just`、跨平台控制面和 Linux 目标端脚本；
- [基础设施说明](../infra/README.md)：部署声明、systemd、monitoring 与 backup 资产；
- [offline 画像](../configs/profiles/offline/README.md)、[simulated 画像](../configs/profiles/simulated/README.md)、
  [live 画像](../configs/profiles/live/README.md)：各生命周期画像的专用约束；
- [期货品种卡](../configs/instruments/products/README.md)：静态合约规格与动态规则的边界。

## 维护规则

1. 修改功能时同步更新其唯一权威文档；不要创建“补充说明”“新版说明”或兼容跳转页。
2. 当前事实、设计目标、验收证据与历史记录必须分开表述；未授权的外部能力不能写成已可运行。
3. 所有仓库内 Markdown 链接必须可解析；文档契约测试会阻止失效链接和关键安全语义漂移。
4. 项目仍在研发中，不为旧文档路径、旧接口或旧配置保留兼容层；同一变更必须迁移全部仓库调用方。
