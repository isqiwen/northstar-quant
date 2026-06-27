from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from northstar_quant.db.base import Base
from northstar_quant.db.models import PositionSnapshotRecord
from northstar_quant.db.repositories import list_latest_positions, save_position_snapshots
from northstar_quant.execution.models import PositionSnapshot


def test_save_position_snapshots_assigns_one_batch_to_the_whole_sync(tmp_path):
    db_path = tmp_path / "positions.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)

    snapshots = [
        PositionSnapshot(
            symbol="AAPL",
            qty=10,
            asof=datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
        ),
        PositionSnapshot(
            symbol="MSFT",
            qty=20,
            asof=datetime(2024, 1, 2, 10, 0, 1, tzinfo=UTC),
        ),
    ]

    with Session(engine, future=True) as session:
        count = save_position_snapshots(session, snapshots)
        latest_rows = list_latest_positions(session)
        stored_rows = list(
            session.scalars(
                select(PositionSnapshotRecord).order_by(PositionSnapshotRecord.symbol.asc())
            )
        )

    assert count == 2
    assert [row.symbol for row in latest_rows] == ["AAPL", "MSFT"]
    assert len({row.snapshot_batch_id for row in stored_rows}) == 1
    assert stored_rows[0].snapshot_batch_id is not None
    assert len({row.asof for row in stored_rows}) == 1
    assert stored_rows[0].asof == datetime(2024, 1, 2, 10, 0, 1, tzinfo=UTC)


def test_list_latest_positions_prefers_batch_id_over_row_level_asof(tmp_path):
    db_path = tmp_path / "positions-batch.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)

    with Session(engine, future=True) as session:
        session.add_all(
            [
                PositionSnapshotRecord(
                    symbol="AAPL",
                    qty=10,
                    asof=datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
                    snapshot_batch_id="batch-001",
                ),
                PositionSnapshotRecord(
                    symbol="MSFT",
                    qty=20,
                    asof=datetime(2024, 1, 2, 10, 0, 1, tzinfo=UTC),
                    snapshot_batch_id="batch-001",
                ),
            ]
        )
        session.commit()

        latest_rows = list_latest_positions(session)

    assert [row.symbol for row in latest_rows] == ["AAPL", "MSFT"]
