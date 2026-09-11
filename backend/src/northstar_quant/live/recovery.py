"""Offline verification of local Live facts; never reconnects or grants execution."""

from uuid import UUID

from sqlalchemy import Engine, text

from northstar_quant.accounting.baselines import BrokerBaselines
from northstar_quant.accounting.funds import BrokerFunds
from northstar_quant.accounting.ledger import BrokerLedger
from northstar_quant.broker.order_transport import verify_all as verify_ctp
from northstar_quant.broker.records import BrokerRecords
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.execution.journal import OrderJournal
from northstar_quant.execution.reviews import OrderReviews
from northstar_quant.live.materials import StrategyMaterials
from northstar_quant.live.opening_budgets import BrokerOpeningBudgets
from northstar_quant.live.storage import require_current
from northstar_quant.live.streams import LiveStreams


def verify(engine: Engine, files: SourceFiles) -> dict[str, int]:
    require_current(engine)
    with engine.connect() as connection:
        if connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() != "ok":
            raise ValueError("Live database integrity failure")
        if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
            raise ValueError("Live database foreign-key evidence failure")
    library = DataLibrary(engine, files)
    library.verify_sources()
    records = BrokerRecords(engine)
    queries = pending = 0
    with engine.connect().execution_options(yield_per=100) as connection:
        for query_id in connection.execute(
            text("SELECT batch_id FROM broker_query_batches ORDER BY batch_id")
        ).scalars():
            query = records.get(UUID(str(query_id)))
            queries += 1
            pending += query["status"] == "PENDING"
        for snapshot_id in connection.execute(
            text(
                "SELECT DISTINCT snapshot_id FROM data_processing_attempts "
                "WHERE snapshot_id IS NOT NULL"
            )
        ).scalars():
            library.load_dataset(UUID(str(snapshot_id)))
    materials = StrategyMaterials(engine).verify_all()
    baselines = BrokerBaselines(engine).verify_all()
    positions = BrokerLedger(engine).verify_all()
    BrokerFunds(engine).verify_all()
    streams = LiveStreams(engine, library).verify_all()
    BrokerOpeningBudgets(engine, library).verify_all()
    return {
        "ctp_orders_count": verify_ctp(engine),
        "query_batches_count": queries,
        "pending_queries_count": pending,
        **baselines,
        **positions,
        "order_checks_count": OrderReviews(engine).verify_all(),
        "streams_count": streams,
        "materials_count": materials,
        # This runtime is never started; verification reads saved runtime identities.
        "local_orders_count": OrderJournal(engine, UUID(int=0)).verify_all(),
    }
