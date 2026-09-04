# Repository map

Each domain has one implementation owner. `northstar-quant` records the seam
and delivery state but does not duplicate its code, test suite, deployment, or
runtime configuration.

| Domain | Owning repository | Responsibility |
| --- | --- | --- |
| Control plane | [northstar-quant](https://github.com/isqiwen/northstar-quant) | Work packages, dependencies, delivery policy, and cross-repository governance. |
| Data platform | [quant-data-hub](https://github.com/isqiwen/quant-data-hub) | Canonical structured/reference data, snapshots, lineage, and quality. |
| Market intelligence | [quant-market-intelligence](https://github.com/isqiwen/quant-market-intelligence) | Evidence-bound acquisition and interpretation of unstructured market information. |
| Factor research | [quant-factor-lab](https://github.com/isqiwen/quant-factor-lab) | Point-in-time factor definition, computation, evaluation, and packaging. |
| Strategy research | [quant-strategy-lab](https://github.com/isqiwen/quant-strategy-lab) | Strategy composition, experiments, validation, and candidate eligibility. |
| Backtesting | [quant-backtest](https://github.com/isqiwen/quant-backtest) | Deterministic futures simulation, order/fill modeling, accounting, and results. |
| Portfolio and risk | [quant-portfolio-risk](https://github.com/isqiwen/quant-portfolio-risk) | Allocation, sizing, exposure, limits, stress, and risk decisions. |
| Live execution | [quant-live](https://github.com/isqiwen/quant-live) | Paper/live broker runtime, OMS, execution, reconciliation, and recovery. |
| Operations | [quant-ops](https://github.com/isqiwen/quant-ops) | Release promotion, deployment, observability, recovery, and incident runbooks. |
| Operator console | [quant-console](https://github.com/isqiwen/quant-console) | Operator UI/BFF for provider-owned APIs and audited commands. |

## Cross-repository seams

A control-plane work package names the producer, consumer, immutable evidence
or API contract, ordering dependency, and acceptance evidence. It does not
choose an implementation language, introduce a shared library, or copy domain
logic into this repository.

When ownership is ambiguous, first decide the enduring authority for the state
or behavior. Record that decision in the work package before implementation;
then change only the owner repository unless a real published contract requires
coordinated changes.
