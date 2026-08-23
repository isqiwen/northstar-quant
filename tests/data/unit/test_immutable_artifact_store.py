"""P1-WP02 追加式不可变制品库的核心验收。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
from threading import Barrier
from types import SimpleNamespace

import pytest

from northstar_quant.data.artifacts.fingerprints import (
    content_sha256,
    derived_identity_hash,
    lineage_hash,
    normalization_identity_hash,
)
from northstar_quant.data.artifacts.immutable_store import (
    ArtifactIntegrityConflict,
    ArtifactNotFoundError,
    ArtifactStore,
    ArtifactStoreError,
)
from northstar_quant.data.contracts.data_domain import (
    ArtifactMetadata,
    ArtifactProvenance,
    ArtifactSnapshot,
    DataLineage,
    DataSource,
    DatasetVersion,
    DerivedArtifact,
    LicenseMetadata,
    NormalizedArtifact,
    QualityStatus,
    RawArtifact,
)


UTC = timezone.utc
BASE_TIME = datetime(2026, 1, 6, 8, tzinfo=UTC)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source(
    *,
    source_id: str = "licensed-source",
    internal_storage: bool = True,
    derived_storage: bool = True,
    config_marker: str = "v1",
) -> DataSource:
    return DataSource(
        source_id=source_id,
        adapter_id="synthetic-adapter",
        name="Synthetic Licensed Source",
        tier="test",
        status="active",
        config_sha256=_hash(f"source-config-{config_marker}"),
        official_references=("https://example.test/catalog",),
        license=LicenseMetadata(
            status="active",
            contract_reference="test-contract-v1",
            effective_from="2026-01-01",
            expires_on="2026-12-31",
            terms_sha256=_hash("test-license"),
            permitted_purposes=("internal_research",),
            allows_internal_storage=internal_storage,
            allows_derived_data_storage=derived_storage,
            allows_live_trading=False,
        ),
    )


def _provenance(source_id: str = "licensed-source") -> ArtifactProvenance:
    return ArtifactProvenance(
        source_id=source_id,
        source_reference="synthetic-fixture-20260106",
        collection_method="fixture-import",
        attributes=(("batch", "p1-wp02"),),
    )


def _metadata(
    *,
    artifact_id: str,
    payload: bytes,
    source_id: str = "licensed-source",
    acquired_at: datetime,
    available_at: datetime,
    schema_version: str,
    transform_version: str,
    quality_status: QualityStatus = QualityStatus.PASS,
) -> ArtifactMetadata:
    return ArtifactMetadata(
        artifact_id=artifact_id,
        source_id=source_id,
        acquired_at=acquired_at,
        available_at=available_at,
        schema_version=schema_version,
        content_hash=content_sha256(payload),
        transform_version=transform_version,
        quality_status=quality_status,
        provenance=_provenance(source_id),
    )


def _raw(
    payload: bytes = b"raw-payload",
    *,
    artifact_id: str = "raw-a",
    available_at: datetime = BASE_TIME + timedelta(hours=2),
    quality_status: QualityStatus = QualityStatus.PASS,
) -> RawArtifact:
    return RawArtifact(
        metadata=_metadata(
            artifact_id=artifact_id,
            payload=payload,
            acquired_at=BASE_TIME + timedelta(hours=1),
            available_at=available_at,
            schema_version="raw.v1",
            transform_version="capture.v1",
            quality_status=quality_status,
        ),
        raw_format="application/octet-stream",
    )


def _normalized(
    raw: RawArtifact,
    payload: bytes = b"normalized-payload",
    *,
    artifact_id: str = "normalized-a",
) -> NormalizedArtifact:
    transform_version = "normalize.v1"
    schema_version = "market.v1"
    return NormalizedArtifact(
        metadata=_metadata(
            artifact_id=artifact_id,
            payload=payload,
            acquired_at=raw.acquired_at + timedelta(hours=2),
            available_at=raw.available_at + timedelta(hours=2),
            schema_version=schema_version,
            transform_version=transform_version,
        ),
        raw_artifact=raw,
        normalization_identity=normalization_identity_hash(
            raw.content_hash,
            content_sha256(payload),
            transform_version,
            schema_version,
        ),
    )


def _derived(
    normalized: NormalizedArtifact,
    payload: bytes = b"derived-payload",
    *,
    artifact_id: str = "derived-a",
) -> DerivedArtifact:
    transform_version = "feature.v1"
    schema_version = "feature.v1"
    return DerivedArtifact(
        metadata=_metadata(
            artifact_id=artifact_id,
            payload=payload,
            acquired_at=normalized.acquired_at + timedelta(hours=2),
            available_at=normalized.available_at + timedelta(hours=2),
            schema_version=schema_version,
            transform_version=transform_version,
        ),
        input_artifacts=(normalized,),
        derivation_identity=derived_identity_hash(
            (normalized.content_hash,),
            transform_version,
            schema_version,
        ),
    )


def _lineage(output: NormalizedArtifact | DerivedArtifact) -> DataLineage:
    if isinstance(output, NormalizedArtifact):
        inputs = (output.raw_artifact,)
    else:
        inputs = output.input_artifacts
    return DataLineage(
        output_artifact=output,
        input_artifacts=inputs,
        transform_version=output.transform_version,
        lineage_identity=lineage_hash(
            output.content_hash,
            (item.content_hash for item in inputs),
            output.transform_version,
        ),
        recorded_at=output.available_at + timedelta(hours=1),
    )


def test_store_deduplicates_blob_but_preserves_raw_normalized_derived_snapshots_and_replay(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    source = _source()
    raw_payload = b"raw-payload"
    normalized_payload = b"normalized-payload"
    derived_payload = b"derived-payload"
    raw = _raw(raw_payload)
    normalized = _normalized(raw, normalized_payload)
    derived = _derived(normalized, derived_payload)

    raw_ref = store.put_raw(source=source, artifact=raw, payload=raw_payload)
    repeated_raw_ref = store.put_raw(source=source, artifact=raw, payload=raw_payload)
    normalized_ref = store.put_normalized(
        source=source,
        artifact=normalized,
        payload=normalized_payload,
        lineage=_lineage(normalized),
    )
    derived_ref = store.put_derived(
        source=source,
        artifact=derived,
        payload=derived_payload,
        lineage=_lineage(derived),
    )
    dataset = DatasetVersion.from_artifacts(
        dataset_id="synthetic-feature-input",
        artifacts=(normalized, derived),
        schema_version="feature.v1",
        transform_version="dataset.v1",
    )
    stored_dataset = store.put_dataset_version(dataset)
    replay = store.replay_dataset_version(dataset.version_hash)

    assert raw_ref == repeated_raw_ref
    assert raw_ref.blob_path.name == f"{raw.content_hash}.blob"
    assert normalized_ref.lineage_snapshot_hash is not None
    assert derived_ref.lineage_snapshot_hash is not None
    assert stored_dataset.manifest_path.is_file()
    assert replay.dataset_version == dataset
    assert {item.payload for item in replay.artifacts} == {normalized_payload, derived_payload}
    assert len(list((store.root / "blobs" / "sha256").rglob("*.blob"))) == 3


def test_store_from_settings_uses_only_storage_artifacts_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_dir = tmp_path / "storage"
    monkeypatch.setattr(
        "northstar_quant.foundation.config.settings.get_settings",
        lambda: SimpleNamespace(storage_dir=storage_dir),
    )

    store = ArtifactStore.from_settings()

    assert store.root == storage_dir / "artifacts"


def test_same_blob_with_different_pit_snapshots_keeps_one_blob_and_two_records(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    source = _source()
    payload = b"same-bytes"
    original = _raw(payload, artifact_id="raw-original")
    revision = _raw(
        payload,
        artifact_id="raw-revision",
        available_at=BASE_TIME + timedelta(hours=3),
    )

    original_ref = store.put_raw(source=source, artifact=original, payload=payload)
    revision_ref = store.put_raw(source=source, artifact=revision, payload=payload)

    assert original_ref.blob_path == revision_ref.blob_path
    assert original_ref.snapshot.snapshot_hash != revision_ref.snapshot.snapshot_hash
    assert len(list((store.root / "blobs" / "sha256").rglob("*.blob"))) == 1
    assert len(list((store.root / "snapshots" / "sha256").rglob("*.json"))) == 2

    original_dataset = DatasetVersion.from_artifacts(
        dataset_id="same-content-revisions",
        artifacts=(original,),
        schema_version="raw.v1",
        transform_version="dataset.v1",
    )
    revision_dataset = DatasetVersion.from_artifacts(
        dataset_id="same-content-revisions",
        artifacts=(revision,),
        schema_version="raw.v1",
        transform_version="dataset.v1",
    )
    store.put_dataset_version(original_dataset)
    store.put_dataset_version(revision_dataset)

    assert original_dataset.version_hash != revision_dataset.version_hash
    assert (
        store.replay_dataset_version(original_dataset.version_hash).dataset_version
        == original_dataset
    )
    assert (
        store.replay_dataset_version(revision_dataset.version_hash).dataset_version
        == revision_dataset
    )


def test_normalization_binding_rejects_different_output_for_same_raw_snapshot_and_transform(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    source = _source()
    raw_payload = b"raw-payload"
    raw = _raw(raw_payload)
    store.put_raw(source=source, artifact=raw, payload=raw_payload)
    first = _normalized(raw, b"normalized-one", artifact_id="normalized-one")
    second = _normalized(raw, b"normalized-two", artifact_id="normalized-two")
    store.put_normalized(
        source=source,
        artifact=first,
        payload=b"normalized-one",
        lineage=_lineage(first),
    )

    with pytest.raises(ArtifactIntegrityConflict, match="绑定不同 normalized 内容"):
        store.put_normalized(
            source=source,
            artifact=second,
            payload=b"normalized-two",
            lineage=_lineage(second),
        )


def test_concurrent_conflicting_normalization_publishes_only_one_completed_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "artifacts"
    store = ArtifactStore(root)
    source = _source()
    raw = _raw(b"raw-payload")
    store.put_raw(source=source, artifact=raw, payload=b"raw-payload")
    first = _normalized(raw, b"normalized-one", artifact_id="normalized-one")
    second = _normalized(raw, b"normalized-two", artifact_id="normalized-two")

    original_precheck = ArtifactStore._assert_normalization_binding_is_compatible
    barrier = Barrier(2)

    def synchronized_precheck(
        checked_store: ArtifactStore,
        artifact: NormalizedArtifact,
        snapshot: ArtifactSnapshot,
    ) -> None:
        original_precheck(checked_store, artifact, snapshot)
        barrier.wait(timeout=5)

    monkeypatch.setattr(
        ArtifactStore,
        "_assert_normalization_binding_is_compatible",
        synchronized_precheck,
    )

    def publish(artifact: NormalizedArtifact, payload: bytes) -> str:
        try:
            ArtifactStore(root).put_normalized(
                source=source,
                artifact=artifact,
                payload=payload,
                lineage=_lineage(artifact),
            )
        except ArtifactIntegrityConflict:
            return "conflict"
        return "published"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(
                lambda values: publish(*values),
                ((first, b"normalized-one"), (second, b"normalized-two")),
            )
        )

    assert sorted(results) == ["conflict", "published"]
    # raw record + 唯一成功的 normalized record；失败写方最多留下不可见 blob，不能留下 record。
    assert len(list((root / "snapshots" / "sha256").rglob("*.json"))) == 2


def test_normalized_parent_must_already_be_saved_and_lineage_must_match(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    source = _source()
    raw = _raw()
    normalized = _normalized(raw)

    with pytest.raises(ArtifactNotFoundError):
        store.put_normalized(
            source=source,
            artifact=normalized,
            payload=b"normalized-payload",
            lineage=_lineage(normalized),
        )


def test_replay_recursively_verifies_parent_blob_integrity(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    source = _source()
    raw_payload = b"raw-payload"
    normalized_payload = b"normalized-payload"
    raw = _raw(raw_payload)
    normalized = _normalized(raw, normalized_payload)

    raw_ref = store.put_raw(source=source, artifact=raw, payload=raw_payload)
    store.put_normalized(
        source=source,
        artifact=normalized,
        payload=normalized_payload,
        lineage=_lineage(normalized),
    )
    dataset = DatasetVersion.from_artifacts(
        dataset_id="recursive-integrity",
        artifacts=(normalized,),
        schema_version="market.v1",
        transform_version="dataset.v1",
    )
    store.put_dataset_version(dataset)

    raw_ref.blob_path.write_bytes(b"x" * len(raw_payload))

    with pytest.raises(ArtifactIntegrityConflict, match="内容哈希"):
        store.replay_dataset_version(dataset.version_hash)


def test_tampered_parent_cycle_is_rejected_during_replay(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    source = _source()
    raw = _raw(b"raw-payload")
    normalized = _normalized(raw, b"normalized-payload")
    store.put_raw(source=source, artifact=raw, payload=b"raw-payload")
    normalized_ref = store.put_normalized(
        source=source,
        artifact=normalized,
        payload=b"normalized-payload",
        lineage=_lineage(normalized),
    )

    record = json.loads(normalized_ref.record_path.read_text(encoding="utf-8"))
    record["relations"]["raw_snapshot_hash"] = normalized_ref.snapshot.snapshot_hash
    normalized_ref.record_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ArtifactIntegrityConflict, match="循环"):
        store.load_artifact(normalized_ref.snapshot.snapshot_hash)


def test_tampered_dataset_manifest_record_hash_is_rejected(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    source = _source()
    raw = _raw(b"raw-payload")
    store.put_raw(source=source, artifact=raw, payload=b"raw-payload")
    dataset = DatasetVersion.from_artifacts(
        dataset_id="manifest-integrity",
        artifacts=(raw,),
        schema_version="raw.v1",
        transform_version="dataset.v1",
    )
    stored = store.put_dataset_version(dataset)

    manifest = json.loads(stored.manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_records"][0]["record_sha256"] = "0" * 64
    stored.manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ArtifactIntegrityConflict, match="record 哈希"):
        store.load_dataset_version(dataset.version_hash)


def test_source_authorization_and_dataset_quality_fail_closed(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    raw_payload = b"raw-payload"
    raw = _raw(raw_payload)
    with pytest.raises(ArtifactStoreError, match="不允许内部保存"):
        store.put_raw(
            source=_source(internal_storage=False),
            artifact=raw,
            payload=raw_payload,
        )

    active_source = _source()
    expired_source = replace(
        active_source,
        license=replace(active_source.license, expires_on="2026-01-05"),
    )
    with pytest.raises(ArtifactStoreError, match="不在数据源授权有效期"):
        store.put_raw(source=expired_source, artifact=raw, payload=raw_payload)

    fail_raw = _raw(b"failed-payload", artifact_id="raw-fail", quality_status=QualityStatus.FAIL)
    source = _source()
    store.put_raw(source=source, artifact=fail_raw, payload=b"failed-payload")
    failing_dataset = DatasetVersion.from_artifacts(
        dataset_id="failed-input",
        artifacts=(fail_raw,),
        schema_version="raw.v1",
        transform_version="dataset.v1",
    )
    with pytest.raises(ArtifactStoreError, match="不能发布"):
        store.put_dataset_version(failing_dataset)


def test_same_snapshot_rejects_changed_frozen_source_configuration(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    payload = b"raw-payload"
    raw = _raw(payload)

    store.put_raw(source=_source(config_marker="v1"), artifact=raw, payload=payload)

    with pytest.raises(ArtifactIntegrityConflict, match="artifact record 已存在但内容不一致"):
        store.put_raw(source=_source(config_marker="v2"), artifact=raw, payload=payload)


def test_corrupted_or_linked_permanent_object_is_never_overwritten_or_replayed(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    source = _source()
    payload = b"raw-payload"
    raw = _raw(payload)
    stored = store.put_raw(source=source, artifact=raw, payload=payload)
    stored.record_path.write_bytes(b'{"format":"corrupted"}\n')

    with pytest.raises(ArtifactIntegrityConflict):
        store.load_artifact(stored.snapshot.snapshot_hash)
    with pytest.raises(ArtifactIntegrityConflict, match="已存在但内容不一致"):
        store.put_raw(source=source, artifact=raw, payload=payload)

    linked_store = ArtifactStore(tmp_path / "linked-artifacts")
    outside = tmp_path / "outside-blob"
    outside.write_bytes(b"outside-content")
    linked_path = linked_store.blob_path(raw.content_hash)
    linked_path.parent.mkdir(parents=True)
    linked_path.parent.chmod(0o700)
    try:
        linked_path.symlink_to(outside)
    except OSError:
        pytest.skip("当前平台不允许创建符号链接")
    with pytest.raises(ArtifactStoreError, match="普通文件"):
        linked_store.put_raw(source=source, artifact=raw, payload=payload)
    assert outside.read_bytes() == b"outside-content"


def test_store_rejects_symbolic_link_root_and_hash_shard(tmp_path: Path) -> None:
    real_root = tmp_path / "real-artifacts"
    ArtifactStore(real_root)
    linked_root = tmp_path / "linked-artifacts"
    try:
        linked_root.symlink_to(real_root, target_is_directory=True)
    except OSError:
        pytest.skip("当前运行环境不允许创建符号链接")
    with pytest.raises(ArtifactStoreError, match="符号链接"):
        ArtifactStore(linked_root)

    store = ArtifactStore(tmp_path / "shard-artifacts")
    source = _source()
    raw = _raw(b"raw-payload")
    external_shard = tmp_path / "external-shard"
    external_shard.mkdir(mode=0o700)
    shard = store.blob_path(raw.content_hash).parent
    try:
        shard.symlink_to(external_shard, target_is_directory=True)
    except OSError:
        pytest.skip("当前运行环境不允许创建符号链接")

    with pytest.raises(ArtifactStoreError, match="制品库目录"):
        store.put_raw(source=source, artifact=raw, payload=b"raw-payload")
    assert list(external_shard.iterdir()) == []


def test_concurrent_identical_publish_converges_without_replacing_final_object(
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifacts"
    source = _source()
    payload = b"raw-payload"
    raw = _raw(payload)

    def publish() -> str:
        return (
            ArtifactStore(root)
            .put_raw(source=source, artifact=raw, payload=payload)
            .snapshot.snapshot_hash
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        hashes = list(executor.map(lambda _: publish(), range(2)))

    assert hashes[0] == hashes[1]
    assert len(list((root / "blobs" / "sha256").rglob("*.blob"))) == 1
    assert len(list((root / "snapshots" / "sha256").rglob("*.json"))) == 1
