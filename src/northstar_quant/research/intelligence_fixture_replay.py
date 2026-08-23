"""Fixture-only intelligence replay evidence for P10 research.

This module is intentionally a narrow, pure computation boundary.  It accepts
only scalar fixture observations and immutable upstream handoff hashes; it does
not read market data, source data, a clock, files, a database, a broker, or an
application composition root.  In particular, it is *not* a P1/PIT market-data
adapter and cannot create a feature value, target, approval, execution plan,
order, trade, fill, or trading permission.

The controlled six-commodity corpus is useful for proving a research-only
point-in-time sequence:

``fixture handoff -> decision-visible observation -> synthetic later outcome``

The outcome is deliberately synthetic and is evaluated only after its declared
availability time.  The resulting score is an offline research statistic, not
a return, signal, target, or instruction.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import InitVar, dataclass, field
from datetime import UTC, datetime
from enum import Enum
import math
import re
from typing import Final

from northstar_quant.data.artifacts.fingerprints import (
    FingerprintError,
    canonical_json_sha256,
    require_sha256,
)


_IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_FORMAT_PREFIX: Final = "northstar.p10.fixture-intelligence-replay"
_RESULT_ISSUER: Final = object()
_SCORE_ISSUER: Final = object()
_MANIFEST_ISSUER: Final = object()

# This is intentionally an exact, closed fixture universe.  A real commodity,
# market, instrument, or contract universe belongs in the data domain and
# intelligence contracts, not in this P10 fixture-only research boundary.
SIX_COMMODITY_FIXTURE_UNIVERSE: Final[frozenset[str]] = frozenset(
    {
        "copper",
        "crude_oil",
        "gold",
        "iron_ore",
        "soybean_meal",
        "palm_oil",
    }
)


class FixtureOnlyReplayError(ValueError):
    """Fixture replay inputs are incomplete, non-deterministic, or unsafe."""


class FixtureObservationLifecycle(str, Enum):
    """The only lifecycle states represented by a replay observation.

    A retraction is an explicit missing feature observation, never a reason to
    reuse a previously confirmed scalar at later decision checkpoints.
    """

    CONFIRMED = "confirmed"
    RESOLVED = "resolved"
    RETRACTED = "retracted"


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value.strip()) is None:
        raise FixtureOnlyReplayError(f"{field_name} must be a non-empty identifier")
    return value.strip()


def _fixture_commodity(value: object, field_name: str) -> str:
    commodity_id = _identifier(value, field_name)
    if commodity_id not in SIX_COMMODITY_FIXTURE_UNIVERSE:
        supported = ", ".join(sorted(SIX_COMMODITY_FIXTURE_UNIVERSE))
        raise FixtureOnlyReplayError(
            f"{field_name} must be one of the closed six-commodity fixture universe: {supported}"
        )
    return commodity_id


def _sha256(value: object, field_name: str) -> str:
    try:
        return require_sha256(value, field_name=field_name)  # type: ignore[arg-type]
    except FingerprintError as exc:
        raise FixtureOnlyReplayError(str(exc)) from exc


def _utc_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise FixtureOnlyReplayError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FixtureOnlyReplayError(f"{field_name} must be a finite number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise FixtureOnlyReplayError(f"{field_name} must be a finite number")
    return normalized


def _require_true(value: object, field_name: str) -> None:
    if value is not True:
        raise FixtureOnlyReplayError(f"{field_name} must be explicitly true")


def _score_hex(value: float) -> str:
    """Bind binary float semantics instead of a locale-dependent rendering."""

    return value.hex()


@dataclass(frozen=True, slots=True)
class FixtureFeatureDefinitionHandoff:
    """A hash-only wrapper around one P10 upstream fixture definition handoff.

    The upstream object remains outside this runtime module.  This wrapper
    preserves its immutable commitment hash without importing test-only corpus
    code or promoting it into an authorized market-data feature.
    """

    commodity_id: str
    feature_id: str
    feature_version_hash: str
    upstream_handoff_hash: str
    fixture_only: bool = True
    research_only: bool = True
    handoff_binding_hash: str = field(init=False)

    @property
    def eligible_for_admission(self) -> bool:
        """A fixture handoff never becomes a candidate admission input."""

        return False

    @property
    def eligible_for_trading(self) -> bool:
        """A fixture handoff never grants trading eligibility."""

        return False

    def __post_init__(self) -> None:
        commodity_id = _fixture_commodity(self.commodity_id, "handoff.commodity_id")
        feature_id = _identifier(self.feature_id, "handoff.feature_id")
        if not feature_id.startswith("intelligence."):
            raise FixtureOnlyReplayError("handoff.feature_id must be an intelligence feature")
        feature_version_hash = _sha256(
            self.feature_version_hash,
            "handoff.feature_version_hash",
        )
        upstream_handoff_hash = _sha256(
            self.upstream_handoff_hash,
            "handoff.upstream_handoff_hash",
        )
        _require_true(self.fixture_only, "handoff.fixture_only")
        _require_true(self.research_only, "handoff.research_only")
        handoff_binding_hash = canonical_json_sha256(
            {
                "commodity_id": commodity_id,
                "feature_id": feature_id,
                "feature_version_hash": feature_version_hash,
                "fixture_only": True,
                "format": f"{_FORMAT_PREFIX}.handoff-binding.v1",
                "research_only": True,
                "upstream_handoff_hash": upstream_handoff_hash,
            }
        )
        object.__setattr__(self, "commodity_id", commodity_id)
        object.__setattr__(self, "feature_id", feature_id)
        object.__setattr__(self, "feature_version_hash", feature_version_hash)
        object.__setattr__(self, "upstream_handoff_hash", upstream_handoff_hash)
        object.__setattr__(self, "fixture_only", True)
        object.__setattr__(self, "research_only", True)
        object.__setattr__(self, "handoff_binding_hash", handoff_binding_hash)

    def as_mapping(self) -> dict[str, object]:
        return {
            "commodity_id": self.commodity_id,
            "feature_id": self.feature_id,
            "feature_version_hash": self.feature_version_hash,
            "fixture_only": True,
            "handoff_binding_hash": self.handoff_binding_hash,
            "research_only": True,
            "upstream_handoff_hash": self.upstream_handoff_hash,
        }


@dataclass(frozen=True, slots=True)
class FixtureReplayObservation:
    """One fixture feature observation known at a particular decision checkpoint.

    ``available_at`` is checked by :class:`FixtureReplayCheckpoint`; a caller
    cannot state that a later observation was visible at an earlier decision.
    A retracted event must be an explicit, scored-as-zero missing observation;
    it cannot silently retain the earlier confirmed value.
    """

    checkpoint_id: str
    commodity_id: str
    feature_id: str
    feature_version_hash: str
    upstream_handoff_hash: str
    available_at: datetime
    lifecycle: FixtureObservationLifecycle
    value: float | None
    missing_reason: str | None = None
    fixture_only: bool = True
    research_only: bool = True
    observation_hash: str = field(init=False)

    def __post_init__(self) -> None:
        checkpoint_id = _identifier(self.checkpoint_id, "observation.checkpoint_id")
        commodity_id = _fixture_commodity(self.commodity_id, "observation.commodity_id")
        feature_id = _identifier(self.feature_id, "observation.feature_id")
        if not feature_id.startswith("intelligence."):
            raise FixtureOnlyReplayError("observation.feature_id must be an intelligence feature")
        feature_version_hash = _sha256(
            self.feature_version_hash,
            "observation.feature_version_hash",
        )
        upstream_handoff_hash = _sha256(
            self.upstream_handoff_hash,
            "observation.upstream_handoff_hash",
        )
        available_at = _utc_datetime(self.available_at, "observation.available_at")
        if not isinstance(self.lifecycle, FixtureObservationLifecycle):
            raise FixtureOnlyReplayError("observation.lifecycle must be a FixtureObservationLifecycle")
        missing_reason = (
            _identifier(self.missing_reason, "observation.missing_reason")
            if self.missing_reason is not None
            else None
        )
        if self.lifecycle is FixtureObservationLifecycle.RETRACTED:
            if self.value is not None or missing_reason != "event_retracted":
                raise FixtureOnlyReplayError(
                    "a retracted observation must have value=None and missing_reason=event_retracted"
                )
            value = None
        else:
            if missing_reason is not None:
                raise FixtureOnlyReplayError(
                    "a scored fixture observation cannot carry a missing_reason"
                )
            value = _finite(self.value, "observation.value")
        _require_true(self.fixture_only, "observation.fixture_only")
        _require_true(self.research_only, "observation.research_only")
        observation_hash = canonical_json_sha256(
            {
                "available_at": available_at.isoformat(),
                "checkpoint_id": checkpoint_id,
                "commodity_id": commodity_id,
                "feature_id": feature_id,
                "feature_version_hash": feature_version_hash,
                "fixture_only": True,
                "format": f"{_FORMAT_PREFIX}.observation.v1",
                "lifecycle": self.lifecycle.value,
                "missing_reason": missing_reason,
                "research_only": True,
                "upstream_handoff_hash": upstream_handoff_hash,
                "value": _score_hex(value) if value is not None else None,
            }
        )
        object.__setattr__(self, "checkpoint_id", checkpoint_id)
        object.__setattr__(self, "commodity_id", commodity_id)
        object.__setattr__(self, "feature_id", feature_id)
        object.__setattr__(self, "feature_version_hash", feature_version_hash)
        object.__setattr__(self, "upstream_handoff_hash", upstream_handoff_hash)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "missing_reason", missing_reason)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "fixture_only", True)
        object.__setattr__(self, "research_only", True)
        object.__setattr__(self, "observation_hash", observation_hash)

    def as_mapping(self) -> dict[str, object]:
        return {
            "available_at": self.available_at.isoformat(),
            "checkpoint_id": self.checkpoint_id,
            "commodity_id": self.commodity_id,
            "feature_id": self.feature_id,
            "feature_version_hash": self.feature_version_hash,
            "fixture_only": True,
            "lifecycle": self.lifecycle.value,
            "missing_reason": self.missing_reason,
            "observation_hash": self.observation_hash,
            "research_only": True,
            "upstream_handoff_hash": self.upstream_handoff_hash,
            "value": _score_hex(self.value) if self.value is not None else None,
        }


@dataclass(frozen=True, slots=True)
class FixtureReplayCheckpoint:
    """A complete six-commodity feature vector at one PIT decision time."""

    checkpoint_id: str
    decision_at: datetime
    observations: tuple[FixtureReplayObservation, ...]
    checkpoint_hash: str = field(init=False)

    @classmethod
    def create(
        cls,
        *,
        checkpoint_id: str,
        decision_at: datetime,
        observations: Iterable[FixtureReplayObservation],
    ) -> "FixtureReplayCheckpoint":
        return cls(
            checkpoint_id=checkpoint_id,
            decision_at=decision_at,
            observations=tuple(observations),
        )

    def __post_init__(self) -> None:
        checkpoint_id = _identifier(self.checkpoint_id, "checkpoint.checkpoint_id")
        decision_at = _utc_datetime(self.decision_at, "checkpoint.decision_at")
        observations = tuple(self.observations)
        if len(observations) != len(SIX_COMMODITY_FIXTURE_UNIVERSE) or not all(
            isinstance(item, FixtureReplayObservation) for item in observations
        ):
            raise FixtureOnlyReplayError(
                "checkpoint.observations must contain exactly one FixtureReplayObservation for each fixture commodity"
            )
        commodities = {item.commodity_id for item in observations}
        if commodities != SIX_COMMODITY_FIXTURE_UNIVERSE:
            raise FixtureOnlyReplayError(
                "checkpoint.observations must cover the exact six-commodity fixture universe"
            )
        if any(item.checkpoint_id != checkpoint_id for item in observations):
            raise FixtureOnlyReplayError("observation.checkpoint_id must bind its exact checkpoint")
        if any(item.available_at > decision_at for item in observations):
            raise FixtureOnlyReplayError(
                "PIT_OBSERVATION_NOT_AVAILABLE_AT_DECISION: observation.available_at must be <= decision_at"
            )
        canonical_observations = tuple(sorted(observations, key=lambda item: item.commodity_id))
        checkpoint_hash = canonical_json_sha256(
            {
                "checkpoint_id": checkpoint_id,
                "decision_at": decision_at.isoformat(),
                "format": f"{_FORMAT_PREFIX}.checkpoint.v1",
                "observation_hashes": [item.observation_hash for item in canonical_observations],
            }
        )
        object.__setattr__(self, "checkpoint_id", checkpoint_id)
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(self, "observations", canonical_observations)
        object.__setattr__(self, "checkpoint_hash", checkpoint_hash)

    def as_mapping(self) -> dict[str, object]:
        return {
            "checkpoint_hash": self.checkpoint_hash,
            "checkpoint_id": self.checkpoint_id,
            "decision_at": self.decision_at.isoformat(),
            "observations": [item.as_mapping() for item in self.observations],
        }


@dataclass(frozen=True, slots=True)
class FixtureSyntheticOutcome:
    """A synthetic holdout outcome, deliberately unavailable at decision time."""

    checkpoint_id: str
    commodity_id: str
    available_at: datetime
    value: float
    fixture_only: bool = True
    research_only: bool = True
    outcome_hash: str = field(init=False)

    def __post_init__(self) -> None:
        checkpoint_id = _identifier(self.checkpoint_id, "outcome.checkpoint_id")
        commodity_id = _fixture_commodity(self.commodity_id, "outcome.commodity_id")
        available_at = _utc_datetime(self.available_at, "outcome.available_at")
        value = _finite(self.value, "outcome.value")
        _require_true(self.fixture_only, "outcome.fixture_only")
        _require_true(self.research_only, "outcome.research_only")
        outcome_hash = canonical_json_sha256(
            {
                "available_at": available_at.isoformat(),
                "checkpoint_id": checkpoint_id,
                "commodity_id": commodity_id,
                "fixture_only": True,
                "format": f"{_FORMAT_PREFIX}.synthetic-outcome.v1",
                "research_only": True,
                "value": _score_hex(value),
            }
        )
        object.__setattr__(self, "checkpoint_id", checkpoint_id)
        object.__setattr__(self, "commodity_id", commodity_id)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "fixture_only", True)
        object.__setattr__(self, "research_only", True)
        object.__setattr__(self, "outcome_hash", outcome_hash)

    def as_mapping(self) -> dict[str, object]:
        return {
            "available_at": self.available_at.isoformat(),
            "checkpoint_id": self.checkpoint_id,
            "commodity_id": self.commodity_id,
            "fixture_only": True,
            "outcome_hash": self.outcome_hash,
            "research_only": True,
            "value": _score_hex(self.value),
        }


@dataclass(frozen=True, slots=True)
class FixtureOnlyReplayPlan:
    """Complete controlled input to the fixture-only research replay runner."""

    upstream_corpus_sha256: str
    handoffs: tuple[FixtureFeatureDefinitionHandoff, ...]
    checkpoints: tuple[FixtureReplayCheckpoint, ...]
    outcomes: tuple[FixtureSyntheticOutcome, ...]
    plan_hash: str = field(init=False)

    @classmethod
    def create(
        cls,
        *,
        upstream_corpus_sha256: str,
        handoffs: Iterable[FixtureFeatureDefinitionHandoff],
        checkpoints: Iterable[FixtureReplayCheckpoint],
        outcomes: Iterable[FixtureSyntheticOutcome],
    ) -> "FixtureOnlyReplayPlan":
        return cls(
            upstream_corpus_sha256=upstream_corpus_sha256,
            handoffs=tuple(handoffs),
            checkpoints=tuple(checkpoints),
            outcomes=tuple(outcomes),
        )

    @property
    def fixture_only(self) -> bool:
        return True

    @property
    def research_only(self) -> bool:
        return True

    @property
    def eligible_for_admission(self) -> bool:
        return False

    @property
    def eligible_for_trading(self) -> bool:
        return False

    def __post_init__(self) -> None:
        upstream_corpus_sha256 = _sha256(
            self.upstream_corpus_sha256,
            "plan.upstream_corpus_sha256",
        )
        handoffs = tuple(self.handoffs)
        if len(handoffs) != len(SIX_COMMODITY_FIXTURE_UNIVERSE) or not all(
            isinstance(item, FixtureFeatureDefinitionHandoff) for item in handoffs
        ):
            raise FixtureOnlyReplayError(
                "plan.handoffs must contain exactly six FixtureFeatureDefinitionHandoff values"
            )
        by_commodity = {item.commodity_id: item for item in handoffs}
        if set(by_commodity) != SIX_COMMODITY_FIXTURE_UNIVERSE or len(by_commodity) != len(handoffs):
            raise FixtureOnlyReplayError(
                "plan.handoffs must bind exactly one handoff for each fixture commodity"
            )
        if len({item.upstream_handoff_hash for item in handoffs}) != len(handoffs):
            raise FixtureOnlyReplayError("plan.handoffs cannot reuse an upstream handoff hash")
        canonical_handoffs = tuple(sorted(handoffs, key=lambda item: item.commodity_id))

        checkpoints = tuple(self.checkpoints)
        if not checkpoints or not all(isinstance(item, FixtureReplayCheckpoint) for item in checkpoints):
            raise FixtureOnlyReplayError(
                "plan.checkpoints must be a non-empty FixtureReplayCheckpoint tuple"
            )
        if tuple(sorted(checkpoints, key=lambda item: item.decision_at)) != checkpoints:
            raise FixtureOnlyReplayError("plan.checkpoints must be ordered by strictly increasing decision_at")
        if len({item.decision_at for item in checkpoints}) != len(checkpoints):
            raise FixtureOnlyReplayError("plan.checkpoints cannot reuse a decision_at")
        if len({item.checkpoint_id for item in checkpoints}) != len(checkpoints):
            raise FixtureOnlyReplayError("plan.checkpoints cannot reuse a checkpoint_id")
        for checkpoint in checkpoints:
            for observation in checkpoint.observations:
                handoff = by_commodity[observation.commodity_id]
                if observation.feature_id != handoff.feature_id:
                    raise FixtureOnlyReplayError(
                        "observation.feature_id must bind the handoff for its commodity"
                    )
                if observation.feature_version_hash != handoff.feature_version_hash:
                    raise FixtureOnlyReplayError(
                        "observation.feature_version_hash must bind the handoff for its commodity"
                    )
                if observation.upstream_handoff_hash != handoff.upstream_handoff_hash:
                    raise FixtureOnlyReplayError(
                        "observation.upstream_handoff_hash must bind the handoff for its commodity"
                    )

        outcomes = tuple(self.outcomes)
        expected_outcomes = len(checkpoints) * len(SIX_COMMODITY_FIXTURE_UNIVERSE)
        if len(outcomes) != expected_outcomes or not all(
            isinstance(item, FixtureSyntheticOutcome) for item in outcomes
        ):
            raise FixtureOnlyReplayError(
                "plan.outcomes must contain exactly one synthetic outcome per checkpoint and fixture commodity"
            )
        checkpoints_by_id = {item.checkpoint_id: item for item in checkpoints}
        outcome_pairs: set[tuple[str, str]] = set()
        for outcome in outcomes:
            try:
                checkpoint = checkpoints_by_id[outcome.checkpoint_id]
            except KeyError as exc:
                raise FixtureOnlyReplayError(
                    "outcome.checkpoint_id must bind an exact replay checkpoint"
                ) from exc
            pair = (outcome.checkpoint_id, outcome.commodity_id)
            if pair in outcome_pairs:
                raise FixtureOnlyReplayError(
                    "plan.outcomes cannot contain duplicate checkpoint/commodity outcomes"
                )
            outcome_pairs.add(pair)
            if outcome.available_at <= checkpoint.decision_at:
                raise FixtureOnlyReplayError(
                    "SYNTHETIC_OUTCOME_VISIBLE_AT_DECISION: outcome.available_at must be strictly after decision_at"
                )
        expected_pairs = {
            (checkpoint.checkpoint_id, commodity_id)
            for checkpoint in checkpoints
            for commodity_id in SIX_COMMODITY_FIXTURE_UNIVERSE
        }
        if outcome_pairs != expected_pairs:
            raise FixtureOnlyReplayError(
                "plan.outcomes must cover the exact fixture commodity universe at every checkpoint"
            )
        canonical_outcomes = tuple(
            sorted(outcomes, key=lambda item: (checkpoints_by_id[item.checkpoint_id].decision_at, item.commodity_id))
        )
        plan_hash = canonical_json_sha256(
            {
                "checkpoint_hashes": [item.checkpoint_hash for item in checkpoints],
                "eligible_for_admission": False,
                "eligible_for_trading": False,
                "fixture_only": True,
                "format": f"{_FORMAT_PREFIX}.plan.v1",
                "handoff_binding_hashes": [item.handoff_binding_hash for item in canonical_handoffs],
                "outcome_hashes": [item.outcome_hash for item in canonical_outcomes],
                "research_only": True,
                "upstream_corpus_sha256": upstream_corpus_sha256,
            }
        )
        object.__setattr__(self, "upstream_corpus_sha256", upstream_corpus_sha256)
        object.__setattr__(self, "handoffs", canonical_handoffs)
        object.__setattr__(self, "checkpoints", checkpoints)
        object.__setattr__(self, "outcomes", canonical_outcomes)
        object.__setattr__(self, "plan_hash", plan_hash)

    def as_mapping(self) -> dict[str, object]:
        return {
            "checkpoints": [item.as_mapping() for item in self.checkpoints],
            "eligible_for_admission": False,
            "eligible_for_trading": False,
            "fixture_only": True,
            "format": f"{_FORMAT_PREFIX}.plan.v1",
            "handoffs": [item.as_mapping() for item in self.handoffs],
            "outcomes": [item.as_mapping() for item in self.outcomes],
            "plan_hash": self.plan_hash,
            "research_only": True,
            "upstream_corpus_sha256": self.upstream_corpus_sha256,
        }


@dataclass(frozen=True, slots=True)
class FixtureReplayScore:
    """One post-outcome scalar score; it is not a return or a trade result."""

    checkpoint_id: str
    decision_at: datetime
    outcome_available_at: datetime
    observation_hashes: tuple[str, ...]
    outcome_hashes: tuple[str, ...]
    synthetic_alignment_score: float
    score_hash: str = field(init=False)
    _issuer: InitVar[object | None] = None

    def __post_init__(self, _issuer: object | None) -> None:
        if _issuer is not _SCORE_ISSUER:
            raise FixtureOnlyReplayError(
                "FixtureReplayScore is factory-only; use FixtureOnlyReplayRunner.run"
            )
        checkpoint_id = _identifier(self.checkpoint_id, "score.checkpoint_id")
        decision_at = _utc_datetime(self.decision_at, "score.decision_at")
        outcome_available_at = _utc_datetime(
            self.outcome_available_at,
            "score.outcome_available_at",
        )
        if outcome_available_at <= decision_at:
            raise FixtureOnlyReplayError(
                "score.outcome_available_at must be strictly after score.decision_at"
            )
        observation_hashes = tuple(_sha256(item, "score.observation_hash") for item in self.observation_hashes)
        outcome_hashes = tuple(_sha256(item, "score.outcome_hash") for item in self.outcome_hashes)
        expected_count = len(SIX_COMMODITY_FIXTURE_UNIVERSE)
        if (
            len(observation_hashes) != expected_count
            or len(outcome_hashes) != expected_count
            or len(set(observation_hashes)) != expected_count
            or len(set(outcome_hashes)) != expected_count
        ):
            raise FixtureOnlyReplayError(
                "score must bind exactly one observation and outcome hash for every fixture commodity"
            )
        score = _finite(self.synthetic_alignment_score, "score.synthetic_alignment_score")
        score_hash = canonical_json_sha256(
            {
                "checkpoint_id": checkpoint_id,
                "decision_at": decision_at.isoformat(),
                "format": f"{_FORMAT_PREFIX}.score.v1",
                "observation_hashes": list(observation_hashes),
                "outcome_available_at": outcome_available_at.isoformat(),
                "outcome_hashes": list(outcome_hashes),
                "synthetic_alignment_score": _score_hex(score),
            }
        )
        object.__setattr__(self, "checkpoint_id", checkpoint_id)
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(self, "outcome_available_at", outcome_available_at)
        object.__setattr__(self, "observation_hashes", observation_hashes)
        object.__setattr__(self, "outcome_hashes", outcome_hashes)
        object.__setattr__(self, "synthetic_alignment_score", score)
        object.__setattr__(self, "score_hash", score_hash)

    def as_mapping(self) -> dict[str, object]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "decision_at": self.decision_at.isoformat(),
            "observation_hashes": list(self.observation_hashes),
            "outcome_available_at": self.outcome_available_at.isoformat(),
            "outcome_hashes": list(self.outcome_hashes),
            "score_hash": self.score_hash,
            "synthetic_alignment_score": _score_hex(self.synthetic_alignment_score),
        }


@dataclass(frozen=True, slots=True)
class FixtureOnlyReplayResult:
    """Deterministic research-only result with execution permanently disabled."""

    plan_hash: str
    scores: tuple[FixtureReplayScore, ...]
    mean_synthetic_alignment_score: float
    positive_score_fraction: float
    result_hash: str = field(init=False)
    fixture_only: bool = field(init=False, default=True)
    research_only: bool = field(init=False, default=True)
    models_orders: bool = field(init=False, default=False)
    models_trades: bool = field(init=False, default=False)
    order_count: int = field(init=False, default=0)
    trade_count: int = field(init=False, default=0)
    eligible_for_admission: bool = field(init=False, default=False)
    eligible_for_trading: bool = field(init=False, default=False)
    _issuer: InitVar[object | None] = None

    def __post_init__(self, _issuer: object | None) -> None:
        if _issuer is not _RESULT_ISSUER:
            raise FixtureOnlyReplayError(
                "FixtureOnlyReplayResult is factory-only; use FixtureOnlyReplayRunner.run"
            )
        plan_hash = _sha256(self.plan_hash, "result.plan_hash")
        scores = tuple(self.scores)
        if not scores or not all(isinstance(item, FixtureReplayScore) for item in scores):
            raise FixtureOnlyReplayError("result.scores must be a non-empty FixtureReplayScore tuple")
        if tuple(sorted(scores, key=lambda item: item.decision_at)) != scores:
            raise FixtureOnlyReplayError("result.scores must be ordered by decision_at")
        if len({item.checkpoint_id for item in scores}) != len(scores):
            raise FixtureOnlyReplayError("result.scores cannot reuse checkpoint_id")
        mean_score = _finite(
            self.mean_synthetic_alignment_score,
            "result.mean_synthetic_alignment_score",
        )
        positive_score_fraction = _finite(
            self.positive_score_fraction,
            "result.positive_score_fraction",
        )
        if not 0.0 <= positive_score_fraction <= 1.0:
            raise FixtureOnlyReplayError("result.positive_score_fraction must be in [0, 1]")
        expected_mean = sum(item.synthetic_alignment_score for item in scores) / len(scores)
        if mean_score != expected_mean:
            raise FixtureOnlyReplayError(
                "result.mean_synthetic_alignment_score must be recomputed from score inputs"
            )
        expected_positive_fraction = sum(
            item.synthetic_alignment_score > 0.0 for item in scores
        ) / len(scores)
        if positive_score_fraction != expected_positive_fraction:
            raise FixtureOnlyReplayError(
                "result.positive_score_fraction must be recomputed from score inputs"
            )
        result_hash = canonical_json_sha256(
            {
                "eligible_for_admission": False,
                "eligible_for_trading": False,
                "fixture_only": True,
                "format": f"{_FORMAT_PREFIX}.result.v1",
                "mean_synthetic_alignment_score": _score_hex(mean_score),
                "models_orders": False,
                "models_trades": False,
                "order_count": 0,
                "plan_hash": plan_hash,
                "positive_score_fraction": _score_hex(positive_score_fraction),
                "research_only": True,
                "score_hashes": [item.score_hash for item in scores],
                "trade_count": 0,
            }
        )
        object.__setattr__(self, "plan_hash", plan_hash)
        object.__setattr__(self, "scores", scores)
        object.__setattr__(self, "mean_synthetic_alignment_score", mean_score)
        object.__setattr__(self, "positive_score_fraction", positive_score_fraction)
        object.__setattr__(self, "result_hash", result_hash)

    def as_mapping(self) -> dict[str, object]:
        return {
            "eligible_for_admission": False,
            "eligible_for_trading": False,
            "fixture_only": True,
            "format": f"{_FORMAT_PREFIX}.result.v1",
            "mean_synthetic_alignment_score": _score_hex(self.mean_synthetic_alignment_score),
            "models_orders": False,
            "models_trades": False,
            "order_count": 0,
            "plan_hash": self.plan_hash,
            "positive_score_fraction": _score_hex(self.positive_score_fraction),
            "research_only": True,
            "result_hash": self.result_hash,
            "scores": [item.as_mapping() for item in self.scores],
            "trade_count": 0,
        }


@dataclass(frozen=True, slots=True)
class FixtureOnlyResearchRunManifest:
    """A Research Card-compatible manifest for a fixture-only replay result.

    This intentionally is not :class:`~northstar_quant.research.backtest.models.RunManifest`:
    it has no market-data reference, target frame, broker simulation, order
    model, or candidate-admission route.  Its two experiment hashes are
    self-describing commitments to a fixture replay methodology, plan, and
    result only.
    """

    plan: FixtureOnlyReplayPlan
    result: FixtureOnlyReplayResult
    methodology_id: str
    methodology_version_hash: str
    code_revision: str
    experiment_spec_hash: str = field(init=False)
    experiment_run_hash: str = field(init=False)
    run_fingerprint: str = field(init=False)
    fixture_only: bool = field(init=False, default=True)
    research_only: bool = field(init=False, default=True)
    eligible_for_admission: bool = field(init=False, default=False)
    eligible_for_trading: bool = field(init=False, default=False)
    _issuer: InitVar[object | None] = None

    @classmethod
    def create(
        cls,
        *,
        plan: FixtureOnlyReplayPlan,
        result: FixtureOnlyReplayResult,
        methodology_id: str,
        methodology_version_hash: str,
        code_revision: str,
    ) -> "FixtureOnlyResearchRunManifest":
        """Issue a non-admissible manifest from an exact validated replay."""

        return cls(
            plan=plan,
            result=result,
            methodology_id=methodology_id,
            methodology_version_hash=methodology_version_hash,
            code_revision=code_revision,
            _issuer=_MANIFEST_ISSUER,
        )

    def __post_init__(self, _issuer: object | None) -> None:
        if _issuer is not _MANIFEST_ISSUER:
            raise FixtureOnlyReplayError(
                "FixtureOnlyResearchRunManifest is factory-only; use .create"
            )
        if type(self.plan) is not FixtureOnlyReplayPlan:
            raise FixtureOnlyReplayError("manifest.plan must be an exact FixtureOnlyReplayPlan")
        if type(self.result) is not FixtureOnlyReplayResult:
            raise FixtureOnlyReplayError("manifest.result must be an exact FixtureOnlyReplayResult")
        if self.result.plan_hash != self.plan.plan_hash:
            raise FixtureOnlyReplayError("manifest.result must bind the exact replay plan")
        methodology_id = _identifier(self.methodology_id, "manifest.methodology_id")
        methodology_version_hash = _sha256(
            self.methodology_version_hash,
            "manifest.methodology_version_hash",
        )
        code_revision = _identifier(self.code_revision, "manifest.code_revision")
        feature_version_hashes = tuple(
            item.feature_version_hash for item in self.plan.handoffs
        )
        experiment_spec_hash = canonical_json_sha256(
            {
                "code_revision": code_revision,
                "feature_version_hashes": list(feature_version_hashes),
                "fixture_only": True,
                "format": f"{_FORMAT_PREFIX}.research-experiment.v1",
                "methodology_id": methodology_id,
                "methodology_version_hash": methodology_version_hash,
                "plan_hash": self.plan.plan_hash,
                "research_only": True,
            }
        )
        experiment_run_hash = canonical_json_sha256(
            {
                "experiment_spec_hash": experiment_spec_hash,
                "fixture_only": True,
                "format": f"{_FORMAT_PREFIX}.research-run.v1",
                "research_only": True,
                "result_hash": self.result.result_hash,
            }
        )
        run_fingerprint = canonical_json_sha256(
            {
                "experiment_run_hash": experiment_run_hash,
                "fixture_only": True,
                "format": f"{_FORMAT_PREFIX}.research-manifest.v1",
                "plan_hash": self.plan.plan_hash,
                "research_only": True,
                "result_hash": self.result.result_hash,
            }
        )
        object.__setattr__(self, "methodology_id", methodology_id)
        object.__setattr__(self, "methodology_version_hash", methodology_version_hash)
        object.__setattr__(self, "code_revision", code_revision)
        object.__setattr__(self, "experiment_spec_hash", experiment_spec_hash)
        object.__setattr__(self, "experiment_run_hash", experiment_run_hash)
        object.__setattr__(self, "run_fingerprint", run_fingerprint)

    @property
    def feature_version_hashes(self) -> tuple[str, ...]:
        return tuple(sorted(item.feature_version_hash for item in self.plan.handoffs))

    def as_mapping(self) -> dict[str, object]:
        return {
            "code_revision": self.code_revision,
            "eligible_for_admission": False,
            "eligible_for_trading": False,
            "experiment_run_hash": self.experiment_run_hash,
            "experiment_spec_hash": self.experiment_spec_hash,
            "feature_version_hashes": list(self.feature_version_hashes),
            "fixture_only": True,
            "format": f"{_FORMAT_PREFIX}.research-manifest.v1",
            "methodology_id": self.methodology_id,
            "methodology_version_hash": self.methodology_version_hash,
            "plan_hash": self.plan.plan_hash,
            "research_only": True,
            "result_hash": self.result.result_hash,
            "run_fingerprint": self.run_fingerprint,
        }


class FixtureOnlyReplayRunner:
    """The sole factory for fixture-only replay results.

    The class has no instance state and deliberately cannot be instantiated.
    Its runner computes a deterministic per-checkpoint alignment statistic only
    after each synthetic outcome became available.  It has no execution model,
    and its result fixes all order/trade fields to zero.
    """

    def __new__(cls, *args: object, **kwargs: object) -> "FixtureOnlyReplayRunner":
        del args, kwargs
        raise TypeError("FixtureOnlyReplayRunner is factory-only; use its class methods")

    @classmethod
    def create_plan(
        cls,
        *,
        upstream_corpus_sha256: str,
        handoffs: Iterable[FixtureFeatureDefinitionHandoff],
        checkpoints: Iterable[FixtureReplayCheckpoint],
        outcomes: Iterable[FixtureSyntheticOutcome],
    ) -> FixtureOnlyReplayPlan:
        """Build one validated plan without touching an external system."""

        del cls
        return FixtureOnlyReplayPlan.create(
            upstream_corpus_sha256=upstream_corpus_sha256,
            handoffs=handoffs,
            checkpoints=checkpoints,
            outcomes=outcomes,
        )

    @classmethod
    def run(cls, plan: FixtureOnlyReplayPlan) -> FixtureOnlyReplayResult:
        """Evaluate a validated plan as fixture-only, post-outcome research evidence."""

        del cls
        if type(plan) is not FixtureOnlyReplayPlan:
            raise FixtureOnlyReplayError("plan must be an exact FixtureOnlyReplayPlan")
        outcomes_by_pair = {
            (item.checkpoint_id, item.commodity_id): item for item in plan.outcomes
        }
        scores: list[FixtureReplayScore] = []
        for checkpoint in plan.checkpoints:
            paired_outcomes = tuple(
                outcomes_by_pair[(checkpoint.checkpoint_id, observation.commodity_id)]
                for observation in checkpoint.observations
            )
            components = tuple(
                (observation.value if observation.value is not None else 0.0) * outcome.value
                for observation, outcome in zip(
                    checkpoint.observations,
                    paired_outcomes,
                    strict=True,
                )
            )
            if not all(math.isfinite(component) for component in components):
                raise FixtureOnlyReplayError("synthetic alignment computation overflowed")
            score = sum(components) / len(components)
            if not math.isfinite(score):
                raise FixtureOnlyReplayError("synthetic alignment score is not finite")
            scores.append(
                FixtureReplayScore(
                    checkpoint_id=checkpoint.checkpoint_id,
                    decision_at=checkpoint.decision_at,
                    outcome_available_at=max(item.available_at for item in paired_outcomes),
                    observation_hashes=tuple(
                        item.observation_hash for item in checkpoint.observations
                    ),
                    outcome_hashes=tuple(item.outcome_hash for item in paired_outcomes),
                    synthetic_alignment_score=score,
                    _issuer=_SCORE_ISSUER,
                )
            )
        canonical_scores = tuple(scores)
        mean_score = sum(item.synthetic_alignment_score for item in canonical_scores) / len(
            canonical_scores
        )
        positive_score_fraction = sum(
            item.synthetic_alignment_score > 0.0 for item in canonical_scores
        ) / len(canonical_scores)
        return FixtureOnlyReplayResult(
            plan_hash=plan.plan_hash,
            scores=canonical_scores,
            mean_synthetic_alignment_score=mean_score,
            positive_score_fraction=positive_score_fraction,
            _issuer=_RESULT_ISSUER,
        )


__all__ = [
    "FixtureFeatureDefinitionHandoff",
    "FixtureOnlyReplayError",
    "FixtureOnlyReplayPlan",
    "FixtureOnlyReplayResult",
    "FixtureOnlyReplayRunner",
    "FixtureOnlyResearchRunManifest",
    "FixtureObservationLifecycle",
    "FixtureReplayCheckpoint",
    "FixtureReplayObservation",
    "FixtureReplayScore",
    "FixtureSyntheticOutcome",
    "SIX_COMMODITY_FIXTURE_UNIVERSE",
]
