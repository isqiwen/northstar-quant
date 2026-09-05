"""Atomic publication and fail-closed resolution of logical snapshots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from northstar_quant.data.catalog.models import (
    CanonicalBar,
    DataSeries,
    DatasetSnapshotImportQualityPin,
    DatasetSnapshotManifest,
    DatasetSnapshotMember,
    DatasetSnapshotPartition,
    DatasetSnapshotSeriesQualityPin,
    FuturesContract,
    FuturesProduct,
    ImportQualityEvaluation,
    ImportRecord,
    ImportRun,
    QualityEvaluation,
    TradingCalendar,
)
from northstar_quant.data.observations.revisions import (
    OBSERVATION_REVISION_SCHEMA_VERSION,
    ObservationRevisionError,
)
from northstar_quant.data.observations.service import select_point_in_time_revisions
from northstar_quant.data.quality.evaluations import (
    DAILY_QUALITY_EVALUATION_SCOPE,
    DAILY_QUALITY_RULE_SET_NAME,
    DAILY_QUALITY_RULE_SET_VERSION,
    IMPORT_QUALITY_RULE_SET_NAME,
    IMPORT_QUALITY_RULE_SET_VERSION,
    MINUTE_QUALITY_EVALUATION_SCOPE,
    MINUTE_QUALITY_RULE_SET_NAME,
    MINUTE_QUALITY_RULE_SET_VERSION,
    DailyQualityEvaluationError,
    ImportQualityCurrentState,
    ImportQualityEvaluationError,
    MinuteQualityEvaluationError,
)
from northstar_quant.data.quality.import_applicability_service import (
    current_import_quality_state_for_evaluation,
)
from northstar_quant.data.quality.minute_service import current_minute_quality_input_fingerprint
from northstar_quant.data.quality.service import current_daily_quality_input_fingerprint
from northstar_quant.data.snapshots.membership import (
    SnapshotMembershipTree,
    snapshot_member_leaf_hash,
)
from northstar_quant.data.snapshots.publication import (
    MAX_SNAPSHOT_MEMBERS,
    MAX_SNAPSHOT_REVISION_ROWS,
    SNAPSHOT_CANONICAL_SCHEMA_VERSION,
    SNAPSHOT_DATASET_KIND,
    SNAPSHOT_MANIFEST_SCHEMA_VERSION,
    DatasetSnapshotPublicationError,
    DatasetSnapshotPublicationResult,
    DatasetSnapshotResolutionError,
    PublishDatasetSnapshotCommand,
    SnapshotImportQualityPinSelection,
    SnapshotPartitionMetadata,
    SnapshotPartitionSelection,
    validate_publish_dataset_snapshot_command,
)


@dataclass(frozen=True)
class ResolvedDatasetSnapshotMember:
    """One current canonical row proven to still match immutable membership."""

    partition_id: UUID
    ordinal: int
    canonical_bar: CanonicalBar
    event_time: datetime
    available_at: datetime
    canonical_bar_fingerprint: str


@dataclass(frozen=True)
class ResolvedDatasetSnapshotMembership:
    """One already-verified storage-2 membership tree keyed by partition."""

    partition_id: UUID
    tree: SnapshotMembershipTree


@dataclass(frozen=True)
class ResolvedDatasetSnapshot:
    """A fully verified snapshot for future bounded read/export adapters."""

    manifest: DatasetSnapshotManifest
    members: tuple[ResolvedDatasetSnapshotMember, ...]
    memberships: tuple[ResolvedDatasetSnapshotMembership, ...]


@dataclass(frozen=True)
class _PreparedMember:
    canonical_bar: CanonicalBar
    event_time: datetime
    available_at: datetime
    fingerprint: str


@dataclass(frozen=True)
class _PreparedSeriesPin:
    evaluation: QualityEvaluation
    selection: SnapshotPartitionSelection


@dataclass(frozen=True)
class _PreparedPartition:
    selection: SnapshotPartitionSelection
    metadata: SnapshotPartitionMetadata
    members: tuple[_PreparedMember, ...]
    quality_pin: _PreparedSeriesPin
    membership_hash: str
    content_hash: str


@dataclass(frozen=True)
class _PreparedImportPin:
    selection: SnapshotImportQualityPinSelection
    import_run: ImportRun
    evaluation: ImportQualityEvaluation


def _freeze_snapshot_partition_metadata(series: DataSeries) -> SnapshotPartitionMetadata:
    """Copy all reader-visible OHLCV interpretation semantics at publication."""

    contract = series.contract
    calendar = series.calendar
    if contract is None or calendar is None or contract.product is None:
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_PARTITION_METADATA_MISSING",
            "the selected data series lacks complete contract or calendar metadata",
        )
    product = contract.product
    exchange = product.exchange
    if exchange is None or calendar.exchange is None:
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_PARTITION_METADATA_MISSING",
            "the selected data series lacks complete exchange metadata",
        )
    if (
        series.contract_id != contract.id
        or series.calendar_id != calendar.id
        or calendar.exchange_id != exchange.id
        or calendar.exchange.id != exchange.id
        or product.exchange_id != exchange.id
    ):
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_PARTITION_IDENTITY_MISMATCH",
            "the selected data series has inconsistent contract, exchange, or calendar identity",
        )
    if (
        series.kind != "OHLCV"
        or series.interval not in {"1m", "1d"}
        or series.adjustment != "RAW"
        or series.timestamp_convention != "BAR_START"
    ):
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_PARTITION_SEMANTICS_UNSUPPORTED",
            "the selected data series does not use supported canonical OHLCV semantics",
        )
    if not isinstance(series.price_scale, int) or not 0 <= series.price_scale <= 12:
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_PARTITION_SCALE_INVALID",
            "the selected data series has an invalid price scale",
        )
    if not isinstance(series.quantity_scale, int) or not 0 <= series.quantity_scale <= 12:
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_PARTITION_SCALE_INVALID",
            "the selected data series has an invalid quantity scale",
        )
    if series.volume_unit is None or series.turnover_currency is None:
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_PARTITION_UNIT_CONFIGURATION_MISSING",
            "the selected data series lacks required canonical volume or turnover units",
        )

    metadata = SnapshotPartitionMetadata(
        series_id=series.id,
        contract_id=contract.id,
        contract_code=contract.contract_code,
        product_code=product.code,
        exchange_code=exchange.code,
        exchange_timezone_name=exchange.timezone_name,
        calendar_id=calendar.id,
        calendar_code=calendar.code,
        calendar_revision=calendar.revision,
        calendar_timezone_name=calendar.timezone_name,
        series_kind=series.kind,
        interval=series.interval,
        adjustment=series.adjustment,
        timestamp_convention=series.timestamp_convention,
        price_scale=series.price_scale,
        quantity_scale=series.quantity_scale,
        price_currency=product.currency,
        price_tick=product.price_tick,
        contract_multiplier=product.contract_multiplier,
        quantity_unit=product.quantity_unit,
        volume_unit=series.volume_unit,
        turnover_currency=series.turnover_currency,
    )
    _assert_snapshot_partition_metadata(
        metadata,
        error_type=DatasetSnapshotPublicationError,
        error_code="SNAPSHOT_PARTITION_METADATA_INVALID",
    )
    return metadata


class DatasetSnapshotPublicationService:
    """Publish an explicit logical snapshot in one authority transaction.

    The service never selects a convenient quality result, mutates canonical
    facts, reads source bytes, writes an export, or creates a latest pointer.
    It materializes immutable membership and exact quality evidence only after all
    required eligibility and applicability checks pass in one repeatable view.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def publish(self, command: PublishDatasetSnapshotCommand) -> DatasetSnapshotPublicationResult:
        """Persist or safely replay one exact snapshot-publication intent."""

        command = validate_publish_dataset_snapshot_command(command)
        _require_clean_idle_session(self._session)
        request_fingerprint = _request_fingerprint(command)
        existing = self._session.scalar(
            select(DatasetSnapshotManifest).where(
                DatasetSnapshotManifest.idempotency_key == command.idempotency_key
            )
        )
        if existing is not None:
            return self._replay_or_reject(existing, request_fingerprint)

        # End the optimistic lookup before fixing the authoritative view that
        # supplies canonical membership, import summaries, and quality applicability.
        self._session.rollback()
        self._begin_consistent_snapshot()
        try:
            existing = self._session.scalar(
                select(DatasetSnapshotManifest).where(
                    DatasetSnapshotManifest.idempotency_key == command.idempotency_key
                )
            )
            if existing is not None:
                return self._replay_or_reject(existing, request_fingerprint)

            _assert_cutoff_is_not_after_snapshot(command.available_at_cutoff, self._session)
            prepared_partitions = self._prepare_partitions(command)
            prepared_import_pins = self._prepare_import_pins(
                command.import_quality_pins,
                prepared_partitions,
            )
            content_hash = _manifest_content_hash(
                available_at_cutoff=command.available_at_cutoff,
                partitions=prepared_partitions,
                import_pins=prepared_import_pins,
            )
            manifest = DatasetSnapshotManifest(
                manifest_schema_version=SNAPSHOT_MANIFEST_SCHEMA_VERSION,
                dataset_kind=SNAPSHOT_DATASET_KIND,
                canonical_schema_version=SNAPSHOT_CANONICAL_SCHEMA_VERSION,
                available_at_cutoff=command.available_at_cutoff,
                request_fingerprint=request_fingerprint,
                content_hash=content_hash,
                partition_count=len(prepared_partitions),
                member_count=sum(len(partition.members) for partition in prepared_partitions),
                import_quality_pin_count=len(prepared_import_pins),
                series_quality_pin_count=len(prepared_partitions),
                idempotency_key=command.idempotency_key,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            )
            self._session.add(manifest)
            self._session.flush()

            self._persist_import_pins(manifest, prepared_import_pins)
            self._persist_partitions(manifest, prepared_partitions)
            self._session.commit()
            return _publication_result(manifest, replayed=False)
        except IntegrityError as error:
            self._session.rollback()
            concurrent = self._session.scalar(
                select(DatasetSnapshotManifest).where(
                    DatasetSnapshotManifest.idempotency_key == command.idempotency_key
                )
            )
            if concurrent is not None:
                return self._replay_or_reject(concurrent, request_fingerprint)
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_PUBLICATION_RESERVATION_CONFLICT",
                "the snapshot-publication reservation conflicted; retry the same command safely",
            ) from error
        except BaseException:
            self._session.rollback()
            raise

    def _begin_consistent_snapshot(self) -> None:
        self._session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))

    def _prepare_partitions(
        self, command: PublishDatasetSnapshotCommand
    ) -> tuple[_PreparedPartition, ...]:
        prepared: list[_PreparedPartition] = []
        member_count = 0
        for selection in sorted(command.partitions, key=lambda item: str(item.series_id)):
            series = self._load_series(selection.series_id)
            metadata = _freeze_snapshot_partition_metadata(series)
            members = self._load_members_for_selection(
                series, selection, command.available_at_cutoff
            )
            member_count += len(members)
            if member_count > MAX_SNAPSHOT_MEMBERS:
                raise DatasetSnapshotPublicationError(
                    "SNAPSHOT_MEMBER_LIMIT_EXCEEDED",
                    f"a snapshot may contain at most {MAX_SNAPSHOT_MEMBERS} canonical members",
                )
            evaluation = self._load_and_verify_series_quality(
                selection=selection,
                series=series,
                available_at_cutoff=command.available_at_cutoff,
            )
            membership_hash = _membership_hash(members)
            quality_pin = _PreparedSeriesPin(evaluation=evaluation, selection=selection)
            content_hash = _partition_content_hash(
                selection=selection,
                metadata=metadata,
                members=members,
                membership_hash=membership_hash,
                quality_pin=quality_pin,
            )
            prepared.append(
                _PreparedPartition(
                    selection=selection,
                    metadata=metadata,
                    members=members,
                    quality_pin=quality_pin,
                    membership_hash=membership_hash,
                    content_hash=content_hash,
                )
            )
        return tuple(prepared)

    def _load_series(self, series_id: UUID) -> DataSeries:
        series = self._session.scalar(
            select(DataSeries)
            .where(DataSeries.id == series_id)
            .options(
                joinedload(DataSeries.calendar).joinedload(TradingCalendar.exchange),
                joinedload(DataSeries.contract)
                .joinedload(FuturesContract.product)
                .joinedload(FuturesProduct.exchange),
            )
        )
        if series is None:
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_SERIES_NOT_FOUND",
                "the requested data series does not exist",
            )
        if series.interval not in {"1d", "1m"}:
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_SERIES_INTERVAL_UNSUPPORTED",
                "the requested data series does not use a supported canonical interval",
            )
        return series

    def _load_members_for_selection(
        self,
        series: DataSeries,
        selection: SnapshotPartitionSelection,
        available_at_cutoff: datetime,
    ) -> tuple[_PreparedMember, ...]:
        candidates = tuple(
            self._session.scalars(
                select(CanonicalBar)
                .where(
                    CanonicalBar.series_id == series.id,
                    CanonicalBar.trading_day.between(
                        selection.from_trading_day, selection.to_trading_day
                    ),
                )
                .order_by(CanonicalBar.trading_day, CanonicalBar.event_time, CanonicalBar.id)
                .limit(MAX_SNAPSHOT_REVISION_ROWS + 1)
            ).all()
        )
        try:
            effective = select_point_in_time_revisions(
                candidates,
                as_of=available_at_cutoff,
                session=self._session,
                retain_earliest_future=False,
            )
        except ObservationRevisionError as error:
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_OBSERVATION_REVISION_CHAIN_INVALID",
                "a selected observation revision chain cannot be interpreted safely",
            ) from error
        selected: list[_PreparedMember] = []
        for bar in effective:
            event_time = _canonical_bar_timestamp(bar.event_time)
            available_at = _canonical_bar_timestamp(bar.available_at)
            if bar.import_run_id is None:
                raise DatasetSnapshotPublicationError(
                    "SNAPSHOT_MEMBER_IMPORT_LINEAGE_MISSING",
                    "every selected canonical member must retain an import-run identity",
                )
            selected.append(
                _PreparedMember(
                    canonical_bar=bar,
                    event_time=event_time,
                    available_at=available_at,
                    fingerprint=_canonical_bar_fingerprint(bar, self._session),
                )
            )
        if len(candidates) > MAX_SNAPSHOT_REVISION_ROWS:
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_REVISION_SCAN_LIMIT_EXCEEDED",
                "one series selection may inspect at most "
                f"{MAX_SNAPSHOT_REVISION_ROWS} retained observation revisions",
            )
        if len(effective) > MAX_SNAPSHOT_MEMBERS:
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_MEMBER_LIMIT_EXCEEDED",
                "one series selection may contain at most "
                f"{MAX_SNAPSHOT_MEMBERS} point-in-time members",
            )
        if not selected:
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_SELECTION_EMPTY",
                "the explicit series/trading-day selection has no canonical "
                "members visible at cutoff",
            )
        return tuple(
            sorted(
                selected,
                key=lambda item: (item.event_time, item.canonical_bar.id),
            )
        )

    def _load_and_verify_series_quality(
        self,
        *,
        selection: SnapshotPartitionSelection,
        series: DataSeries,
        available_at_cutoff: datetime,
    ) -> QualityEvaluation:
        evaluation = self._session.get(QualityEvaluation, selection.quality_evaluation_id)
        if evaluation is None:
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_SERIES_QUALITY_NOT_FOUND",
                "the selected series-quality evaluation does not exist",
            )
        if evaluation.series_id != series.id:
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_SERIES_QUALITY_SERIES_MISMATCH",
                "the selected series-quality evaluation belongs to a different data series",
            )
        if (
            evaluation.calendar_id != series.calendar_id
            or evaluation.calendar_revision != series.calendar.revision
        ):
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_SERIES_QUALITY_CALENDAR_MISMATCH",
                "the selected series-quality evaluation does not match the "
                "current calendar revision",
            )
        if (
            evaluation.trading_day_from > selection.from_trading_day
            or evaluation.trading_day_to < selection.to_trading_day
        ):
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_SERIES_QUALITY_RANGE_INSUFFICIENT",
                "the selected series-quality evaluation does not cover the "
                "requested trading-day range",
            )
        if _as_utc(evaluation.as_of) != available_at_cutoff:
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_SERIES_QUALITY_CUTOFF_MISMATCH",
                "the selected series-quality evaluation must use the snapshot availability cutoff",
            )
        if evaluation.outcome not in {"PASS", "WARN"} or evaluation.delivery_gate != "ELIGIBLE":
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_SERIES_QUALITY_BLOCKED",
                "the selected series-quality evaluation is not eligible for delivery",
            )
        expected_rule = _expected_series_quality_rule(series.interval)
        if (
            evaluation.evaluation_scope,
            evaluation.rule_set_name,
            evaluation.rule_set_version,
        ) != expected_rule:
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_SERIES_QUALITY_RULE_UNSUPPORTED",
                "the selected series-quality evaluation does not match this series interval",
            )
        try:
            current_fingerprint = _current_series_quality_fingerprint(self._session, evaluation)
        except (DailyQualityEvaluationError, MinuteQualityEvaluationError) as error:
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_SERIES_QUALITY_INPUT_UNINTERPRETABLE",
                "the selected series-quality evaluation cannot be revalidated "
                "against current facts",
            ) from error
        if current_fingerprint != evaluation.input_fingerprint:
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_SERIES_QUALITY_REVALIDATION_REQUIRED",
                "the selected series-quality evaluation no longer matches current canonical inputs",
            )
        return evaluation

    def _prepare_import_pins(
        self,
        selections: Sequence[SnapshotImportQualityPinSelection],
        partitions: Sequence[_PreparedPartition],
    ) -> tuple[_PreparedImportPin, ...]:
        expected_run_series: dict[UUID, set[UUID]] = {}
        for partition in partitions:
            for member in partition.members:
                import_run_id = member.canonical_bar.import_run_id
                if import_run_id is None:  # pragma: no cover - checked during selection
                    raise DatasetSnapshotPublicationError(
                        "SNAPSHOT_MEMBER_IMPORT_LINEAGE_MISSING",
                        "every selected canonical member must retain an import-run identity",
                    )
                expected_run_series.setdefault(import_run_id, set()).add(
                    partition.metadata.series_id
                )
        command_pins = {selection.import_run_id: selection for selection in selections}
        if set(command_pins) != set(expected_run_series):
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_IMPORT_QUALITY_PIN_SET_MISMATCH",
                "the supplied import-quality pins must match selected member import runs exactly",
            )
        prepared: list[_PreparedImportPin] = []
        for import_run_id in sorted(command_pins, key=str):
            selection = command_pins[import_run_id]
            import_run = self._session.get(ImportRun, import_run_id)
            evaluation = self._session.get(
                ImportQualityEvaluation, selection.import_quality_evaluation_id
            )
            if import_run is None or evaluation is None:
                raise DatasetSnapshotPublicationError(
                    "SNAPSHOT_IMPORT_QUALITY_NOT_FOUND",
                    "a supplied import run or import-quality evaluation does not exist",
                )
            self._verify_import_quality_pin(
                import_run=import_run,
                evaluation=evaluation,
                expected_series=expected_run_series[import_run_id],
            )
            prepared.append(
                _PreparedImportPin(
                    selection=selection,
                    import_run=import_run,
                    evaluation=evaluation,
                )
            )
        return tuple(prepared)

    def _verify_import_quality_pin(
        self,
        *,
        import_run: ImportRun,
        evaluation: ImportQualityEvaluation,
        expected_series: set[UUID],
    ) -> None:
        if evaluation.import_run_id != import_run.id:
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_IMPORT_QUALITY_RUN_MISMATCH",
                "the import-quality evaluation does not belong to the supplied import run",
            )
        if (
            evaluation.rule_set_name != IMPORT_QUALITY_RULE_SET_NAME
            or evaluation.rule_set_version != IMPORT_QUALITY_RULE_SET_VERSION
        ):
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_IMPORT_QUALITY_RULE_UNSUPPORTED",
                "the import-quality evaluation does not use a supported immutable rule version",
            )
        if evaluation.outcome not in {"PASS", "WARN"} or evaluation.delivery_gate != "ELIGIBLE":
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_IMPORT_QUALITY_BLOCKED",
                "the supplied import-quality evaluation is not eligible for delivery",
            )
        if (
            import_run.status not in {"SUCCEEDED", "FAILED", "QUARANTINED"}
            or import_run.effect is None
            or import_run.status != evaluation.observed_status
            or import_run.effect != evaluation.observed_effect
        ):
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_IMPORT_RUN_STATE_DRIFT",
                "the supplied import run no longer matches its immutable quality conclusion",
            )
        if import_run.series_id is None or import_run.series_id not in expected_series:
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_IMPORT_RUN_SERIES_MISMATCH",
                "the supplied import run does not match selected canonical-member series",
            )
        if len(expected_series) != 1:
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_IMPORT_RUN_MULTISERIES_UNSUPPORTED",
                "a selected import run must map to exactly one canonical series",
            )
        if (
            import_run.rows_read != evaluation.rows_read
            or import_run.rows_accepted != evaluation.rows_accepted
            or import_run.rows_rejected != evaluation.rows_rejected
            or import_run.rows_inserted != evaluation.rows_inserted
            or import_run.rows_duplicate_identical != evaluation.rows_duplicate_identical
            or import_run.rows_conflicted != evaluation.rows_conflicted
        ):
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_IMPORT_RUN_COUNT_DRIFT",
                "the supplied import run counts no longer match its immutable quality conclusion",
            )
        record_count = self._session.scalar(
            select(func.count())
            .select_from(ImportRecord)
            .where(ImportRecord.import_run_id == import_run.id)
        )
        if record_count != evaluation.record_count:
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_IMPORT_RECORD_COUNT_DRIFT",
                "the supplied import-record count no longer matches its "
                "immutable quality conclusion",
            )
        try:
            current = _current_import_quality_state(self._session, evaluation)
        except ImportQualityEvaluationError as error:
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_IMPORT_QUALITY_INPUT_UNINTERPRETABLE",
                "the supplied import-quality evaluation cannot be revalidated "
                "against current durable evidence",
            ) from error
        if current.input_fingerprint != evaluation.input_fingerprint:
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_IMPORT_QUALITY_REVALIDATION_REQUIRED",
                "the supplied import-quality evaluation no longer matches current durable evidence",
            )
        if not _import_quality_state_matches_evaluation(current, evaluation):
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_IMPORT_QUALITY_APPLICABILITY_DRIFT",
                "the supplied import-quality evaluation no longer reproduces "
                "its current applicability",
            )

    def _persist_import_pins(
        self,
        manifest: DatasetSnapshotManifest,
        pins: Sequence[_PreparedImportPin],
    ) -> None:
        for pin in pins:
            evaluation = pin.evaluation
            self._session.add(
                DatasetSnapshotImportQualityPin(
                    manifest_id=manifest.id,
                    import_run_id=pin.import_run.id,
                    import_quality_evaluation_id=evaluation.id,
                    rule_set_name=evaluation.rule_set_name,
                    rule_set_version=evaluation.rule_set_version,
                    input_fingerprint=evaluation.input_fingerprint,
                    outcome=evaluation.outcome,
                    delivery_gate=evaluation.delivery_gate,
                )
            )

    def _persist_partitions(
        self,
        manifest: DatasetSnapshotManifest,
        partitions: Sequence[_PreparedPartition],
    ) -> None:
        for prepared in partitions:
            members = prepared.members
            metadata = prepared.metadata
            partition = DatasetSnapshotPartition(
                manifest_id=manifest.id,
                series_id=metadata.series_id,
                contract_id=metadata.contract_id,
                contract_code=metadata.contract_code,
                product_code=metadata.product_code,
                exchange_code=metadata.exchange_code,
                exchange_timezone_name=metadata.exchange_timezone_name,
                calendar_id=metadata.calendar_id,
                calendar_code=metadata.calendar_code,
                calendar_revision=metadata.calendar_revision,
                calendar_timezone_name=metadata.calendar_timezone_name,
                series_kind=metadata.series_kind,
                interval=metadata.interval,
                adjustment=metadata.adjustment,
                timestamp_convention=metadata.timestamp_convention,
                price_scale=metadata.price_scale,
                quantity_scale=metadata.quantity_scale,
                price_currency=metadata.price_currency,
                price_tick=metadata.price_tick,
                contract_multiplier=metadata.contract_multiplier,
                quantity_unit=metadata.quantity_unit,
                volume_unit=metadata.volume_unit,
                turnover_currency=metadata.turnover_currency,
                trading_day_from=prepared.selection.from_trading_day,
                trading_day_to=prepared.selection.to_trading_day,
                event_time_from=members[0].event_time,
                event_time_to=members[-1].event_time,
                row_count=len(members),
                membership_hash=prepared.membership_hash,
                content_hash=prepared.content_hash,
            )
            self._session.add(partition)
            self._session.flush()
            evaluation = prepared.quality_pin.evaluation
            self._session.add(
                DatasetSnapshotSeriesQualityPin(
                    partition_id=partition.id,
                    quality_evaluation_id=evaluation.id,
                    calendar_id=evaluation.calendar_id,
                    calendar_revision=evaluation.calendar_revision,
                    evaluation_scope=evaluation.evaluation_scope,
                    rule_set_name=evaluation.rule_set_name,
                    rule_set_version=evaluation.rule_set_version,
                    trading_day_from=evaluation.trading_day_from,
                    trading_day_to=evaluation.trading_day_to,
                    as_of=_as_utc(evaluation.as_of),
                    input_fingerprint=evaluation.input_fingerprint,
                    outcome=evaluation.outcome,
                    delivery_gate=evaluation.delivery_gate,
                )
            )
            for ordinal, member in enumerate(members):
                self._session.add(
                    DatasetSnapshotMember(
                        partition_id=partition.id,
                        canonical_bar_id=member.canonical_bar.id,
                        ordinal=ordinal,
                        event_time=member.event_time,
                        trading_day=member.canonical_bar.trading_day,
                        available_at=member.available_at,
                        canonical_bar_fingerprint=member.fingerprint,
                    )
                )

    def _replay_or_reject(
        self,
        manifest: DatasetSnapshotManifest,
        request_fingerprint: str,
    ) -> DatasetSnapshotPublicationResult:
        if manifest.request_fingerprint != request_fingerprint:
            self._session.rollback()
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_IDEMPOTENCY_KEY_REUSED",
                "the snapshot idempotency key was already used for a different intent",
            )
        manifest_id = manifest.id
        self._session.rollback()
        self._begin_consistent_read_view()
        try:
            verified = DatasetSnapshotResolutionService(self._session).resolve(manifest_id)
            result = _publication_result(verified.manifest, replayed=True)
        except DatasetSnapshotResolutionError as error:
            self._session.rollback()
            raise DatasetSnapshotPublicationError(
                "SNAPSHOT_PUBLICATION_INTEGRITY_FAILURE",
                "the immutable snapshot evidence could not be verified",
            ) from error
        except BaseException:
            self._session.rollback()
            raise
        self._session.rollback()
        return result

    def _begin_consistent_read_view(self) -> None:
        _require_clean_idle_session(self._session)
        self._session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))


class DatasetSnapshotResolutionService:
    """Resolve only stored membership and reject any canonical drift.

    This is intentionally an internal primitive. Read API and export
    adapters must call this verified path instead of reading canonical ranges.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve(self, snapshot_id: UUID) -> ResolvedDatasetSnapshot:
        """Resolve a snapshot while rendering corrupt canonical state safely.

        Publication helpers deliberately use ``DatasetSnapshotPublicationError``
        because they describe operator input.  Resolution can encounter the
        same malformed state only after publication, where it is immutable
        evidence drift and must be exposed as a controlled resolution failure.
        """

        try:
            return self._resolve_verified(snapshot_id)
        except DatasetSnapshotPublicationError as error:
            raise DatasetSnapshotResolutionError(
                "SNAPSHOT_CANONICAL_DATA_INVALID",
                "a canonical member cannot be interpreted safely for this immutable snapshot",
            ) from error

    def _resolve_verified(self, snapshot_id: UUID) -> ResolvedDatasetSnapshot:
        manifest = self._session.get(DatasetSnapshotManifest, snapshot_id)
        if manifest is None:
            raise DatasetSnapshotResolutionError(
                "SNAPSHOT_NOT_FOUND",
                "the requested immutable dataset snapshot does not exist",
            )
        if manifest.manifest_schema_version != SNAPSHOT_MANIFEST_SCHEMA_VERSION:
            raise DatasetSnapshotResolutionError(
                "SNAPSHOT_MANIFEST_SCHEMA_VERSION_UNSUPPORTED",
                "the immutable snapshot uses an unsupported storage manifest version",
            )
        partitions = tuple(
            self._session.scalars(
                select(DatasetSnapshotPartition)
                .where(DatasetSnapshotPartition.manifest_id == manifest.id)
                .order_by(DatasetSnapshotPartition.series_id)
            ).all()
        )
        import_pins = tuple(
            self._session.scalars(
                select(DatasetSnapshotImportQualityPin)
                .where(DatasetSnapshotImportQualityPin.manifest_id == manifest.id)
                .order_by(DatasetSnapshotImportQualityPin.import_run_id)
            ).all()
        )
        if (
            len(partitions) != manifest.partition_count
            or len(import_pins) != manifest.import_quality_pin_count
            or manifest.series_quality_pin_count != manifest.partition_count
        ):
            raise DatasetSnapshotResolutionError(
                "SNAPSHOT_MANIFEST_STRUCTURE_MISMATCH",
                "the immutable snapshot manifest has inconsistent child counts",
            )

        partition_ids = tuple(partition.id for partition in partitions)
        members_by_partition: dict[
            UUID,
            list[tuple[DatasetSnapshotMember, CanonicalBar | None]],
        ] = {partition_id: [] for partition_id in partition_ids}
        if partition_ids:
            all_member_rows = self._session.execute(
                select(DatasetSnapshotMember, CanonicalBar)
                .select_from(DatasetSnapshotMember)
                .outerjoin(
                    CanonicalBar,
                    DatasetSnapshotMember.canonical_bar_id == CanonicalBar.id,
                )
                .where(DatasetSnapshotMember.partition_id.in_(partition_ids))
                .order_by(DatasetSnapshotMember.partition_id, DatasetSnapshotMember.ordinal)
            )
            for member, bar in all_member_rows:
                partition_members = members_by_partition.get(member.partition_id)
                if partition_members is None:  # pragma: no cover - query scope guard
                    raise DatasetSnapshotResolutionError(
                        "SNAPSHOT_PARTITION_STRUCTURE_MISMATCH",
                        "the immutable snapshot partition has inconsistent membership",
                    )
                partition_members.append((member, bar))

        series_pins = tuple(
            self._session.scalars(
                select(DatasetSnapshotSeriesQualityPin).where(
                    DatasetSnapshotSeriesQualityPin.partition_id.in_(partition_ids)
                )
            ).all()
        )
        pins_by_partition = {pin.partition_id: pin for pin in series_pins}
        if len(series_pins) != len(partitions) or len(pins_by_partition) != len(partitions):
            raise DatasetSnapshotResolutionError(
                "SNAPSHOT_PARTITION_STRUCTURE_MISMATCH",
                "the immutable snapshot partition has inconsistent membership or quality pin",
            )

        resolved_members: list[ResolvedDatasetSnapshotMember] = []
        rebuilt_partitions: list[
            tuple[
                DatasetSnapshotPartition,
                tuple[DatasetSnapshotMember, ...],
                DatasetSnapshotSeriesQualityPin,
            ]
        ] = []
        for partition in partitions:
            partition_member_rows = tuple(members_by_partition[partition.id])
            members = tuple(member for member, _bar in partition_member_rows)
            pin = pins_by_partition.get(partition.id)
            if pin is None or len(members) != partition.row_count:
                raise DatasetSnapshotResolutionError(
                    "SNAPSHOT_PARTITION_STRUCTURE_MISMATCH",
                    "the immutable snapshot partition has inconsistent membership or quality pin",
                )
            if tuple(member.ordinal for member in members) != tuple(range(len(members))):
                raise DatasetSnapshotResolutionError(
                    "SNAPSHOT_MEMBER_ORDINAL_MISMATCH",
                    "the immutable snapshot partition has non-contiguous member ordering",
                )
            previous_member_order: tuple[datetime, UUID] | None = None
            for member, bar in partition_member_rows:
                if (
                    bar is None
                    or _canonical_bar_fingerprint(bar, self._session)
                    != member.canonical_bar_fingerprint
                ):
                    raise DatasetSnapshotResolutionError(
                        "SNAPSHOT_MEMBER_FINGERPRINT_MISMATCH",
                        "a canonical member no longer matches the immutable snapshot fingerprint",
                    )
                event_time = _canonical_bar_timestamp(bar.event_time)
                available_at = _canonical_bar_timestamp(bar.available_at)
                member_order = (event_time, bar.id)
                if previous_member_order is not None and member_order <= previous_member_order:
                    raise DatasetSnapshotResolutionError(
                        "SNAPSHOT_MEMBER_ORDER_MISMATCH",
                        "storage-2 membership is not ordered by event time and canonical identity",
                    )
                previous_member_order = member_order
                if (
                    event_time != _as_utc(member.event_time)
                    or bar.trading_day != member.trading_day
                    or available_at != _as_utc(member.available_at)
                ):
                    raise DatasetSnapshotResolutionError(
                        "SNAPSHOT_MEMBER_SELECTOR_MISMATCH",
                        "a canonical member no longer matches immutable snapshot selectors",
                    )
                resolved_members.append(
                    ResolvedDatasetSnapshotMember(
                        partition_id=partition.id,
                        ordinal=member.ordinal,
                        canonical_bar=bar,
                        event_time=event_time,
                        available_at=available_at,
                        canonical_bar_fingerprint=member.canonical_bar_fingerprint,
                    )
                )
            rebuilt_partitions.append((partition, members, pin))
        if len(resolved_members) != manifest.member_count:
            raise DatasetSnapshotResolutionError(
                "SNAPSHOT_MEMBER_COUNT_MISMATCH",
                "the immutable snapshot manifest has an inconsistent member count",
            )
        verified_memberships = _assert_persisted_hashes(
            manifest=manifest,
            partitions=rebuilt_partitions,
            import_pins=import_pins,
            session=self._session,
        )
        return ResolvedDatasetSnapshot(
            manifest=manifest,
            members=tuple(resolved_members),
            memberships=verified_memberships,
        )


def _require_clean_idle_session(session: Session) -> None:
    if session.in_transaction() or session.new or session.dirty or session.deleted:
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_PUBLICATION_SESSION_NOT_CLEAN",
            "snapshot publication requires a clean, idle dedicated database session",
        )


def _assert_cutoff_is_not_after_snapshot(available_at_cutoff: datetime, session: Session) -> None:
    snapshot_now = session.scalar(select(func.current_timestamp()))
    if not isinstance(snapshot_now, datetime):  # pragma: no cover - database result guard
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_AUTHORITY_TIME_UNAVAILABLE",
            "the authoritative database did not return an interpretable current timestamp",
        )
    if available_at_cutoff > _as_utc(snapshot_now):
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_CUTOFF_FUTURE",
            "available_at_cutoff must not be after the authoritative database snapshot time",
        )


def _expected_series_quality_rule(interval: str) -> tuple[str, str, str]:
    if interval == "1d":
        return (
            DAILY_QUALITY_EVALUATION_SCOPE,
            DAILY_QUALITY_RULE_SET_NAME,
            DAILY_QUALITY_RULE_SET_VERSION,
        )
    return (
        MINUTE_QUALITY_EVALUATION_SCOPE,
        MINUTE_QUALITY_RULE_SET_NAME,
        MINUTE_QUALITY_RULE_SET_VERSION,
    )


def _current_series_quality_fingerprint(session: Session, evaluation: QualityEvaluation) -> str:
    if evaluation.evaluation_scope == DAILY_QUALITY_EVALUATION_SCOPE:
        return current_daily_quality_input_fingerprint(session, evaluation)
    if evaluation.evaluation_scope == MINUTE_QUALITY_EVALUATION_SCOPE:
        return current_minute_quality_input_fingerprint(session, evaluation)
    raise DatasetSnapshotPublicationError(
        "SNAPSHOT_SERIES_QUALITY_RULE_UNSUPPORTED",
        "the selected series-quality evaluation has an unsupported scope",
    )


def _current_import_quality_state(
    session: Session, evaluation: ImportQualityEvaluation
) -> ImportQualityCurrentState:
    """Dispatch only to the exact stored import-quality protocol.

    Publication must never substitute a convenient rule revision. Each owning quality
    module recomputes its own bounded evidence and fingerprint in the caller's
    current transaction view; this small dispatcher deliberately has no write
    or transaction-management behavior.
    """

    return current_import_quality_state_for_evaluation(session, evaluation)


def _import_quality_state_matches_evaluation(
    current: ImportQualityCurrentState, evaluation: ImportQualityEvaluation
) -> bool:
    """Require the remaining outcome-bearing conclusion to stay applicable.

    Status/effect, row-count, and record-count drift checks deliberately occur
    before this helper so each failure keeps its precise public error code.
    """

    return (
        current.outcome == evaluation.outcome
        and current.delivery_gate == evaluation.delivery_gate
        and current.finding_count == evaluation.finding_count
    )


def _request_fingerprint(command: PublishDatasetSnapshotCommand) -> str:
    return _hash_payload(
        {
            "protocol": "dataset_snapshot_publication_request/1.0.0",
            "available_at_cutoff": _render_timestamp(command.available_at_cutoff),
            "partitions": [
                {
                    "series_id": str(selection.series_id),
                    "from_trading_day": selection.from_trading_day.isoformat(),
                    "to_trading_day": selection.to_trading_day.isoformat(),
                    "quality_evaluation_id": str(selection.quality_evaluation_id),
                }
                for selection in sorted(command.partitions, key=lambda item: str(item.series_id))
            ],
            "import_quality_pins": [
                {
                    "import_run_id": str(selection.import_run_id),
                    "import_quality_evaluation_id": str(selection.import_quality_evaluation_id),
                }
                for selection in sorted(
                    command.import_quality_pins, key=lambda item: str(item.import_run_id)
                )
            ],
            "correlation_id": command.correlation_id,
            "causation_id": command.causation_id,
        }
    )


def _canonical_bar_payload(
    bar: CanonicalBar,
    session: Session,
) -> dict[str, object]:
    if bar.normalized_payload_hash is None:
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_MEMBER_NORMALIZED_PAYLOAD_HASH_MISSING",
            "every published canonical member must retain its normalized payload identity",
        )
    if len(bar.normalized_payload_hash) != 64 or any(
        character not in "0123456789abcdef" for character in bar.normalized_payload_hash
    ):
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_MEMBER_NORMALIZED_PAYLOAD_HASH_INVALID",
            "every published canonical member must retain a lowercase SHA-256 payload identity",
        )
    return {
        "canonical_bar_id": str(bar.id),
        "observation_revision_schema_version": OBSERVATION_REVISION_SCHEMA_VERSION,
        "revision_number": bar.revision_number,
        "supersedes_observation_id": (
            str(bar.supersedes_canonical_bar_id)
            if bar.supersedes_canonical_bar_id is not None
            else None
        ),
        "series_id": str(bar.series_id),
        "import_run_id": str(bar.import_run_id) if bar.import_run_id is not None else None,
        "event_time": _render_timestamp(_canonical_bar_timestamp(bar.event_time)),
        "trading_day": bar.trading_day.isoformat(),
        "available_at": _render_timestamp(_canonical_bar_timestamp(bar.available_at)),
        "ingested_at": _render_timestamp(_authority_timestamp(bar.ingested_at)),
        "source_timezone_name": bar.source_timezone_name,
        "source_name": bar.source_name,
        "source_record_id": bar.source_record_id,
        "source_content_hash": bar.source_content_hash,
        "normalized_payload_hash": bar.normalized_payload_hash,
        "open_price": _render_decimal(bar.open_price),
        "high_price": _render_decimal(bar.high_price),
        "low_price": _render_decimal(bar.low_price),
        "close_price": _render_decimal(bar.close_price),
        "volume": _render_decimal(bar.volume),
        "turnover": _render_decimal(bar.turnover),
        "open_interest": _render_decimal(bar.open_interest),
    }


def _canonical_bar_fingerprint(
    bar: CanonicalBar,
    session: Session,
) -> str:
    return _hash_payload(_canonical_bar_payload(bar, session))


def _membership_hash(
    members: Sequence[_PreparedMember],
) -> str:
    return _ordered_membership_tree(members).content_hash


def _member_payload(member: _PreparedMember, ordinal: int) -> dict[str, object]:
    return {
        "ordinal": ordinal,
        "canonical_bar_id": str(member.canonical_bar.id),
        "event_time": _render_timestamp(member.event_time),
        "trading_day": member.canonical_bar.trading_day.isoformat(),
        "available_at": _render_timestamp(member.available_at),
        "canonical_bar_fingerprint": member.fingerprint,
    }


def _member_leaf_hash(member: _PreparedMember, ordinal: int) -> str:
    payload = _member_payload(member, ordinal)
    return snapshot_member_leaf_hash(
        ordinal=ordinal,
        event_time=str(payload["event_time"]),
        canonical_bar_fingerprint=str(payload["canonical_bar_fingerprint"]),
    )


def _ordered_membership_tree(members: Sequence[_PreparedMember]) -> SnapshotMembershipTree:
    previous_order: tuple[datetime, UUID] | None = None
    leaf_hashes: list[str] = []
    for ordinal, member in enumerate(members):
        member_order = (member.event_time, member.canonical_bar.id)
        if previous_order is not None and member_order <= previous_order:
            raise ValueError(
                "storage-2 membership must be ordered by event time and canonical identity"
            )
        previous_order = member_order
        leaf_hashes.append(_member_leaf_hash(member, ordinal))
    return SnapshotMembershipTree.build(leaf_hashes)


def _series_quality_pin_payload(evaluation: QualityEvaluation) -> dict[str, object]:
    return {
        "quality_evaluation_id": str(evaluation.id),
        "calendar_id": str(evaluation.calendar_id),
        "calendar_revision": evaluation.calendar_revision,
        "evaluation_scope": evaluation.evaluation_scope,
        "rule_set_name": evaluation.rule_set_name,
        "rule_set_version": evaluation.rule_set_version,
        "trading_day_from": evaluation.trading_day_from.isoformat(),
        "trading_day_to": evaluation.trading_day_to.isoformat(),
        "as_of": _render_timestamp(_as_utc(evaluation.as_of)),
        "input_fingerprint": evaluation.input_fingerprint,
        "outcome": evaluation.outcome,
        "delivery_gate": evaluation.delivery_gate,
    }


def _partition_content_hash(
    *,
    selection: SnapshotPartitionSelection,
    metadata: SnapshotPartitionMetadata,
    members: Sequence[_PreparedMember],
    membership_hash: str,
    quality_pin: _PreparedSeriesPin,
) -> str:
    return _hash_payload(
        {
            "protocol": "dataset_snapshot_partition/2.0.0",
            "metadata": _snapshot_partition_metadata_payload(metadata),
            "trading_day_from": selection.from_trading_day.isoformat(),
            "trading_day_to": selection.to_trading_day.isoformat(),
            "event_time_from": _render_timestamp(members[0].event_time),
            "event_time_to": _render_timestamp(members[-1].event_time),
            "row_count": len(members),
            "membership_hash": membership_hash,
            "series_quality_pin": _series_quality_pin_payload(quality_pin.evaluation),
        }
    )


def _snapshot_partition_metadata_payload(
    metadata: SnapshotPartitionMetadata,
) -> dict[str, object]:
    """Render the sealed partition interpretation in deterministic hash order."""

    payload: dict[str, object] = {
        "series_id": str(metadata.series_id),
        "contract_id": str(metadata.contract_id),
        "contract_code": metadata.contract_code,
        "product_code": metadata.product_code,
        "exchange_code": metadata.exchange_code,
        "exchange_timezone_name": metadata.exchange_timezone_name,
        "calendar_id": str(metadata.calendar_id),
        "calendar_code": metadata.calendar_code,
        "calendar_revision": metadata.calendar_revision,
        "calendar_timezone_name": metadata.calendar_timezone_name,
        "series_kind": metadata.series_kind,
        "interval": metadata.interval,
        "adjustment": metadata.adjustment,
        "timestamp_convention": metadata.timestamp_convention,
        "price_scale": metadata.price_scale,
        "quantity_scale": metadata.quantity_scale,
        "price_currency": metadata.price_currency,
        "price_tick": _render_decimal(metadata.price_tick),
        "contract_multiplier": _render_decimal(metadata.contract_multiplier),
        "quantity_unit": metadata.quantity_unit,
        "volume_unit": metadata.volume_unit,
        "turnover_currency": metadata.turnover_currency,
    }
    payload.update(
        {
            "open_interest_unit": metadata.volume_unit,
            "turnover_multiplier": "1",
        }
    )
    return payload


def _stored_snapshot_partition_metadata(
    partition: DatasetSnapshotPartition,
) -> SnapshotPartitionMetadata:
    """Rebuild only the persisted partition meaning; never read live catalog rows."""

    metadata = SnapshotPartitionMetadata(
        series_id=partition.series_id,
        contract_id=partition.contract_id,
        contract_code=partition.contract_code,
        product_code=partition.product_code,
        exchange_code=partition.exchange_code,
        exchange_timezone_name=partition.exchange_timezone_name,
        calendar_id=partition.calendar_id,
        calendar_code=partition.calendar_code,
        calendar_revision=partition.calendar_revision,
        calendar_timezone_name=partition.calendar_timezone_name,
        series_kind=partition.series_kind,
        interval=partition.interval,
        adjustment=partition.adjustment,
        timestamp_convention=partition.timestamp_convention,
        price_scale=partition.price_scale,
        quantity_scale=partition.quantity_scale,
        price_currency=partition.price_currency,
        price_tick=partition.price_tick,
        contract_multiplier=partition.contract_multiplier,
        quantity_unit=partition.quantity_unit,
        volume_unit=partition.volume_unit,
        turnover_currency=partition.turnover_currency,
    )
    _assert_snapshot_partition_metadata(
        metadata,
        error_type=DatasetSnapshotResolutionError,
        error_code="SNAPSHOT_PARTITION_METADATA_INVALID",
    )
    return metadata


def _assert_snapshot_partition_metadata(
    metadata: SnapshotPartitionMetadata,
    *,
    error_type: type[DatasetSnapshotPublicationError] | type[DatasetSnapshotResolutionError],
    error_code: str,
) -> None:
    """Fail closed when a frozen value cannot safely explain canonical rows."""

    code_values = (
        (metadata.contract_code, 32),
        (metadata.product_code, 24),
        (metadata.exchange_code, 16),
        (metadata.calendar_code, 32),
    )
    if (
        not isinstance(metadata.series_id, UUID)
        or not isinstance(metadata.contract_id, UUID)
        or not isinstance(metadata.calendar_id, UUID)
        or any(
            not isinstance(value, str)
            or value != value.strip()
            or not 1 <= len(value) <= maximum
            or value != value.upper()
            for value, maximum in code_values
        )
        or metadata.series_kind != "OHLCV"
        or metadata.interval not in {"1m", "1d"}
        or metadata.adjustment != "RAW"
        or metadata.timestamp_convention != "BAR_START"
        or not isinstance(metadata.calendar_revision, int)
        or metadata.calendar_revision <= 0
        or not isinstance(metadata.price_scale, int)
        or not 0 <= metadata.price_scale <= 12
        or not isinstance(metadata.quantity_scale, int)
        or not 0 <= metadata.quantity_scale <= 12
        or not _is_upper_currency_code(metadata.price_currency)
        or not _is_upper_currency_code(metadata.turnover_currency)
        or not isinstance(metadata.price_tick, Decimal)
        or metadata.price_tick <= 0
        or not isinstance(metadata.contract_multiplier, Decimal)
        or metadata.contract_multiplier <= 0
        or not isinstance(metadata.quantity_unit, str)
        or metadata.quantity_unit != metadata.quantity_unit.strip()
        or not 1 <= len(metadata.quantity_unit) <= 32
        or metadata.quantity_unit != metadata.quantity_unit.upper()
        or not isinstance(metadata.volume_unit, str)
        or metadata.volume_unit != metadata.volume_unit.strip()
        or not 1 <= len(metadata.volume_unit) <= 32
        or metadata.volume_unit != metadata.volume_unit.upper()
    ):
        raise error_type(
            error_code,
            "snapshot partition metadata is incomplete or uses unsupported canonical semantics",
        )
    if (
        not isinstance(metadata.exchange_timezone_name, str)
        or not isinstance(metadata.calendar_timezone_name, str)
        or metadata.exchange_timezone_name != metadata.exchange_timezone_name.strip()
        or metadata.calendar_timezone_name != metadata.calendar_timezone_name.strip()
        or not 1 <= len(metadata.exchange_timezone_name) <= 64
        or not 1 <= len(metadata.calendar_timezone_name) <= 64
        or metadata.exchange_timezone_name != metadata.calendar_timezone_name
    ):
        raise error_type(
            error_code,
            "snapshot partition metadata has incomplete or inconsistent "
            "exchange/calendar timezones",
        )
    try:
        ZoneInfo(metadata.exchange_timezone_name)
        ZoneInfo(metadata.calendar_timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise error_type(
            error_code,
            "snapshot partition metadata has an invalid exchange/calendar timezone",
        ) from error


def _is_upper_currency_code(value: object) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and len(value) == 3
        and value == value.upper()
    )


def _import_quality_pin_payload(evaluation: ImportQualityEvaluation) -> dict[str, object]:
    return {
        "import_run_id": str(evaluation.import_run_id),
        "import_quality_evaluation_id": str(evaluation.id),
        "rule_set_name": evaluation.rule_set_name,
        "rule_set_version": evaluation.rule_set_version,
        "input_fingerprint": evaluation.input_fingerprint,
        "outcome": evaluation.outcome,
        "delivery_gate": evaluation.delivery_gate,
    }


def _manifest_content_hash(
    *,
    available_at_cutoff: datetime,
    partitions: Sequence[_PreparedPartition],
    import_pins: Sequence[_PreparedImportPin],
) -> str:
    partition_payloads = [
        _prepared_manifest_partition_payload(partition)
        for partition in sorted(partitions, key=lambda item: str(item.metadata.series_id))
    ]
    return _hash_payload(
        {
            "protocol": f"dataset_snapshot_manifest/{SNAPSHOT_MANIFEST_SCHEMA_VERSION}",
            "manifest_schema_version": SNAPSHOT_MANIFEST_SCHEMA_VERSION,
            "dataset_kind": SNAPSHOT_DATASET_KIND,
            "canonical_schema_version": SNAPSHOT_CANONICAL_SCHEMA_VERSION,
            "available_at_cutoff": _render_timestamp(available_at_cutoff),
            "partitions": partition_payloads,
            "import_quality_pins": [
                _import_quality_pin_payload(pin.evaluation)
                for pin in sorted(import_pins, key=lambda item: str(item.import_run.id))
            ],
        }
    )


def _prepared_manifest_partition_payload(partition: _PreparedPartition) -> dict[str, object]:
    """Render the complete frozen partition meaning into the manifest root."""

    return {
        "metadata": _snapshot_partition_metadata_payload(partition.metadata),
        "trading_day_from": partition.selection.from_trading_day.isoformat(),
        "trading_day_to": partition.selection.to_trading_day.isoformat(),
        "event_time_from": _render_timestamp(partition.members[0].event_time),
        "event_time_to": _render_timestamp(partition.members[-1].event_time),
        "row_count": len(partition.members),
        "membership_hash": partition.membership_hash,
        "content_hash": partition.content_hash,
        "series_quality_pin": _series_quality_pin_payload(partition.quality_pin.evaluation),
    }


def _assert_persisted_hashes(
    *,
    manifest: DatasetSnapshotManifest,
    partitions: Sequence[
        tuple[
            DatasetSnapshotPartition,
            tuple[DatasetSnapshotMember, ...],
            DatasetSnapshotSeriesQualityPin,
        ]
    ],
    import_pins: Sequence[DatasetSnapshotImportQualityPin],
    session: Session,
) -> tuple[ResolvedDatasetSnapshotMembership, ...]:
    rebuilt: list[_PreparedPartition] = []
    verified_memberships: list[ResolvedDatasetSnapshotMembership] = []
    for partition, members, pin in partitions:
        metadata = _stored_snapshot_partition_metadata(partition)
        prepared_members: list[_PreparedMember] = []
        for member in members:
            bar = session.get(CanonicalBar, member.canonical_bar_id)
            if bar is None:  # pragma: no cover - FK prevents normal deletion
                raise DatasetSnapshotResolutionError(
                    "SNAPSHOT_MEMBER_MISSING",
                    "a snapshot member no longer references a canonical bar",
                )
            prepared_members.append(
                _PreparedMember(
                    canonical_bar=bar,
                    event_time=_as_utc(member.event_time),
                    available_at=_as_utc(member.available_at),
                    fingerprint=member.canonical_bar_fingerprint,
                )
            )
        membership_tree = _ordered_membership_tree(prepared_members)
        membership_hash = membership_tree.content_hash
        if membership_hash != partition.membership_hash:
            raise DatasetSnapshotResolutionError(
                "SNAPSHOT_PARTITION_MEMBERSHIP_HASH_MISMATCH",
                "a snapshot partition membership hash does not verify",
            )
        verified_memberships.append(
            ResolvedDatasetSnapshotMembership(
                partition_id=partition.id,
                tree=membership_tree,
            )
        )
        evaluation = session.get(QualityEvaluation, pin.quality_evaluation_id)
        if evaluation is None or _series_quality_pin_payload(
            evaluation
        ) != _stored_series_pin_payload(pin):
            raise DatasetSnapshotResolutionError(
                "SNAPSHOT_SERIES_QUALITY_PIN_MISMATCH",
                "a snapshot series-quality pin no longer matches its immutable evidence",
            )
        selection = SnapshotPartitionSelection(
            series_id=metadata.series_id,
            from_trading_day=partition.trading_day_from,
            to_trading_day=partition.trading_day_to,
            quality_evaluation_id=pin.quality_evaluation_id,
        )
        content_hash = _partition_content_hash(
            selection=selection,
            metadata=metadata,
            members=prepared_members,
            membership_hash=membership_hash,
            quality_pin=_PreparedSeriesPin(evaluation=evaluation, selection=selection),
        )
        if content_hash != partition.content_hash:
            raise DatasetSnapshotResolutionError(
                "SNAPSHOT_PARTITION_CONTENT_HASH_MISMATCH",
                "a snapshot partition content hash does not verify",
            )
        rebuilt.append(
            _PreparedPartition(
                selection=selection,
                metadata=metadata,
                members=tuple(prepared_members),
                quality_pin=_PreparedSeriesPin(evaluation=evaluation, selection=selection),
                membership_hash=membership_hash,
                content_hash=content_hash,
            )
        )
    prepared_import_pins: list[_PreparedImportPin] = []
    for import_pin in import_pins:
        import_evaluation = session.get(
            ImportQualityEvaluation, import_pin.import_quality_evaluation_id
        )
        import_run = session.get(ImportRun, import_pin.import_run_id)
        if (
            import_evaluation is None
            or import_run is None
            or _import_quality_pin_payload(import_evaluation)
            != _stored_import_pin_payload(import_pin)
        ):
            raise DatasetSnapshotResolutionError(
                "SNAPSHOT_IMPORT_QUALITY_PIN_MISMATCH",
                "a snapshot import-quality pin no longer matches its immutable evidence",
            )
        prepared_import_pins.append(
            _PreparedImportPin(
                selection=SnapshotImportQualityPinSelection(
                    import_run_id=import_pin.import_run_id,
                    import_quality_evaluation_id=import_pin.import_quality_evaluation_id,
                ),
                import_run=import_run,
                evaluation=import_evaluation,
            )
        )
    rebuilt_manifest_hash = _manifest_content_hash(
        available_at_cutoff=_as_utc(manifest.available_at_cutoff),
        partitions=rebuilt,
        import_pins=prepared_import_pins,
    )
    if rebuilt_manifest_hash != manifest.content_hash:
        raise DatasetSnapshotResolutionError(
            "SNAPSHOT_MANIFEST_CONTENT_HASH_MISMATCH",
            "the immutable snapshot manifest content hash does not verify",
        )
    return tuple(verified_memberships)


def _stored_series_pin_payload(pin: DatasetSnapshotSeriesQualityPin) -> dict[str, object]:
    return {
        "quality_evaluation_id": str(pin.quality_evaluation_id),
        "calendar_id": str(pin.calendar_id),
        "calendar_revision": pin.calendar_revision,
        "evaluation_scope": pin.evaluation_scope,
        "rule_set_name": pin.rule_set_name,
        "rule_set_version": pin.rule_set_version,
        "trading_day_from": pin.trading_day_from.isoformat(),
        "trading_day_to": pin.trading_day_to.isoformat(),
        "as_of": _render_timestamp(_as_utc(pin.as_of)),
        "input_fingerprint": pin.input_fingerprint,
        "outcome": pin.outcome,
        "delivery_gate": pin.delivery_gate,
    }


def _stored_import_pin_payload(pin: DatasetSnapshotImportQualityPin) -> dict[str, object]:
    return {
        "import_run_id": str(pin.import_run_id),
        "import_quality_evaluation_id": str(pin.import_quality_evaluation_id),
        "rule_set_name": pin.rule_set_name,
        "rule_set_version": pin.rule_set_version,
        "input_fingerprint": pin.input_fingerprint,
        "outcome": pin.outcome,
        "delivery_gate": pin.delivery_gate,
    }


def _publication_result(
    manifest: DatasetSnapshotManifest, *, replayed: bool
) -> DatasetSnapshotPublicationResult:
    return DatasetSnapshotPublicationResult(
        snapshot_id=manifest.id,
        manifest_schema_version=manifest.manifest_schema_version,
        content_hash=manifest.content_hash,
        available_at_cutoff=_as_utc(manifest.available_at_cutoff),
        partition_count=manifest.partition_count,
        member_count=manifest.member_count,
        import_quality_pin_count=manifest.import_quality_pin_count,
        replayed=replayed,
    )


def _canonical_bar_timestamp(value: datetime) -> datetime:
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(UTC)
    raise DatasetSnapshotPublicationError(
        "SNAPSHOT_CANONICAL_TIMESTAMP_AMBIGUOUS",
        "a canonical member has an ambiguous timestamp in the authority database",
    )


def _authority_timestamp(value: datetime) -> datetime:
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(UTC)
    raise DatasetSnapshotPublicationError(
        "SNAPSHOT_AUTHORITY_TIMESTAMP_AMBIGUOUS",
        "an authority timestamp is ambiguous in the authoritative database",
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DatasetSnapshotPublicationError(
            "SNAPSHOT_AUTHORITY_TIMESTAMP_AMBIGUOUS",
            "snapshot authority timestamps must be timezone-aware",
        )
    return value.astimezone(UTC)


def _render_timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _render_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    rendered = format(value.normalize(), "f")
    return "0" if rendered in {"-0", ""} else rendered


def _hash_payload(payload: object) -> str:
    rendered = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()
