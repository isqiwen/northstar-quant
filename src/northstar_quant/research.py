"""One reproducible research loop shared by batch and incremental callers."""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from uuid import UUID

from northstar_quant.data.research import DatasetDetails, Market, ResearchBar, ResearchDataset
from northstar_quant.execution import Account, PendingOrder
from northstar_quant.risk import PortfolioState, RiskPolicy, evaluate_risk
from northstar_quant.strategy import decimal_text, momentum_intent


@dataclass(frozen=True, slots=True)
class ResearchConfig:
    initial_cash: Decimal = Decimal("100000")
    lookback: int = 1
    threshold: Decimal = Decimal("0.005")
    target_fraction: Decimal = Decimal("0.5")
    max_lots: int = 10
    max_gross_notional: Decimal = Decimal("1000000")
    max_margin_fraction: Decimal = Decimal("0.5")
    initial_margin_fraction: Decimal = Decimal("0.1")
    max_adverse_price_move_fraction: Decimal = Decimal("0.1")
    fee_per_lot: Decimal = Decimal("2")
    slippage_ticks: int = 1
    order_lifetime_seconds: int = 3600

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name in {"lookback", "max_lots", "slippage_ticks", "order_lifetime_seconds"}:
                minimum = 0 if item.name == "slippage_ticks" else 1
                maximum = 86400 if item.name == "order_lifetime_seconds" else 10000
                if type(value) is not int or not minimum <= value <= maximum:
                    raise ValueError(
                        f"research.{item.name} must be an integer in [{minimum}, {maximum}]"
                    )
            else:
                if (
                    not isinstance(value, Decimal)
                    or not value.is_finite()
                    or value < 0
                    or value >= Decimal("1e18")
                ):
                    raise ValueError(f"research.{item.name} must be a bounded nonnegative Decimal")
                exponent = value.as_tuple().exponent
                if not isinstance(exponent, int) or exponent < -18:
                    raise ValueError(f"research.{item.name} must use at most 18 decimal places")
        for name in ("initial_cash", "max_gross_notional"):
            if getattr(self, name) <= 0:
                raise ValueError(f"research.{name} must be positive")
        for name in ("target_fraction", "max_margin_fraction", "initial_margin_fraction"):
            if not Decimal(0) < getattr(self, name) <= Decimal(1):
                raise ValueError(f"research.{name} must be in (0, 1]")
        if not Decimal(0) < self.max_adverse_price_move_fraction < Decimal(1):
            raise ValueError("research.max_adverse_price_move_fraction must be in (0, 1)")
        if self.threshold > Decimal(1):
            raise ValueError("research.threshold must be in [0, 1]")

    @classmethod
    def from_mapping(cls, value: dict[str, object]) -> ResearchConfig:
        if not isinstance(value, dict):
            raise ValueError("research must be an object")
        defaults = asdict(cls())
        unknown = set(value) - set(defaults)
        if unknown:
            raise ValueError(f"unknown research fields: {', '.join(sorted(unknown))}")
        for key, item in value.items():
            if isinstance(defaults[key], Decimal):
                if (
                    not isinstance(item, str)
                    or len(item) > 38
                    or re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", item) is None
                ):
                    raise ValueError(f"research.{key} must be a plain decimal string")
                defaults[key] = Decimal(item)
            else:
                if type(item) is not int:
                    raise ValueError(f"research.{key} must be an integer")
                defaults[key] = item
        return cls(**defaults)

    def to_dict(self) -> dict[str, object]:
        return {
            key: decimal_text(value) if isinstance(value, Decimal) else value
            for key, value in asdict(self).items()
        }


@dataclass(frozen=True, slots=True)
class ResearchResult:
    """Canonical immutable bytes prevent callers mutating stored result identity."""

    _document: str

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = json.loads(self._document)
        return result


class TradingSession:
    """Advance one completed bar: old fill, account, strategy, then Risk.

    There is at most one pending target. A new decision replaces an unfilled
    prior target, so two decisions never spend the same account capacity.
    Identical observation retries are no-ops; changed or time-regressing facts
    fail before any account mutation.
    """

    def __init__(
        self,
        market: Market,
        config: ResearchConfig,
        *,
        snapshot_id: UUID,
        content_hash: str,
        data_details: DatasetDetails | None = None,
    ) -> None:
        if not isinstance(snapshot_id, UUID) or re.fullmatch(r"[0-9a-f]{64}", content_hash) is None:
            raise ValueError("research requires an exact snapshot UUID and SHA-256 identity")
        if (
            not isinstance(market.contract_id, UUID)
            or not isinstance(market.price_tick, Decimal)
            or not isinstance(market.multiplier, Decimal)
            or not market.price_tick.is_finite()
            or not market.multiplier.is_finite()
            or market.price_tick <= 0
            or market.multiplier <= 0
            or type(market.interval_seconds) is not int
            or market.interval_seconds <= 0
        ):
            raise ValueError("market must have exact positive economics and interval")
        for value in (market.price_tick, market.multiplier):
            exponent = value.as_tuple().exponent
            if (
                not isinstance(exponent, int)
                or exponent < -18
                or len(value.as_tuple().digits) > 34
                or value.adjusted() > 33
            ):
                raise ValueError("market economics exceed the 34-digit/18-place financial domain")
        self.market, self.config = market, config
        self.snapshot_id, self.content_hash = snapshot_id, content_hash
        if data_details is not None and (
            data_details.summary.snapshot_id != snapshot_id
            or data_details.summary.content_hash != content_hash
        ):
            raise ValueError("source evidence does not belong to this research snapshot")
        self._data_details = data_details
        self.account = Account(config.initial_cash, market)
        self.pending: PendingOrder | None = None
        self._policy = RiskPolicy(
            config.max_lots,
            config.max_gross_notional,
            config.max_margin_fraction,
            config.initial_margin_fraction,
            config.max_adverse_price_move_fraction,
            config.fee_per_lot,
            config.slippage_ticks,
        )
        self._history: deque[ResearchBar] = deque(maxlen=config.lookback + 1)
        self._seen: dict[UUID, ResearchBar] = {}
        self._trading_day: date | None = None
        self._last: ResearchBar | None = None
        self._decisions: list[dict[str, object]] = []
        self._curve: list[dict[str, object]] = []
        self._peak = config.initial_cash
        self._maximum_drawdown = Decimal(0)
        self._maximum_drawdown_fraction = Decimal(0)

    def advance(self, bar: ResearchBar) -> dict[str, object] | None:
        self._validate_bar(bar)
        if bar.observation_id in self._seen:
            if self._seen[bar.observation_id] != bar:
                raise ValueError("observation identity was reused with different facts")
            return None
        if self._last is not None and (
            bar.event_time <= self._last.event_time or bar.available_at < self._last.available_at
        ):
            raise ValueError("research rejects late or revised bars until revision replay exists")
        if self._trading_day is not None and self._trading_day != bar.trading_day:
            raise ValueError(
                "research currently supports one trading day; settlement is not modeled"
            )

        with localcontext() as context:
            context.prec = 96
            context.rounding = ROUND_HALF_EVEN
            return self._advance(bar)

    def _advance(self, bar: ResearchBar) -> dict[str, object]:
        self._trading_day = bar.trading_day
        if self.pending is not None:
            self.account.execute(
                self.pending,
                bar,
                fee_per_lot=self.config.fee_per_lot,
                slippage_ticks=self.config.slippage_ticks,
            )
            self.pending = None
        self._history.append(bar)
        self._seen[bar.observation_id] = bar
        self._last = bar
        equity = self.account.equity(bar.close)
        self._peak = max(self._peak, equity)
        drawdown = self._peak - equity
        drawdown_fraction = drawdown / self._peak
        self._maximum_drawdown = max(self._maximum_drawdown, drawdown)
        self._maximum_drawdown_fraction = max(self._maximum_drawdown_fraction, drawdown_fraction)
        point: dict[str, object] = {
            "observation_id": str(bar.observation_id),
            "at": bar.available_at.isoformat(),
            "close": decimal_text(bar.close),
            "cash": decimal_text(self.account.cash),
            "position_lots": self.account.position_lots,
            "realized_pnl": decimal_text(self.account.realized_pnl),
            "unrealized_pnl": decimal_text(self.account.unrealized_pnl(bar.close)),
            "total_fees": decimal_text(self.account.total_fees),
            "equity": decimal_text(equity),
            "drawdown": decimal_text(drawdown),
            "drawdown_fraction": decimal_text(drawdown_fraction),
        }
        self._curve.append(point)
        if len(self._history) > self.config.lookback:
            intent = momentum_intent(
                observation_id=bar.observation_id,
                contract_id=self.market.contract_id,
                at=bar.available_at,
                previous_close=self._history[0].close,
                close=bar.close,
                threshold=self.config.threshold,
                target_fraction=self.config.target_fraction,
                lifetime_seconds=self.config.order_lifetime_seconds,
            )
            risk = evaluate_risk(
                intent,
                PortfolioState(bar.available_at, equity, self.account.position_lots, bar.close),
                self._policy,
                self.market,
            )
            self._decisions.append(
                {
                    "observation_id": str(bar.observation_id),
                    "at": bar.available_at.isoformat(),
                    "momentum": decimal_text(intent.momentum),
                    "target_fraction": decimal_text(intent.target_fraction),
                    "outcome": risk.outcome.value,
                    "reason": risk.reason,
                    "desired_position_lots": risk.desired_position_lots,
                    "approved_position_lots": risk.approved_position_lots,
                    "side": None if risk.side is None else risk.side.value,
                    "quantity_lots": risk.quantity_lots,
                    "minimum_fill_price": None
                    if risk.minimum_fill_price is None
                    else decimal_text(risk.minimum_fill_price),
                    "maximum_fill_price": None
                    if risk.maximum_fill_price is None
                    else decimal_text(risk.maximum_fill_price),
                    "expires_at": risk.expires_at.isoformat(),
                }
            )
            if risk.quantity_lots:
                assert risk.side is not None
                assert risk.minimum_fill_price is not None and risk.maximum_fill_price is not None
                self.pending = PendingOrder(
                    intent.intent_id,
                    bar.observation_id,
                    bar.available_at,
                    risk.expires_at,
                    risk.side,
                    risk.quantity_lots,
                    risk.minimum_fill_price,
                    risk.maximum_fill_price,
                )
        return dict(point)

    def result(self) -> ResearchResult:
        if self._last is None:
            raise ValueError("research requires at least one bar")
        with localcontext() as context:
            context.prec = 96
            context.rounding = ROUND_HALF_EVEN
            equity = self.account.equity(self._last.close)
            payload: dict[str, object] = {
                "mode": "research",
                "data": None if self._data_details is None else self._data_details.to_dict(),
                "snapshot": {"id": str(self.snapshot_id), "content_hash": self.content_hash},
                "market": {
                    key: str(value)
                    if isinstance(value, UUID)
                    else decimal_text(value)
                    if isinstance(value, Decimal)
                    else value
                    for key, value in asdict(self.market).items()
                },
                "config": self.config.to_dict(),
                "summary": {
                    "bar_count": len(self._seen),
                    "decision_count": len(self._decisions),
                    "fill_count": len(self.account.fills),
                    "initial_cash": decimal_text(self.config.initial_cash),
                    "ending_cash": decimal_text(self.account.cash),
                    "ending_position_lots": self.account.position_lots,
                    "realized_pnl": decimal_text(self.account.realized_pnl),
                    "unrealized_pnl": decimal_text(self.account.unrealized_pnl(self._last.close)),
                    "total_fees": decimal_text(self.account.total_fees),
                    "ending_equity": decimal_text(equity),
                    "total_return": decimal_text(equity / self.config.initial_cash - 1),
                    "max_drawdown": decimal_text(self._maximum_drawdown),
                    "max_drawdown_fraction": decimal_text(self._maximum_drawdown_fraction),
                },
                "fills": [fill.to_dict() for fill in self.account.fills],
                "equity_curve": self._curve,
                "decisions": self._decisions,
                "pending_order": None if self.pending is None else self.pending.to_dict(),
                "assumptions": [
                    "Single-contract, single-trading-day linear futures; no settlement or funding.",
                    "A decision uses completed bars available then; fills use a strictly later "
                    "completed bar's close plus adverse tick slippage.",
                    "Orders fill completely without liquidity or volume modeling "
                    "and are replaced at each new decision.",
                    "Fees are charged per filled lot; Risk reserves fees "
                    "and mark-to-close slippage.",
                    "Open terminal positions are marked to the final observed close, "
                    "not forcibly liquidated.",
                    "Historical research is not live Paper or broker execution; "
                    "no annualized performance is inferred.",
                    "Fees, slippage and margin fractions are declared simulation assumptions, "
                    "not independently verified historical broker or exchange terms.",
                    *(
                        ("Calculation input has no verified source evidence.",)
                        if self._data_details is None
                        else self._data_details.limitations
                    ),
                ],
            }
        content = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        payload["result_hash"] = hashlib.sha256(content.encode()).hexdigest()
        return ResearchResult(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        )

    def _validate_bar(self, bar: ResearchBar) -> None:
        if not isinstance(bar, ResearchBar) or not isinstance(bar.observation_id, UUID):
            raise ValueError("research requires canonical observations")
        for at in (bar.event_time, bar.completed_at, bar.available_at):
            if not isinstance(at, datetime) or at.utcoffset() != timedelta(0):
                raise ValueError("bar times must be aware UTC")
        if (
            bar.completed_at != bar.event_time + timedelta(seconds=self.market.interval_seconds)
            or bar.available_at < bar.completed_at
        ):
            raise ValueError("bar availability cannot precede its declared completion")
        if type(bar.trading_day) is not date:
            raise ValueError("bar trading_day must be explicit")
        if not isinstance(bar.close, Decimal) or not bar.close.is_finite() or bar.close <= 0:
            raise ValueError("bar close must be a positive Decimal")
        exponent = bar.close.as_tuple().exponent
        if not isinstance(exponent, int) or exponent < -18 or len(bar.close.as_tuple().digits) > 34:
            raise ValueError("bar close exceeds the bounded 34-digit/18-place financial domain")
        if not isinstance(bar.volume, Decimal) or not bar.volume.is_finite() or bar.volume < 0:
            raise ValueError("bar volume must be a nonnegative Decimal")
        numerator, denominator = bar.close.as_integer_ratio()
        tick_numerator, tick_denominator = self.market.price_tick.as_integer_ratio()
        if (numerator * tick_denominator) % (denominator * tick_numerator):
            raise ValueError("bar close must be tick aligned")


def run_research(dataset: ResearchDataset, config: ResearchConfig) -> ResearchResult:
    if not isinstance(dataset, ResearchDataset) or len(dataset.bars) <= config.lookback:
        raise ValueError("research requires more bars than the configured lookback")
    if len(dataset.bars) > 100000:
        raise ValueError("research input exceeds 100000 bars")
    session = TradingSession(
        dataset.market,
        config,
        snapshot_id=dataset.snapshot_id,
        content_hash=dataset.content_hash,
        data_details=dataset.details,
    )
    for bar in sorted(
        dataset.bars,
        key=lambda item: (item.available_at, item.completed_at, str(item.observation_id)),
    ):
        session.advance(bar)
    return session.result()
