"""Real SQLite and a synthetic SDK producer exercise the routed core failure path."""

from dataclasses import replace
from uuid import uuid4

from northstar_quant.broker.events import BrokerEvent
from northstar_quant.live import streams as module
from northstar_quant.live.storage import initialize, open_store
from northstar_quant.live.streams import LiveStreams
from northstar_quant.research.factor_catalog import initialize_factor_catalog
from tests.live.test_streams import prepare, start


def test_routed_receiver_stops_on_projection_failure_and_keeps_raw_fact(tmp_path, monkeypatch):
    engine = open_store(tmp_path / "live.sqlite")
    initialize(engine)
    with engine.begin() as connection:
        initialize_factor_catalog(connection)
    library, source, configuration, calls = prepare(engine, tmp_path, monkeypatch)
    streams = LiveStreams(engine, library)
    identifier = uuid4()
    delivered = []
    outcomes = []

    def receive(*args, **kwargs):
        event = BrokerEvent(
            1,
            "TD",
            "OnRspUserLogin",
            1,
            True,
            "2026-09-07T01:00:00Z",
            0,
            {"UserID": "123456", "BrokerID": "9999", "TradingDay": "20260907"},
        )
        delivered.append(event)
        calls["ready"].set()
        for item in (event, replace(event, sequence=2)):
            try:
                kwargs["on_event"](item)
            except Exception as error:
                outcomes.append(error)
            else:
                outcomes.append(None)
        return None  # A swallowed callback failure must still mark reception FAILED.

    monkeypatch.setattr(module.ctp, "stream_account", receive)
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TRIGGER fail_projection BEFORE INSERT ON broker_stream_steps
            BEGIN SELECT RAISE(FAIL, 'injected projection failure'); END
        """)
    try:
        start(streams, source, configuration, identifier)
        assert calls["ready"].wait(3)
        # close joins the existing receiver; it never starts or reconnects one.
        streams.close()
        state = streams.get(identifier)
        assert len(delivered) == 1
        assert len(outcomes) == 2 and outcomes[0] is not None
        assert isinstance(outcomes[1], RuntimeError) and "faulted" in str(outcomes[1])
        assert state["status"] == "FAILED"
        assert state["reason"] == "RECEPTION_OR_PERSISTENCE_FAILED"
        events = streams.events(identifier)
        assert len(events) == 1
        assert events[0]["event"] == delivered[0].to_dict()
    finally:
        streams.close()
        engine.dispose()
