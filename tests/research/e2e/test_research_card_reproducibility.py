"""P2-WP09: an offline Dataset-to-Feature-to-Experiment-to-Backtest-to-Validation-to-Card chain."""

from __future__ import annotations

from tests.helpers.research_candidate import build_research_candidate_chain


def test_offline_research_e2e_is_reproducible_and_never_grants_trading(tmp_path) -> None:
    first = build_research_candidate_chain(tmp_path / "first")
    second = build_research_candidate_chain(tmp_path / "second")

    assert first.experiment.eligible_for_backtest is False
    assert first.experiment_run.eligible_for_admission is False
    assert second.experiment.eligible_for_backtest is False
    assert second.experiment_run.eligible_for_admission is False
    assert first.card.card_hash == second.card.card_hash
    assert first.card.to_json() == second.card.to_json()
    assert first.card.as_mapping()["decision"]["state"] == "candidate"
    assert first.card.eligible_for_trading is False
