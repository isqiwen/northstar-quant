# Northstar Quant Context

This file records the stable language used at the seams of the local factor-research system. It is a
domain glossary, not an operations guide or a source of runtime configuration.

## Core research terms

```text
Commodity != Instrument != Contract
Document != Event != Feature != Strategy
StrategyTarget != PortfolioTarget != ExecutionPlan != BrokerOrder
```

For local factor mining:

```text
FactorCandidateProposal
!= FactorCandidateDiscoveryResult
!= FactorMiningSelectionCommitment
!= FactorMiningOOSRelease
!= LocalFactorMiningCampaignDeclaration
!= LocalFactorMiningRunBundle
!= LocalFactorMiningRunManifest
!= FactorMiningCampaignRequest
!= FactorMiningCampaignLedgerEvent
!= ResearchDecision
```

| Term | Meaning | Must not be confused with |
|---|---|---|
| `FactorCandidateProposal` | A bounded choice from a host-owned primitive, direction, and finite parameter grid. | Formula code, data selection, a signal, or a strategy. |
| `FactorMiningDiscoveryResult` | Hash-bound IS/validation evidence for one sealed generation receipt. It intentionally contains no OOS/full-run result. | An OOS result, selection, or admission decision. |
| `FactorMiningSelectionCommitment` | The deterministic, immutable researcher-side record of which discovery results may expose OOS evidence. | A strategy approval, portfolio target, or trading authorization. |
| `FactorMiningOOSRelease` | A one-shot, research-only release of OOS evidence for the committed subset. | A Research Decision, SimNow handoff, or any execution action. |
| `FactorMiningStageEvidence` | Hash-bound evidence for one declared IS, validation, or OOS stage and one cost scenario set. | A raw market-data artifact or a live PnL statement. |
| `FactorMiningRunnerResourceBudget` | The frozen candidate, concurrency, CPU, memory, wall-clock, data-row, and artifact-byte limits for one trusted local runner execution. | `FactorSearchBudget`, a scheduler policy, or a trading/risk limit. |
| `LocalFactorMiningCampaignDeclaration` | The immutable, receipt-free pre-generation declaration binding exact DatasetVersions, replay plan, campaign, fixed config, and runner resource budget. | A run bundle, raw provider request, mutable job payload, or automatic retry permission. |
| `LocalFactorMiningRunBundle` | An immutable typed declaration binding exact DatasetVersions, replay plan, campaign, receipt, retention policy and trusted code-revision hash. | A raw file, notebook state, `latest` selector, mutable config, or an AI-owned execution request. |
| `LocalFactorMiningRunManifest` | A hash-only reference to the complete governed evidence graph for one bundle. | An arbitrary derived artifact, a live audit record, or a strategy/portfolio decision. |
| `FactorMiningCampaignRunRequest` | A durable request identity and bounded initiating actor reserved before candidate generation or research computation begins. | A provider call, a receipt, a result, or a queue task that may be silently resumed. |
| `FactorMiningCampaignLedgerEvent` | One ordered, hash-linked PostgreSQL audit fact for the campaign/request lifecycle. | A governed research artifact, raw prompt/response, chain-of-thought, secret, or trading state. |
| `FactorMiningCampaignWorkerSupervisor` | The DB-free Linux guard that applies one cumulative CPU/wall-clock and address-space boundary to a local worker attempt. | A scheduler, database authority, broker capability, or permission to relax a host resource cap. |
| `ReplayAuthorization` | A verifier-confirmed human authorization that terminally records `REPLAY_AUTHORIZED` on one unresolved request and permits one distinct, atomically linked request identity. | A caller-supplied actor/evidence claim, an automatic retry, resume, recovery, or release of a prior reservation. |
| `UNRESOLVED` | The fail-closed state for crash, cancellation, timeout, write ambiguity, or partial failure. | `FAILED`, `CANCELLED`, or permission to generate/release OOS again. |

## Time semantics for discovery

The local discovery protocol uses one globally isolated layout:

```text
shared IS → shared validation → maturity / embargo → selection_at → ordered OOS folds
```

- All discovery folds share exactly the same IS and validation periods.
- OOS folds are after validation, ordered, and non-overlapping.
- A forward outcome belongs to a stage only if both origin and evaluation checkpoints are inside that same stage.
- Boundary-crossing outcomes are purged, not reassigned.
- Retained discovery outcomes must have `evaluation_at < selection_at`.
- Each stage is `FLAT_START_FORCED_CLOSE`: it begins flat and attributes terminal close costs to itself.

`selection_at` and OOS release are research controls. They never relax point-in-time input rules:

```text
available_time <= decision_time
```

## Authority boundaries

AI can generate a receipt and call the discovery-only typed tool. It cannot commit a selection, release
OOS, access raw data, choose DatasetVersions, alter costs, call a broker, create a trading object, or obtain
database-session capability.

`DurableFactorMiningCampaignRunner` is a separate application composition boundary. It first verifies the
receipt-free declaration and data authorization, then transactionally reserves a PostgreSQL request before it
can call a generator or evaluate research. Campaign registration, reservation, redacted receipt commitment,
discovery/selection/OOS commitments, output identity, resource attestation, terminal failure facts, and replay
authorizations are append-only and hash-linked. The campaign root binds the declaration hash and immutable
declaration snapshot; every request chain binds its initiating actor. A durable OOS reservation precedes the
actual OOS release. It never writes a raw prompt, raw response, chain-of-thought, secret,
broker/account/portfolio state, or a substitute trading ledger.
The DB-free worker supervisor applies Linux `ITIMER_REAL` / `ITIMER_PROF` and a non-relaxing `RLIMIT_AS`
boundary across the same attempt's generator, discovery, OOS and publication stages; an unavailable or tripped
guard leaves the durable request `UNRESOLVED` for explicit human review.

Duplicate, concurrent, restarted, cancelled, timed-out, crashed, partially written, or otherwise ambiguous
requests remain `UNRESOLVED`; neither the local service nor the durable runner may automatically resume them.
Only an explicit human `ReplayAuthorization` may form a new request identity, and it remains research-only.
The public replay intent contains only a verifier-issued approval reference and the unresolved request hash: a private,
trusted verifier must confirm that exact binding and return the approver identity plus a verifier receipt hash before
PostgreSQL can append `REPLAY_AUTHORIZED`. The shipped verifier is unavailable by default, so it fails closed rather
than treating a caller-supplied actor or evidence hash as human approval. That authorization closes the source request
as `REPLAY_AUTHORIZED`; it never resumes the source reservation. The Foundation replay-write DTO and writer are private
to this verifier bridge, and architecture tests prohibit every other application surface from importing them. A future
deployed verifier must additionally use a dedicated database role; direct database write authority is outside the
in-process capability boundary and remains an external prerequisite.
The in-memory campaign runner still protects its local protocol, but it is not the cross-process authority.

`northstar-research factor replay` verifies an existing manifest and deterministically projects the entire
evidence graph, including payload, snapshot, lineage and manifest/result identities; it does not publish a
second evidence graph. Definition publication is a trusted composition boundary, while
the artifact writer for exposures, weights, analyses, selection/OOS evidence, report and manifest is internal
to that local research composition.
