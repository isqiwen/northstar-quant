"""Canonical continuous-series frames derived from immutable factor objects.

The builders are deliberately below application composition so full pipeline
runs and the discovery/OOS stage protocol use identical zero-weight and symbol
coverage semantics.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from northstar_quant.research.factors.models import (
    FactorCheckpointData,
    FactorPortfolioProposal,
    FactorResearchError,
    ProposalStatus,
)


def build_factor_market_frame(checkpoints: tuple[FactorCheckpointData, ...]) -> pl.DataFrame:
    """Build the complete continuous research market panel from PIT checkpoints."""

    if not checkpoints:
        raise FactorResearchError("factor checkpoints 不能为空")
    expected_symbols: set[str] | None = None
    rows: list[dict[str, object]] = []
    sessions: set[date] = set()
    for checkpoint in checkpoints:
        symbols = {item.symbol for item in checkpoint.market_slices}
        if expected_symbols is None:
            expected_symbols = symbols
        elif symbols != expected_symbols:
            raise FactorResearchError("每个 PIT checkpoint 必须覆盖相同连续研究标的池")
        if checkpoint.decision_session in sessions:
            raise FactorResearchError("factor checkpoints 不能包含重复 decision_session")
        sessions.add(checkpoint.decision_session)
        rows.extend(
            {
                "date": item.decision_session,
                "symbol": item.symbol,
                "close": item.close,
            }
            for item in checkpoint.market_slices
        )
    if not rows:
        raise FactorResearchError("factor market frame 不能为空")
    return pl.DataFrame(
        rows,
        schema={"date": pl.Date, "symbol": pl.String, "close": pl.Float64},
        strict=True,
    ).sort(["date", "symbol"])


def build_factor_target_frame(
    proposals: tuple[FactorPortfolioProposal, ...],
    checkpoints: tuple[FactorCheckpointData, ...],
) -> pl.DataFrame:
    """Build every checkpoint's target panel, writing explicit warm-up zeroes."""

    checkpoint_by_hash = {item.checkpoint_hash: item for item in checkpoints}
    if len(checkpoint_by_hash) != len(checkpoints):
        raise FactorResearchError("factor checkpoints 不能包含重复 checkpoint_hash")
    if len(proposals) != len(checkpoints):
        raise FactorResearchError("proposal 必须与 factor checkpoint 一一对应")
    rows: list[dict[str, object]] = []
    has_proposal = False
    for proposal in proposals:
        try:
            checkpoint = checkpoint_by_hash[proposal.checkpoint_hash]
        except KeyError as exc:
            raise FactorResearchError("proposal 不能绑定未知 factor checkpoint") from exc
        if proposal.decision_session != checkpoint.decision_session:
            raise FactorResearchError("proposal.decision_session 与 checkpoint 不一致")
        if proposal.status is ProposalStatus.NO_PROPOSAL_WARMUP:
            rows.extend(
                {
                    "date": proposal.decision_session,
                    "symbol": market_slice.symbol,
                    "target_weight": 0.0,
                }
                for market_slice in checkpoint.market_slices
            )
            continue
        has_proposal = True
        rows.extend(
            {
                "date": proposal.decision_session,
                "symbol": weight.symbol,
                "target_weight": weight.target_weight,
            }
            for weight in proposal.weights
        )
    if not has_proposal:
        raise FactorResearchError("所有 checkpoint 都未产生 proposal，拒绝空回测")
    return pl.DataFrame(
        rows,
        schema={"date": pl.Date, "symbol": pl.String, "target_weight": pl.Float64},
        strict=True,
    ).sort(["date", "symbol"])


__all__ = ["build_factor_market_frame", "build_factor_target_frame"]
