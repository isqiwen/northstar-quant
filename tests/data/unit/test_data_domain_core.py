"""P1-WP01 数据领域不可变契约。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from northstar_quant.data.artifacts.fingerprints import (
    canonical_json_sha256,
    content_sha256,
    dataset_version_hash,
    derived_identity_hash,
    lineage_hash,
    normalization_identity_hash,
)
from northstar_quant.data.contracts.data_domain import (
    ArtifactMetadata,
    ArtifactProvenance,
    ArtifactSnapshot,
    DataDomainError,
    DataLineage,
    DataQualityResult,
    DataSource,
    DatasetVersion,
    DerivedArtifact,
    LicenseMetadata,
    NormalizedArtifact,
    QualityStatus,
    RawArtifact,
)
from northstar_quant.foundation.config.data_sources import get_data_source
from tests.helpers.paths import PROJECT_ROOT


UTC = timezone.utc
BASE_TIME = datetime(2026, 1, 5, 8, tzinfo=UTC)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _provenance(source_id: str = "source-a") -> ArtifactProvenance:
    return ArtifactProvenance(
        source_id=source_id,
        source_reference="vendor-dataset-20260105",
        collection_method="api-export",
        attributes=(("request_id", "request-42"),),
    )


def _metadata(
    *,
    artifact_id: str,
    content_hash: str,
    source_id: str = "source-a",
    schema_version: str = "market.v1",
    transform_version: str = "capture.v1",
    acquired_at: datetime = BASE_TIME + timedelta(hours=1),
    available_at: datetime = BASE_TIME + timedelta(hours=2),
    quality_status: QualityStatus = QualityStatus.PASS,
) -> ArtifactMetadata:
    return ArtifactMetadata(
        artifact_id=artifact_id,
        source_id=source_id,
        acquired_at=acquired_at,
        available_at=available_at,
        schema_version=schema_version,
        content_hash=content_hash,
        transform_version=transform_version,
        quality_status=quality_status,
        provenance=_provenance(source_id),
    )


def _raw(*, content_hash: str | None = None, artifact_id: str = "raw-a") -> RawArtifact:
    return RawArtifact(
        metadata=_metadata(
            artifact_id=artifact_id,
            content_hash=content_hash or _hash(artifact_id),
        ),
        raw_format="application/json",
    )


def _normalized(raw: RawArtifact, *, artifact_id: str = "normalized-a") -> NormalizedArtifact:
    transform_version = "normalize.v1"
    schema_version = "market.v2"
    content_hash = _hash(artifact_id)
    return NormalizedArtifact(
        metadata=_metadata(
            artifact_id=artifact_id,
            content_hash=content_hash,
            schema_version=schema_version,
            transform_version=transform_version,
            acquired_at=BASE_TIME + timedelta(hours=3),
            available_at=BASE_TIME + timedelta(hours=4),
        ),
        raw_artifact=raw,
        normalization_identity=normalization_identity_hash(
            raw.content_hash,
            content_hash,
            transform_version,
            schema_version,
        ),
    )


def test_artifact_metadata_is_immutable_and_normalizes_aware_time_to_utc() -> None:
    hong_kong = timezone(timedelta(hours=8))
    metadata = _metadata(
        artifact_id="raw-hk",
        content_hash=_hash("raw-hk"),
        acquired_at=datetime(2026, 1, 5, 17, tzinfo=hong_kong),
        available_at=datetime(2026, 1, 5, 18, tzinfo=hong_kong),
    )

    raw = RawArtifact(metadata=metadata, raw_format="application/json")

    assert raw.acquired_at == datetime(2026, 1, 5, 9, tzinfo=UTC)
    assert raw.available_at == datetime(2026, 1, 5, 10, tzinfo=UTC)
    assert raw.source_id == "source-a"
    with pytest.raises(FrozenInstanceError):
        raw.metadata.source_id = "other"  # type: ignore[misc]


def test_same_raw_and_versioning_produce_stable_normalization_identity_without_time_or_path() -> (
    None
):
    raw_hash = _hash("identical-raw")
    raw_one = _raw(content_hash=raw_hash, artifact_id="raw-one")
    raw_two = RawArtifact(
        metadata=_metadata(
            artifact_id="raw-two",
            content_hash=raw_hash,
            acquired_at=BASE_TIME + timedelta(days=30),
            available_at=BASE_TIME + timedelta(days=30, hours=1),
        ),
        raw_format="application/json",
    )
    transform_version = "normalize.v1"
    schema_version = "market.v2"

    output_one = canonical_json_sha256({"symbol": "RB", "rows": [{"close": 3512.0}]})
    output_two = canonical_json_sha256({"rows": [{"close": 3512.0}], "symbol": "RB"})
    identity_one = normalization_identity_hash(
        raw_one.content_hash,
        output_one,
        transform_version,
        schema_version,
    )
    identity_two = normalization_identity_hash(
        raw_two.content_hash,
        output_two,
        transform_version,
        schema_version,
    )

    assert identity_one == identity_two
    assert output_one == output_two
    assert identity_one != normalization_identity_hash(
        _hash("other"), output_one, transform_version, schema_version
    )
    assert identity_one != normalization_identity_hash(
        raw_hash, output_one, "normalize.v2", schema_version
    )
    assert identity_one != normalization_identity_hash(
        raw_hash, output_one, transform_version, "market.v3"
    )
    assert identity_one != normalization_identity_hash(
        raw_hash, _hash("different-output"), transform_version, schema_version
    )


def test_artifact_metadata_rejects_naive_time_bad_hash_and_pit_time_reversal() -> None:
    with pytest.raises(DataDomainError, match="带时区"):
        _metadata(
            artifact_id="naive",
            content_hash=_hash("naive"),
            acquired_at=datetime(2026, 1, 5, 9),
        )
    with pytest.raises(DataDomainError, match="SHA-256"):
        _metadata(artifact_id="bad-hash", content_hash="not-a-hash")
    with pytest.raises(DataDomainError, match="不能早于"):
        _metadata(
            artifact_id="future",
            content_hash=_hash("future"),
            acquired_at=BASE_TIME + timedelta(seconds=1),
            available_at=BASE_TIME,
        )


def test_normalized_and_derived_artifacts_fail_closed_on_parent_time_or_identity_mismatch() -> None:
    raw = _raw()
    with pytest.raises(DataDomainError, match="available_at 不能早于 raw_artifact"):
        NormalizedArtifact(
            metadata=_metadata(
                artifact_id="early-normalized",
                content_hash=_hash("early-normalized"),
                schema_version="market.v2",
                transform_version="normalize.v1",
                acquired_at=BASE_TIME + timedelta(hours=1, minutes=30),
                available_at=BASE_TIME + timedelta(hours=1, minutes=45),
            ),
            raw_artifact=raw,
            normalization_identity=normalization_identity_hash(
                raw.content_hash,
                _hash("early-normalized"),
                "normalize.v1",
                "market.v2",
            ),
        )

    normalized = _normalized(raw)
    with pytest.raises(DataDomainError, match="normalization_identity"):
        NormalizedArtifact(
            metadata=_metadata(
                artifact_id="normalized-wrong-content-binding",
                content_hash=_hash("normalized-wrong-content-binding"),
                schema_version="market.v2",
                transform_version="normalize.v1",
                acquired_at=BASE_TIME + timedelta(hours=3),
                available_at=BASE_TIME + timedelta(hours=4),
            ),
            raw_artifact=raw,
            normalization_identity=normalization_identity_hash(
                raw.content_hash,
                _hash("different-normalized-output"),
                "normalize.v1",
                "market.v2",
            ),
        )
    with pytest.raises(DataDomainError, match="derivation_identity"):
        DerivedArtifact(
            metadata=_metadata(
                artifact_id="derived-bad",
                content_hash=_hash("derived-bad"),
                schema_version="feature.v1",
                transform_version="feature.v1",
                acquired_at=BASE_TIME + timedelta(hours=5),
                available_at=BASE_TIME + timedelta(hours=6),
            ),
            input_artifacts=(normalized,),
            derivation_identity=_hash("wrong"),
        )

    derived_hash = _hash("derived-ok")
    derived = DerivedArtifact(
        metadata=_metadata(
            artifact_id="derived-ok",
            content_hash=derived_hash,
            schema_version="feature.v1",
            transform_version="feature.v1",
            acquired_at=BASE_TIME + timedelta(hours=5),
            available_at=BASE_TIME + timedelta(hours=6),
        ),
        input_artifacts=(normalized,),
        derivation_identity=derived_identity_hash(
            (normalized.content_hash,),
            "feature.v1",
            "feature.v1",
        ),
    )
    assert derived.kind.value == "derived"
    assert derived_identity_hash(
        (raw.content_hash, normalized.content_hash), "join.v1", "feature.v1"
    ) != (
        derived_identity_hash((normalized.content_hash, raw.content_hash), "join.v1", "feature.v1")
    )


def test_lineage_quality_and_dataset_version_preserve_evidence_and_stable_order() -> None:
    raw_one = _raw(artifact_id="raw-one")
    raw_two = _raw(artifact_id="raw-two")
    normalized_one = _normalized(raw_one, artifact_id="normalized-one")
    normalized_two = _normalized(raw_two, artifact_id="normalized-two")
    output = DerivedArtifact(
        metadata=_metadata(
            artifact_id="derived-output",
            content_hash=_hash("derived-output"),
            schema_version="feature.v1",
            transform_version="feature.v1",
            acquired_at=BASE_TIME + timedelta(hours=5),
            available_at=BASE_TIME + timedelta(hours=6),
            quality_status=QualityStatus.WARN,
        ),
        input_artifacts=(normalized_one, normalized_two),
        derivation_identity=derived_identity_hash(
            (normalized_one.content_hash, normalized_two.content_hash),
            "feature.v1",
            "feature.v1",
        ),
    )
    lineage = DataLineage(
        output_artifact=output,
        input_artifacts=(normalized_one, normalized_two),
        transform_version="feature.v1",
        lineage_identity=lineage_hash(
            output.content_hash,
            (normalized_one.content_hash, normalized_two.content_hash),
            "feature.v1",
        ),
        recorded_at=BASE_TIME + timedelta(hours=7),
    )
    quality = DataQualityResult(
        artifact=output,
        check_id="no-lookahead",
        quality_status=QualityStatus.WARN,
        checked_at=BASE_TIME + timedelta(hours=5, minutes=30),
        summary="输入可用时间已记录。",
    )
    with pytest.raises(DataDomainError, match="精确匹配"):
        DataLineage(
            output_artifact=output,
            input_artifacts=(raw_one, raw_two),
            transform_version="feature.v1",
            lineage_identity=lineage_hash(
                output.content_hash,
                (raw_one.content_hash, raw_two.content_hash),
                "feature.v1",
            ),
            recorded_at=BASE_TIME + timedelta(hours=7),
        )
    first = DatasetVersion.from_artifacts(
        dataset_id="feature-inputs",
        artifacts=(normalized_one, normalized_two),
        schema_version="market.v2",
        transform_version="dataset.v1",
    )
    second = DatasetVersion.from_artifacts(
        dataset_id="feature-inputs",
        artifacts=(normalized_two, normalized_one),
        schema_version="market.v2",
        transform_version="dataset.v1",
    )

    assert lineage.lineage_identity
    assert lineage_hash(
        output.content_hash,
        (normalized_one.content_hash, normalized_two.content_hash),
        "feature.v1",
    ) != lineage_hash(
        output.content_hash,
        (normalized_two.content_hash, normalized_one.content_hash),
        "feature.v1",
    )
    assert quality.content_hash == output.content_hash
    assert quality.source_id == "source-a"
    assert first.version_hash == second.version_hash
    assert first.artifact_content_hashes == tuple(sorted(first.artifact_content_hashes))
    assert first.source_ids == ("source-a",)
    assert first.quality_status is QualityStatus.PASS
    assert first.version_hash == dataset_version_hash(
        "feature-inputs",
        first.artifact_snapshot_hashes,
        "market.v2",
        "dataset.v1",
        first.source_ids,
    )


def test_data_source_snapshot_is_config_hashed_and_does_not_copy_credential_reference() -> None:
    config = get_data_source(
        "akshare_continuous_public_v1",
        path=PROJECT_ROOT / "configs" / "data" / "sources.yaml",
    )
    snapshot = DataSource.from_config(config)

    assert snapshot.source_id == config.source_id
    assert snapshot.config_sha256
    assert snapshot.license.status == config.license.status
    assert "credential_env_var" not in repr(snapshot)


def test_license_and_provenance_fail_closed_on_secrets_wrong_booleans_and_invalid_period() -> None:
    with pytest.raises(DataDomainError, match="凭据、令牌"):
        ArtifactProvenance(
            source_id="source-a",
            source_reference="https://vendor.example/data?token=plain-secret",  # secret-scan: allow; reason: disposable test fixture
            collection_method="api-export",
        )
    with pytest.raises(DataDomainError, match="凭据、令牌"):
        ArtifactProvenance(
            source_id="source-a",
            source_reference="vendor-dataset",
            collection_method="Authorization: Bearer plain-secret",  # secret-scan: allow; reason: disposable test fixture
        )
    with pytest.raises(DataDomainError, match="绝对路径"):
        ArtifactProvenance(
            source_id="source-a",
            source_reference="/home/qiwen/private/source.csv",
            collection_method="api-export",
        )
    with pytest.raises(DataDomainError, match="绝对路径"):
        ArtifactProvenance(
            source_id="source-a",
            source_reference=r"C:\\private\\source.csv",
            collection_method="api-export",
        )
    with pytest.raises(DataDomainError, match="绝对路径"):
        ArtifactProvenance(
            source_id="source-a",
            source_reference="file:///home/qiwen/private/source.csv",
            collection_method="api-export",
        )
    with pytest.raises(DataDomainError, match="凭据、令牌"):
        ArtifactProvenance(
            source_id="source-a",
            source_reference="vendor-dataset",
            collection_method="api-export",
            attributes=(("authorization", "plain-secret"),),
        )
    with pytest.raises(DataDomainError, match="必须是 bool"):
        LicenseMetadata(
            status="active",
            contract_reference="contract-2026",
            effective_from="2026-02-01",
            expires_on="2026-12-31",
            terms_sha256=_hash("terms"),
            permitted_purposes=("internal_research",),
            allows_internal_storage="false",  # type: ignore[arg-type]
            allows_derived_data_storage=False,
            allows_live_trading=False,
        )
    with pytest.raises(DataDomainError, match="不能晚于"):
        LicenseMetadata(
            status="active",
            contract_reference="contract-2026",
            effective_from="2026-12-31",
            expires_on="2026-02-01",
            terms_sha256=_hash("terms"),
            permitted_purposes=("internal_research",),
            allows_internal_storage=False,
            allows_derived_data_storage=False,
            allows_live_trading=False,
        )


def test_dataset_version_distinguishes_revised_pit_snapshot_even_when_blob_bytes_match() -> None:
    content_hash = _hash("same-bytes")
    original = RawArtifact(
        metadata=_metadata(
            artifact_id="raw-revision",
            content_hash=content_hash,
            acquired_at=BASE_TIME + timedelta(hours=1),
            available_at=BASE_TIME + timedelta(hours=2),
        ),
        raw_format="application/json",
    )
    revised = RawArtifact(
        metadata=_metadata(
            artifact_id="raw-revision",
            content_hash=content_hash,
            acquired_at=BASE_TIME + timedelta(hours=1),
            available_at=BASE_TIME + timedelta(hours=3),
        ),
        raw_format="application/json",
    )

    original_version = DatasetVersion.from_artifacts(
        dataset_id="pit-revision",
        artifacts=(original,),
        schema_version="market.v1",
        transform_version="dataset.v1",
    )
    revised_version = DatasetVersion.from_artifacts(
        dataset_id="pit-revision",
        artifacts=(revised,),
        schema_version="market.v1",
        transform_version="dataset.v1",
    )

    assert original_version.artifact_content_hashes == revised_version.artifact_content_hashes
    assert original_version.artifact_snapshot_hashes != revised_version.artifact_snapshot_hashes
    assert original_version.version_hash != revised_version.version_hash


def test_snapshot_rejects_mismatched_direct_constructor_fields() -> None:
    snapshot = ArtifactSnapshot.from_artifact(_raw())

    with pytest.raises(DataDomainError, match="snapshot_hash"):
        replace(snapshot, snapshot_hash=_hash("forged-snapshot"))


def test_deterministic_normalizer_factory_repeats_the_same_raw_transform() -> None:
    raw_payload = b'{"symbol":"RB","rows":[{"close":3512.0}]}'
    raw = _raw(content_hash=content_sha256(raw_payload), artifact_id="raw-transform")

    def canonicalize(payload: bytes) -> bytes:
        return payload.replace(b" ", b"")

    first = NormalizedArtifact.from_deterministic_transform(
        artifact_id="normalized-transform-one",
        raw_artifact=raw,
        raw_payload=raw_payload,
        normalize=canonicalize,
        acquired_at=BASE_TIME + timedelta(hours=3),
        available_at=BASE_TIME + timedelta(hours=4),
        schema_version="market.v2",
        transform_version="normalize.v1",
        quality_status=QualityStatus.PASS,
        provenance=_provenance(),
    )
    second = NormalizedArtifact.from_deterministic_transform(
        artifact_id="normalized-transform-two",
        raw_artifact=raw,
        raw_payload=raw_payload,
        normalize=canonicalize,
        acquired_at=BASE_TIME + timedelta(hours=3),
        available_at=BASE_TIME + timedelta(hours=4),
        schema_version="market.v2",
        transform_version="normalize.v1",
        quality_status=QualityStatus.PASS,
        provenance=_provenance(),
    )

    assert first.content_hash == second.content_hash
    assert first.normalization_identity == second.normalization_identity

    calls = 0

    def non_deterministic(_: bytes) -> bytes:
        nonlocal calls
        calls += 1
        return f"normalized-{calls}".encode("utf-8")

    with pytest.raises(DataDomainError, match="不一致"):
        NormalizedArtifact.from_deterministic_transform(
            artifact_id="normalized-nondeterministic",
            raw_artifact=raw,
            raw_payload=raw_payload,
            normalize=non_deterministic,
            acquired_at=BASE_TIME + timedelta(hours=3),
            available_at=BASE_TIME + timedelta(hours=4),
            schema_version="market.v2",
            transform_version="normalize.v1",
            quality_status=QualityStatus.PASS,
            provenance=_provenance(),
        )


def test_quality_result_must_be_available_for_publication_and_must_not_leak_secrets() -> None:
    raw = _raw()
    with pytest.raises(DataDomainError, match="不得弱于"):
        DataQualityResult(
            artifact=raw,
            check_id="schema",
            quality_status=QualityStatus.FAIL,
            checked_at=BASE_TIME + timedelta(hours=1, minutes=30),
            summary="字段缺失",
        )
    with pytest.raises(DataDomainError, match="不能早于制品 acquired_at"):
        DataQualityResult(
            artifact=raw,
            check_id="schema",
            quality_status=QualityStatus.PASS,
            checked_at=BASE_TIME + timedelta(minutes=30),
            summary="字段齐全",
        )
    with pytest.raises(DataDomainError, match="不能晚于制品 available_at"):
        DataQualityResult(
            artifact=raw,
            check_id="schema",
            quality_status=QualityStatus.PASS,
            checked_at=BASE_TIME + timedelta(hours=2, minutes=1),
            summary="字段齐全",
        )
    with pytest.raises(DataDomainError, match="凭据、令牌"):
        DataQualityResult(
            artifact=raw,
            check_id="schema",
            quality_status=QualityStatus.PASS,
            checked_at=BASE_TIME + timedelta(hours=1, minutes=30),
            summary="Authorization: Bearer plain-secret",  # secret-scan: allow; reason: disposable test fixture
        )


def test_dataset_version_rejects_duplicate_input_content_and_lineage_rejects_self_reference() -> (
    None
):
    raw = _raw()
    normalized = _normalized(raw)
    with pytest.raises(DataDomainError, match="重复内容哈希"):
        DatasetVersion.from_artifacts(
            dataset_id="duplicate-input",
            artifacts=(normalized, normalized),
            schema_version="market.v2",
            transform_version="dataset.v1",
        )
    with pytest.raises(DataDomainError, match="输出不能引用自身"):
        DataLineage(
            output_artifact=normalized,
            input_artifacts=(normalized,),
            transform_version="normalize.v1",
            lineage_identity=lineage_hash(
                normalized.content_hash,
                (_hash("different"),),
                "normalize.v1",
            ),
            recorded_at=BASE_TIME + timedelta(hours=3),
        )
