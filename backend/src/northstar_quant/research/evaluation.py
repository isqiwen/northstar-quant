"""One fixed exploratory evaluation policy, bound before historical execution."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from uuid import UUID

from northstar_quant.accounting.amounts import decimal_text
from northstar_quant.data_management.research import DatasetDetails


@dataclass(frozen=True, slots=True)
class EvaluationPlan:
    snapshot_id: UUID
    content_hash: str
    event_start: datetime | None
    event_end: datetime | None
    expected_bars: int | None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.snapshot_id, UUID)
            or re.fullmatch(r"[0-9a-f]{64}", self.content_hash) is None
        ):
            raise ValueError("evaluation requires a fixed snapshot UUID and SHA-256 identity")
        if self.expected_bars is None:
            if self.event_start is not None or self.event_end is not None:
                raise ValueError("unverified evaluation cannot invent a source extent")
        elif (
            type(self.expected_bars) is not int
            or not 1 <= self.expected_bars <= 100000
            or self.event_start is None
            or self.event_end is None
            or self.event_start.utcoffset() != timedelta(0)
            or self.event_end.utcoffset() != timedelta(0)
            or self.event_start >= self.event_end
        ):
            raise ValueError("evaluation requires an ordered UTC window and bounded input count")

    @classmethod
    def bind(
        cls, snapshot_id: UUID, content_hash: str, details: DatasetDetails | None
    ) -> EvaluationPlan:
        if details is None:
            return cls(snapshot_id, content_hash, None, None, None)
        summary = details.summary
        if summary.snapshot_id != snapshot_id or summary.content_hash != content_hash:
            raise ValueError("evaluation must bind the same immutable research input")
        return cls(
            snapshot_id,
            content_hash,
            summary.session_open,
            summary.session_close,
            summary.bar_count,
        )

    def to_dict(self) -> dict[str, object]:
        document: dict[str, object] = {
            "revision": "1",
            "snapshot_id": str(self.snapshot_id),
            "content_hash": self.content_hash,
            "window": "ENTIRE_FIXED_SNAPSHOT",
            "event_start": None if self.event_start is None else self.event_start.isoformat(),
            "event_end": None if self.event_end is None else self.event_end.isoformat(),
            "expected_bars": self.expected_bars,
            "benchmark": "UNINVESTED_CASH_NO_INTEREST",
            "annualization": "NONE",
            "risk_free_rate": "0",
            "sample_use": "EXPLORATORY_NOT_OUT_OF_SAMPLE",
        }
        identity = hashlib.sha256(
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return {"plan_id": identity, **document}

    def evaluate(
        self, *, initial_cash: Decimal, ending_equity: Decimal, observed_bars: int
    ) -> dict[str, object]:
        """The benchmark makes no trades, pays no interest and incurs no costs.

        Returns come from audited account equity, not another pricing/backtest
        implementation. No Sharpe or annualized return is inferred from intraday
        observations, and a selected window is never declared unused sample data.
        """
        if (
            not initial_cash.is_finite()
            or initial_cash <= 0
            or not ending_equity.is_finite()
            or type(observed_bars) is not int
            or observed_bars < 0
            or self.expected_bars is not None
            and observed_bars > self.expected_bars
        ):
            raise ValueError("evaluation observations exceed their fixed input or account scope")
        with localcontext() as context:
            context.prec = 96
            context.rounding = ROUND_HALF_EVEN
            return {
                "plan": self.to_dict(),
                "status": "UNVERIFIED_INPUT"
                if self.expected_bars is None
                else "COMPLETE_WINDOW"
                if observed_bars == self.expected_bars
                else "INCOMPLETE_WINDOW",
                "observed_bars": observed_bars,
                "benchmark_ending_equity": decimal_text(initial_cash),
                "benchmark_return": "0",
                "excess_return": decimal_text(ending_equity / initial_cash - 1),
                "annualized_return": None,
                "sharpe": None,
                "limitations": [
                    "Window selection and repeated parameter search do not establish "
                    "out-of-sample evidence.",
                    "The cash benchmark is not a tradable futures index or a "
                    "buy-and-hold futures strategy.",
                    "A complete input window does not prove historical terms "
                    "or strategy profitability.",
                ],
            }
