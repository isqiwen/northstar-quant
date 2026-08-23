"""Controlled publication composition for P4 intelligence feature projections.

The module owns the narrow hand-off from an immutable P4 projection receipt to
the P1 source-publication boundary.  It does not construct source
configuration, quality policy, or storage; those capabilities stay injected in
``DataSourcePublisher``.  The adapter is deliberately pure and can only
replay the canonical P4 receipt supplied at construction time.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import math
import re
from typing import Literal, cast

import polars as pl

from northstar_quant.application.intelligence_feature_projection_evidence import (
    ImmutableIntelligenceFeatureProjectionEvidenceVerifier,
    IntelligenceFeatureProjectionEvidenceError,
)
from northstar_quant.data_platform.contracts.data_domain import QualityStatus
from northstar_quant.data_platform.sources.protocol import (
    CANONICAL_NORMALIZED_FORMAT,
    AdapterMetadata,
    NormalizedTable,
    PublicationPurpose,
    PublicationScope,
    RawCapture,
    SourceFetchRequest,
)
from northstar_quant.data_platform.sources.publisher import (
    DataSourcePublisher,
    PublishedSourceDataset,
    SourcePublicationSpec,
)
from northstar_quant.intelligence.feature_projection import (
    INTELLIGENCE_FEATURE_INPUT_PROVENANCE_COLUMNS,
    INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS,
    INTELLIGENCE_FEATURE_INPUT_SCORE_COLUMNS,
    INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS,
    INTELLIGENCE_FEATURE_PROJECTION_SCHEMA_VERSION,
    INTELLIGENCE_METRIC_MISSING_REASONS,
    IntelligenceFeatureProjectionError,
    IntelligenceFeatureProjectionObservation,
    IntelligenceMetricKind,
    VersionedIntelligenceFeatureProjection,
)
from northstar_quant.research.features.intelligence import (
    INTELLIGENCE_EVENT_INPUT,
)


__all__ = [
    "IntelligenceFeatureProjectionAdapter",
    "IntelligenceFeatureProjectionAdapterError",
    "IntelligenceFeatureProjectionPublisher",
    "ImmutableIntelligenceFeatureProjectionEvidenceVerifier",
    "IntelligenceFeatureProjectionEvidenceError",
]


class IntelligenceFeatureProjectionAdapterError(ValueError):
    """Raised when the immutable P4-to-P1 hand-off is incomplete or inconsistent."""


_ADAPTER_ID = "intelligence_feature_projection_v3"
_IMPLEMENTATION_VERSION = "intelligence_feature_projection_adapter_v2"
_RAW_FORMAT = "application/vnd.northstar.intelligence-feature-projection.v3+json"
_TRANSFORM_VERSION = "intelligence_feature_projection_normalize_v2"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SAFE_PURPOSES = frozenset(
    {
        PublicationPurpose.HISTORICAL_BACKTEST,
        PublicationPurpose.INTERNAL_RESEARCH,
    }
)

_INPUT_VALUE_COLUMNS = tuple(INTELLIGENCE_EVENT_INPUT.value_columns or ())
_INPUT_COLUMNS = (
    *INTELLIGENCE_EVENT_INPUT.entity_key_columns,
    INTELLIGENCE_EVENT_INPUT.event_time_column,
    INTELLIGENCE_EVENT_INPUT.available_at_column,
    *_INPUT_VALUE_COLUMNS,
)


def _identifier(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or value.strip() != value
        or _IDENTIFIER_PATTERN.fullmatch(value) is None
    ):
        raise IntelligenceFeatureProjectionAdapterError(
            f"{field_name} must be a non-empty stable identifier"
        )
    return value


def _hash(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise IntelligenceFeatureProjectionAdapterError(
            f"{field_name} must be a lowercase SHA-256 hash"
        )
    return value


def _time(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise IntelligenceFeatureProjectionAdapterError(
            f"{field_name} must be a timezone-aware datetime"
        )
    return value.astimezone(UTC)


def _score(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        raise IntelligenceFeatureProjectionAdapterError(
            f"{field_name} must be null or a finite score in [0, 1]"
        )
    return float(value)


def _missing_reason(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or value.strip() != value
        or value not in INTELLIGENCE_METRIC_MISSING_REASONS
    ):
        raise IntelligenceFeatureProjectionAdapterError(
            f"{field_name} must be null or an explicit closed missing-data code"
        )
    return value


def _validate_contract_alignment() -> None:
    """Require P4's narrow projection and P2's registered input to stay identical."""

    if INTELLIGENCE_EVENT_INPUT.schema_version != INTELLIGENCE_FEATURE_PROJECTION_SCHEMA_VERSION:
        raise IntelligenceFeatureProjectionAdapterError(
            "P2 input schema must exactly match the P4 projection schema"
        )
    if not _INPUT_VALUE_COLUMNS:
        raise IntelligenceFeatureProjectionAdapterError("P2 input must declare value columns")
    if _INPUT_VALUE_COLUMNS != INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS:
        raise IntelligenceFeatureProjectionAdapterError(
            "P2 feature input value columns must remain internally consistent"
        )
    if (
        INTELLIGENCE_FEATURE_INPUT_PROVENANCE_COLUMNS
        + INTELLIGENCE_FEATURE_INPUT_SCORE_COLUMNS
        + INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS
        != INTELLIGENCE_FEATURE_INPUT_VALUE_COLUMNS
    ):
        raise IntelligenceFeatureProjectionAdapterError(
            "P4 narrow feature rows must retain their exact value-column order"
        )
    metric_columns = tuple(f"{kind.value}_input" for kind in IntelligenceMetricKind)
    if metric_columns != INTELLIGENCE_FEATURE_INPUT_SCORE_COLUMNS:
        raise IntelligenceFeatureProjectionAdapterError(
            "P4 metric kinds must exactly cover the P2 score columns"
        )


def _validate_scope(scope: object) -> PublicationScope:
    if type(scope) is not PublicationScope:
        raise IntelligenceFeatureProjectionAdapterError(
            "publication scope must be a PublicationScope"
        )
    if scope.purpose not in _SAFE_PURPOSES:
        raise IntelligenceFeatureProjectionAdapterError(
            "projection publication is restricted to research and historical backtest use"
        )
    if (
        scope.actual_contract_data
        or scope.requires_authoritative_calendar
        or scope.requires_authoritative_dynamic_rules
    ):
        raise IntelligenceFeatureProjectionAdapterError(
            "projection publication cannot claim contract or authoritative-rule data"
        )
    return scope


def _metadata() -> AdapterMetadata:
    return AdapterMetadata(
        adapter_id=_ADAPTER_ID,
        implementation_version=_IMPLEMENTATION_VERSION,
        raw_format=_RAW_FORMAT,
        normalized_schema_version=INTELLIGENCE_EVENT_INPUT.schema_version,
        transform_version=_TRANSFORM_VERSION,
        normalized_format=CANONICAL_NORMALIZED_FORMAT,
    )


def _frame_schema() -> dict[str, pl.DataType]:
    schema: dict[str, pl.DataType] = {
        column: pl.String()
        for column in (
            *INTELLIGENCE_EVENT_INPUT.entity_key_columns,
            *INTELLIGENCE_FEATURE_INPUT_PROVENANCE_COLUMNS,
        )
    }
    schema[INTELLIGENCE_EVENT_INPUT.event_time_column] = pl.Datetime(
        time_unit="us",
        time_zone="UTC",
    )
    schema[INTELLIGENCE_EVENT_INPUT.available_at_column] = pl.Datetime(
        time_unit="us",
        time_zone="UTC",
    )
    schema.update({column: pl.Float64() for column in INTELLIGENCE_FEATURE_INPUT_SCORE_COLUMNS})
    schema.update(
        {column: pl.String() for column in INTELLIGENCE_FEATURE_INPUT_MISSING_REASON_COLUMNS}
    )
    return {column: schema[column] for column in _INPUT_COLUMNS}


def _metric_values(
    observation: IntelligenceFeatureProjectionObservation,
) -> dict[str, tuple[float | None, str | None]]:
    values = observation.metric_values
    if not isinstance(values, tuple) or len(values) != len(IntelligenceMetricKind):
        raise IntelligenceFeatureProjectionAdapterError(
            "projection observation must carry every intelligence metric exactly once"
        )
    by_column: dict[str, tuple[float | None, str | None]] = {}
    for metric in values:
        if type(metric.kind) is not IntelligenceMetricKind:
            raise IntelligenceFeatureProjectionAdapterError(
                "projection observation metric kind is invalid"
            )
        column = f"{metric.kind.value}_input"
        if column in by_column:
            raise IntelligenceFeatureProjectionAdapterError(
                "projection observation cannot duplicate an intelligence metric"
            )
        score = _score(metric.score, column)
        missing_reason = _missing_reason(
            metric.missing_reason,
            f"{metric.kind.value}_missing_reason",
        )
        if score is None and missing_reason is None:
            raise IntelligenceFeatureProjectionAdapterError(
                f"{metric.kind.value}_missing_reason is required when {column} is null"
            )
        if score is not None and missing_reason is not None:
            raise IntelligenceFeatureProjectionAdapterError(
                f"{metric.kind.value}_missing_reason must be null when {column} is populated"
            )
        by_column[column] = (score, missing_reason)
    if tuple(sorted(by_column)) != tuple(sorted(INTELLIGENCE_FEATURE_INPUT_SCORE_COLUMNS)):
        raise IntelligenceFeatureProjectionAdapterError(
            "projection observation must exactly cover the P2 score columns"
        )
    return by_column


def _canonical_projection(
    projection: object,
) -> VersionedIntelligenceFeatureProjection:
    """Re-run P4 receipt validation before crossing into the P1 publisher."""

    if type(projection) is not VersionedIntelligenceFeatureProjection:
        raise IntelligenceFeatureProjectionAdapterError(
            "projection must be a VersionedIntelligenceFeatureProjection"
        )
    try:
        canonical = VersionedIntelligenceFeatureProjection(
            projection_version=projection.projection_version,
            projection_hash=projection.projection_hash,
            observations=projection.observations,
        )
    except IntelligenceFeatureProjectionError as exc:
        raise IntelligenceFeatureProjectionAdapterError(
            "projection must retain its exact P4 receipt identity"
        ) from exc
    except AttributeError as exc:
        raise IntelligenceFeatureProjectionAdapterError(
            "projection must retain its typed P4 receipt fields"
        ) from exc
    if projection.collection_schema != canonical.collection_schema:
        raise IntelligenceFeatureProjectionAdapterError(
            "projection collection schema must retain the fixed P4 schema"
        )
    if projection.eligible_for_trading is not False:
        raise IntelligenceFeatureProjectionAdapterError("projection must remain non-tradable")
    return canonical


def _validate_projection(
    projection: object,
) -> tuple[
    VersionedIntelligenceFeatureProjection,
    datetime,
    tuple[Mapping[str, object], ...],
]:
    _validate_contract_alignment()
    canonical_projection = _canonical_projection(projection)
    if canonical_projection.collection_schema != INTELLIGENCE_EVENT_INPUT.schema_version:
        raise IntelligenceFeatureProjectionAdapterError(
            "projection collection schema must exactly match the P2 input schema"
        )
    projection_hash = _hash(
        canonical_projection.projection_hash,
        "projection.projection_hash",
    )
    available_at = _time(
        canonical_projection.available_at,
        "projection.available_at",
    )
    observations = canonical_projection.observations
    if (
        not isinstance(observations, tuple)
        or not observations
        or not all(type(item) is IntelligenceFeatureProjectionObservation for item in observations)
    ):
        raise IntelligenceFeatureProjectionAdapterError(
            "projection must contain typed observations"
        )
    if any(
        observation.available_at != available_at
        or observation.collection_schema != canonical_projection.collection_schema
        or observation.eligible_for_trading is not False
        or observation.projection_hash != projection_hash
        for observation in observations
    ):
        raise IntelligenceFeatureProjectionAdapterError(
            "projection observations must share the collection identity and availability"
        )

    rows_value = canonical_projection.as_feature_input_rows()
    if (
        not isinstance(rows_value, tuple)
        or len(rows_value) != len(observations)
        or not all(isinstance(row, Mapping) for row in rows_value)
    ):
        raise IntelligenceFeatureProjectionAdapterError(
            "projection feature rows must exactly correspond to typed observations"
        )
    rows = tuple(cast(Mapping[str, object], row) for row in rows_value)
    observations_by_id = {
        observation.projection_observation_id: observation for observation in observations
    }
    normalized_rows: list[Mapping[str, object]] = []
    seen_keys: set[tuple[str, str, datetime]] = set()

    for row in rows:
        if tuple(row) != _INPUT_COLUMNS:
            raise IntelligenceFeatureProjectionAdapterError(
                "projection feature rows must have the exact P2 column order"
            )
        observation_id = _identifier(row["projection_observation_id"], "projection_observation_id")
        observation = observations_by_id.get(observation_id)
        if observation is None:
            raise IntelligenceFeatureProjectionAdapterError(
                "projection feature row does not bind a collection observation"
            )
        commodity_id = _identifier(row["commodity_id"], "commodity_id")
        event_time = _time(row[INTELLIGENCE_EVENT_INPUT.event_time_column], "event_time")
        row_available_at = _time(
            row[INTELLIGENCE_EVENT_INPUT.available_at_column],
            "available_at",
        )
        if event_time > row_available_at or row_available_at != available_at:
            raise IntelligenceFeatureProjectionAdapterError(
                "projection feature row violates the collection point-in-time boundary"
            )
        if (
            commodity_id != observation.commodity_id
            or event_time != observation.event_time
            or row_available_at != observation.available_at
        ):
            raise IntelligenceFeatureProjectionAdapterError(
                "projection feature row does not exactly bind its observation timing"
            )
        for column in INTELLIGENCE_FEATURE_INPUT_PROVENANCE_COLUMNS:
            value = row[column]
            if column == "ontology_version":
                _identifier(value, column)
            else:
                _hash(value, column)
            if value != getattr(observation, column):
                raise IntelligenceFeatureProjectionAdapterError(
                    "projection feature row provenance does not bind its observation"
                )
        if row["projection_hash"] != projection_hash:
            raise IntelligenceFeatureProjectionAdapterError(
                "projection feature row must bind the collection projection hash"
            )
        expected_metrics = _metric_values(observation)
        normalized_row = dict(row)
        for column in INTELLIGENCE_FEATURE_INPUT_SCORE_COLUMNS:
            value = _score(row[column], column)
            expected_value, expected_missing_reason = expected_metrics[column]
            if value != expected_value:
                raise IntelligenceFeatureProjectionAdapterError(
                    "projection feature row metric does not bind its observation"
                )
            normalized_row[column] = value
            missing_reason_column = f"{column.removesuffix('_input')}_missing_reason"
            missing_reason = _missing_reason(
                row[missing_reason_column],
                missing_reason_column,
            )
            if missing_reason != expected_missing_reason:
                raise IntelligenceFeatureProjectionAdapterError(
                    "projection feature row missing-data declaration does not bind its observation"
                )
            normalized_row[missing_reason_column] = missing_reason
        logical_key = (commodity_id, observation_id, event_time)
        if logical_key in seen_keys:
            raise IntelligenceFeatureProjectionAdapterError(
                "projection feature rows cannot duplicate a P2 logical key"
            )
        seen_keys.add(logical_key)
        normalized_rows.append(normalized_row)

    if set(observations_by_id) != {row["projection_observation_id"] for row in rows}:
        raise IntelligenceFeatureProjectionAdapterError(
            "projection feature rows must cover every collection observation once"
        )
    return canonical_projection, available_at, tuple(normalized_rows)


def _canonical_payload(projection: VersionedIntelligenceFeatureProjection) -> bytes:
    payload = projection.canonical_payload
    if not isinstance(payload, bytes) or not payload:
        raise IntelligenceFeatureProjectionAdapterError(
            "projection canonical payload must be non-empty immutable bytes"
        )
    return payload


@dataclass(frozen=True, slots=True)
class IntelligenceFeatureProjectionAdapter:
    """Pure P1 adapter for exactly one immutable P4 projection receipt."""

    projection: VersionedIntelligenceFeatureProjection
    _available_at: datetime = field(init=False, repr=False)
    _canonical_payload: bytes = field(init=False, repr=False)
    _canonical_payload_hash: str = field(init=False, repr=False)
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        canonical_projection, available_at, _ = _validate_projection(self.projection)
        payload = _canonical_payload(canonical_projection)
        object.__setattr__(self, "_available_at", available_at)
        object.__setattr__(self, "_canonical_payload", payload)
        object.__setattr__(self, "_canonical_payload_hash", sha256(payload).hexdigest())

    @property
    def adapter_id(self) -> str:
        """Return the fixed technical identity required by the P1 source contract."""

        return _ADAPTER_ID

    @property
    def available_at(self) -> datetime:
        """Return the canonical collection availability after revalidating the receipt."""

        _, available_at, _ = self._validated_state()
        return available_at

    def metadata(self, scope: PublicationScope) -> AdapterMetadata:
        """Return fixed P1 metadata only for non-tradable research use."""

        self._validated_state()
        _validate_scope(scope)
        return _metadata()

    def fetch(self, request: SourceFetchRequest) -> RawCapture:
        """Replay the frozen P4 receipt locally; this method has no external capability."""

        if type(request) is not SourceFetchRequest:
            raise IntelligenceFeatureProjectionAdapterError(
                "fetch request must be a SourceFetchRequest"
            )
        projection_hash, available_at, _ = self._validated_state()
        _validate_scope(request.scope)
        return RawCapture(
            payload=self._canonical_payload,
            raw_format=_RAW_FORMAT,
            source_reference=f"intelligence-feature-projection:{projection_hash}",
            collection_method="projection-receipt",
            available_at=available_at,
            capture_quality_status=QualityStatus.PASS,
            provenance_attributes=(("projection_hash", projection_hash),),
        )

    def normalize(
        self,
        raw_payload: bytes,
        *,
        metadata: AdapterMetadata,
    ) -> NormalizedTable:
        """Convert only the exact frozen receipt bytes into the canonical P1 table."""

        _, _, rows = self._validated_state()
        if not isinstance(raw_payload, bytes):
            raise IntelligenceFeatureProjectionAdapterError("raw payload must be bytes")
        if raw_payload != self._canonical_payload or (
            sha256(raw_payload).hexdigest() != self._canonical_payload_hash
        ):
            raise IntelligenceFeatureProjectionAdapterError(
                "raw payload does not exactly match the frozen projection receipt"
            )
        if type(metadata) is not AdapterMetadata or metadata != _metadata():
            raise IntelligenceFeatureProjectionAdapterError(
                "normalization metadata must exactly match the projection adapter"
            )
        ordered_rows = sorted(
            rows,
            key=lambda row: (
                cast(str, row["commodity_id"]),
                cast(str, row["projection_observation_id"]),
                cast(datetime, row[INTELLIGENCE_EVENT_INPUT.event_time_column]).isoformat(),
            ),
        )
        frame = pl.DataFrame(ordered_rows, schema=_frame_schema(), strict=True)
        if tuple(frame.columns) != _INPUT_COLUMNS:
            raise IntelligenceFeatureProjectionAdapterError(
                "normalized table must preserve the exact P2 column order"
            )
        return NormalizedTable.from_frame(frame)

    def _validated_state(
        self,
    ) -> tuple[str, datetime, tuple[Mapping[str, object], ...]]:
        canonical_projection, available_at, rows = _validate_projection(self.projection)
        payload = _canonical_payload(canonical_projection)
        if (
            available_at != self._available_at
            or payload != self._canonical_payload
            or sha256(payload).hexdigest() != self._canonical_payload_hash
        ):
            raise IntelligenceFeatureProjectionAdapterError(
                "projection receipt identity changed after adapter construction"
            )
        return canonical_projection.projection_hash, available_at, rows


@dataclass(frozen=True, slots=True)
class IntelligenceFeatureProjectionPublisher:
    """Compose one P4 receipt into P1 only through an injected publication boundary."""

    data_source_publisher: DataSourcePublisher
    evidence_verifier: ImmutableIntelligenceFeatureProjectionEvidenceVerifier

    def __post_init__(self) -> None:
        if not isinstance(self.data_source_publisher, DataSourcePublisher):
            raise IntelligenceFeatureProjectionAdapterError(
                "data_source_publisher must be a DataSourcePublisher"
            )
        if type(self.evidence_verifier) is not ImmutableIntelligenceFeatureProjectionEvidenceVerifier:
            raise IntelligenceFeatureProjectionAdapterError(
                "evidence_verifier must be an ImmutableIntelligenceFeatureProjectionEvidenceVerifier"
            )
        try:
            publication_store = self.data_source_publisher.artifact_store
        except AttributeError as exc:
            raise IntelligenceFeatureProjectionAdapterError(
                "data_source_publisher must expose its immutable artifact store"
            ) from exc
        if publication_store is not self.evidence_verifier.artifact_store:
            raise IntelligenceFeatureProjectionAdapterError(
                "evidence verifier and P1 publisher must use the same immutable artifact store"
            )

    def publish(
        self,
        *,
        projection: VersionedIntelligenceFeatureProjection,
        publication_spec: SourcePublicationSpec,
    ) -> PublishedSourceDataset:
        """Publish one validated projection through the caller-provided P1 policy and scope."""

        if type(publication_spec) is not SourcePublicationSpec:
            raise IntelligenceFeatureProjectionAdapterError(
                "publication_spec must be a SourcePublicationSpec"
            )
        if type(publication_spec.request) is not SourceFetchRequest:
            raise IntelligenceFeatureProjectionAdapterError(
                "publication_spec.request must be a SourceFetchRequest"
            )
        preflight_adapter = IntelligenceFeatureProjectionAdapter(projection=projection)
        normalized_available_at = _time(
            publication_spec.normalized_available_at,
            "publication_spec.normalized_available_at",
        )
        if normalized_available_at != preflight_adapter.available_at:
            raise IntelligenceFeatureProjectionAdapterError(
                "publication spec normalized availability must exactly match projection availability"
            )
        preflight_adapter.metadata(publication_spec.request.scope)
        try:
            canonical_projection = self.evidence_verifier.verify(projection)
        except IntelligenceFeatureProjectionEvidenceError as exc:
            raise IntelligenceFeatureProjectionAdapterError(
                "projection immutable source/context evidence is invalid"
            ) from exc
        adapter = IntelligenceFeatureProjectionAdapter(projection=canonical_projection)
        if adapter.available_at != normalized_available_at:
            raise IntelligenceFeatureProjectionAdapterError(
                "verified projection availability changed before P1 publication"
            )
        adapter.metadata(publication_spec.request.scope)
        return self.data_source_publisher.publish(
            adapter=adapter,
            spec=publication_spec,
            released_at=adapter.available_at,
        )
