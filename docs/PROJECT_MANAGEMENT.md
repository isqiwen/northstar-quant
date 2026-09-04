# GitHub Project management

[GitHub Project 1](https://github.com/users/isqiwen/projects/1) is the live
execution view for [the roadmap](ROADMAP.md). The roadmap explains why and in
what order; the Project records what is currently eligible and evidenced.

## Reset record

On 2026-09-04 the obsolete architecture plan was retired in full:

- 113 open planning issues across the nine domain repositories were closed with
  a supersession note. They remain searchable as historical evidence.
- 142 old Issue and pull-request cards are removed from Project 1; their source
  Issue/PR history is not deleted and can be re-added if an audit needs it.
- `docs/planning/MASTER_IMPLEMENTATION_PLAN.md` and the former operating model
  are deleted rather than carried into architecture R1.
- Project 1 is repopulated only with the 18 rolling-wave work packages in
  [the new roadmap](ROADMAP.md).

## Required fields

Every Project item must populate all of these fields. The issue body supplies
the textual fields that Project does not model directly.

| Field | Allowed values or required content |
| --- | --- |
| Status | `Backlog`, `Todo`, `In Progress`, `Review`, `Done` |
| Priority | `P0`, `P1`, `P2`, `P3` |
| Kind | `Feature`, `Bug`, `Task`, `Research` |
| Effort | `S`, `M`, `L`, `XL` |
| Target | `2026-Q3`, `2026-Q4`, `2027-Q1`, `Later` |
| Owner | Exactly one repository named by the issue location |
| Dependencies | Roadmap codes and links, or an explicit `None` |
| Exclusions | Authority deliberately outside the work package |
| Acceptance evidence | Observable artifacts, tests, compatibility fixtures, or merged PRs |

Title format is `[R1-<OWNER>-NN] <outcome>`. A code is immutable after
publication; replacement work receives a new code and a supersession link.

## Lifecycle and WIP

```text
Backlog --dependencies satisfied--> Todo --work starts--> In Progress
        --owner PR opened--> Review --merged evidence accepted--> Done
```

- `Backlog`: planned, but one or more dependencies or entry conditions are not
  satisfied.
- `Todo`: dependency-ready and eligible to start.
- `In Progress`: active implementation. Maximum three globally and one per
  repository.
- `Review`: an owner pull request exists and required checks or review remain.
- `Done`: the owner pull request is merged and every acceptance item is linked
  or recorded. Issue closure alone is insufficient.

If evidence regresses, move the item to the earliest truthful status. Never use
Project order, an assignee, or a target quarter as implicit execution authority.

## Synchronization rules

1. Architecture changes merge here before a dependent contract is published.
2. A roadmap change and its Project-field changes are one control-plane change;
   neither may silently disagree with the other.
3. The owner repository issue is the work-package record. Implementation and
   tests live in that repository; cross-repository evidence is linked from it.
4. Dependencies move to `Todo` only after the predecessor is `Done` and its
   published interface/evidence can be consumed from the default branch.
5. Move to `Review` when the owner PR exists. Move to `Done` only after checking
   merge state, required checks and each acceptance bullet.
6. Any scope expansion updates outcome, exclusions, dependencies and acceptance
   before implementation starts. A changed authority boundary requires a new
   architecture revision.
7. Use UTC dates in issues and evidence; never infer freshness from Project
   update time.

## Periodic audit

At each planning pass, verify:

- every Project card belongs to the current architecture revision;
- all five custom fields are populated and match the roadmap;
- dependencies are acyclic and statuses reflect actual predecessor evidence;
- global and per-repository WIP limits hold;
- every `Review` card links an open PR and every `Done` card links a merged PR;
- no archived plan identifier, implicit `latest`, or real-money authority has
  re-entered the active plan.
