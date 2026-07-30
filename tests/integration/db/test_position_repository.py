from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from northstar_quant.db.models import (
    PositionSnapshotBatchRecord,
    PositionSnapshotRecord,
)
from northstar_quant.db.repositories import (
    list_latest_positions,
    save_position_snapshot_batch,
)
from northstar_quant.execution.models import PositionSnapshot


def test_save_position_snapshot_batch_assigns_one_batch_to_the_whole_sync(postgresql_engine):
    engine = postgresql_engine

    snapshots = [
        PositionSnapshot(
            symbol="MA2405",
            qty=10,
            asof=datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
        ),
        PositionSnapshot(
            symbol="TA2405",
            qty=20,
            asof=datetime(2024, 1, 2, 10, 0, 1, tzinfo=UTC),
        ),
    ]

    with Session(engine, future=True) as session:
        batch = save_position_snapshot_batch(session, snapshots)
        latest_rows = list_latest_positions(session)
        stored_rows = list(
            session.scalars(
                select(PositionSnapshotRecord).order_by(PositionSnapshotRecord.symbol.asc())
            )
        )

    assert batch.position_count == 2
    assert [row.symbol for row in latest_rows] == ["MA2405", "TA2405"]
    assert len({row.snapshot_batch_id for row in stored_rows}) == 1
    assert stored_rows[0].snapshot_batch_id is not None
    assert len({row.asof for row in stored_rows}) == 1
    assert stored_rows[0].asof == datetime(2024, 1, 2, 10, 0, 1, tzinfo=UTC)


def test_list_latest_positions_prefers_batch_id_over_row_level_asof(postgresql_engine):
    engine = postgresql_engine

    with Session(engine, future=True) as session:
        session.add_all(
            [
                PositionSnapshotBatchRecord(
                    snapshot_batch_id="batch-001",
                    position_count=2,
                    asof=datetime(2024, 1, 2, 10, 0, 1, tzinfo=UTC),
                ),
                PositionSnapshotRecord(
                    symbol="MA2405",
                    qty=10,
                    asof=datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
                    snapshot_batch_id="batch-001",
                ),
                PositionSnapshotRecord(
                    symbol="TA2405",
                    qty=20,
                    asof=datetime(2024, 1, 2, 10, 0, 1, tzinfo=UTC),
                    snapshot_batch_id="batch-001",
                ),
            ]
        )
        session.commit()

        latest_rows = list_latest_positions(session)

    assert [row.symbol for row in latest_rows] == ["MA2405", "TA2405"]


def test_empty_position_batch_supersedes_previous_non_empty_batch(postgresql_engine):
    engine = postgresql_engine

    with Session(engine, future=True) as session:
        save_position_snapshot_batch(
            session,
            [
                PositionSnapshot(
                    symbol="RB2405",
                    qty=2,
                    account="paper-a",
                    asof=datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
                )
            ],
            broker="paper",
            account="paper-a",
            profile_id="trend_simulated",
        )
        empty_batch = save_position_snapshot_batch(
            session,
            [],
            broker="paper",
            account="paper-a",
            profile_id="trend_simulated",
            asof=datetime(2024, 1, 2, 11, 0, tzinfo=UTC),
        )
        latest_rows = list_latest_positions(
            session,
            broker="paper",
            account="paper-a",
            profile_id="trend_simulated",
        )
        latest_batch = session.scalar(
            select(PositionSnapshotBatchRecord).order_by(
                PositionSnapshotBatchRecord.asof.desc()
            )
        )

    assert empty_batch.position_count == 0
    assert latest_rows == []
    assert latest_batch is not None
    assert latest_batch.position_count == 0


def test_latest_positions_are_isolated_by_broker_account_and_profile(postgresql_engine):
    engine = postgresql_engine

    with Session(engine, future=True) as session:
        save_position_snapshot_batch(
            session,
            [
                PositionSnapshot(
                    symbol="RB2405",
                    qty=2,
                    account="paper-a",
                    asof=datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
                )
            ],
            broker="paper",
            account="paper-a",
            profile_id="trend-a",
        )
        save_position_snapshot_batch(
            session,
            [
                PositionSnapshot(
                    symbol="CU2405",
                    qty=7,
                    account="paper-b",
                    asof=datetime(2024, 1, 2, 11, 0, tzinfo=UTC),
                )
            ],
            broker="paper",
            account="paper-b",
            profile_id="trend-b",
        )

        account_a = list_latest_positions(
            session,
            broker="paper",
            account="paper-a",
            profile_id="trend-a",
        )
        account_b = list_latest_positions(
            session,
            broker="paper",
            account="paper-b",
            profile_id="trend-b",
        )

    assert [(row.symbol, row.qty) for row in account_a] == [("RB2405", 2)]
    assert [(row.symbol, row.qty) for row in account_b] == [("CU2405", 7)]
