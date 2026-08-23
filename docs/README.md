# 文档导航

这里是 Northstar Quant 的**唯一文档目录**。每个主题只保留一个权威说明；README 只提供
启动入口，不重复配置、回测、执行或运维细节。

## 按任务阅读

| 你要做什么 | 阅读文档 | 说明 |
| --- | --- | --- |
| 第一次实现策略并完成回测 | [第一个策略与回测教程](00_第一个策略与回测教程.md) | 从画像、策略、测试、数据到报告分析的完整研究路径。 |
| 理解六大领域、闭环与模块职责 | [架构总览](01_架构总览.md) | 六大领域、业务闭环、依赖边界与真实交易阻断条件。 |
| 配置环境、画像、数据和调度 | [配置说明](02_配置说明.md) | Settings、交易画像、数据源、品种池、manifest 和调度配置。 |
| 理解执行与安全状态 | [执行与安全边界](03_执行与安全边界.md) | paper、ctp_sim、真实 CTP 的能力边界和不可跳过的前置条件。 |
| 运行并审计期货回测 | [期货回测器说明](04_期货回测器说明.md) | 三类回测器、数据契约、换月、成交与可信边界。 |
| 生成报告、PDF、邮件投递或查看即时告警边界 | [报告与通知](05_报告_PDF与通知.md) | 报告制品、PDF 渲染、私有 ntfy 即时告警与邮件报告投递的安全边界。 |
| 编写代码或配置注释 | [代码与配置注释规范](06_代码与配置注释规范.md) | 量化假设、配置字段和安全边界的注释要求。 |
| 从 Windows/Linux 工作站部署或运维 Linux 服务器 | [Linux 一键部署](07_Linux一键部署.md) | Just/Python 控制面、制品、迁移、回退、systemd、私网 Dashboard、受限备份包与 PostgreSQL 证据门禁。 |
| 了解项目阶段与 AI 实施约束 | [项目主规划与实施状态](08_项目主规划与实施状态.md) | 当前阶段、依赖链、验收标准和禁止事项。 |
| 审核数据供应商与策略研究资格 | [研究准入政策与数据治理](09_研究准入政策与数据治理.md) | 授权边界、目标品种、候选研究阈值与激活流程。 |
| 审核机密、输出脱敏、审计事件和服务权限 | [平台安全与审计](platform_security_audit.md) | 密钥扫描、日志与报告脱敏、稳定安全审计事件和最小权限边界。 |
| 审核 AI 研究与运维 Agent 的能力、PIT 与交易隔离边界 | [AI 研究工具边界](10_AI研究工具边界.md) | 九项 research-only allowlist、独立单项 Ops snapshot allowlist、注入式 ports 与 fail-closed 约束。 |

## 开发辅助文档

- [Codex 主实施计划](planning/MASTER_IMPLEMENTATION_PLAN.md)：当前阶段、Work Package、验收状态与下一任务的唯一事实来源。
- [测试说明](../tests/README.md)：Windows/Linux 测试分层、本地 PostgreSQL 与质量命令。
- [脚本说明](../scripts/README.md)：Just、跨平台控制面与 Linux 目标端脚本职责。
- [离线画像说明](../configs/profiles/offline/README.md)：研究画像的创建约束。
- [模拟画像说明](../configs/profiles/simulated/README.md)：本地 `ctp_sim` 语义演练边界。
- [真实画像说明](../configs/profiles/live/README.md)：真实账户画像当前为什么不可创建。
- [期货品种卡说明](../configs/instruments/products/README.md)：静态合约规格与动态规则的区别。

## 文档维护规则

1. 新增功能先更新所属权威文档；不要为同一主题再创建“版本说明”或“增强说明”。
2. 先执行 `just env-bootstrap`；之后命令以 `uv run --offline --no-sync northstar ...` 为准，任何会接触账户的命令必须同时说明当前安全门禁。
3. 当前事实、未来设计和历史记录必须分开表述。未来设计不能写成已可运行能力。
4. 本地 Markdown 链接必须可解析；文档契约测试会阻止失效的仓库内链接。
