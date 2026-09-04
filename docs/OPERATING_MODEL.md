# Operating model

## Sources of truth

| Question | Authority |
| --- | --- |
| What may be worked on now? | [Master implementation plan](planning/MASTER_IMPLEMENTATION_PLAN.md) and its authorized GitHub Project item. |
| Who implements it? | [Repository map](REPOSITORY_MAP.md). |
| What source and tests prove it? | The owning repository and its linked issue/PR. |
| Is it delivered? | The owner PR is merged, required checks have passed, and the Project item is `Done`. |

## Work-package flow

1. The control plane records a bounded goal, owner, dependencies, exclusions,
   acceptance criteria, and Project item.
2. The owner repository implements the work on its own branch and validates it
   with its own documented checks.
3. The owner PR links the issue and reports the exact verification evidence.
4. Once merged, verify the merge commit and project status before updating the
   control plan. The control plane never treats an open PR as delivered.
5. Clear or advance the active work package only after all stated acceptance
   criteria are evidenced.

## Status semantics

- `TODO`: authorized but not started.
- `IN_PROGRESS`: the owner is actively implementing the work.
- `VERIFY`: implementation is available but still needs a stated external or
  merged-evidence check.
- `BLOCKED`: an external prerequisite prevents safe progress.
- `DONE`: owner evidence is merged and the Project item agrees.

For work that affects trading, data authorization, production access, or live
execution, unknown state is blocking. This repository records the prerequisite;
it cannot grant the missing authority.

## Control-plane change policy

Changes here are documentation-only and are reviewed as operational decisions:

- preserve explicit owner, dependency, and acceptance language;
- link to the concrete Project issue and merged PR rather than describing work
  as complete from memory;
- avoid duplicating owner-repository implementation detail;
- validate Markdown links and `git diff --check` before opening the control-plane PR.
