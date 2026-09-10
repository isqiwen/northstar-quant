"""Research orchestration over shared computations and the simulation adapter."""

from collections.abc import Callable

from northstar_quant.data_management.research import ResearchDataset
from northstar_quant.research.configuration import ResearchConfig

from .report import ResearchResult, build_result
from .session import STEP_COMPLETED, TradingSession, TradingStep

__all__ = ["run_research", "ResearchResult"]


def run_research(
    dataset: ResearchDataset,
    config: ResearchConfig,
    *,
    progress: Callable[[int, int], None] | None = None,
) -> ResearchResult:
    if not isinstance(dataset, ResearchDataset) or len(dataset.bars) <= (
        config.strategy.history_bars - 1
    ):
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
    session.kernel.subscribe(STEP_COMPLETED, steps.append)
    try:
        ordered = sorted(
            dataset.bars,
            key=lambda item: (item.available_at, item.completed_at, str(item.observation_id)),
        )
        session.validate_inputs(ordered)
        for index, bar in enumerate(ordered, 1):
            if progress is not None:
                progress(index - 1, len(dataset.bars))
            session.advance(bar)
        if progress is not None:
            progress(len(dataset.bars), len(dataset.bars))
        return build_result(session, steps)
    finally:
        session.close()
