"""Contract tests for the standalone local research CLI surface."""

from __future__ import annotations

from typer.testing import CliRunner

from northstar_quant.application.research_cli import app


runner = CliRunner()


def test_local_research_cli_exposes_only_hash_addressed_factor_operations() -> None:
    root_help = runner.invoke(app, ["--help"])
    factor_help = runner.invoke(app, ["factor", "--help"])
    run_help = runner.invoke(app, ["factor", "run", "--help"])
    replay_help = runner.invoke(app, ["factor", "replay", "--help"])
    inspect_help = runner.invoke(app, ["factor", "inspect", "--help"])

    assert root_help.exit_code == 0
    assert factor_help.exit_code == 0
    assert run_help.exit_code == 0
    assert replay_help.exit_code == 0
    assert inspect_help.exit_code == 0
    assert {"run", "replay", "inspect"}.issubset(factor_help.output.split())
    assert "--bundle-snapshot" in run_help.output
    assert "--expected-manifest-snapshot" in replay_help.output
    assert "--artifact-snapshot" in inspect_help.output
    for output in (root_help.output, factor_help.output, run_help.output, replay_help.output):
        assert "--input" not in output
        assert "--dataset-path" not in output
        assert "--config" not in output
        assert "--profile" not in output


def test_local_research_cli_rejects_paths_and_latest_before_any_run() -> None:
    path_result = runner.invoke(
        app,
        ["factor", "run", "--bundle-snapshot", "/tmp/unverified.json"],
    )
    latest_result = runner.invoke(
        app,
        ["factor", "inspect", "--artifact-snapshot", "latest"],
    )

    assert path_result.exit_code == 2
    assert latest_result.exit_code == 2
    assert "SHA-256" in path_result.output
    assert "SHA-256" in latest_result.output
