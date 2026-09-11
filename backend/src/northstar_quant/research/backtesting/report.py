"""Build immutable research reports from the complete committed event history."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import TYPE_CHECKING
from uuid import UUID

from northstar_quant.accounting.amounts import decimal_text
from northstar_quant.accounting.fifo import Account
from northstar_quant.accounting.portfolio import value_single_contract
from northstar_quant.execution.history import OrderHistory
from northstar_quant.execution.orders import reservation

if TYPE_CHECKING:
    from .session import TradingSession, TradingStep


@dataclass(frozen=True, slots=True)
class ResearchResult:
    """Canonical immutable bytes prevent callers mutating stored result identity."""

    _document: str

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = json.loads(self._document)
        return result


def _verify_valuations(session: TradingSession, steps: Sequence[TradingStep]) -> None:
    """Derive every money row from the same identified facts before publishing it.

    This is an on-demand report audit, not a second runtime account or a substitute
    for the caller's immutable market-input and committed-step identity checks.
    """
    account = Account(session.account.initial_cash, (session.market,))
    terms = {item.terms_id: item for item in session._terms}
    peak = account.initial_cash
    maximum = maximum_fraction = Decimal(0)
    previous = None
    orders = OrderHistory()
    for step in steps:
        point = step.point
        try:
            at = datetime.fromisoformat(str(point["at"]))
            if at.utcoffset() != timedelta(0) or previous is not None and at < previous:
                raise ValueError("report valuation times must be ordered UTC observations")
            previous = at
            for settlement in step.settlements:
                if account.settle(settlement.fact, at=at) != settlement:
                    raise ValueError("report settlement differs from its account facts")
            if step.fill is not None:
                if step.fill.fact.filled_at > at or account.apply(step.fill.fact) != step.fill:
                    raise ValueError("report fill differs from its account facts")
            if step.fill is not None:
                orders.accept_fill(step.fill.fact)
            active_terms = terms[str(point["terms_id"])] if terms else None
            for update in step.orders:
                orders.observe(update, at=at)
                if update.at == update.order.submitted_at:
                    expected_budget = session._risk.budget(
                        side=update.order.side,
                        offset=update.order.offset,
                        maximum_fill_price=update.order.maximum_fill_price,
                        terms=active_terms,
                    )
                    if expected_budget != (
                        update.order.fee_budget_per_lot,
                        update.order.margin_budget_per_lot,
                    ):
                        raise ValueError(
                            "report order reservation differs from its fixed risk budget"
                        )
            orders.require_pending(step.new_order)
            valuation = value_single_contract(
                account, Decimal(str(point["close"])), at=at, terms=active_terms
            )
            expected = valuation.to_dict()
            expected.update(reservation(step.new_order))
            if valuation.margin_used is not None:
                expected["available_after_reservations"] = decimal_text(
                    valuation.equity
                    - valuation.margin_used
                    - Decimal(str(expected["reserved_fee"]))
                    - Decimal(str(expected["reserved_margin"]))
                )
            if not terms and any(
                name in point
                for name in ("terms_id", "margin_used", "available", "available_after_reservations")
            ):
                raise ValueError("report margin lacks fixed effective terms")
            peak = max(peak, valuation.equity)
            drawdown = peak - valuation.equity
            fraction = drawdown / peak
            maximum, maximum_fraction = max(maximum, drawdown), max(maximum_fraction, fraction)
            expected.update(
                drawdown=decimal_text(drawdown), drawdown_fraction=decimal_text(fraction)
            )
            if any(point.get(name) != value for name, value in expected.items()):
                raise ValueError("report valuation differs from the identified account ledger")
        except (KeyError, TypeError, ArithmeticError) as error:
            raise ValueError("report valuation evidence is incomplete or invalid") from error
    orders.require_pending(session.pending)
    if (
        account.checkpoint() != session.account.checkpoint()
        or peak != session._peak
        or maximum != session._maximum_drawdown
        or maximum_fraction != session._maximum_drawdown_fraction
    ):
        raise ValueError("report valuation history differs from its final account and drawdown")


def build_result(session: TradingSession, steps: Sequence[TradingStep]) -> ResearchResult:
    """Assemble a report on demand, never copying historical rows per advance."""

    if session._last is None:
        raise ValueError("research requires at least one bar")
    if (
        len(steps) != session._bar_count
        or sum(step.decision is not None for step in steps) != session._decision_count
        or tuple(step.fill for step in steps if step.fill is not None)
        != session.account.applied_fills
        or tuple(fact for step in steps for fact in step.settlements)
        != session.account.applied_settlements
        or steps[-1].point["observation_id"] != str(session._last.observation_id)
    ):
        raise ValueError("research report requires the complete committed step history")
    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        _verify_valuations(session, steps)
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
            "evaluation": session.evaluation.evaluate(
                initial_cash=session.account.initial_cash,
                ending_equity=Decimal(str(session.summary()["ending_equity"])),
                observed_bars=session._bar_count,
            ),
            "fills": [step.fill.to_dict() for step in steps if step.fill is not None],
            "settlements": [fact.to_dict() for step in steps for fact in step.settlements],
            "orders": [item.to_dict() for step in steps for item in step.orders],
            "equity_curve": [step.point for step in steps],
            "decisions": [step.decision for step in steps if step.decision is not None],
            "pending_order": None if session.pending is None else session.pending.to_dict(),
            "assumptions": [
                "Single-contract linear futures; cross-day variation uses fixed settlement facts. "
                "No funding flows.",
                "Orders explicitly open or close yesterday/today inventory. Reversal closes the "
                "position before a later decision can open the opposite side.",
                "A decision uses completed bars available then; fills use a strictly later "
                "completed bar's close plus adverse tick slippage.",
                "Simulated participation uses only complete post-order bar volume. Partial fills "
                "retain remaining lots; target replacement explicitly cancels the remainder.",
                "With fixed daily limits, buying at the upper limit or selling at the lower "
                "limit remains unfilled: bar volume does not establish queue priority.",
                "Fees follow the fixed offset-specific terms and explicit rounding. Risk budgets "
                "the worst fee and directional margin over the fixed daily price interval. "
                "Account available excludes holds; available_after_reservations deducts the "
                "remaining order's fixed fee and new-margin budgets without spread offsets."
                if session._terms
                else "Per-lot fees and mark-to-close slippage are included in Risk budgets.",
                "Open terminal positions are marked to the final observed close, "
                "not forcibly liquidated.",
                "Historical research is not live Paper or broker execution; "
                "no annualized performance is inferred.",
                "Fee/margin/limit revisions are fixed inputs with declared source references; "
                "their independent historical verification must be established separately. "
                "Margin is marked at observed close without portfolio offsets; actual broker "
                "charges and frozen funds remain independent facts."
                if session._terms
                else "Fees, slippage and margin fractions are declared simulation assumptions, "
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
