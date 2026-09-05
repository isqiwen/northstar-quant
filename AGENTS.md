# Northstar Quant

This repository is the sole maintained personal domestic-futures trading system.
The ultimate objective is controlled live trading. Deliver the shortest safe
vertical path through a concrete broker simulation to a bounded real-account
round trip; research and internal Paper support that path, not a prerequisite
platform to finish in full. Read `docs/ARCHITECTURE.md` for changes to domain
meaning, trading, broker integration, account recovery or runtime topology; read
`README.md` to run the application. Code documents implemented details.

## Permanent engineering rules

- Keep agent instructions in their owning Git repository. Never create
  `../AGENTS.md` in the non-repository workspace directory.
- Maintain one current project. Complete replacements by removing superseded
  projects, code, configuration and runtime resources; do not retain parallel
  legacy implementations, retirement-only repositories or compatibility layers.
- Greenfield: implement exactly one current architecture, storage model and
  protocol. There is neither backward nor forward compatibility. Replace all
  affected callers atomically and remove superseded code, tests and active prose.
  Immutable content identities preserve reproducibility, not old implementations.
- Repository count is a design choice. The current choice is one repository,
  one Python package, one application process and PostgreSQL. Modules call typed
  Python functions in process. Add another deployment only for demonstrated need.
- Deliver vertical behavior: accepted market/account facts → Strategy and Risk →
  authorized execution → confirmed fills and ledger → reconciliation and browser
  explanation. Distinguish research, internal Paper, broker simulation and live
  evidence. Advanced research and portfolio features do not block the first
  bounded live path; account safety and explicit trading authority do.
- Keep types and invariant enforcement beside the behavior that owns them.
  Never create generic `contracts/`, `schemas/`, `validators/`, or `fixtures/`
  layers or renamed equivalents. Generate external descriptions only when used.
- Keep only tests for costly observable failures: causality, money, authorization,
  immutable data, reproducibility and real integration. Never test documents,
  Markdown, links, directory layout, trivial constants or private implementation.

## Domain rules

Data owns ingestion, source evidence, calendars, quality and immutable snapshots.
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
