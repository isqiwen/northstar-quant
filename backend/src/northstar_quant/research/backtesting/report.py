"""Build immutable research reports from the complete committed event history."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import TYPE_CHECKING
from uuid import UUID

from northstar_quant.accounting.amounts import decimal_text

if TYPE_CHECKING:
    from .session import TradingSession, TradingStep


@dataclass(frozen=True, slots=True)
class ResearchResult:
    """Canonical immutable bytes prevent callers mutating stored result identity."""

    _document: str

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = json.loads(self._document)
        return result


def build_result(session: TradingSession, steps: Sequence[TradingStep]) -> ResearchResult:
    """Assemble a report on demand, never copying historical rows per advance."""

    if session._last is None:
        raise ValueError("research requires at least one bar")
    if (
        len(steps) != session._bar_count
        or sum(step.decision is not None for step in steps) != session._decision_count
        or sum(step.fill is not None for step in steps) != session.account.fill_count
        or steps[-1].point["observation_id"] != str(session._last.observation_id)
    ):
        raise ValueError("research report requires the complete committed step history")
    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        payload: dict[str, object] = {
            "mode": "research",
            "environment": session.kernel.status.environment.value,
            "data": None if session._data_details is None else session._data_details.to_dict(),
            "snapshot": {"id": str(session.snapshot_id), "content_hash": session.content_hash},
            "market": {
                key: str(value)
                if isinstance(value, UUID)
                else decimal_text(value)
                if isinstance(value, Decimal)
                else value
                for key, value in asdict(session.market).items()
            },
            "config": session.config.to_dict(),
            "summary": session.summary(),
            "fills": [step.fill.to_dict() for step in steps if step.fill is not None],
            "equity_curve": [step.point for step in steps],
            "decisions": [step.decision for step in steps if step.decision is not None],
            "pending_order": None if session.pending is None else session.pending.to_dict(),
            "assumptions": [
                "Single-contract, single-trading-day linear futures; no settlement or funding.",
                "Orders explicitly open or close today; reversal first closes the existing "
                "position and requires a later decision to open the opposite side.",
                "A decision uses completed bars available then; fills use a strictly later "
                "completed bar's close plus adverse tick slippage.",
                "Orders fill completely without liquidity or volume modeling "
                "and are replaced at each new decision.",
                "Fees are charged per filled lot; Risk reserves fees and mark-to-close slippage.",
                "Open terminal positions are marked to the final observed close, "
                "not forcibly liquidated.",
                "Historical research is not live Paper or broker execution; "
                "no annualized performance is inferred.",
                "Fees, slippage and margin fractions are declared simulation assumptions, "
                "not independently verified historical broker or exchange terms.",
                *(
                    ("Calculation input has no verified source evidence.",)
                    if session._data_details is None
                    else session._data_details.limitations
                ),
            ],
        }
    content = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    payload["result_hash"] = hashlib.sha256(content.encode()).hexdigest()
    return ResearchResult(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    )
