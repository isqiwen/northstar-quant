# Northstar Quant

面向中国商品期货的量化研究、情报、组合、风险和交易平台。项目是 real-money-adjacent 系统：当前支持的正向证据只限
offline、paper 与本地 `ctp_sim`；没有真实 CTP 连接、真实账户或实盘交易能力。

默认安全设置：

```text
NORTHSTAR_BROKER=paper
NORTHSTAR_LIVE_TRADING_ENABLED=false
```

账户、持仓、订单、风险、市场数据、日历、合约、保证金、授权或 broker 状态未知时，系统必须 `NO NEW RISK`。

## 快速开始

```powershell
just env-bootstrap
just dev-setup
just check
just test
```

需要隔离 PostgreSQL 时：

```powershell
just db-up
just db-migrate
```

数据库自动化只前向迁移和复用已有数据。仓库自动化绝不删除或清空数据库、表、schema 或 Docker 数据卷；
数据库删除或清空只能由用户在仓库自动化之外手动执行。

## 文档

[文档导航](docs/README.md) 是唯一入口：

- [架构设计](docs/ARCHITECTURE.md)：领域边界、证据流、执行链和非升级边界；
- [开发与研究工作流](docs/DEVELOPMENT.md)：本地设置、画像、策略、回测和质量门禁；
- [运行、配置与部署手册](docs/OPERATIONS.md)：配置、运行模式、报告、部署、备份与故障处理；
- [数据、研究、AI 与安全治理](docs/GOVERNANCE.md)：数据授权、研究准入、AI 权限、审计和人工控制；
- [主实施计划](docs/planning/MASTER_IMPLEMENTATION_PLAN.md)：唯一实施进度事实来源；
- [P10 验收证据](docs/planning/P10_MATURE_V1_ACCEPTANCE_EVIDENCE.md) 与
  [交易故障矩阵](docs/planning/P10_TRADING_FAILURE_MATRIX.md)：已验证能力和外部阻塞的受控记录。

## 当前边界

P10 已完成 7/9 个 Work Package（78%）。生产灾备与权威数据 onboarding 仍需要外部授权、主机、数据许可和制品证据；
它们不会因本地 Docker、fixture 或 `ctp_sim` 成功而自动升级。详见
[主实施计划](docs/planning/MASTER_IMPLEMENTATION_PLAN.md)。

## License

仓库当前未附带单独许可证文件。若需开源发布，应先补充明确的 `LICENSE`。
