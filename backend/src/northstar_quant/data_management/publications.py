"""Data-owned immutable values published for consumers without database credentials."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pydantic import TypeAdapter

from .files import SourceFiles
from .research import DatasetDetails, DatasetSummary, ResearchDataset
from .storage_identity import require_identity


class DatasetReader(Protocol):
    def load_dataset(self, snapshot_id: UUID) -> ResearchDataset: ...
    def describe_dataset(self, snapshot_id: UUID) -> DatasetDetails: ...
    def list_datasets(self, *, limit: int = 50) -> tuple[DatasetSummary, ...]: ...
    def lineage(self, snapshot_id: UUID) -> dict[str, object]: ...


_dataset = TypeAdapter(ResearchDataset)


class PublishedDatasets:
    """An explicitly mounted, read-only publication interface; no SQL or source writes."""

    def __init__(
        self,
        root: Path,
        *,
        usages: Callable[[Sequence[UUID]], list[dict[str, object]]] | None = None,
    ) -> None:
        self.root = root
        self._usages = usages

    @classmethod
    def from_environment(
        cls, *, usages: Callable[[Sequence[UUID]], list[dict[str, object]]] | None = None
    ) -> PublishedDatasets:
        root = Path(os.environ["NORTHSTAR_MARKET_DIR"])
        require_identity(root, os.environ["NORTHSTAR_MARKET_STORAGE_ID"])
        if (root / ".restore-incomplete").exists():
            raise ValueError("restore is incomplete; refuse application startup")
        return cls(root, usages=usages)

    def publish(self, dataset: ResearchDataset) -> None:
        if dataset.details is None:
            raise ValueError("publication requires verified source and quality evidence")
        payload = _dataset.dump_json(dataset)
        envelope = json.dumps(
            {"sha256": hashlib.sha256(payload).hexdigest(), "payload": payload.decode()},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{dataset.snapshot_id}.json"
        if path.exists():
            if self.load_dataset(dataset.snapshot_id) != dataset:
                raise ValueError("published snapshot identity conflicts with retained content")
            return
        import tempfile

        descriptor, temporary = tempfile.mkstemp(prefix=".publication-", dir=self.root)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(envelope)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if self.load_dataset(dataset.snapshot_id) != dataset:
                    raise ValueError("conflicting concurrent publication") from None
            SourceFiles._sync(self.root)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def load_dataset(self, snapshot_id: UUID) -> ResearchDataset:
        path = self.root / f"{UUID(str(snapshot_id))}.json"
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                import stat

                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("publication must be a regular file")
                content = stream.read(32 * 1024 * 1024 + 1)
            if len(content) > 32 * 1024 * 1024:
                raise ValueError("publication exceeds the bounded input size")
            record = json.loads(content)
            payload = record["payload"].encode()
            if hashlib.sha256(payload).hexdigest() != record["sha256"]:
                raise ValueError("publication checksum mismatch")
            dataset = _dataset.validate_json(payload)
            if dataset.snapshot_id != snapshot_id or dataset.details is None:
                raise ValueError("publication snapshot identity or evidence mismatch")
            if dataset.details.summary.content_hash != dataset.content_hash:
                raise ValueError("publication data identity mismatch")
            return dataset
        except FileNotFoundError as error:
            raise LookupError("fixed publication not found") from error
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("invalid publication envelope") from error

    def describe_dataset(self, snapshot_id: UUID) -> DatasetDetails:
        details = self.load_dataset(snapshot_id).details
        assert details is not None
        return details

    def list_datasets(self, *, limit: int = 50) -> tuple[DatasetSummary, ...]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        values = []
        for path in self.root.glob("*.json"):
            try:
                values.append(self.describe_dataset(UUID(path.stem)).summary)
            except (ValueError, LookupError):
                continue
        return tuple(sorted(values, key=lambda value: value.published_at, reverse=True)[:limit])

    def lineage(self, snapshot_id: UUID) -> dict[str, object]:
        details = self.describe_dataset(snapshot_id)
        return {
            "snapshot_id": str(snapshot_id),
            "sources": [s.to_dict() for s in details.sources],
            "attempts": [],
            "usages": [] if self._usages is None else self._usages([snapshot_id]),
        }
