---
name: northstar-issue-delivery
description: Deliver an authorized Northstar Issue or continue its Project work, reconciling existing evidence with the next vertical implementation. Not for general questions or read-only reviews.
---

# Northstar Issue delivery

Resolve paths from this Git repository's root. `AGENTS.md` owns engineering and
authorization rules; this workflow adds Issue-specific delivery mechanics.

Read `docs/ROADMAP.md`, the selected Issue's body, delivery comments and native
Blocked by links in `isqiwen/northstar-quant`, then its item in
https://github.com/users/isqiwen/projects/1. A supplied Issue number does not prove
it is open or unimplemented. For a closed baseline, preserve its evidence and
identify the requested regression instead of reopening it by default.

Compare acceptance claims with current code and commit. Distinguish an application
entrypoint from a durable worker, HTTP checks from browser acceptance, and saved
broker evidence from a new external run. Select one user-observable unfinished
result; separate missing external inputs from implementable local work. Order
ranks candidates; only native dependencies express hard prerequisites.

For changed behavior, consult the relevant section of `docs/ARCHITECTURE.md` and
trace a real caller through its owning Module to the persisted result. Change that
path and affected callers, not a parallel implementation. Use `CONTEXT.md` to
resolve ambiguous business terms before changing their meaning. New directories,
types or documentation do not prove the associated application capability exists.

Choose verification from existing checks. For installed-app or process-lifecycle
acceptance read the adjacent `../northstar-runtime-verification/SKILL.md`.
A missing dependency is not permission to connect a broker, provision a host or
operate a personal deployment.

Follow `AGENTS.md` → "Asynchronous CI follow-up": continue independent authorized
work while CI runs; do not block on every push. At task boundaries or on resumption,
check the pending run for its exact SHA. Record delivered
behavior, reproducible checks, data/evidence category and remaining acceptance
gaps in the Issue; align Project status. If ending before CI completes, leave its
SHA/run URL and pending status for the next turn. Partial or unverified delivery
stays open; acceptance requires the delivered SHA's checks to pass. A new
version's CLI check does not replace a required browser or broker acceptance.

If a GitHub write times out, read back before retrying comments or creating Issues.
Keep local completion, successful push, CI and external acceptance distinct when
network failure prevents verification. Stop retries when they no longer yield
progress and report the precise pending operation, not assumed remote success.


## Finish delivery without a manual user merge

Apply `AGENTS.md` → "Automatic PR delivery". On resumption inspect outstanding
PR/check state. For a complete PR, resolve failures, mark ready, merge the exact
validated head, and verify the target branch; use verified GitHub auto-merge if
only checks remain and the repository supports it. Do not ask the user to merge.
Incomplete PR scope stays draft with concrete remaining work. Do not bypass
checks, review, protection or external acceptance, and do not mark incomplete
Issues Done merely because their partial implementation merged.
A final reply must report merged commit, actually enabled auto-merge, or the
specific blocker and next action; a promise alone is not delivery or monitoring.
