#!/usr/bin/env python3
"""Fixed control-bundle CLI for release transaction milestones.

This file is invoked only by ``gate_release.sh`` after the root release gate
has signature-verified and root-owned the control bundle.  It intentionally
contains no deployer-selected executable paths and never performs a recovery
action: it merely persists or reports an immutable transaction state.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ``-I`` intentionally removes the script directory from ``sys.path``.  The
# release gate has already resolved this exact file from a signature-verified,
# root-owned control tree, so add only that resolved sibling directory back;
# do not inherit CWD, PYTHONPATH, site packages, or deployment-user staging.
_CONTROL_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(_CONTROL_DIRECTORY))
from release_transaction import (  # noqa: E402
    ReleaseTransactionError,
    ReleaseTransactionState,
    ReleaseTransactionStore,
)


_DEFAULT_ROOT = Path("/var/lib/northstar/deploy-state/transactions")


def _store(root: Path) -> ReleaseTransactionStore:
    return ReleaseTransactionStore(root)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persist or inspect a Northstar release transaction.")
    parser.add_argument("--root", type=Path, default=_DEFAULT_ROOT)
    subcommands = parser.add_subparsers(dest="operation", required=True)
    begin = subcommands.add_parser("begin")
    begin.add_argument("release_id")
    begin.add_argument("request_sha256")
    begin.add_argument("artifact_sha256")
    begin.add_argument("--previous-release-id")
    transition = subcommands.add_parser("transition")
    transition.add_argument("release_id")
    transition.add_argument("state", choices=tuple(state.value for state in ReleaseTransactionState))
    inspect = subcommands.add_parser("inspect")
    inspect.add_argument("release_id")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        store = _store(args.root)
        if args.operation == "begin":
            transaction = store.begin(
                release_id=args.release_id,
                request_sha256=args.request_sha256,
                artifact_sha256=args.artifact_sha256,
                previous_release_id=args.previous_release_id,
            )
            print(
                json.dumps(
                    {
                        "release_id": transaction.release_id,
                        "state": transaction.state.value,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            return 0
        if args.operation == "transition":
            transaction = store.transition(args.release_id, ReleaseTransactionState(args.state))
            print(
                json.dumps(
                    {
                        "release_id": transaction.release_id,
                        "state": transaction.state.value,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            return 0
        decision = store.recovery_decision(args.release_id)
        print(
            json.dumps(
                {
                    "automatic_database_rollback_allowed": decision.automatic_database_rollback_allowed,
                    "automatic_service_resume_allowed": decision.automatic_service_resume_allowed,
                    "category": decision.category.value,
                    "last_state": None if decision.last_state is None else decision.last_state.value,
                    "release_id": decision.release_id,
                    "requires_operator_action": decision.requires_operator_action,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    except (OSError, ReleaseTransactionError, ValueError) as exc:
        # Root gate callers receive a non-secret error category only.
        print(f"release transaction operation failed: {type(exc).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
