# Nine-repository roadmap

Architecture revision: `NORTHSTAR-ARCH-R1`

Planning baseline: 2026-09-04

System of record: [GitHub Project 1](https://github.com/users/isqiwen/projects/1)

This is a rolling-wave plan for the nine domain repositories. It replaces the
former phase plan in full; old phase and milestone identifiers are not carried
forward. Only work with an observable outcome and a stable owner enters the
Project. Later outcomes stay here as themes until their prerequisites are real.

## Current focus

- In review: `R1-GOV-01`, via
  [northstar-quant PR #11](https://github.com/isqiwen/northstar-quant/pull/11).
- Next after `R1-GOV-01` is merged: `R1-GOV-02`, align the nine
  repository-local context documents. It remains Backlog until then.
- Work-in-progress limit: three items globally and one item per repository.
- First integrated target: deterministic offline research evidence with no
  broker path and no trading authority.

## Work packages

Each code links to its owner-repository issue. Dependencies refer to codes in
this table and form the acyclic delivery graph
in [the architecture](ARCHITECTURE.md#current-wave-delivery-dependency-graph).

| Code | Owner | Outcome | Status | Priority | Kind | Effort | Target | Depends on |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [`R1-GOV-01`](https://github.com/isqiwen/northstar-quant/issues/10) | northstar-quant | Publish architecture R1 and reset Project 1. | Review | P0 | Task | M | 2026-Q3 | — |
| [`R1-GOV-02`](https://github.com/isqiwen/northstar-quant/issues/9) | northstar-quant | Align repository-local ecosystem context to R1. | Backlog | P0 | Task | M | 2026-Q3 | GOV-01 |
| [`R1-DH-01`](https://github.com/isqiwen/quant-data-hub/issues/32) | quant-data-hub | Establish a unique post-`v0.9.0` release baseline and contract inventory. | Backlog | P0 | Task | M | 2026-Q3 | GOV-02 |
| [`R1-OPS-01`](https://github.com/isqiwen/quant-ops/issues/16) | quant-ops | Repair evidence, preflight, rollback and schema correctness in `0.1`. | Backlog | P0 | Bug | M | 2026-Q3 | GOV-02 |
| [`R1-BT-01`](https://github.com/isqiwen/quant-backtest/issues/23) | quant-backtest | Publish the deterministic simulation protocol seam. | Backlog | P0 | Feature | L | 2026-Q3 | GOV-02 |
| [`R1-RISK-01`](https://github.com/isqiwen/quant-portfolio-risk/issues/14) | quant-portfolio-risk | Bootstrap the pure risk module and producer-owned contracts. | Backlog | P0 | Feature | L | 2026-Q3 | GOV-02 |
| [`R1-SL-01`](https://github.com/isqiwen/quant-strategy-lab/issues/14) | quant-strategy-lab | Bootstrap strategy definitions, releases and account-neutral intents. | Backlog | P0 | Feature | L | 2026-Q3 | GOV-02 |
| [`R1-DH-02`](https://github.com/isqiwen/quant-data-hub/issues/31) | quant-data-hub | Publish immutable revision/supersession semantics and Snapshot interface `1.0`. | Backlog | P0 | Feature | XL | 2026-Q4 | DH-01 |
| [`R1-OPS-02`](https://github.com/isqiwen/quant-ops/issues/15) | quant-ops | Publish the cross-repository release-verification interface. | Backlog | P1 | Feature | L | 2026-Q4 | OPS-01 |
| [`R1-BT-02`](https://github.com/isqiwen/quant-backtest/issues/22) | quant-backtest | Implement the minimum deterministic simulator slice. | Backlog | P1 | Feature | XL | 2026-Q4 | BT-01, DH-02, RISK-01 |
| [`R1-RISK-02`](https://github.com/isqiwen/quant-portfolio-risk/issues/13) | quant-portfolio-risk | Implement a deterministic sizing, exposure and limit-decision slice. | Backlog | P1 | Feature | L | 2026-Q4 | RISK-01, SL-01 |
| [`R1-FL-01`](https://github.com/isqiwen/quant-factor-lab/issues/19) | quant-factor-lab | Publish factor-domain contracts and the control catalog. | Backlog | P1 | Feature | L | 2026-Q4 | DH-01 |
| [`R1-FL-02`](https://github.com/isqiwen/quant-factor-lab/issues/18) | quant-factor-lab | Implement a point-in-time factor-computation slice. | Backlog | P1 | Feature | L | 2026-Q4 | FL-01, DH-02 |
| [`R1-MI-01`](https://github.com/isqiwen/quant-market-intelligence/issues/13) | quant-market-intelligence | Bootstrap the executable evidence module and producer contracts. | Backlog | P2 | Feature | L | 2026-Q4 | GOV-02 |
| [`R1-LIVE-01`](https://github.com/isqiwen/quant-live/issues/15) | quant-live | Bootstrap a disarmed, no-broker-effect runtime contract foundation. | Backlog | P1 | Feature | L | 2026-Q4 | GOV-02 |
| [`R1-SL-02`](https://github.com/isqiwen/quant-strategy-lab/issues/15) | quant-strategy-lab | Produce the first inert StrategyRelease through official Backtest and pinned Risk. | Backlog | P1 | Feature | XL | 2027-Q1 | SL-01, FL-02, BT-02, RISK-02 |
| [`R1-CON-01`](https://github.com/isqiwen/quant-console/issues/12) | quant-console | Bootstrap a secure read-only BFF/UI with pinned provider compatibility. | Backlog | P2 | Feature | L | 2027-Q1 | GOV-02, DH-02 |
| [`R1-E2E-01`](https://github.com/isqiwen/northstar-quant/issues/8) | northstar-quant | Accept the cross-repository offline research golden path. | Backlog | P1 | Task | M | 2027-Q1 | SL-02 |

## Acceptance evidence

### Governance

- `R1-GOV-01`: architecture, repository map, roadmap and Project policy are
  merged; the old plan is absent; Project 1 contains only the replacement work
  packages with all required fields populated.
- `R1-GOV-02`: all nine default branches link to `NORTHSTAR-ARCH-R1`, remove the
  copied no-umbrella assertion, declare local authority/exclusions, and pass
  their documentation checks.

### Data and intelligence

- `R1-DH-01`: one new immutable release identifies the exact default-branch
  code and enumerates every supported cross-repository contract/version; no tag
  and branch share a version while differing in bytes.
- `R1-DH-02`: fixtures prove immutable revisions, explicit supersession,
  bounded reads, lineage and fail-closed major-version handling; release notes
  name the Snapshot `1.0` compatibility policy. Reference fixtures bind an
  explicit ID, version and hash, never `latest`, and reject invalid time or
  precision, missing pins and incompatible versions.
- `R1-MI-01`: an installable module publishes versioned `DocumentManifest`,
  `EvidenceReference` and `IntelligenceArtifact` schemas plus producer fixtures;
  tests reject missing provenance, ambiguous time and mutable identity. It does
  not fetch arbitrary URLs, call a model, or publish into Data Hub.

### Factors, strategy, risk and simulation

- `R1-FL-01`: producer schemas and fixtures cover definition, run request,
  evaluation/gate evidence and inert package manifest; control identifiers and
  tri-state outcomes have one documented owner.
- `R1-FL-02`: a pinned Data Hub snapshot produces a reproducible factor artifact
  without look-ahead; tests cover event/available time, missing members,
  revision drift and repeated-run hash equality. It contains no local
  simulation/fill/cost/accounting module or trading/promotion authority.
- `R1-SL-01`: installable schemas and fixtures cover `StrategyDefinition`, inert
  `StrategyRelease` and account-neutral `StrategyIntent`; tests reject account,
  lot, order, approval and activation authority.
- `R1-RISK-01`: pure deterministic contracts cover `RiskPolicy`, normalized
  `RiskPortfolioStateInput`, `PortfolioProposal` and `RiskDecision`; unknown,
  stale or incompatible account facts fail closed.
- `R1-RISK-02`: fixed intent, account state and policy fixtures reproduce sizing,
  exposure and limit outcomes with reason codes; boundary and property tests
  prove no decision exceeds policy.
- `R1-BT-01`: versioned fixtures define participant lifecycle, intent/risk seam,
  event order, simulated command/order/fill/account facts and result identity;
  protocols contain no broker or wall-clock dependency.
- `R1-BT-02`: a pinned snapshot, participant and Risk decision run end-to-end
  twice with byte-identical result hashes; ledger conservation, settlement,
  margin, ordering and failure fixtures pass.
- `R1-SL-02`: an inert release binds exact factor, data, simulator, risk-policy
  and experiment evidence; the official Backtest reproduces the declared result
  and no Live activation path exists.

### Runtime, operations and presentation

- `R1-OPS-01`: tests prove complete approval identity, observation freshness,
  distinct current/predecessor rollback digests and resolvable schema recipes;
  unknown evidence stops the plan.
- `R1-OPS-02`: a versioned manifest and verification result bind repository,
  commit, artifact hash, schema, environment and approval; consumer fixtures
  prove tamper and incompatible-version rejection. It cannot arm Live.
- `R1-LIVE-01`: an installable service exposes versioned readiness, safety,
  reconciliation, capture-manifest and command-status fixtures; it cannot arm,
  connect to a broker or create any external effect, and `REAL_MONEY` is
  structurally impossible in this slice.
- `R1-CON-01`: authenticated browser-to-BFF reads one pinned Data Hub provider
  interface, renders stale/unavailable/incompatible/unknown states, and has no
  generic proxy or direct database, artifact-store, SSH or broker credential.

### Integrated acceptance

- `R1-E2E-01`: clean documented macOS and Linux x86_64 environments resolve
  pinned Data Hub data into factor evidence, an inert StrategyRelease,
  deterministic Risk and Backtest evidence; two executions reproduce all
  declared hashes, failure injection is fail-closed, and the acceptance
  manifest links every merged owner PR.

## Later themes, not Project items

- Market Intelligence to Data Hub to Factor Lab evidence lineage.
- Paper end-to-end rehearsal with fake broker and operator Console; entry
  requires `R1-E2E-01`, `R1-LIVE-01`, `R1-OPS-02` and `R1-CON-01` evidence.
- SimNow qualification after Paper reliability evidence.
- Production/read-only observation, then a separately authorized real-money
  gate. No date or issue is assigned until the preceding safety evidence exists.

Expanding a theme requires an architecture review, a named owner, explicit
exclusions, dependency-ready producer interfaces and observable acceptance
evidence. It must not be decomposed into speculative placeholder issues.
