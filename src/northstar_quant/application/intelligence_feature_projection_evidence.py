"""Immutable evidence verification for the P4-to-P1 projection hand-off.

P4 intentionally remains a pure, hash-only projection boundary.  Resolving a
publication receipt or replaying a DatasetVersion therefore belongs here, at
the application composition boundary immediately before P1 publication.  This
module accepts only the concrete immutable :class:`ArtifactStore`; it never
falls back to configuration, a latest pointer, or a caller supplied snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
import re
from typing import Protocol, cast

from northstar_quant.data_platform.artifacts.immutable_store import (
    ArtifactReplay,
    ArtifactStore,
    ArtifactStoreError,
    DatasetReplay,
    StoredArtifact,
    StoredPublicationAuthorization,
)
from northstar_quant.data_platform.artifacts.fingerprints import content_sha256
from northstar_quant.data_platform.contracts.data_domain import ArtifactKind
from northstar_quant.data_platform.sources.protocol import PublicationPurpose
from northstar_quant.intelligence.context import MarketContextError, MarketContextSnapshot
from northstar_quant.intelligence.feature_projection import (
    AuthorizedMarketContext,
    EventEvidenceAvailability,
    INTELLIGENCE_MARKET_CONTEXT_SNAPSHOT_ROW_COLUMNS,
    IntelligenceFeatureProjectionError,
    VersionedIntelligenceFeatureProjection,
)


__all__ = [
    "ImmutableIntelligenceFeatureProjectionEvidenceVerifier",
    "IntelligenceFeatureProjectionEvidenceError",
]


class IntelligenceFeatureProjectionEvidenceError(ValueError):
    """Raised when immutable source or context evidence cannot be verified."""


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SAFE_PURPOSE_VALUES = frozenset(
    {
        PublicationPurpose.INTERNAL_RESEARCH.value,
        PublicationPurpose.HISTORICAL_BACKTEST.value,
    }
)
_UNSAFE_SCOPE_FLAGS = (
    "actual_contract_data",
    "requires_authoritative_calendar",
    "requires_authoritative_dynamic_rules",
)
_CANONICAL_FRAME_FORMAT = "northstar.data_quality.canonical_frame.v1"
_CONTEXT_TEXT_COLUMNS = frozenset(
    {
        "snapshot_id",
        "commodity_id",
        "market_id",
        "macro_regime",
        "seasonality",
    }
)
_CONTEXT_TIME_COLUMNS = frozenset({"as_of", "available_at"})
_CONTEXT_FLOAT_COLUMNS = frozenset(
    {
        "inventory",
        "term_structure",
        "basis",
        "positioning",
        "volatility",
        "usd",
        "cny",
    }
)
_CONTEXT_SCHEMA_DTYPES = {
    **{column: "String" for column in _CONTEXT_TEXT_COLUMNS},
    **{
        column: "Datetime(time_unit='us', time_zone='UTC')"
        for column in _CONTEXT_TIME_COLUMNS
    },
    **{column: "Float64" for column in _CONTEXT_FLOAT_COLUMNS},
}


class _EvidenceBoundObservation(Protocol):
    """The immutable evidence fields required from the P4 public receipt.

    The structural protocol keeps this application seam bound only to the
    evidence fields it consumes.  Its absence is rejected at runtime rather
    than silently treating a receipt bundle hash as evidence that individual
    source receipts exist.
    """

    available_at: datetime
    event_time: datetime
    context_artifact_snapshot_hash: str
    context_content_commitment_hash: str
    context_dataset_version_hash: str
    context_identity_hash: str
    context_publication_receipt_hash: str
    source_publication_receipt_hashes: tuple[str, ...]
    event_evidence: tuple[EventEvidenceAvailability, ...]


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise IntelligenceFeatureProjectionEvidenceError(
            f"{field_name} must be a lowercase SHA-256 hash"
        )
    return value


def _time(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise IntelligenceFeatureProjectionEvidenceError(
            f"{field_name} must be a timezone-aware datetime"
        )
    return value.astimezone(UTC)


def _source_receipt_hashes(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or type(value) is not tuple:
        raise IntelligenceFeatureProjectionEvidenceError(
            "source_publication_receipt_hashes must be a tuple of SHA-256 hashes"
        )
    hashes = tuple(
        _sha256(item, "source_publication_receipt_hashes") for item in value
    )
    if not hashes:
        raise IntelligenceFeatureProjectionEvidenceError(
            "source_publication_receipt_hashes cannot be empty"
        )
    if len(set(hashes)) != len(hashes) or hashes != tuple(sorted(hashes)):
        raise IntelligenceFeatureProjectionEvidenceError(
            "source_publication_receipt_hashes must be unique and canonically sorted"
        )
    return hashes


def _canonical_json(payload: bytes, *, field_name: str) -> dict[str, object]:
    """Decode one immutable canonical-frame payload without JSON ambiguity."""

    if not isinstance(payload, bytes):
        raise IntelligenceFeatureProjectionEvidenceError(
            f"{field_name} must be immutable bytes"
        )

    def reject_duplicate_keys(pairs: list[tuple[object, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if not isinstance(key, str) or key in result:
                raise IntelligenceFeatureProjectionEvidenceError(
                    f"{field_name} contains an ambiguous JSON object"
                )
            result[key] = value
        return result

    try:
        decoded = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntelligenceFeatureProjectionEvidenceError(
            f"{field_name} must be a canonical UTF-8 JSON payload"
        ) from exc
    if type(decoded) is not dict:
        raise IntelligenceFeatureProjectionEvidenceError(
            f"{field_name} must be a canonical JSON object"
        )
    return decoded


def _context_cell(
    value: object,
    *,
    column: str,
    row_index: int,
) -> str | float | datetime:
    field_name = f"market-context row {row_index}.{column}"
    if type(value) is not dict or set(value) != {"type", "value"}:
        raise IntelligenceFeatureProjectionEvidenceError(
            f"{field_name} must be one typed canonical-frame cell"
        )
    cell_type = value["type"]
    raw_value = value["value"]
    if column in _CONTEXT_TEXT_COLUMNS:
        if cell_type != "str" or not isinstance(raw_value, str):
            raise IntelligenceFeatureProjectionEvidenceError(
                f"{field_name} must be a canonical string cell"
            )
        return raw_value
    if column in _CONTEXT_TIME_COLUMNS:
        if cell_type != "datetime" or not isinstance(raw_value, str):
            raise IntelligenceFeatureProjectionEvidenceError(
                f"{field_name} must be a canonical datetime cell"
            )
        try:
            parsed = _time(datetime.fromisoformat(raw_value), field_name)
        except ValueError as exc:
            raise IntelligenceFeatureProjectionEvidenceError(
                f"{field_name} must contain a valid canonical datetime"
            ) from exc
        if raw_value != parsed.isoformat():
            raise IntelligenceFeatureProjectionEvidenceError(
                f"{field_name} must be normalized to UTC canonical datetime text"
            )
        return parsed
    if cell_type != "float" or not isinstance(raw_value, str):
        raise IntelligenceFeatureProjectionEvidenceError(
            f"{field_name} must be a canonical Float64 cell"
        )
    try:
        parsed_float = float.fromhex(raw_value)
    except ValueError as exc:
        raise IntelligenceFeatureProjectionEvidenceError(
            f"{field_name} must contain a valid canonical float"
        ) from exc
    if not math.isfinite(parsed_float) or parsed_float.hex() != raw_value:
        raise IntelligenceFeatureProjectionEvidenceError(
            f"{field_name} must contain a finite canonical float"
        )
    return parsed_float


def _context_snapshots_from_artifact(
    payload: bytes,
    *,
    context_dataset_version_hash: str,
) -> tuple[MarketContextSnapshot, ...]:
    """Reconstruct closed-schema context rows from one immutable normalized blob."""

    decoded = _canonical_json(payload, field_name="market-context normalized artifact")
    if set(decoded) != {"format", "schema", "rows"}:
        raise IntelligenceFeatureProjectionEvidenceError(
            "market-context normalized artifact must have the exact canonical-frame envelope"
        )
    if decoded["format"] != _CANONICAL_FRAME_FORMAT:
        raise IntelligenceFeatureProjectionEvidenceError(
            "market-context normalized artifact has an unsupported canonical-frame format"
        )
    schema = decoded["schema"]
    if type(schema) is not list or len(schema) != len(
        INTELLIGENCE_MARKET_CONTEXT_SNAPSHOT_ROW_COLUMNS
    ):
        raise IntelligenceFeatureProjectionEvidenceError(
            "market-context normalized artifact must have the full closed context schema"
        )
    expected_columns = INTELLIGENCE_MARKET_CONTEXT_SNAPSHOT_ROW_COLUMNS
    for index, (entry, expected_column) in enumerate(zip(schema, expected_columns, strict=True)):
        if type(entry) is not dict or set(entry) != {"dtype", "name"}:
            raise IntelligenceFeatureProjectionEvidenceError(
                "market-context normalized artifact schema entries are invalid"
            )
        if (
            entry["name"] != expected_column
            or entry["dtype"] != _CONTEXT_SCHEMA_DTYPES[expected_column]
        ):
            raise IntelligenceFeatureProjectionEvidenceError(
                f"market-context normalized artifact schema mismatch at column {index}"
            )
    rows = decoded["rows"]
    if type(rows) is not list or not rows:
        raise IntelligenceFeatureProjectionEvidenceError(
            "market-context normalized artifact must contain non-empty context rows"
        )
    snapshots: list[MarketContextSnapshot] = []
    for row_index, raw_row in enumerate(rows):
        if type(raw_row) is not dict or set(raw_row) != set(expected_columns):
            raise IntelligenceFeatureProjectionEvidenceError(
                "market-context normalized artifact rows must use the full closed context schema"
            )
        values = {
            column: _context_cell(raw_row[column], column=column, row_index=row_index)
            for column in expected_columns
        }
        try:
            snapshots.append(
                MarketContextSnapshot(
                    snapshot_id=cast(str, values["snapshot_id"]),
                    commodity_id=cast(str, values["commodity_id"]),
                    market_id=cast(str, values["market_id"]),
                    dataset_version=context_dataset_version_hash,
                    as_of=cast(datetime, values["as_of"]),
                    available_at=cast(datetime, values["available_at"]),
                    inventory=cast(float, values["inventory"]),
                    term_structure=cast(float, values["term_structure"]),
                    basis=cast(float, values["basis"]),
                    positioning=cast(float, values["positioning"]),
                    volatility=cast(float, values["volatility"]),
                    usd=cast(float, values["usd"]),
                    cny=cast(float, values["cny"]),
                    macro_regime=cast(str, values["macro_regime"]),
                    seasonality=cast(str, values["seasonality"]),
                )
            )
        except MarketContextError as exc:
            raise IntelligenceFeatureProjectionEvidenceError(
                "market-context normalized artifact row is not a valid typed context snapshot"
            ) from exc
    return tuple(snapshots)


def _receipt_authorized_at(
    receipt: StoredPublicationAuthorization,
    *,
    receipt_role: str,
) -> datetime:
    payload = receipt.authorization
    if type(payload) is not dict:
        raise IntelligenceFeatureProjectionEvidenceError(
            f"{receipt_role} immutable receipt payload must be a mapping"
        )
    value = payload.get("authorized_at")
    if not isinstance(value, str):
        raise IntelligenceFeatureProjectionEvidenceError(
            f"{receipt_role} immutable receipt is missing authorized_at"
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise IntelligenceFeatureProjectionEvidenceError(
            f"{receipt_role} immutable receipt has an invalid authorized_at"
        ) from exc
    return _time(parsed, f"{receipt_role} receipt authorized_at")


def _assert_research_safe_receipt(
    receipt: StoredPublicationAuthorization,
    *,
    receipt_hash: str,
    receipt_role: str,
    available_at: datetime,
) -> None:
    if receipt.authorization_hash != receipt_hash:
        raise IntelligenceFeatureProjectionEvidenceError(
            f"{receipt_role} immutable receipt identity does not match its requested hash"
        )
    payload = receipt.authorization
    if type(payload) is not dict:
        raise IntelligenceFeatureProjectionEvidenceError(
            f"{receipt_role} immutable receipt payload must be a mapping"
        )
    scope = payload.get("scope")
    if type(scope) is not dict:
        raise IntelligenceFeatureProjectionEvidenceError(
            f"{receipt_role} immutable receipt is missing a canonical publication scope"
        )
    purpose = scope.get("purpose")
    if purpose not in _SAFE_PURPOSE_VALUES:
        raise IntelligenceFeatureProjectionEvidenceError(
            f"{receipt_role} immutable receipt is not limited to research or historical backtest"
        )
    for field_name in _UNSAFE_SCOPE_FLAGS:
        if scope.get(field_name) is not False:
            raise IntelligenceFeatureProjectionEvidenceError(
                f"{receipt_role} immutable receipt has an unsafe scope: {field_name}"
            )
    if _receipt_authorized_at(receipt, receipt_role=receipt_role) > available_at:
        raise IntelligenceFeatureProjectionEvidenceError(
            f"{receipt_role} immutable receipt was authorized after projection availability"
        )


def _source_event_evidence(
    value: object,
) -> tuple[EventEvidenceAvailability, ...]:
    """Reconstruct the retained P4 evidence records before artifact replay.

    The projection collection is itself immutable and canonicalized, but this
    extra reconstruction keeps the application boundary fail-closed if a
    caller mutates a frozen DTO through ``object.__setattr__`` after its
    original construction.
    """

    if (
        type(value) is not tuple
        or not value
        or not all(type(item) is EventEvidenceAvailability for item in value)
    ):
        raise IntelligenceFeatureProjectionEvidenceError(
            "P4 observation must retain non-empty typed event evidence records"
        )
    try:
        return tuple(
            EventEvidenceAvailability(
                document_id=item.document_id,
                content_hash=item.content_hash,
                span_start=item.span_start,
                span_end=item.span_end,
                available_at=item.available_at,
                source_publication_receipt_hash=item.source_publication_receipt_hash,
                source_artifact_snapshot_hash=item.source_artifact_snapshot_hash,
            )
            for item in value
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise IntelligenceFeatureProjectionEvidenceError(
            "P4 observation event evidence is invalid"
        ) from exc


@dataclass(frozen=True, slots=True)
class ImmutableIntelligenceFeatureProjectionEvidenceVerifier:
    """Verify all P4 source receipts and its immutable market-context DatasetVersion.

    A successful call returns a freshly reconstructed P4 collection receipt.
    Callers should publish that returned instance, not a caller-owned DTO, so
    the verification and later P1 adapter consume the same canonical values.
    """

    artifact_store: ArtifactStore

    def __post_init__(self) -> None:
        if type(self.artifact_store) is not ArtifactStore:
            raise IntelligenceFeatureProjectionEvidenceError(
                "artifact_store must be the exact immutable ArtifactStore"
            )

    def verify(
        self,
        projection: VersionedIntelligenceFeatureProjection,
    ) -> VersionedIntelligenceFeatureProjection:
        """Fail closed unless every source and context evidence binding replays exactly."""

        if type(projection) is not VersionedIntelligenceFeatureProjection:
            raise IntelligenceFeatureProjectionEvidenceError(
                "projection must be a VersionedIntelligenceFeatureProjection"
            )
        canonical_projection = self._canonical_projection(projection)
        for observation in canonical_projection.observations:
            self._verify_observation(cast(_EvidenceBoundObservation, observation))
        return canonical_projection

    @staticmethod
    def _canonical_projection(
        projection: VersionedIntelligenceFeatureProjection,
    ) -> VersionedIntelligenceFeatureProjection:
        try:
            return VersionedIntelligenceFeatureProjection(
                projection_version=projection.projection_version,
                projection_hash=projection.projection_hash,
                observations=projection.observations,
            )
        except (AttributeError, IntelligenceFeatureProjectionError, TypeError) as exc:
            raise IntelligenceFeatureProjectionEvidenceError(
                "P4 projection receipt identity is invalid"
            ) from exc

    def _verify_observation(self, observation: _EvidenceBoundObservation) -> None:
        available_at = _time(observation.available_at, "projection observation available_at")
        try:
            source_receipts = _source_receipt_hashes(
                observation.source_publication_receipt_hashes
            )
        except AttributeError as exc:
            raise IntelligenceFeatureProjectionEvidenceError(
                "P4 observation must retain individual source publication receipt hashes"
            ) from exc
        try:
            event_evidence = _source_event_evidence(observation.event_evidence)
        except AttributeError as exc:
            raise IntelligenceFeatureProjectionEvidenceError(
                "P4 observation must retain individual event evidence records"
            ) from exc
        expected_source_receipts = tuple(
            sorted(
                {
                    item.source_publication_receipt_hash
                    for item in event_evidence
                }
            )
        )
        if source_receipts != expected_source_receipts:
            raise IntelligenceFeatureProjectionEvidenceError(
                "P4 observation source receipt list does not exactly bind retained event evidence"
            )
        for evidence in event_evidence:
            self._verify_source_evidence(
                evidence,
                projection_available_at=available_at,
            )

        context_receipt_hash = _sha256(
            observation.context_publication_receipt_hash,
            "context_publication_receipt_hash",
        )
        context_receipt = self._load_receipt(
            context_receipt_hash,
            receipt_role="market context",
        )
        _assert_research_safe_receipt(
            context_receipt,
            receipt_hash=context_receipt_hash,
            receipt_role="market context",
            available_at=available_at,
        )

        context_dataset_hash = _sha256(
            observation.context_dataset_version_hash,
            "context_dataset_version_hash",
        )
        context_replay = self._replay_context_dataset(context_dataset_hash)
        if context_replay.dataset_version.version_hash != context_dataset_hash:
            raise IntelligenceFeatureProjectionEvidenceError(
                "market-context DatasetVersion identity does not match its requested hash"
            )
        context_scope = context_receipt.authorization.get("scope")
        if (
            type(context_scope) is not dict
            or context_scope.get("dataset_id") != context_replay.dataset_version.dataset_id
        ):
            raise IntelligenceFeatureProjectionEvidenceError(
                "market-context receipt must exactly bind the replayed DatasetVersion dataset_id"
            )
        if context_replay.dataset_version.available_at > available_at:
            raise IntelligenceFeatureProjectionEvidenceError(
                "market-context DatasetVersion is available after projection availability"
            )
        if len(context_replay.artifacts) != 1:
            raise IntelligenceFeatureProjectionEvidenceError(
                "market-context DatasetVersion must contain exactly one immutable normalized artifact"
            )
        context_artifact = context_replay.artifacts[0]
        if context_artifact.stored.snapshot.kind is not ArtifactKind.NORMALIZED:
            raise IntelligenceFeatureProjectionEvidenceError(
                "market-context artifact must be one immutable normalized P1 artifact"
            )
        if context_artifact.stored.snapshot.available_at > available_at:
            raise IntelligenceFeatureProjectionEvidenceError(
                "market-context artifact is available after projection availability"
            )
        if context_artifact.stored.publication_authorization_hash != context_receipt_hash:
            raise IntelligenceFeatureProjectionEvidenceError(
                "market-context DatasetVersion artifacts must uniformly bind the context receipt"
            )
        context_artifact_snapshot_hash = _sha256(
            observation.context_artifact_snapshot_hash,
            "context_artifact_snapshot_hash",
        )
        if (
            context_artifact.stored.snapshot.snapshot_hash
            != context_artifact_snapshot_hash
        ):
            raise IntelligenceFeatureProjectionEvidenceError(
                "market-context artifact snapshot is not a member of the replayed DatasetVersion"
            )
        self._verify_context_content(
            observation=observation,
            context_artifact=context_artifact,
            context_dataset_version_hash=context_dataset_hash,
            context_publication_receipt_hash=context_receipt_hash,
            projection_available_at=available_at,
        )

    @staticmethod
    def _verify_context_content(
        *,
        observation: _EvidenceBoundObservation,
        context_artifact: ArtifactReplay,
        context_dataset_version_hash: str,
        context_publication_receipt_hash: str,
        projection_available_at: datetime,
    ) -> None:
        """Bind a P4 context identity to one unique normalized P1 row."""

        snapshots = _context_snapshots_from_artifact(
            context_artifact.payload,
            context_dataset_version_hash=context_dataset_version_hash,
        )
        context_artifact_snapshot_hash = _sha256(
            observation.context_artifact_snapshot_hash,
            "context_artifact_snapshot_hash",
        )
        expected_commitment = _sha256(
            observation.context_content_commitment_hash,
            "context_content_commitment_hash",
        )
        candidates: list[AuthorizedMarketContext] = []
        for snapshot in snapshots:
            try:
                candidate = AuthorizedMarketContext(
                    market_context=snapshot,
                    context_dataset_version_hash=context_dataset_version_hash,
                    context_publication_receipt_hash=(
                        context_publication_receipt_hash
                    ),
                    context_artifact_snapshot_hash=context_artifact_snapshot_hash,
                )
            except IntelligenceFeatureProjectionError as exc:
                raise IntelligenceFeatureProjectionEvidenceError(
                    "market-context normalized artifact cannot reconstruct a bound P4 context"
                ) from exc
            if candidate.context_content_commitment_hash == expected_commitment:
                candidates.append(candidate)
        if not candidates:
            raise IntelligenceFeatureProjectionEvidenceError(
                "market-context DatasetVersion does not contain the exact P4 context content row"
            )
        if len(candidates) != 1:
            raise IntelligenceFeatureProjectionEvidenceError(
                "market-context DatasetVersion contains ambiguous P4 context content rows"
            )
        bound_context = candidates[0]
        expected_identity = _sha256(
            observation.context_identity_hash,
            "context_identity_hash",
        )
        if bound_context.context_identity_hash != expected_identity:
            raise IntelligenceFeatureProjectionEvidenceError(
                "market-context content row does not exactly bind the P4 context identity"
            )
        event_time = _time(observation.event_time, "projection observation event_time")
        if bound_context.market_context.available_at > projection_available_at:
            raise IntelligenceFeatureProjectionEvidenceError(
                "market-context content row is available after projection availability"
            )
        if bound_context.market_context.as_of > event_time:
            raise IntelligenceFeatureProjectionEvidenceError(
                "market-context content row as_of is after the Event time"
            )

    def _verify_source_evidence(
        self,
        evidence: EventEvidenceAvailability,
        *,
        projection_available_at: datetime,
    ) -> None:
        """Prove one Event evidence tuple against exactly one raw P1 artifact.

        A source publication receipt only proves authorization.  It cannot by
        itself prove that a particular document, content hash, or character
        span was actually persisted.  The P4 receipt therefore retains an
        exact raw-artifact snapshot hash.  We replay that immutable snapshot
        here, bind it to the same receipt, and validate the evidence span
        against its UTF-8 document payload.  No payload is returned or passed
        to P2.
        """

        evidence_available_at = _time(
            evidence.available_at,
            "event evidence available_at",
        )
        if evidence_available_at > projection_available_at:
            raise IntelligenceFeatureProjectionEvidenceError(
                "event evidence is available after projection availability"
            )
        receipt_hash = _sha256(
            evidence.source_publication_receipt_hash,
            "event evidence source_publication_receipt_hash",
        )
        receipt = self._load_receipt(
            receipt_hash,
            receipt_role="source evidence",
        )
        _assert_research_safe_receipt(
            receipt,
            receipt_hash=receipt_hash,
            receipt_role="source evidence",
            available_at=evidence_available_at,
        )
        snapshot_hash = _sha256(
            evidence.source_artifact_snapshot_hash,
            "event evidence source_artifact_snapshot_hash",
        )
        stored = self._load_source_artifact(snapshot_hash)
        snapshot = stored.snapshot
        if snapshot.kind is not ArtifactKind.RAW:
            raise IntelligenceFeatureProjectionEvidenceError(
                "source evidence must bind an immutable raw document artifact"
            )
        if stored.publication_authorization_hash != receipt_hash:
            raise IntelligenceFeatureProjectionEvidenceError(
                "source evidence artifact must exactly bind its publication receipt"
            )
        if snapshot.artifact_id != evidence.document_id:
            raise IntelligenceFeatureProjectionEvidenceError(
                "source evidence artifact document_id does not match the Event evidence"
            )
        content_hash = _sha256(evidence.content_hash, "event evidence content_hash")
        if snapshot.content_hash != content_hash:
            raise IntelligenceFeatureProjectionEvidenceError(
                "source evidence artifact content_hash does not match the Event evidence"
            )
        if snapshot.available_at > evidence_available_at:
            raise IntelligenceFeatureProjectionEvidenceError(
                "source evidence artifact is available after Event evidence availability"
            )
        payload = self._read_source_payload(snapshot_hash)
        if content_sha256(payload, field_name="source evidence payload") != content_hash:
            raise IntelligenceFeatureProjectionEvidenceError(
                "source evidence payload content_hash does not match the Event evidence"
            )
        try:
            document_text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IntelligenceFeatureProjectionEvidenceError(
                "source evidence payload must be a UTF-8 document for character-span verification"
            ) from exc
        if evidence.span_end > len(document_text):
            raise IntelligenceFeatureProjectionEvidenceError(
                "source evidence span is outside the immutable document payload"
            )

    def _load_source_artifact(self, snapshot_hash: str) -> StoredArtifact:
        try:
            return self.artifact_store.load_artifact(snapshot_hash)
        except (ArtifactStoreError, OSError) as exc:
            raise IntelligenceFeatureProjectionEvidenceError(
                "source evidence immutable artifact cannot be resolved"
            ) from exc

    def _read_source_payload(self, snapshot_hash: str) -> bytes:
        try:
            return self.artifact_store.read_payload(snapshot_hash)
        except (ArtifactStoreError, OSError) as exc:
            raise IntelligenceFeatureProjectionEvidenceError(
                "source evidence immutable artifact payload cannot be replayed"
            ) from exc

    def _load_receipt(
        self,
        receipt_hash: str,
        *,
        receipt_role: str,
    ) -> StoredPublicationAuthorization:
        try:
            return self.artifact_store.load_publication_authorization(receipt_hash)
        except (ArtifactStoreError, OSError) as exc:
            raise IntelligenceFeatureProjectionEvidenceError(
                f"{receipt_role} immutable receipt cannot be resolved"
            ) from exc

    def _replay_context_dataset(self, dataset_version_hash: str) -> DatasetReplay:
        try:
            return self.artifact_store.replay_dataset_version(dataset_version_hash)
        except (ArtifactStoreError, OSError) as exc:
            raise IntelligenceFeatureProjectionEvidenceError(
                "market-context DatasetVersion cannot be replayed"
            ) from exc
