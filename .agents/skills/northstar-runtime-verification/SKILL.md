---
name: northstar-runtime-verification
description: Verify or diagnose Northstar installed applications, persistence and Web/worker lifecycle isolation using the existing acceptance paths. Not for pure computation changes or routine documentation edits.
---

# Northstar runtime verification

Resolve paths from this Git repository's root. Start with the claimed behavior and
environment, not a blanket restart. Diagnosis alone authorizes checks, not fixes,
personal-service changes or new broker activity.

Read `Makefile`, `.github/workflows/ci.yml` and the relevant README section for
current commands and dependencies; do not copy stale test counts or image tags.

| Claim | Existing path and meaning |
|---|---|
| Persisted business behavior | Affected tests against disposable PostgreSQL; inspect `backend/tests/conftest.py` before selecting the database |
| Clean installation and cross-app research/Paper flow | Build/install a fresh wheel as CI does; run `scripts/acceptance/check_install.py` with the installed interpreter, not the source environment |
| Standalone Live supervision/storage failure | Read `deploy/live/README.md`; run `scripts/acceptance/check_live_deployment.py` with the image built from the change under verification |
| User can operate a page | Actual browser interaction; HTTP 200 does not execute React controls or verify dynamic API interactions |
| Current broker/cloud behavior | Explicitly scoped external acceptance; local containers, replay and stored callbacks cannot establish it |

Inspect the selected script's side effects, inputs and cleanup targets before
running it. Reuse its mechanics instead of writing another orchestration script.
Use unique disposable database/container identities, source directories and ports;
personal volumes, saved market evidence and active services are not test resources.
Never load SimNow credentials just to verify topology. Missing PostgreSQL clients
can skip restore checks: report the skip or supply the declared tools and rerun,
not a blanket "all persistence checks passed". Keep private config out of output.

For a restart claim, observe independent runtime identity and progress before and
after restarting only the management Web. For failure, check both the unavailable
owner and the still-serving management process; readiness is not tradability.
A restarted kernel must not silently create another receiver or inherit execution
authority. Use the existing bounded acceptance, not YAML or PID counts alone.

Distinguish synthetic research, final-revised history, reconstructed capture,
internal Paper and external broker facts. Preserve original evidence; adapt a
private configuration copy to the current format when necessary, never add
compatibility merely to make an old acceptance command succeed.

Report tested commit/image, checks actually run, skips/unknowns and cleanup outcome.
Remove only the disposable resources this run created; leave the personal deployment
unchanged unless its operation was explicitly in scope.


## Data browsing acceptance

For Data Hub discovery changes, exercise `scripts/acceptance/check_browser.py` from
an ordinary `/browse` entry: select a contract with a nonempty publication without manually supplying
contract/dates, inspect the resulting chart and exact rows, then recover from an
empty manual date range using the available-data list. Search by a supplied Chinese
contract name and confirm the unchanged supplier code. For chart changes, switch
only available native periods and verify their populated dates, inspect whole-range chart counts
across detail pagination, and exercise zoom/indicator controls. Chart presentation
must not infer realtime prices or replace exact source values with indicator floats. Contract catalog membership,
sync counters and a nonempty publication request window are not proof that a chosen
date range contains rows. The owning `exploration.discovery` entry selects a current native publication and opens a pinned
receipt through the same file verification as `exploration.rows`; PostgreSQL tests
in `backend/tests/data_management/test_exploration.py` cover that boundary. Use the
existing synthetic installed fixtures; do not depend on today's date or personal
historical downloads for repeatable browser acceptance.

For the contract workbench, assert one row per contract across native periods. Open
the date-range drawer for manual ranges and the detail drawer for raw rows/source
checks; close drawers before chart interactions. Ordinary browse re-entry restores
the last contract/period in the tab; explicit receipt links take precedence.

Exercise detail pagination for both native and compacted views. A parent that
clears the current result during every page fetch unmounts the drawer and loses
its open state; preserve the current fixed view until the next page arrives,
while clearing it when the owning artifact identity changes.


### Contract completeness review

`POST /api/sync/contracts/review` reports a real contract's listing-to-expiry
requirements. Check actual listing, last trading and last delivery dates separately.
Active or delivery-in-progress contracts are ineligible; missing/conflicting metadata stays unknown.
Search last trading dates from 2015-01-01 ascending, preserving pre-2015 listing history.
Exercise both planning and claiming against an existing newer queue; changing only SQL planning
order leaves already queued downloads ahead of historical candidates. Unknown lifecycle metadata
must also block rejected-source cleanup, not just new planning.
Use the browser's “整体验收” action on downloaded contract data. Check a complete
calendar through the candidate cutoff; an old maximum open day is insufficient.
A response/window count, nonempty chart or successful HTTP request is not whole
contract acceptance. The report verifies actual owned daily/period/term files, and explicitly records unresolved minute
session/label and native dataset applicability evidence; it cannot authorize
source deletion. Test with `backend/tests/data_management/test_contract_review.py`
against the disposable PostgreSQL database. Preserve old fixed receipts when
changing response quality rules; rejection of new mixed-quality responses must
not publish their remaining good rows.

## Complete retired-contract catalog

Data Hub discovery and Research handoff require a contract snapshot membership.
A validated supplier response alone is private processing evidence. Installed
browser fixtures may explicitly pin synthetic snapshot facts to exercise readers;
never interpret those facts as supplier lifecycle acceptance. Verify manifest and partition hashes
and exact-byte restoration in joint backup/restore. Real admission must
show verified listing/last-trading/last-delivery dates and all type-applicable required dataset completeness;
unknown historical sessions or applicability keep publication blocked.

A full, explicitly authorized Data Hub schema reset also removes extensions in
`public`. Before initializing that empty schema through the application role,
restore the baseline's existing `btree_gist` prerequisite through the database
administrator. Do not grant database administration to the application role.
A 42501 during `maintenance init-db` must be diagnosed from the database error;
retrying or reopening registration does not resolve it. After initialization,
verify empty old-data tables, unchanged account/token fingerprints and stopped
sync before enabling the new collector through its owning settings interface.


For synchronization UI changes, enter `/sync` without a request deep link. Verify
contract counts and one row per contract; dataset/request counters belong only to
an explicitly opened diagnostics view. Exercise exchange/product filters and server
pagination beyond 100 collections. Open contract details for native dataset checks,
and contract request diagnostics for owner-linked product requests (job scope may
be a product, not the contract). A validated response must never display as a complete
contract publication. Token configuration is under the collapsed collection settings;
reopen it after reload before checking that the token field is empty.


When investigating rejected Tushare bars, compare the response's fields/items with
pre-normalization diagnostic values. A successful HTTP request or nonempty response
is not valid OHLC. Use the recorded source-release audit before claiming a missing
raw file is corruption; rejected unreferenced originals may have been intentionally
released. New reports retain bounded numeric/time samples, not arbitrary provider text.
Do not present a fresh supplier probe as the original historical response. Distinguish
response rejection, collection gaps and unresolved session evidence in contract reasons.

If the worker repeatedly exits inside rejected-source candidate selection, inspect
PostgreSQL's original error. A failed shared-memory resize may come from parallel
hash joins in the maintenance query, even when ordinary storage is healthy. Keep
that bounded scan serial with transaction-local settings; do not globally disable
parallel query or delete data to recover space without diagnosing the allocation.


For low Data Hub utilization, inspect pg_stat_activity and the exact retention
EXPLAIN plan before increasing container CPU/RAM or /dev/shm. Verify indexes are
valid after concurrent creation; canceled builds can leave invalid indexes that
IF NOT EXISTS does not repair. A stopped client can leave its active read query
running: cancel only the identified obsolete maintenance query before retrying DDL.
The data-retention child must remain independent of collector supervision; inspect
its own retention logs, PID and bounded elapsed time. Candidate discovery must not
hold the source gate. Recheck new owner/reference pins under the gate before release.
Benchmark both candidate discovery and locked reference checks, including the
no-candidate case; LIMIT alone does not bound a join's scanned rows.


For type-policy changes, exercise the same `contract_data.requirements` decision
through planning, authenticated review and snapshot input selection. Test DCE V_F
with matching monthly-average/cash metadata, physical V, CFFEX stock-index/bond,
INE EC and conflicting/unknown metadata. Missing warehouse for cash/bond types
must not block; missing minute/settlement or UNKNOWN applicability must still block.
Independent continuous/adjusted/index series must not be scheduled per contract.
The report must name record/session evidence still missing, not certify window
coverage. Verify the browser shows type, delivery and distinct applicability states.
An explicitly authorized Data Hub reset must pause via owned settings, stop all
Data Hub writers before schema/files deletion, preserve authentication/token/storage
identity, recreate btree_gist with the administrator and verify an empty baseline
before enabling collection. Verify oldest eligible lifecycle and new attempt times;
metadata discovery alone does not prove historical downloads have restarted.


For domain catalog changes, use test_contract_snapshots.py and the authenticated
standard-catalog drawer in check_browser.py. Remove private source files only in
a disposable fixture and verify the fixed read still works; corrupt a referenced
partition and require refusal. A new file in the same Hive partition must not
change an old snapshot. Backup must pin normalized Parquet and manifest bytes,
not rerun an encoder to reconstruct old hashes. Keep raw sources private, contract
admission separate from physical partitioning, and unknown time/fee/identity
semantics explicit. Renamed schema columns require the current baseline; do not
introduce a fallback reader. Any reset or empty-baseline transition must retain
workspace authentication and verify no collector resumed before deployment.

Configured storage may use a platform ancestor alias such as macOS /tmp. Resolve
the bound root once in PublishedDatasets, reject a symlink root itself, and continue
to reject links inside the catalog. Exercise this through the installed browser
fixture; pytest temporary paths may already be canonical and miss this failure.


For independent research series, run test_series_data.py and the installed `/series`
browser path. Verify actual response identities, same-product real mapping targets,
fixed interval history and exact archive restoration; no series may increment complete
contract publications. Test source retry without another supplier request. Catalog
metadata requests exclude settle_date; do not remove daily settlement prices or fee
terms along with that unrelated field. New owner tables require an additive baseline
transition under stopped Data Hub writers; preserve current downloads and account identity.

Tushare fut_index_daily lists ts_code as optional, but the live range request without
a code returned a parameter refusal. Use the documented Nanhua identities per request;
do not infer index symbols from commodity codes or classify this refusal as missing history.


For actual contract-record checks, run test_contract_review.py with PostgreSQL and
source files. Test a revised calendar against a previously validated response,
missing fee/margin values, conflicting receipts, and missing immutable files.
Storage/receipt corruption stays UNKNOWN rather than rejecting a contract and
triggering source cleanup. Week/month API requests must include the final period
label even when the real contract ends earlier; do not shorten a weekend endpoint
back to Friday and introduce gaps into the request union. Keep metadata expiry fixed.
SHFE pre-2013-07-05 and DCE pre-2014-07-01 daily/minute date comparison can detect an entirely missing
traded day, but cannot prove a complete intraday grid. Never promote that partial
check to VERIFIED. Supplier-declared history bounds are not proof that every
product is covered from that date; preserve listing history before the search floor.
When a reset is conditional on finishing repairs, unresolved positive admission
is not permission to wipe early. Report exact data/proof gaps and deployment state.

For native product weekly reports, run test_product_weekly_review.py. Historical
week identifiers mix five/six digits and can differ from ISO weeks. Use the planned
native-year envelopes and actual week_date; never reconstruct an absent date from
the identifier. Verify conflicting reports, missing weeks, missing dates, malformed
dates and wrong products independently. Day splitting of these envelopes repeats
the same request and must not create a self-linked retry. Supplier year envelopes
are collection bounds, not evidence that every expected report exists.

The current user-approved margin policy requires daily long/short settlement margin
rates, but permits a null ft_limit m_ratio. Preserve null values and bounded unknown
field diagnostics; never fill them from a different margin concept. Non-null invalid
m_ratio and missing daily price limits remain failures. Supplier summary history
floors are not hard absence evidence: live DCE settlement rows preceded 2012.
When native minute totals disagree with daily data, compare a bounded fresh raw
response before blaming normalization. Keep diagnostics separate from original
provenance and do not rescale volume or replace one native period with another.


## Project Tushare MCP diagnostics

The local project `.codex/config.toml` may configure `tushareMcp`; its credential
URL is private, mode 0600, and excluded through `.git/info/exclude`. Never print
or commit that configuration. Discover tools before claiming coverage: on
2026-09-15 the authenticated endpoint `/mcp/?token=...` returned 253 tools,
with 17 futures/calendar tools enabled locally. Initialize and list tools first,
then validate a bounded historical read. New project configuration is not proof
that an existing Codex session has loaded its tools.

MCP diagnostic results are fresh supplier observations, not original response
history or contract publication evidence. Data Hub collection continues through
its owned durable queue, quota control and retained source/receipt pipeline.
The observed MCP ft_mins description did not establish historical bar labels or
zero-volume rules. Do not treat another transport as evidence of complete data.


## Core admission and auxiliary quality

For whole-contract publication, check `requirements[].admission_role` and the pinned
`completeness`/`quality` report separately. All five native minute intervals remain core.
A received response, day-presence check or mocked minute verifier is not a verified
historical intraday grid. Test missing each core interval independently. Auxiliary
failures must not reject the contract or enter its published inputs. Subsequent auxiliary
completion must create a new snapshot without changing existing identities. Before
source cleanup, recheck core INVALID for every shared owner; stale REJECTED alone
is insufficient. Preserve referenced evidence and keep UNKNOWN distinct from INVALID.


For minute discrepancies, read `requirements[].diagnosis` before recommending another download.
Compare only owner-linked, file-verified receipts and deduplicate overlapping timestamps before
summing. Zero-volume bars are observations, not missing bars. Natural-date comparison is only
valid inside the explicitly supported historical no-night-session boundary. A different sum is
not supplier-missing proof and an equal sum is not grid completeness. Recheck a bounded example
against retained raw JSON before blaming normalization; keep all period/source identities and
never rescale values or delete evidence to reconcile the totals.


For calendar-driven requests, use test_request_calendar.py with PostgreSQL. An incomplete
exchange calendar must enqueue calendar work only; add closed days explicitly instead of
guessing weekdays. Verify previous-open-date minute envelopes across weekends/year edges,
real HTTP bounds in row validation, and split/merge edge preservation. Exchange calendars
select query dates, not historical intraday schedules. Do not declare minute grid completeness
from this change. Search-floor changes must also exclude already queued older contracts and
independent-series work while preserving immutable receipts and full eligible lifetimes.
