"""One reproducible research loop shared by batch and incremental callers."""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from collections.abc import Sequence
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from uuid import UUID

from northstar_quant.accounting import Account, AppliedFill, FillFact
from northstar_quant.data.research import DatasetDetails, Market, ResearchBar, ResearchDataset
from northstar_quant.execution import PendingOrder, simulate_fill
from northstar_quant.risk import PortfolioState, RiskPolicy, evaluate_risk
from northstar_quant.strategy import decimal_text, momentum_intent, validate_momentum_parameters


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
        validate_momentum_parameters(
            self.threshold, self.target_fraction, self.order_lifetime_seconds
        )
        self.risk_policy()

    def risk_policy(self) -> RiskPolicy:
        return RiskPolicy(
            self.max_lots,
            self.max_gross_notional,
            self.max_margin_fraction,
            self.initial_margin_fraction,
            self.max_adverse_price_move_fraction,
            self.fee_per_lot,
            self.slippage_ticks,
        )

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


@dataclass(frozen=True, slots=True)
class TradingStep:
    """Only the new facts from one advance; callers own the full persistent log."""

    point: dict[str, object]
    decision: dict[str, object] | None
    fill: AppliedFill | None
    new_order: PendingOrder | None

    def to_dict(self) -> dict[str, object]:
        return {
            "point": dict(self.point),
            "decision": None if self.decision is None else dict(self.decision),
            "fill": None if self.fill is None else self.fill.to_dict(),
            "new_order": None if self.new_order is None else self.new_order.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> TradingStep:
        try:
            if set(value) != {"point", "decision", "fill", "new_order"}:
                raise ValueError("trading step must contain its four fact fields")
            raw_fill = value["fill"]
            fill = None
            if raw_fill is not None:
                item = _object(raw_fill)
                position = item["position_lots"]
                if type(position) is not int:
                    raise ValueError("ledger position must be integer lots")
                fill = AppliedFill(
                    FillFact.from_dict(item),
                    _money(item["realized_pnl"]),
                    position,
                    _money(item["cash"]),
                    _money(item["total_fees"]),
                )
            return cls(
                dict(_object(value["point"])),
                None if value["decision"] is None else dict(_object(value["decision"])),
                fill,
                None
                if value["new_order"] is None
                else PendingOrder.from_dict(_object(value["new_order"])),
            )
        except (KeyError, TypeError, ArithmeticError) as error:
            raise ValueError("invalid persisted trading step") from error


class TradingSession:
    """Bounded shared research/Paper core: simulate, account, strategy, then Risk.

    There is at most one pending target. A new decision replaces an unfilled
    prior target, so two decisions never spend the same account capacity.
    Recent identical observations are no-ops; older retries are owned by the
    caller's persistent input identity. Changed or time-regressing facts fail
    before mutation. History is bounded by lookback, not the number of steps.
    This remains a simulated execution loop, not a broker execution adapter.
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
        self._policy = config.risk_policy()
        self._history: deque[ResearchBar] = deque(maxlen=config.lookback + 1)
        self._trading_day: date | None = None
        self._last: ResearchBar | None = None
        self._bar_count = 0
        self._decision_count = 0
        self._peak = config.initial_cash
        self._maximum_drawdown = Decimal(0)
        self._maximum_drawdown_fraction = Decimal(0)

    def advance(self, bar: ResearchBar) -> TradingStep | None:
        self._validate_bar(bar)
        for previous in self._history:
            if bar.observation_id == previous.observation_id:
                if previous != bar:
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

    def _advance(self, bar: ResearchBar) -> TradingStep:
        self._trading_day = bar.trading_day
        fill = None
        if self.pending is not None:
            fact = simulate_fill(
                self.pending,
                bar,
                self.market,
                fee_per_lot=self.config.fee_per_lot,
                slippage_ticks=self.config.slippage_ticks,
            )
            if fact is not None:
                fill = self.account.apply(fact)
            self.pending = None
        self._history.append(bar)
        self._bar_count += 1
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
        decision: dict[str, object] | None = None
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
            decision = {
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
            self._decision_count += 1
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
        return TradingStep(point, decision, fill, self.pending)

    def summary(self) -> dict[str, object]:
        """Current metrics, including a genuinely empty initialized account."""

        with localcontext() as context:
            context.prec = 96
            context.rounding = ROUND_HALF_EVEN
            unrealized = (
                Decimal(0) if self._last is None else self.account.unrealized_pnl(self._last.close)
            )
            equity = self.account.cash + unrealized
            return {
                "bar_count": self._bar_count,
                "decision_count": self._decision_count,
                "fill_count": self.account.fill_count,
                "initial_cash": decimal_text(self.config.initial_cash),
                "ending_cash": decimal_text(self.account.cash),
                "ending_position_lots": self.account.position_lots,
                "realized_pnl": decimal_text(self.account.realized_pnl),
                "unrealized_pnl": decimal_text(unrealized),
                "total_fees": decimal_text(self.account.total_fees),
                "ending_equity": decimal_text(equity),
                "total_return": decimal_text(equity / self.config.initial_cash - 1),
                "max_drawdown": decimal_text(self._maximum_drawdown),
                "max_drawdown_fraction": decimal_text(self._maximum_drawdown_fraction),
            }

    def result(self, steps: Sequence[TradingStep]) -> ResearchResult:
        """Assemble a report on demand, never copying historical rows per advance."""

        if self._last is None:
            raise ValueError("research requires at least one bar")
        if (
            len(steps) != self._bar_count
            or sum(step.decision is not None for step in steps) != self._decision_count
            or sum(step.fill is not None for step in steps) != self.account.fill_count
            or steps[-1].point["observation_id"] != str(self._last.observation_id)
        ):
            raise ValueError("research report requires the complete committed step history")
        with localcontext() as context:
            context.prec = 96
            context.rounding = ROUND_HALF_EVEN
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
                "summary": self.summary(),
                "fills": [step.fill.to_dict() for step in steps if step.fill is not None],
                "equity_curve": [step.point for step in steps],
                "decisions": [step.decision for step in steps if step.decision is not None],
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

    def checkpoint(self) -> dict[str, object]:
        """Persist only the current projection and bounded strategy warmup.

        This is acceleration material. Restoration requires an independently
        rebuilt Account; the caller protects this document and its committed
        step identity in the same transaction as the ledger facts.
        """

        return {
            "snapshot_id": str(self.snapshot_id),
            "content_hash": self.content_hash,
            "market": {
                name: str(value)
                if isinstance(value, UUID)
                else decimal_text(value)
                if isinstance(value, Decimal)
                else value
                for name, value in asdict(self.market).items()
            },
            "config": self.config.to_dict(),
            "account": self.account.checkpoint(),
            "history": [_bar_dict(bar) for bar in self._history],
            "last": None if self._last is None else _bar_dict(self._last),
            "trading_day": None if self._trading_day is None else self._trading_day.isoformat(),
            "pending": None if self.pending is None else self.pending.to_dict(),
            "bar_count": self._bar_count,
            "decision_count": self._decision_count,
            "peak": decimal_text(self._peak),
            "maximum_drawdown": decimal_text(self._maximum_drawdown),
            "maximum_drawdown_fraction": decimal_text(self._maximum_drawdown_fraction),
        }

    @classmethod
    def from_checkpoint(
        cls,
        market: Market,
        config: ResearchConfig,
        *,
        snapshot_id: UUID,
        content_hash: str,
        checkpoint: dict[str, object],
        account: Account,
        data_details: DatasetDetails | None = None,
    ) -> TradingSession:
        """Resume bounded computation after comparing the independently rebuilt ledger.

        No market history is replayed here. Account facts are never read from
        this checkpoint to construct balances; the persistent caller supplies
        its ledger-derived Account and verifies committed step/checkpoint hashes.
        """

        session = cls(
            market,
            config,
            snapshot_id=snapshot_id,
            content_hash=content_hash,
            data_details=data_details,
        )
        initial = session.checkpoint()
        if not isinstance(checkpoint, dict) or set(checkpoint) != set(initial):
            raise ValueError("checkpoint fields do not match the current trading implementation")
        for name in ("snapshot_id", "content_hash", "market", "config"):
            if checkpoint[name] != initial[name]:
                raise ValueError("checkpoint differs from its fixed input or configuration")
        if (
            account.market != market
            or account.initial_cash != config.initial_cash
            or checkpoint["account"] != account.checkpoint()
        ):
            raise ValueError("checkpoint account differs from the verified fill ledger")
        count, decisions = checkpoint["bar_count"], checkpoint["decision_count"]
        if (
            type(count) is not int
            or not 0 <= count <= 100000
            or type(decisions) is not int
            or decisions != max(0, count - config.lookback)
            or account.fill_count > max(0, decisions - 1)
        ):
            raise ValueError("checkpoint counters differ from the trading sequence")
        history = checkpoint["history"]
        if not isinstance(history, list) or len(history) != min(count, config.lookback + 1):
            raise ValueError("checkpoint warmup must have the exact bounded history")
        previous = None
        history_ids: set[UUID] = set()
        for item in history:
            bar = _bar_from_dict(_object(item))
            session._validate_bar(bar)
            if bar.observation_id in history_ids:
                raise ValueError("checkpoint history repeats an observation identity")
            history_ids.add(bar.observation_id)
            if previous is not None and (
                bar.event_time <= previous.event_time
                or bar.available_at < previous.available_at
                or bar.trading_day != previous.trading_day
                or bar.observation_id == previous.observation_id
            ):
                raise ValueError("checkpoint history is not one ordered trading day")
            session._history.append(bar)
            previous = bar
        session._last = previous
        session._trading_day = None if previous is None else previous.trading_day
        if checkpoint["last"] != (None if previous is None else _bar_dict(previous)) or checkpoint[
            "trading_day"
        ] != (None if previous is None else previous.trading_day.isoformat()):
            raise ValueError("checkpoint last observation differs from its warmup history")
        pending = checkpoint["pending"]
        session.pending = None if pending is None else PendingOrder.from_dict(_object(pending))
        if session.pending is not None and (
            previous is None
            or not decisions
            or session.pending.observation_id != previous.observation_id
            or session.pending.submitted_at != previous.available_at
            or session.pending.quantity_lots > config.max_lots
        ):
            raise ValueError("checkpoint pending order differs from its last decision")
        session.account = account
        session._bar_count, session._decision_count = count, decisions
        session._peak = _money(checkpoint["peak"])
        session._maximum_drawdown = _money(checkpoint["maximum_drawdown"])
        session._maximum_drawdown_fraction = _money(checkpoint["maximum_drawdown_fraction"])
        if (
            session._peak < config.initial_cash
            or session._maximum_drawdown < 0
            or session._maximum_drawdown_fraction < 0
        ):
            raise ValueError("checkpoint drawdown state is invalid")
        if previous is not None:
            with localcontext() as context:
                context.prec = 96
                context.rounding = ROUND_HALF_EVEN
                equity = account.equity(previous.close)
                current_drawdown = session._peak - equity
                if (
                    current_drawdown < 0
                    or session._maximum_drawdown < current_drawdown
                    or session._maximum_drawdown_fraction < current_drawdown / session._peak
                ):
                    raise ValueError("checkpoint drawdown disagrees with current account equity")
        if not count and session.checkpoint() != initial:
            raise ValueError("empty checkpoint must describe its initialized account")
        if session.checkpoint() != checkpoint:
            raise ValueError("checkpoint representation is not canonical")
        return session

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
        if (
            not isinstance(exponent, int)
            or exponent < -18
            or len(bar.close.as_tuple().digits) > 34
            or bar.close.adjusted() > 33
        ):
            raise ValueError("bar close exceeds the bounded 34-digit/18-place financial domain")
        if not isinstance(bar.volume, Decimal) or not bar.volume.is_finite() or bar.volume < 0:
            raise ValueError("bar volume must be a nonnegative Decimal")
        volume_exponent = bar.volume.as_tuple().exponent
        if (
            not isinstance(volume_exponent, int)
            or volume_exponent < -18
            or len(bar.volume.as_tuple().digits) > 34
            or bar.volume.adjusted() > 33
        ):
            raise ValueError("bar volume exceeds the bounded observation domain")
        numerator, denominator = bar.close.as_integer_ratio()
        tick_numerator, tick_denominator = self.market.price_tick.as_integer_ratio()
        if (numerator * tick_denominator) % (denominator * tick_numerator):
            raise ValueError("bar close must be tick aligned")


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("persisted trading state must be an object")
    return value


def _money(value: object) -> Decimal:
    if not isinstance(value, str) or len(value) > 256:
        raise ValueError("persisted trading amounts must be exact decimal strings")
    try:
        result = Decimal(value)
    except ArithmeticError as error:
        raise ValueError("invalid persisted trading amount") from error
    exponent = result.as_tuple().exponent
    if (
        not result.is_finite()
        or not isinstance(exponent, int)
        or exponent < -192
        or result.adjusted() > 96
        or len(result.as_tuple().digits) > 192
    ):
        raise ValueError("persisted trading amount must be finite and bounded")
    return result


def _bar_dict(bar: ResearchBar) -> dict[str, object]:
    return {
        "observation_id": str(bar.observation_id),
        "event_time": bar.event_time.isoformat(),
        "completed_at": bar.completed_at.isoformat(),
        "available_at": bar.available_at.isoformat(),
        "trading_day": bar.trading_day.isoformat(),
        "close": decimal_text(bar.close),
        "volume": decimal_text(bar.volume),
    }


def _bar_from_dict(value: dict[str, object]) -> ResearchBar:
    try:
        return ResearchBar(
            UUID(str(value["observation_id"])),
            datetime.fromisoformat(str(value["event_time"])),
            datetime.fromisoformat(str(value["completed_at"])),
            datetime.fromisoformat(str(value["available_at"])),
            date.fromisoformat(str(value["trading_day"])),
            _money(value["close"]),
            _money(value["volume"]),
        )
    except (KeyError, TypeError, ArithmeticError) as error:
        raise ValueError("invalid persisted warmup observation") from error


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
    steps: list[TradingStep] = []
    for bar in sorted(
        dataset.bars,
        key=lambda item: (item.available_at, item.completed_at, str(item.observation_id)),
    ):
        step = session.advance(bar)
        if step is not None:
            steps.append(step)
    return session.result(steps)
