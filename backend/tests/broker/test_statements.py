"""Incomplete/mismatched documents must not become financial evidence."""

import base64
from copy import deepcopy

import pytest

from northstar_quant.broker.statements import assemble, validate_day


def _section():
    raw = "手续费：12.34\n".encode("gbk")
    return {
        "status": "COMPLETE",
        "rows": [
            dict(
                BrokerID="9999",
                InvestorID="123",
                AccountID="123",
                CurrencyID="CNY",
                TradingDay="20260903",
                SettlementID=1,
                SequenceNo=i,
                ContentBase64=base64.b64encode(fragment).decode("ascii"),
            )
            for i, fragment in enumerate((raw[:1], raw[1:]), 1)
        ],
    }


def _assemble(section):
    return assemble(section, day="2026-09-03", broker_id="9999", account_id="123")


@pytest.mark.parametrize(
    "field,value",
    [
        ("BrokerID", "other"),
        ("InvestorID", "other"),
        ("AccountID", "other"),
        ("CurrencyID", "USD"),
        ("TradingDay", "20260904"),
        ("SettlementID", 2),
        ("SequenceNo", 3),
        ("SequenceNo", 1),
        ("ContentBase64", "invalid!"),
    ],
)
def test_conflicting_fragment_retains_unknown_document(field, value):
    section = _section()
    section["rows"][1][field] = value
    result = _assemble(section)
    assert result["status"] == "INCOMPLETE"
    assert result["content"] is None
    assert result["content_sha256"] is None
    assert result["problems"]
    assert result["ledger_posted"] is False


def test_document_assembly_requires_complete_terminal_and_encoding():
    original = _section()
    assert _assemble(original)["content"] == "手续费：12.34\n"
    truncated = deepcopy(original)
    truncated["status"] = "WAITING"
    assert _assemble(truncated)["content"] is None
    truncated["status"] = "COMPLETE"
    truncated["rows"] = truncated["rows"][:1]
    assert _assemble(truncated)["content"] is None  # split multibyte tail
    assert _assemble({"status": "COMPLETE", "rows": []})["status"] == "NOT_RETURNED"
    shifted = deepcopy(original)
    for row in shifted["rows"]:
        row["SequenceNo"] += 2
    assert _assemble(shifted)["content"] is None


@pytest.mark.parametrize("day", ["", "20260903", "2026-02-30", "2026-09-03T00:00:00", 12])
def test_statement_query_requires_explicit_canonical_day(day):
    with pytest.raises((ValueError, TypeError)):
        validate_day(day)


def test_native_content_is_copied_before_binding_unicode_conversion():
    import ctypes

    from northstar_quant.broker._ctp_worker import _copy_fields

    class Fragment(ctypes.Structure):
        _fields_ = [("Content", ctypes.c_char * 501)]

        def __getattribute__(self, name):
            if name == "Content":
                raise AssertionError("must copy raw bytes before wrapper decoding")
            return super().__getattribute__(name)

    native = Fragment()
    native.Content = b"\xca"
    copied = _copy_fields("OnRspQrySettlementInfo", native)
    assert copied["ContentBase64"] == base64.b64encode(b"\xca").decode("ascii")
