"""Resolve a fixed Data Hub manifest, then compute against the local read-only mount."""

import hashlib
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import UUID

import httpx2

from northstar_quant.web.protobuf import decode, methods

from .publications import PublishedDatasets
from .research import DatasetDetails, DatasetSummary, ResearchDataset


class PublicationClient:
    def __init__(
        self,
        url: str,
        root: Path,
        storage_id: str,
        *,
        usages: Callable[[Sequence[UUID]], list[dict[str, object]]] | None = None,
        transport: Any = None,
    ) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("invalid Data Hub publication URL")
        self.url, self.storage_id = url.rstrip("/"), storage_id
        self.reader = PublishedDatasets(root, usages=usages)
        self.transport = transport
        self._manifests: dict[UUID, dict[str, Any]] = {}

    @classmethod
    def from_environment(
        cls, *, usages: Callable[[Sequence[UUID]], list[dict[str, object]]] | None = None
    ) -> "PublicationClient":
        reader = PublishedDatasets.from_environment()
        return cls(
            os.environ["NORTHSTAR_DATA_HUB_URL"],
            reader.root,
            os.environ["NORTHSTAR_MARKET_STORAGE_ID"],
            usages=usages,
        )

    def _get(self, path: str, template: str) -> dict[str, Any]:
        try:
            with httpx2.Client(
                timeout=10, follow_redirects=False, trust_env=False, transport=self.transport
            ) as client:
                with client.stream("GET", self.url + path) as response:
                    if response.status_code != 200:
                        raise ValueError(
                            f"Data Hub publication unavailable (HTTP {response.status_code})"
                        )
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > 1024 * 1024:
                            raise ValueError("publication response exceeds limit")
                    descriptor = methods("data_hub")[("GET", template)].output_type
                    return cast(dict[str, Any], decode(descriptor, bytes(content)))
        except httpx2.HTTPError:
            raise ValueError("Data Hub publication service unavailable") from None

    def load_dataset(self, snapshot_id: UUID) -> ResearchDataset:
        manifest = self._manifests.get(snapshot_id)
        if manifest is None:
            manifest = self._get(
                f"/api/publications/{snapshot_id}", "/api/publications/{snapshot_id}"
            )
        if (
            manifest["snapshot_id"] != str(snapshot_id)
            or manifest["storage_id"] != self.storage_id
            or manifest["path"] != f"{snapshot_id}.json"
        ):
            raise ValueError("publication identity or storage mismatch")
        path = self.reader.root / manifest["path"]
        if path.is_symlink() or not path.is_file():
            raise ValueError("published market manifest unavailable")
        if (
            not 0 < manifest["bytes"] <= 32 * 1024 * 1024
            or path.stat().st_size != manifest["bytes"]
        ):
            raise ValueError("publication manifest size mismatch")
        content = path.read_bytes()
        if (
            len(content) != manifest["bytes"]
            or hashlib.sha256(content).hexdigest() != manifest["sha256"]
        ):
            raise ValueError("publication manifest checksum mismatch")
        dataset = self.reader.load_dataset(snapshot_id)
        self._manifests[snapshot_id] = manifest
        return dataset

    def describe_dataset(self, snapshot_id: UUID) -> DatasetDetails:
        details = self.load_dataset(snapshot_id).details
        assert details is not None
        return details

    def list_datasets(self, *, limit: int = 50) -> tuple[DatasetSummary, ...]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        catalog = self._get(f"/api/publications?limit={limit}", "/api/publications")
        return tuple(self.describe_dataset(UUID(item)).summary for item in catalog["snapshots"])

    def lineage(self, snapshot_id: UUID) -> dict[str, object]:
        self.load_dataset(snapshot_id)
        return self.reader.lineage(snapshot_id)
