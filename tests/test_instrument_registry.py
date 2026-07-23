from pathlib import Path

import pytest
import yaml

from northstar_quant.config.instrument_registry import (
    InstrumentRegistryError,
    load_instrument_registry,
)


def _instrument(**overrides):
    values = {
        "data_symbol": "spy.data",
        "broker_symbol": "spy",
        "con_id": 12345,
        "sec_type": "stk",
        "exchange": "smart",
        "primary_exchange": "arca",
        "currency": "usd",
        "enabled": True,
    }
    values.update(overrides)
    return values


def _write_registry(path: Path, instruments: list[dict]) -> Path:
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "broker": "ibkr",
                "instruments": instruments,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def test_instrument_registry_loads_and_normalizes_mapping(tmp_path):
    path = _write_registry(tmp_path / "ibkr.yaml", [_instrument()])

    registry = load_instrument_registry(path)
    instrument = registry.resolve("SPY.DATA")

    assert registry.broker == "ibkr"
    assert registry.version == 1
    assert instrument.data_symbol == "SPY.DATA"
    assert instrument.broker_symbol == "SPY"
    assert instrument.con_id == 12345
    assert instrument.sec_type == "STK"
    assert instrument.exchange == "SMART"
    assert instrument.primary_exchange == "ARCA"
    assert instrument.currency == "USD"
    assert registry.resolve_con_id(12345) == instrument


def test_tracked_ibkr_registry_is_safe_and_empty():
    registry_path = (
        Path(__file__).resolve().parents[1] / "configs" / "instruments" / "ibkr.yaml"
    )

    registry = load_instrument_registry(registry_path)

    assert registry.instruments == ()


def test_instrument_registry_fails_closed_for_missing_or_disabled_mapping(tmp_path):
    path = _write_registry(
        tmp_path / "ibkr.yaml",
        [_instrument(enabled=False)],
    )
    registry = load_instrument_registry(path)

    with pytest.raises(InstrumentRegistryError, match="INSTRUMENT_DISABLED"):
        registry.resolve("SPY.DATA")
    with pytest.raises(InstrumentRegistryError, match="INSTRUMENT_NOT_CONFIGURED"):
        registry.resolve("QQQ.DATA")


@pytest.mark.parametrize("con_id", [0, -1, "12345", True, None])
def test_instrument_registry_requires_positive_integer_con_id(tmp_path, con_id):
    path = _write_registry(
        tmp_path / "ibkr.yaml",
        [_instrument(con_id=con_id)],
    )

    with pytest.raises(InstrumentRegistryError, match="con_id 必须是大于 0 的整数"):
        load_instrument_registry(path)


@pytest.mark.parametrize(
    ("second", "message"),
    [
        (_instrument(con_id=54321), "data_symbol 重复"),
        (_instrument(data_symbol="QQQ.DATA"), "con_id 重复"),
        (
            _instrument(data_symbol="QQQ.DATA", con_id=54321),
            "券商合约身份重复",
        ),
    ],
)
def test_instrument_registry_rejects_ambiguous_identity(
    tmp_path,
    second,
    message,
):
    path = _write_registry(
        tmp_path / "ibkr.yaml",
        [_instrument(), second],
    )

    with pytest.raises(InstrumentRegistryError, match=message):
        load_instrument_registry(path)


def test_instrument_registry_rejects_unknown_or_missing_fields(tmp_path):
    unknown = _instrument(unexpected="value")
    unknown_path = _write_registry(tmp_path / "unknown.yaml", [unknown])

    with pytest.raises(InstrumentRegistryError, match="包含未知字段"):
        load_instrument_registry(unknown_path)

    missing = _instrument()
    missing.pop("primary_exchange")
    missing_path = _write_registry(tmp_path / "missing.yaml", [missing])

    with pytest.raises(InstrumentRegistryError, match="缺少字段"):
        load_instrument_registry(missing_path)
