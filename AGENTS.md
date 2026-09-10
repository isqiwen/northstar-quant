# Northstar Quant

This repository is the sole maintained personal domestic-futures trading system.
The ultimate objective is controlled live trading. Round one must deliver a complete
single-strategy, single-real-contract cross-day backtest and a concrete broker
Live Sim round trip with simulated funds. Data collection, cleaning, visual inspection,
fixed publication and durable research execution are part of that outcome. Live Sim
uses the same Live kernel, Strategy/Risk/Execution/Broker/Accounting rules and recovery
path intended for production; only the explicitly bound environment, credentials,
account and effective terms differ. Never substitute internal Paper fills for broker
facts. Production funding/admission, mandatory cloud deployment, multi-contract
portfolios and advanced mining follow this first-round acceptance. Read `docs/ARCHITECTURE.md` for changes to data
lifecycle, factor/strategy/risk configuration, workspace controls, trading,
broker integration, recovery or runtime topology; read
`README.md` to run the application. Code documents implemented details.

## Long-term architecture reference: NautilusTrader

Use [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) as the
primary external architecture reference when designing or changing trading,
market-data catalogs, execution or recovery. Read the relevant current official
guides: [Architecture](https://nautilustrader.io/docs/latest/concepts/architecture/),
[Data catalog](https://nautilustrader.io/docs/latest/concepts/data/),
[Execution](https://nautilustrader.io/docs/latest/concepts/execution/),
[Execution reconciliation](https://nautilustrader.io/docs/latest/concepts/reconciliation/),
and [Event sourcing](https://nautilustrader.io/docs/latest/concepts/event_sourcing/).
Record the consulted version/date and the concrete Northstar decisions in
`docs/ARCHITECTURE.md`; documentation on `latest` is mutable.

Apply its separation of market-data processing, strategy lifecycle, risk,
execution, derived portfolio state and runtime composition to actual callers.
Keep market values independent of ORM/storage, strategy output account-neutral,
and order commands distinct from confirmed execution facts. Backtest and Live
reuse decisions and accounting rules with explicit environment-specific input
and execution adapters; broker simulation must never use internal simulated fills.
An in-memory cache is a reconstructible read projection, not another account
authority. Async cache/event persistence is not a substitute for durable
pre-send order identity/reservations or external reconciliation after restart.
Catalog range queries and compaction must preserve immutable publications and
provenance; file names and directory shape alone do not establish correctness.

Reference principles, not package names or implementation language. Do not
rewrite the Python kernel in Rust, add NautilusTrader as a runtime dependency,
or introduce infrastructure solely to resemble the reference.
Research and Live use the shared instance-local `messaging` bus for typed synchronous
routing. Keep business messages beside their owners, account transactions intact,
and each bus confined to its core thread/process. Data Hub and cross-app APIs do
not share this bus. Failed notifications do not undo committed facts or authorize retries.
Domestic futures calendars, settlement, fees, margin and CTP semantics still
require their own verified rules. Preserve Northstar's three independent apps,
local Live SQLite, account ownership and explicit execution authorization.

## Trading environments

A node binds one immutable `Environment`: `BACKTEST` for historical replay,
`SANDBOX` for simulated funds (including external SimNow), `LIVE` for real money.
All contexts share the trading kernel and domain rules. Broker profiles remain
separate (`simnow_trading`, `simnow_dev`, `ctp_production`); the context/profile
pair must agree and cannot change for a running account or saved instance.
SimNow fills remain external CTP facts, never internal simulator fills. Current
file-driven Paper is historical replay and therefore BACKTEST. Context labels
never grant credentials, production admission or execution authorization.

## Module-specific reference projects

Read [the reference catalog](docs/REFERENCES.md) when designing or changing a
related module. It preserves the user's selected projects and their intended
Northstar roles; NautilusTrader remains the primary overall architecture reference.
Consult only the relevant candidates. Before reusing code or adding a dependency,
verify its current official implementation, license, maintenance and applicable
market semantics; record the version/date, decision and limitations in
`docs/ARCHITECTURE.md`. Inclusion in the catalog is not dependency approval or
evidence of implemented functionality. Preserve existing ownership, persistence,
reproducibility and Live safety rules; do not create competing engines or stores
merely to incorporate a reference.

## Permanent engineering rules

- Python implementation, tests, dependencies and build live in `backend/`; Next.js lives
  in `frontend/`; shared protocol sources live in `proto/`. Run root Python commands
  with `uv run --project backend`; keep cross-application tooling in root `scripts/`.

- Keep agent instructions in their owning Git repository. Never create
  `../AGENTS.md` in the non-repository workspace directory.
- Maintain one current project. Complete replacements by removing superseded
  projects, code, configuration and runtime resources; do not retain parallel
  legacy implementations, retirement-only repositories or compatibility layers.
- Greenfield: implement exactly one current architecture, storage model and
  protocol. There is neither backward nor forward compatibility. Replace all
  affected callers atomically and remove superseded code, tests and active prose.
  Immutable content identities preserve reproducibility, not old implementations.
- Deliver one repository and one Python package as three independent applications:
  Northstar Data Hub (`apps/data_hub/`) on core, Northstar Research
  (`apps/research/`) on the workstation, Northstar Live (`apps/live/`) on its own host.
  Cloud is the later production target, not a prerequisite for first-round Live Sim.
  Each owns its Web, configuration, deployment and diagnostics. There is no Console
  application. Share presentation components, not authority or lifecycle.
  `data_management/` owns data business behavior; `apps/data_hub/` composes its app.
  App entries exist; durable Research execution, collectors and cloud delivery
  require their own acceptance and are not implied by separate Web processes.
- Supervise Live Web and its trading kernel independently. Durable Research
  execution and persistent Data collectors must survive browser/Web restart;
  an application is not a fixed number of processes. Persist work before execution.
- Research should use available CPUs and algorithm-supported GPUs, scheduling
  against measured memory, disk and management responsiveness, not arbitrary core
  counts or fixed resource percentages. Protect cancellation and host access.
  Size production Live from measured needs; constrain noncritical management work.
- Research/Paper and Live use the shared `trading.TradingKernel` for single-threaded
  ingress, messaging and failure lifecycle. Environment processors retain their
  transaction ownership: Research may retry only after full rollback; Live faults
  preserve committed facts and require recovery. `market_data.MarketWindow` owns
  bounded causal inputs; portfolio/cache remain derived projections. SimNow uses
  the external broker path, never internal Sandbox fills.
- Keep Strategy, Risk and Accounting as shared Python Modules. Cross-process
  callers use the owner's small Interface, not its tables or in-memory workers.
  Live owns realtime inputs, execution authority and durable account facts without
  depending on Data, Research or a WAN database. Transfer fixed artifacts and
  archives asynchronously; each fact has one authoritative writer. Web restart
  never owns a trading shutdown. Deliver the first-round Data/Research workflow;
  unbounded platform expansion is not a prerequisite for Live Sim.
- Deliver vertical behavior: accepted market/account facts → Strategy and Risk →
  authorized execution → confirmed fills and ledger → reconciliation and browser
  explanation. Distinguish research, internal Paper, broker simulation and live
  evidence. Advanced research and portfolio features do not block the first
  Live Sim acceptance; account safety and explicit simulation authority do.
- Keep types and invariant enforcement beside the behavior that owns them.
  Never create generic `contracts/`, `schemas/`, `validators/`, or `fixtures/`
  layers or renamed equivalents. Generate external descriptions only when used.
- Split touched code into cohesive Modules and, where useful, module folders.
  Keep runtime composition, transport, business behavior and presentation separate;
  move a complete responsibility before extending an already multi-purpose file.
  Prefer a small public Interface and local implementation details over arbitrary
  line-count splits, pass-through layers or a shared catch-all utilities file.
- Build three independent Next.js/React/TypeScript frontend services in `frontend/apps/`,
  with shared presentation in `frontend/shared/` and test code in `frontend/tests/`.
  Run them independently of Python APIs;
  use owned Protobuf messages for browser/API communication. Keep authoritative `.proto`
  sources in root `proto/`, organized by owner; generate Python and TypeScript code into
  their respective implementation directories. Keep business rules with their owners.
  Use mature Ant Design controls
  and library charts. App-owned FastAPI APIs keep authority and business behavior.
  Remove superseded pages and scripts together; do not keep another UI runtime.
- Keep only tests for costly observable failures: causality, money, authorization,
  immutable data, reproducibility and real integration. Never test documents,
  Markdown, links, directory layout, trivial constants or private implementation.

## Domain rules

Data owns file imports, Tushare historical/post-close synchronization, source evidence, calendars, quality and immutable snapshots. Data Hub does not provide realtime recording or a CTP Recorder; Live owns its independent realtime market intake.
Its interface owns source retention, processing provenance and publication; the
workspace calls it rather than manipulating files or database tables. Preserve
referenced evidence; back up database records and their files together. Temporary
processing material is disposable, published data and trading facts are not.
Its public research interface returns immutable values and hides SQLAlchemy.
Strategy maps point-in-time features to account-neutral target exposure. Risk
owns sizing and limits. Accounting applies identified execution/account facts;
Simulation produces simulated fills only. Broker execution owns external order
state, sending and reconciliation; live balances start from verified broker facts,
never research initial cash or simulated fills. Share decisions and ledger meaning,
not the assumption that every order fills on a later bar.
Simulation applies available account facts and effective terms before filling a
previously authorized order on a later executable market event. Broker sessions
apply confirmed external facts, never invent fills from bars. Both observe,
decide and authorize against the updated account and unresolved orders.

Use Decimal for financial values, explicit tick/multiplier/currency and the
canonical futures contract UUID. Preserve bar start, bar completion, availability
and trading day separately. Derive portfolio state from the ledger, never from
operator-supplied state. Persist complete inputs, data identity and implementation
identity with every result. State model limitations truthfully; research results
do not establish strategy profitability or live execution authority.
Save strategy and Risk parameters as immutable configuration revisions; bind runs
to exact revisions and implementation identities. Editing a template never changes
an active session or grants execution authority. Factor management starts with
actual strategy calculations and their data, not a separate platform.
Research owns factor mining/catalogs, strategy versions, experiments, evaluation
and release candidates, not just backtest execution. Shared factor and strategy
Modules own computation; Live owns deployments, instances and execution authority.
Keep evaluation labels out of decision inputs and fitted parameters fixed outside
training. For these changes read architecture sections 3 and 6 and `CONTEXT.md`.
Grow actual behavior into packages atomically; target directories are not empty
scaffolding. Evaluation, deployment acceptance and execution authorization remain
separate facts.

Live safety: default to no order sending. Bind explicit runtime authorization to the
broker environment, account, instrument, strategy/configuration, time window and
risk limits. Startup/reconnect/restore requires reconciliation before re-arming.
The user personally enables real investment execution in the verified software;
the development agent does not send real investment orders on the user's behalf.
Persist order identity and reservations before sending; unknown outcomes keep
their reservations and require inquiry, never blind resubmission. Deduplicate
individual broker fills, support partial fills, and distinguish cancellation or
flatten requests from confirmed completion. One account has one confirmed sender;
lease expiry alone does not authorize failover. Unresolved facts stop new risk.

## Workflow

Repository-owned skills live in `.agents/skills/` with `northstar-` names.
Keep project workflows here rather than in global user-installed skill collections;
official OpenAI skills remain client-managed. Start Codex at this repository root
to discover local skills. See `.agents/skills/README.md` for scope and verification;
this prose cannot unload host/session instructions.

Inspect the worktree first and preserve unrelated user edits. Architecture and
implementation may change together in the same local work package; a separate
documentation merge is never an implementation dependency.
For product development, read `docs/ROADMAP.md`, then the selected issue and its
blocking dependencies in https://github.com/users/isqiwen/projects/1. Use issues
in this repository only, keep Project status aligned with observed progress,
and deliver one primary vertical outcome at a time. Record missing external
inputs without blocking independent work. Close issues only with reviewable
implementation and acceptance evidence; local work alone is not remote delivery.
Verify affected behavior and a real PostgreSQL vertical path when storage or
integration changes.
For agreed implementation tasks, routine edits, commits, non-forced pushes,
CI follow-up and Issue/Project updates are authorized without repeated approval.
Read-only questions authorize inspection, not implementation or publication.
Merges, releases, broker connections and irreversible data operations require
their own explicit task scope; routine development authority does not imply them.

### Asynchronous CI follow-up

After appropriate local checks, commit/push and start CI; do not block on every
run or idle through the pipeline when independent authorized work is available.
Continue that work and check CI at natural task boundaries. Wait only when the
result is needed for the next dependent action or an acceptance conclusion.
A turn may end with "pushed; CI pending". Record the exact SHA and run URL in
the owning Issue (or the final handoff for a documentation-only task), and check
that pending run when work resumes. Do not imply monitoring continues after the turn.
Fix relevant failures before claiming acceptance. Close an Issue or mark its
Project item Done only after the required checks for the delivered SHA pass;
local success, a successful push and a superseded run are not that evidence.
Avoid repetitive unchanged CI progress messages and tight polling; report failures,
meaningful milestones or required user input. A superseded redundant run may be
cancelled after its replacement is confirmed, but cancellation is not a pass.

### Current local refactoring batch (2026-09-11)

The user requests completion of the agreed system refactoring before a combined
GitHub push. Work locally and keep reviewable local commits; do not push this
batch or mark remote Issues/Project items delivered until the complete agreed
batch is ready. This overrides the routine per-slice push workflow above.
Scope: fixed research inputs/time/effective terms, shared futures accounting,
order lifecycle and constrained simulation, reconciled reports, and Live Sim
authorization/execution/recovery. Portfolio/roll/ML and real-money admission are
still later stages. Track implementation, checks and remaining external
acceptance in docs/ROADMAP.md. Do not claim external broker or host acceptance
from synthetic tests; missing external evidence does not block independent code.
