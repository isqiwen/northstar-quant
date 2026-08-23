# P10 Trading Failure Matrix

> 建立包：P10-WP07（2026-08-23）。
>
> 本文是 T05 的可审计测试索引。它只汇总受控的本地 `ctp_sim`、paper 和隔离 PostgreSQL
> 验证；不构成真实 CTP、真实账户、真实资金或 production trading 验收。

## 范围与判定

- 每一行都映射 P5-WP10 的一个必测故障类别，或 P10-WP07 新增的 P3 `BLOCK` 无副作用与真实
  CTP 拒绝边界。
- `VERIFIED_SIMULATION` 表示测试在本地模拟器或隔离 PostgreSQL 路径中通过；它不提升任何真实
  broker 或实盘能力。
- `SAFE_BOUNDARY` 表示代码在连接前拒绝危险路径，而非对真实 CTP 的集成验证。
- 任一未知、冲突、持久化失败或不可解释状态均保持 `NO NEW RISK`；本矩阵不能改变
  `NORTHSTAR_BROKER=paper` 或 `NORTHSTAR_LIVE_TRADING_ENABLED=false` 的安全默认值。

## Matrix

| ID | 故障 / 条件 | 预期 fail-closed 结果 | 可复核测试 | 证据模式 |
|---|---|---|---|---|
| T05-01 | disconnect / restart | 断连后的同步明确失败；重连后只从持久状态恢复并对账订单、成交和持仓，不凭推测创建重复新风险。 | `tests/trading_execution/unit/test_ctp_sim_broker.py::test_ctp_sim_recovers_submitted_order_after_disconnect`；`tests/trading_execution/integration/test_ctp_sim_recovery.py::test_ctp_sim_disconnect_recovery_reconciles_order_fill_and_position`；`tests/trading_execution/integration/test_durable_order_submission.py::test_chase_restart_restores_persisted_price_and_quantity` | `VERIFIED_SIMULATION` |
| T05-02 | duplicate / out-of-order callback | 已消费的重复部分成交通知幂等；终态回退和乱序 broker 回调被拒绝，不篡改既有生命周期。 | `tests/trading_execution/unit/test_order_state_machine.py::test_order_state_machine_handles_submit_fill_cancel_and_duplicate_callbacks`；`tests/trading_execution/unit/test_order_state_machine.py::test_order_state_machine_rejects_out_of_order_broker_callback` | `VERIFIED_SIMULATION` |
| T05-03 | unknown broker order | 未解释的 broker 订单使账户作用域进入 sticky `HALT`；正常对账不能自行解除，必须走具名人工恢复。 | `tests/trading_execution/integration/test_reconciliation_state.py::test_unexplained_broker_order_halts_until_named_manual_recovery` | `VERIFIED_SIMULATION` |
| T05-04 | stale market/runtime facts | 陈旧行情或过高保证金使 runtime risk 禁止提交；最终适配器锁在模拟器状态变更前拒绝陈旧 facts。 | `tests/trading_execution/unit/test_runtime_risk.py::test_runtime_risk_blocks_stale_quotes_and_high_margin`；`tests/application/unit/test_ctp_sim_candidate_execution.py::test_final_adapter_lock_refuses_stale_runtime_facts_before_simulator_mutation` | `VERIFIED_SIMULATION` |
| T05-05 | database unavailable | durable 提交在数据库不可用时不调用 broker 提交。 | `tests/trading_execution/integration/test_durable_order_submission.py::test_database_unavailable_prevents_any_broker_submission` | `VERIFIED_SIMULATION` |
| T05-06 | timeout / network partition | 不确定提交状态保持 `UNKNOWN`，不能自动重试并增加风险。 | `tests/trading_execution/integration/test_durable_order_submission.py::test_timeout_or_network_partition_stays_unknown_and_cannot_be_retried` | `VERIFIED_SIMULATION` |
| T05-07 | position / identity mismatch | 后续身份校验失败时对账状态写入整体回滚，并记录失败；不留下部分可信状态。 | `tests/trading_execution/integration/test_reconciliation_state.py::test_reconcile_rolls_back_all_state_rows_when_later_identity_check_fails` | `VERIFIED_SIMULATION` |
| T05-08 | insufficient margin | 开仓保证金不足被拒绝，且没有待处理订单。 | `tests/trading_execution/unit/test_ctp_sim_broker.py::test_ctp_sim_rejects_opening_order_when_margin_is_insufficient` | `VERIFIED_SIMULATION` |
| T05-09 | price limit | 涨停买入在提交前被 pre-trade gate 阻断。 | `tests/trading_execution/failure/test_execution_failure_matrix.py::test_price_limit_blocks_buy_at_upper_limit_before_submission` | `VERIFIED_SIMULATION` |
| T05-10 | cancel reject | 撤单拒绝被持久记录，系统不得声称订单已经取消。 | `tests/trading_execution/integration/test_durable_order_cancellation.py::test_cancel_reject_is_durable_and_does_not_claim_cancellation` | `VERIFIED_SIMULATION` |
| T05-11 | trading-day rollover | 换日后必须显式指定 SHFE/INE 平仓语义，并分别追踪今昨仓；泛化平仓被拒绝。 | `tests/trading_execution/unit/test_ctp_sim_broker.py::test_ctp_sim_requires_explicit_shfe_close_and_tracks_yesterday` | `VERIFIED_SIMULATION` |
| T05-12 | genuine P3 portfolio-risk `BLOCK` | 精确 P3 `BLOCK` 在任何执行工作前失败：不持久化 plan、不组装 order、不调用 `broker.submit_order`、不创建 intent/order/consumption，且模拟器状态字节不变。 | `tests/application/integration/test_p10_portfolio_risk_authority_candidate.py::test_authority_bound_p3_block_cannot_reach_candidate_plan_intent_or_simulator` | `VERIFIED_SIMULATION` |
| T05-13 | real CTP path requested | application composition 在连接前拒绝 `ctp`；adapter 只允许 `FakeCtpFront`；candidate executor 的架构依赖不能到达真实 CTP。 | `tests/trading_execution/unit/test_live_service.py::test_application_composition_root_still_rejects_real_ctp_before_connecting`；`tests/trading_execution/unit/test_ctp_broker_skeleton.py::test_ctp_skeleton_rejects_any_non_fake_front_before_connection`；`tests/architecture/test_ctp_sim_candidate_execution_boundaries.py::test_candidate_executor_cannot_reach_live_ctp_or_ai_control_surfaces` | `SAFE_BOUNDARY` |

## P3 BLOCK no-mutation boundary

T05-12 使用真实的 `PortfolioRiskReviewStatus.BLOCK`，而不是伪造的 preflight 结果。测试在同一条
candidate 执行路径上设置三个哨兵：plan persistence、order assembly 和 `broker.submit_order`。三个
哨兵均不得被调用；同时检查 `ExecutionPlanRecord`、`ExecutionProvenanceConsumptionRecord`、`OrderRecord`
以及 simulator state，以证明风险 gate 在 P8 final fence 之前中断。

## 非升级边界

本文证明的是本地失败关闭行为和真实 CTP 的拒绝边界。它不能用于连接真实 CTP、恢复 `HALT`、创建真实
订单、启用 live trading，或把任何模拟结论声明为 production acceptance。
