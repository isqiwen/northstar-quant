# Repository map

Baseline date: 2026-09-04. Maturity describes verified default-branch contents,
not issue titles or aspirational architecture documents.

Verified default-branch anchors: Data Hub `1dbd3f9`, Market Intelligence
`9b6b829`, Factor Lab `1807ed3`, Strategy Lab `4472910`, Backtest `2979609`,
Portfolio Risk `fb3480d`, Live `e5b5e2a`, Ops `a06769a`, and Console `ddf2c11`.

| Repository | Domain authority | Verified baseline | Explicit exclusions |
| --- | --- | --- | --- |
| [quant-data-hub](https://github.com/isqiwen/quant-data-hub) | Canonical structured/reference data, lineage, quality evidence, immutable dataset snapshots and delivery. | Executable FastAPI/PostgreSQL module. Release `v0.9.0` is `6db13fb`; default branch `1dbd3f9` adds PR #30 while package metadata still says `0.9.0`, creating unreleased version drift. Current manifests/membership are append-only and drift-fail-closed, but complete observation revision/supersession is not implemented. | Factors, strategies, simulation, portfolio risk, orders. |
| [quant-market-intelligence](https://github.com/isqiwen/quant-market-intelligence) | Unstructured acquisition, document/evidence versions, descriptive claims, review, and `IntelligenceArtifact`. | Architecture-only repository; no package, contract, test, or CI implementation. | Canonical structured-data authority, trading signals, factors, execution. |
| [quant-factor-lab](https://github.com/isqiwen/quant-factor-lab) | Point-in-time factor definition, computation, statistical evidence, tri-state publication gate, and inert `FactorPackage`. | Package metadata `0.1.0`, with no tag/release; strict Data Hub `v0.9.0` M4-B manifest/bar reader and 31 tests; no M4-C export, factor computation or publication yet. | Complete strategies, simulation, account risk, orders. |
| [quant-strategy-lab](https://github.com/isqiwen/quant-strategy-lab) | Strategy composition, preregistered experiments, validation, eligibility, inert `StrategyRelease` and `StrategyIntent`. | Architecture-only repository. | Data/factor authority, simulator, position sizing, risk approval, activation. |
| [quant-backtest](https://github.com/isqiwen/quant-backtest) | Deterministic simulation time/order, point-in-time data access, simulated orders/fills/accounting/settlement, and immutable results. | Python `0.1.0`; published `BacktestRunSpec v1` and `BacktestResult v1` validation contracts; no executable simulator. | Factor/strategy selection, risk policy meaning, brokers, live effects. |
| [quant-portfolio-risk](https://github.com/isqiwen/quant-portfolio-risk) | Cross-strategy allocation, sizing, exposure, limits, stress, and `RiskDecision`. | Architecture-only repository. | Account truth, OMS/fills, strategy research, deployment, broker effects. |
| [quant-live](https://github.com/isqiwen/quant-live) | Broker/session authority, capture handoff, live market data, OMS, account projection, reconciliation, safety state, Paper/SimNow execution. | Architecture-only repository; no runtime, contracts, tests, or CI. | Research, backtesting, portfolio-risk semantics, deployment authority. |
| [quant-ops](https://github.com/isqiwen/quant-ops) | Artifact/approval verification, release/deployment/rollback mechanics, environment inventory, observability transport, backup/restore mechanics and incident evidence. | Python `0.1.0` local planning/rendering prototype; 17 tests; no remote apply, release gate, durable evidence store, or restore automation. | Domain decisions and integrity predicates, secrets, live activation, broker/account authority. |
| [quant-console](https://github.com/isqiwen/quant-console) | Operator presentation/BFF, secure browser session, provider adapters, degraded-state UX, and non-authoritative command correlation/status tracking. | Architecture-only repository. | Domain truth or audit authority, direct database/artifact/broker/SSH access, business rules. |

`northstar-quant` is not a tenth runtime domain. It is the portfolio-level
implementation control plane: it owns topology, sequencing and acceptance, but
no domain contract or operational decision.
