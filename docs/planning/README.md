# 规划与验收控制面

本目录保存项目计划、验收证据与故障矩阵。它们是控制面文档，不与架构、开发、运行或治理说明重复。

| 文档 | 权威范围 |
|---|---|
| [MASTER_IMPLEMENTATION_PLAN.md](MASTER_IMPLEMENTATION_PLAN.md) | 唯一实施进度事实来源：active phase、active work package、next task、blocked work packages、验收标准与变更日志。 |
| [P10_MATURE_V1_ACCEPTANCE_EVIDENCE.md](P10_MATURE_V1_ACCEPTANCE_EVIDENCE.md) | Mature v1 的受控实现、离线/仿真验证、SAFE_BOUNDARY 与外部阻塞登记。 |
| [P10_TRADING_FAILURE_MATRIX.md](P10_TRADING_FAILURE_MATRIX.md) | 交易失败路径、P3 `BLOCK` 无 mutation 与真实 CTP 连接前拒绝的可审计索引。 |

使用规则：

- 任何非 trivial 改动先读取根目录 `AGENTS.md` 和主实施计划，再执行 `git status`；
- 每次只处理一个 Work Package，完成后同步实现、测试、配置、schema/migration、CLI、脚本和文档；
- `DONE` 只在完整验收后标记；`BLOCKED` 必须写明外部前提，不能以本地模拟替代；
- 计划中记录的生产、数据或真实交易缺口不会因为存在代码或测试而自动升级。

系统结构说明请读[架构设计](../ARCHITECTURE.md)；仓库文档入口见[文档导航](../README.md)。
