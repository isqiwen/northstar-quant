from northstar_quant.live.trading_calendar import is_trading_session


def test_is_trading_session_returns_bool():
    assert isinstance(is_trading_session(), bool)
