"""Research-owned immutable reports and public data-use receipts on its writable share."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Engine, String, literal, select, union_all
from sqlalchemy import cast as sql_cast

from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.storage_identity import require_identity


class ResearchUsages:
    """Owner-side query; no Data module reads Research tables."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def list(self, snapshots: Sequence[UUID]) -> list[dict[str, object]]:
        from .experiments import _plans
        from .paper import _sessions
        from .runs import _runs
        from .tasks.store import jobs

        query = union_all(
            select(
                literal("RESEARCH").label("kind"),
                _runs.c.run_id.label("use_id"),
                _runs.c.snapshot_id,
                _runs.c.created_at,
            ).where(_runs.c.snapshot_id.in_(snapshots)),
            select(
                literal("PAPER"),
                sql_cast(_sessions.c.session_id, String),
                _sessions.c.snapshot_id,
                _sessions.c.created_at,
            ).where(_sessions.c.snapshot_id.in_(snapshots)),
        )
        with self.engine.connect() as connection:
            rows: list[Any] = list(
                connection.execute(query.order_by("created_at").limit(200)).mappings().all()
            )
            rows.extend(
                connection.execute(
                    select(
                        literal("RESEARCH_TASK").label("kind"),
                        jobs.c.task_id.label("use_id"),
                        jobs.c.snapshot_id,
                        jobs.c.created_at,
                    )
                    .where(jobs.c.snapshot_id.in_([str(s) for s in snapshots]))
                    .limit(200)
                )
                .mappings()
                .all()
            )
            wanted = {str(s) for s in snapshots}
            for plan in connection.execute(select(_plans)).mappings():
                for phase, window in plan["plan"]["windows"].items():
                    if window["snapshot_id"] in wanted:
                        rows.append(
                            {
                                "kind": "RESEARCH_EXPERIMENT",
                                "use_id": f"{plan['experiment_id']}:{phase}",
                                "snapshot_id": window["snapshot_id"],
                                "created_at": plan["created_at"],
                            }
                        )
        return [
            {
                k: str(v) if k in {"use_id", "snapshot_id", "created_at"} else v
                for k, v in row.items()
            }
            for row in rows
        ]


class ResearchArtifacts:
    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def from_environment(cls) -> ResearchArtifacts:
        root = Path(os.environ["NORTHSTAR_RESEARCH_DIR"])
        require_identity(root, os.environ["NORTHSTAR_RESEARCH_STORAGE_ID"])
        if (root / ".restore-incomplete").exists():
            raise ValueError("restore is incomplete; refuse application startup")
        return cls(root)

    def save(self, kind: str, identity: str, payload: Mapping[str, object]) -> str:
        content = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
        digest = hashlib.sha256(content).hexdigest()
        # User-visible identifiers never become a filesystem path.
        key = hashlib.sha256(f"{kind}:{identity}".encode()).hexdigest()
        path = self.root / f"{kind.lower()}-{key}.json"
        envelope = json.dumps(
            {"kind": kind, "identity": identity, "sha256": digest, "payload": content.decode()},
            ensure_ascii=False,
        ).encode()
        if path.exists():
            if self._read(path)["sha256"] != digest:
                raise ValueError("research artifact identity conflict")
            return digest
        descriptor, temporary = tempfile.mkstemp(prefix=".research-", dir=self.root)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(envelope)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if self._read(path)["sha256"] != digest:
                    raise ValueError("research artifact conflict") from None
            SourceFiles._sync(self.root)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return digest

    def _read(self, path: Path) -> dict[str, object]:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            import stat

            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("research artifact is not a regular file")
            content = stream.read(64 * 1024 * 1024 + 1)
        if len(content) > 64 * 1024 * 1024:
            raise ValueError("research artifact exceeds the bounded size")
        record = json.loads(content)
        if hashlib.sha256(record["payload"].encode()).hexdigest() != record["sha256"]:
            raise ValueError("research artifact checksum mismatch")
        return cast(dict[str, object], record)

    def verify_backtest(self, identity: str) -> None:
        key = hashlib.sha256(f"BACKTEST:{identity}".encode()).hexdigest()
        record = self._read(self.root / f"backtest-{key}.json")
        if (
            record["sha256"] != identity
            or record["identity"] != identity
            or record["kind"] != "BACKTEST"
        ):
            raise ValueError("backtest report does not match its persisted content identity")

    def usages(self, snapshots: Sequence[UUID]) -> list[dict[str, object]]:
        identities = {str(value) for value in snapshots}
        values = []
        for path in self.root.glob("usage-*.json"):
            record = self._read(path)
            if record["kind"] == "USAGE":
                payload = json.loads(str(record["payload"]))
                if payload["snapshot_id"] in identities:
                    values.append(payload)
        return sorted(values, key=lambda value: value["created_at"], reverse=True)[:200]


def publish_usage(engine: Engine, snapshot: UUID) -> None:
    if not os.environ.get("NORTHSTAR_RESEARCH_DIR"):
        return
    artifacts = ResearchArtifacts.from_environment()
    for receipt in ResearchUsages(engine).list([snapshot]):
        artifacts.save("USAGE", f"{receipt['kind']}:{receipt['use_id']}", receipt)
