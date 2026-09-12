"""Compose admitted fixed sessions without silently selecting newer observations."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from northstar_quant.accounting.settlement import SettlementFact
from northstar_quant.accounting.terms import FuturesTerms

from .catalog.models import DatasetSnapshotManifest, DatasetSnapshotPartition
from .maintenance import library_write
from .quality.evaluations import MinuteQualityEvaluationCommand
from .quality.minute_service import MinuteQualityEvaluationService
from .research import ResearchDataset, _digest
from .research_reader import load_dataset
from .snapshots.publication import (
    MAX_SNAPSHOT_PARTITIONS,
    PublishDatasetSnapshotCommand,
    SnapshotImportQualityPinSelection,
    SnapshotPartitionSelection,
)
from .snapshots.service import DatasetSnapshotPublicationService

if TYPE_CHECKING:
    from .library import DataLibrary


def assemble(
    library: DataLibrary,
    snapshot_ids: tuple[UUID, ...],
    *,
    settlements: tuple[SettlementFact, ...],
    terms: tuple[FuturesTerms, ...],
) -> ResearchDataset:
    """Retryable database publication followed by an atomic fixed-file export.

    Inputs are admitted snapshots, never mutable series/latest selectors. Each
    partition is re-evaluated at the common information cutoff. A changed source
    selection is rejected rather than being substituted into the requested input.
    """
    if not 2 <= len(snapshot_ids) <= MAX_SNAPSHOT_PARTITIONS:
        raise ValueError("research assembly requires 2 to 32 fixed input snapshots")
    if any(not isinstance(item, UUID) for item in snapshot_ids):
        raise ValueError("research assembly requires snapshot UUIDs")
    if len(set(snapshot_ids)) != len(snapshot_ids):
        raise ValueError("research assembly repeats an input snapshot")
    with library_write(library._engine):
        inputs = sorted(
            (library.load_dataset(item) for item in snapshot_ids),
            key=lambda item: item.bars[0].event_time,
        )
        first = inputs[0]
        assert first.details is not None
        all_settlements = {item.settlement_id: item for item in settlements}
        all_terms = {item.terms_id: item for item in terms}
        if len(all_settlements) != len(settlements) or len(all_terms) != len(terms):
            raise ValueError("research assembly repeats an economic fact identity")
        for dataset in inputs:
            assert dataset.details is not None
            for fact in dataset.details.settlements:
                if (
                    fact.settlement_id in all_settlements
                    and all_settlements[fact.settlement_id] != fact
                ):
                    raise ValueError("research assembly conflicts with fixed settlement evidence")
                all_settlements[fact.settlement_id] = fact
            for term in dataset.details.terms:
                if term.terms_id in all_terms and all_terms[term.terms_id] != term:
                    raise ValueError("research assembly conflicts with fixed terms evidence")
                all_terms[term.terms_id] = term
        settlements = tuple(sorted(all_settlements.values(), key=lambda item: item.settlement_id))
        terms = tuple(sorted(all_terms.values(), key=lambda item: item.terms_id))
        expected = tuple(bar for item in inputs for bar in item.bars)
        for item in inputs:
            assert item.details is not None
            if item.market != first.market or item.interval_seconds != first.interval_seconds:
                raise ValueError("research assembly mixes contract identity or interval")
            if (
                item.details.volume_unit != first.details.volume_unit
                or item.details.adjustment != first.details.adjustment
                or item.details.processing_provenance != first.details.processing_provenance
                or item.details.import_specs[0].availability_basis
                != first.details.import_specs[0].availability_basis
            ):
                raise ValueError("research assembly mixes source meaning")
        for before, after in zip(expected, expected[1:]):
            if before.completed_at > after.event_time:
                raise ValueError("research assembly contains overlapping sessions")
            if before.trading_day > after.trading_day:
                raise ValueError("research assembly contains decreasing trading days")
            if before.available_at > after.available_at:
                raise ValueError("research assembly contains decreasing availability")
        key = "research-assembly-" + _digest(
            {
                "inputs": [
                    {"id": str(item.snapshot_id), "hash": item.content_hash} for item in inputs
                ],
                "settlements": [item.to_dict() for item in settlements],
                "terms": [item.to_dict() for item in terms],
            }
        )
        with Session(library._engine) as session:
            existing = session.scalar(
                select(DatasetSnapshotManifest.id).where(
                    DatasetSnapshotManifest.idempotency_key == key
                )
            )
            partitions = session.scalars(
                select(DatasetSnapshotPartition)
                .where(DatasetSnapshotPartition.manifest_id.in_(snapshot_ids))
                .order_by(DatasetSnapshotPartition.series_id)
            ).all()
        if len(partitions) > MAX_SNAPSHOT_PARTITIONS:
            raise ValueError("research assembly exceeds the fixed partition limit")
        if len({part.series_id for part in partitions}) != len(partitions):
            raise ValueError("research assembly repeats a source series")
        if existing is None:
            cutoff = max(item.available_at for item in expected)
            selections = []
            for index, part in enumerate(partitions):
                with Session(library._engine) as session:
                    quality = MinuteQualityEvaluationService(session).evaluate(
                        MinuteQualityEvaluationCommand(
                            series_id=part.series_id,
                            from_trading_day=part.trading_day_from,
                            to_trading_day=part.trading_day_to,
                            as_of=cutoff,
                            idempotency_key=f"{key}-{index}",
                            correlation_id=key,
                        )
                    )
                selections.append(
                    SnapshotPartitionSelection(
                        part.series_id,
                        part.trading_day_from,
                        part.trading_day_to,
                        quality.quality_evaluation_id,
                    )
                )
            pins = {
                pin.import_run_id: SnapshotImportQualityPinSelection(
                    pin.import_run_id, pin.evaluation_id
                )
                for item in inputs
                if item.details is not None
                for pin in item.details.import_quality
            }
            with Session(library._engine) as session:
                existing = DatasetSnapshotPublicationService(session).publish(
                    PublishDatasetSnapshotCommand(
                        available_at_cutoff=cutoff,
                        partitions=tuple(selections),
                        import_quality_pins=tuple(pins.values()),
                        idempotency_key=key,
                        correlation_id=key,
                        settlements=settlements,
                        terms=terms,
                    )
                ).snapshot_id
        fixed = load_dataset(library._engine, existing)
        if fixed.market != first.market or fixed.bars != expected:
            raise ValueError("research assembly source selection changed at the common cutoff")
        library._verify_dataset_sources(fixed)
        # A crash here leaves a retryable logical manifest, never a half-written
        # usable input. Readers require the verified export before admitting it.
        library.publications.publish(fixed)
        return library.load_dataset(existing)
