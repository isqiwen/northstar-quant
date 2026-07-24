from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from northstar_quant.db.base import Base
from northstar_quant.db.models import FillRecord, OrderRecord, TradeAttributionRecord
from northstar_quant.db.repositories import save_fill_snapshots
from northstar_quant.execution.models import FillSnapshot


def test_save_fill_snapshots_creates_buy_trade_attribution(tmp_path):
    db_path = tmp_path / "buy-attribution.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    filled_at = datetime(2024, 3, 4, 15, 36, tzinfo=UTC)

    with Session(engine, future=True) as session:
        session.add(
            OrderRecord(
                profile_id="cn_futures_daily_live",
                strategy_id="core_portfolio",
                symbol="RB2405",
                side="BUY",
                qty=100.0,
                order_type="MKT",
                account="paper-account",
                reference_price=100.0,
                reference_price_source="broker_snapshot",
                execution_planner_id="bar_close_rebalance",
                run_id="run-001",
                batch_id="batch-001",
                plan_id="plan-001",
                broker_order_id="paper-123",
                status="Submitted",
                submitted_at=filled_at,
            )
        )
        session.commit()

        count = save_fill_snapshots(
            session,
            [
                FillSnapshot(
                    broker_order_id="paper-123",
                    symbol="RB2405",
                    qty=100.0,
                    price=100.5,
                    side="BUY",
                    filled_at=filled_at,
                )
            ],
        )
        row = session.scalar(
            select(TradeAttributionRecord).where(
                TradeAttributionRecord.broker_order_id == "paper-123"
            )
        )

    assert count == 1
    assert row is not None
    assert row.run_id == "run-001"
    assert row.plan_id == "plan-001"
    assert row.reference_price == 100.0
    assert row.reference_price_source == "broker_snapshot"
    assert row.actual_notional == 10050.0
    assert row.reference_notional == 10000.0
    assert row.implementation_shortfall == 50.0
    assert row.implementation_shortfall_bps == 50.0


def test_save_fill_snapshots_creates_sell_trade_attribution_with_correct_sign(tmp_path):
    db_path = tmp_path / "sell-attribution.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    filled_at = datetime(2024, 3, 4, 15, 36, tzinfo=UTC)

    with Session(engine, future=True) as session:
        session.add(
            OrderRecord(
                profile_id="cn_futures_daily_live",
                strategy_id="core_portfolio",
                symbol="I2405",
                side="SELL",
                qty=50.0,
                order_type="LMT",
                limit_price=200.0,
                account="paper-account",
                execution_planner_id="bar_close_rebalance",
                run_id="run-002",
                batch_id="batch-002",
                plan_id="plan-002",
                broker_order_id="paper-456",
                status="Submitted",
                submitted_at=filled_at,
            )
        )
        session.commit()

        save_fill_snapshots(
            session,
            [
                FillSnapshot(
                    broker_order_id="paper-456",
                    symbol="I2405",
                    qty=50.0,
                    price=199.0,
                    side="SELL",
                    filled_at=filled_at,
                )
            ],
        )
        row = session.scalar(
            select(TradeAttributionRecord).where(
                TradeAttributionRecord.broker_order_id == "paper-456"
            )
        )

    assert row is not None
    assert row.reference_price == 200.0
    assert row.reference_price_source == "order_limit"
    assert row.actual_notional == 9950.0
    assert row.reference_notional == 10000.0
    assert row.implementation_shortfall == 50.0
    assert row.implementation_shortfall_bps == 50.0


def test_save_fill_snapshots_uses_broker_execution_identity(tmp_path):
    db_path = tmp_path / "fill-identity.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    filled_at = datetime(2024, 3, 4, 15, 36, tzinfo=UTC)

    with Session(engine, future=True) as session:
        session.add(
            OrderRecord(
                strategy_id="core_portfolio",
                symbol="RB2405",
                side="BUY",
                qty=2.0,
                broker="ctp",
                account="DU123456",
                broker_order_id="42",
                status="Submitted",
                submitted_at=filled_at,
            )
        )
        session.commit()
        first = FillSnapshot(
            broker_order_id="42",
            symbol="RB2405",
            qty=2.0,
            price=500.0,
            side="BUY",
            filled_at=filled_at,
            account="DU123456",
            exec_id="exec-42.1",
            perm_id=100,
            client_id=7,
            instrument_id="rb2601",
        )

        assert save_fill_snapshots(session, [first], broker="ctp") == 1
        assert save_fill_snapshots(session, [first], broker="ctp") == 0
        fill_row = session.scalar(
            select(FillRecord).where(FillRecord.exec_id == "exec-42.1")
        )
        order_row = session.scalar(
            select(OrderRecord).where(OrderRecord.broker_order_id == "42")
        )

    assert fill_row is not None
    assert fill_row.broker == "ctp"
    assert fill_row.account == "DU123456"
    assert fill_row.instrument_id == "rb2601"
    assert order_row is not None
    assert order_row.status == "Filled"


def test_fill_identity_uses_perm_id_before_reused_order_id(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'fill-client-identity.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    filled_at = datetime(2024, 3, 4, 15, 36, tzinfo=UTC)

    with Session(engine, future=True) as session:
        client_one = OrderRecord(
            strategy_id="core",
            symbol="RB2405",
            side="BUY",
            qty=1.0,
            broker="ctp",
            account="DU123456",
            broker_order_id="42",
            client_id=1,
            perm_id=101,
            instrument_id="rb2601",
            status="Submitted",
        )
        client_two = OrderRecord(
            strategy_id="core",
            symbol="I2405",
            side="BUY",
            qty=1.0,
            broker="ctp",
            account="DU123456",
            broker_order_id="42",
            client_id=2,
            perm_id=202,
            instrument_id="i2601",
            status="Submitted",
        )
        session.add_all([client_one, client_two])
        session.commit()

        assert (
            save_fill_snapshots(
                session,
                [
                    FillSnapshot(
                        broker_order_id="42",
                        symbol="I2405",
                        qty=1.0,
                        price=500.0,
                        side="BUY",
                        filled_at=filled_at,
                        account="DU123456",
                        exec_id="exec-client-2",
                        perm_id=202,
                        client_id=2,
                        instrument_id="i2601",
                    )
                ],
                broker="ctp",
            )
            == 1
        )
        fill = session.scalar(
            select(FillRecord).where(FillRecord.exec_id == "exec-client-2")
        )

        assert fill is not None
        assert fill.order_id == client_two.id
        assert session.get(OrderRecord, client_one.id).status == "Submitted"
        assert session.get(OrderRecord, client_two.id).status == "Filled"


def test_partial_fill_does_not_downgrade_cancelled_terminal_order(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'fill-terminal.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    filled_at = datetime(2024, 3, 4, 15, 36, tzinfo=UTC)

    with Session(engine, future=True) as session:
        order = OrderRecord(
            strategy_id="core",
            symbol="RB2405",
            side="BUY",
            qty=2.0,
            broker="ctp",
            account="DU123456",
            broker_order_id="42",
            client_id=7,
            perm_id=100,
            instrument_id="rb2601",
            status="Cancelled",
        )
        session.add(order)
        session.commit()

        assert (
            save_fill_snapshots(
                session,
                [
                    FillSnapshot(
                        broker_order_id="42",
                        symbol="RB2405",
                        qty=1.0,
                        price=500.0,
                        side="BUY",
                        filled_at=filled_at,
                        account="DU123456",
                        exec_id="exec-partial",
                        perm_id=100,
                        client_id=7,
                        instrument_id="rb2601",
                    )
                ],
                broker="ctp",
            )
            == 1
        )
        refreshed = session.get(OrderRecord, order.id)

        assert refreshed.status == "Cancelled"
        assert refreshed.filled_qty == 1.0
        assert refreshed.remaining_qty == 1.0


def test_existing_unlinked_fill_is_backfilled_by_order_ref(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'fill-order-ref-backfill.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    filled_at = datetime(2024, 3, 4, 15, 36, tzinfo=UTC)

    with Session(engine, future=True) as session:
        order = OrderRecord(
            strategy_id="core",
            symbol="RB2405",
            side="BUY",
            qty=2.0,
            broker="ctp",
            account="DU123456",
            order_ref="NSQ-plan-1",
            reference_price=499.0,
            status="SubmissionUnknown",
        )
        session.add(order)
        session.flush()
        fill = FillRecord(
            order_id=None,
            broker="ctp",
            account="DU123456",
            exec_id="exec-backfill-1",
            broker_order_id="42",
            symbol="RB2405",
            side="BUY",
            qty=1.0,
            price=500.0,
            filled_at=filled_at,
        )
        session.add(fill)
        session.commit()

        count = save_fill_snapshots(
            session,
            [
                FillSnapshot(
                    broker_order_id="42",
                    symbol="RB2405",
                    qty=1.0,
                    price=500.0,
                    side="BUY",
                    filled_at=filled_at,
                    account="DU123456",
                    exec_id="exec-backfill-1",
                    order_ref="NSQ-plan-1",
                    perm_id=1001,
                    client_id=7,
                    instrument_id="rb2601",
                )
            ],
            broker="ctp",
        )
        refreshed_fill = session.get(FillRecord, fill.id)
        attribution = session.scalar(
            select(TradeAttributionRecord).where(
                TradeAttributionRecord.fill_id == fill.id
            )
        )

        assert count == 1
        assert refreshed_fill is not None
        assert refreshed_fill.order_id == order.id
        assert attribution is not None
        assert attribution.order_id == order.id


def test_fill_ledger_does_not_reduce_completed_cumulative_progress(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'fill-progress-monotonic.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    filled_at = datetime(2024, 3, 4, 15, 36, tzinfo=UTC)

    with Session(engine, future=True) as session:
        order = OrderRecord(
            strategy_id="core",
            symbol="RB2405",
            side="BUY",
            qty=2.0,
            broker="ctp",
            account="DU123456",
            order_ref="NSQ-plan-filled",
            perm_id=1002,
            instrument_id="rb2601",
            status="Filled",
            filled_qty=2.0,
            remaining_qty=0.0,
        )
        session.add(order)
        session.commit()

        assert (
            save_fill_snapshots(
                session,
                [
                    FillSnapshot(
                        broker_order_id="43",
                        symbol="RB2405",
                        qty=1.0,
                        price=500.0,
                        side="BUY",
                        filled_at=filled_at,
                        account="DU123456",
                        exec_id="exec-window-only-one",
                        order_ref="NSQ-plan-filled",
                        perm_id=1002,
                        client_id=7,
                        instrument_id="rb2601",
                    )
                ],
                broker="ctp",
            )
            == 1
        )
        refreshed = session.get(OrderRecord, order.id)

        assert refreshed is not None
        assert refreshed.status == "Filled"
        assert refreshed.filled_qty == 2.0
        assert refreshed.remaining_qty == 0.0
