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
Search last trading dates from 2012-01-01 ascending, preserving pre-2012 listing history.
Exercise both planning and claiming against an existing newer queue; changing only SQL planning
order leaves already queued downloads ahead of historical candidates. Unknown lifecycle metadata
must also block rejected-source cleanup, not just new planning.
Use the browser's “整体验收” action on downloaded contract data. Check a complete
calendar through the candidate cutoff; an old maximum open day is insufficient.
A response/window count, nonempty chart or successful HTTP request is not whole
contract acceptance. The current report explicitly records unresolved minute
session/label and native dataset applicability evidence; it cannot authorize
source deletion. Test with `backend/tests/data_management/test_contract_review.py`
against the disposable PostgreSQL database. Preserve old fixed receipts when
changing response quality rules; rejection of new mixed-quality responses must
not publish their remaining good rows.

## Complete retired-contract catalog

Data Hub discovery and Research handoff require a contract package membership.
A validated supplier response alone is private processing evidence. Installed
browser fixtures may explicitly pin synthetic package facts to exercise readers;
never interpret those facts as supplier lifecycle acceptance. Verify package hash
and deterministic reconstruction in joint backup/restore. Real admission must
show verified listing/last-trading/last-delivery dates and all supported dataset completeness;
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
