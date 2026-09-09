"""Real Git/SSH command transport with disposable process doubles, never personal hosts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def deployment(tmp_path: Path) -> tuple[Path, Path, dict]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "scripts").mkdir()
    shutil.copyfile(ROOT / "scripts/northstarctl.py", repo / "scripts/northstarctl.py")
    shutil.copytree(
        ROOT / "scripts/operations",
        repo / "scripts/operations",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    # Rewrite the copied program's fixed root only inside this disposable test repository.
    for source in (repo / "scripts").rglob("*.py"):
        source.write_text(source.read_text().replace("/opt/northstar", str(tmp_path)))
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "Makefile").write_text("up-database up-data up-research up-live:\n\t@true\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "test",
        ],
        check=True,
    )
    private = tmp_path / "private.env"
    private.write_text("PASSWORD=not-for-output\n")
    private.chmod(0o600)
    (tmp_path / "config").mkdir()
    for app in ("database", "data-hub", "research", "live"):
        os.link(private, tmp_path / "config" / f"{app}.env")
    config = tmp_path / "hosts.toml"
    config.write_text(
        "\n".join(
            f'[{app.replace("-", "_")}]\nhost="example.invalid"\nuser="test"\n'
            for app in ("database", "data-hub", "research", "live")
        )
    )
    binaries = tmp_path / "bin"
    binaries.mkdir()
    stub = f"""#!{sys.executable}
import json, os, subprocess, sys
from pathlib import Path
name = Path(sys.argv[0]).name
with open(os.environ['RECORD'], 'a') as file:
    file.write(json.dumps([name, *sys.argv[1:]]) + '\\n')
if name == 'ssh':
    sys.exit(subprocess.run(sys.argv[-1], shell=True).returncode)
if name == 'docker' and sys.argv[1:] == ['info']:
    sys.exit(int(os.environ.get('DOCKER_INFO_RESULT', '0')))
if name == 'uv': sys.exit(int(os.environ.get('MOUNT_RESULT', '0')))
if name == 'docker' and 'up' in sys.argv: sys.exit(int(os.environ.get('DEPLOY_UP_RESULT', '0')))
"""
    for tool in ("ssh", "docker", "make", "uv", "curl"):
        file = binaries / tool
        file.write_text(stub)
        file.chmod(0o755)
    env = dict(
        os.environ,
        PATH=str(binaries) + os.pathsep + os.environ["PATH"],
        RECORD=str(tmp_path / "calls"),
    )
    return repo, config, env


def invoke(
    deployment: tuple[Path, Path, dict], action: str, app: str, *options: str
) -> subprocess.CompletedProcess:
    repo, config, env = deployment
    return subprocess.run(
        [
            sys.executable,
            str(repo / "scripts/northstarctl.py"),
            action,
            app,
            "--config",
            str(config),
            *options,
        ],
        env=env,
        text=True,
        capture_output=True,
    )


@pytest.mark.parametrize(
    "app,target",
    [("database", "database"), ("data-hub", "data"), ("research", "research"), ("live", "live")],
)
def test_transfer_committed_release_and_isolated_lifecycle(deployment, app, target):
    repo, config, env = deployment
    result = invoke(deployment, "deploy", app)
    assert result.returncode == 0, result.stderr
    revision = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    assert revision in result.stdout
    assert "not-for-output" not in result.stdout + result.stderr
    for action in ("status", "logs", "stop", "start", "restart"):
        result = invoke(deployment, action, app)
        assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in Path(env["RECORD"]).read_text().splitlines()]
    assert not any(c[0] == "make" for c in calls)
    assert (
        any(c[:2] == ["docker", "compose"] and "--build" in c for c in calls)
        if app != "database"
        else any("initialize" in c for c in calls)
    )
    for call in calls:
        if call[:2] == ["docker", "compose"] and "-p" in call:
            assert call[call.index("-p") + 1] == "northstar-" + app
    assert (config.parent / "apps" / app / "successful-revision").read_text().strip() == revision


def test_failed_up_remains_inspectable_without_false_success(deployment):
    _, config, env = deployment
    env["DEPLOY_UP_RESULT"] = "7"
    assert invoke(deployment, "deploy", "research").returncode != 0
    assert not (config.parent / "apps/research/successful-revision").exists()
    result = invoke(deployment, "status", "research")
    assert result.returncode == 0
    assert "最后成功版本：无" in result.stdout


def test_dirty_source_never_connects_or_deploys(deployment):
    repo, _, env = deployment
    (repo / "uncommitted").write_text("new source")
    assert invoke(deployment, "deploy", "research").returncode != 0
    assert not Path(env["RECORD"]).exists()


def test_private_configuration_is_required_before_mutation(deployment):
    _, config, env = deployment
    (config.parent / "private.env").chmod(0o644)
    result = invoke(deployment, "deploy", "database")
    assert result.returncode != 0
    assert "chmod 600" in result.stderr
    calls = [json.loads(line) for line in Path(env["RECORD"]).read_text().splitlines()]
    assert not any(c[0] in ("make", "docker") for c in calls)


def test_modified_remote_release_is_not_overwritten(deployment):
    _, config, env = deployment
    assert invoke(deployment, "deploy", "research").returncode == 0
    release = (config.parent / "apps/research/current").resolve()
    (release / "Makefile").write_text("locally changed")
    Path(env["RECORD"]).write_text("")
    result = invoke(deployment, "deploy", "research")
    assert result.returncode != 0
    assert "拒绝覆盖" in result.stderr
    assert (release / "Makefile").read_text() == "locally changed"
    calls = [json.loads(line) for line in Path(env["RECORD"]).read_text().splitlines()]
    assert not any(c[0] == "make" for c in calls)


def test_path_override_is_rejected_before_ssh(deployment):
    _, config, env = deployment
    config.write_text(
        config.read_text().replace("[database]", '[database]\ndirectory="/tmp/override"')
    )
    assert invoke(deployment, "deploy", "database").returncode != 0
    assert not Path(env["RECORD"]).exists()


@pytest.mark.parametrize("action", ["start", "restart", "stop"])
def test_live_uses_complete_deployment_lifecycle(deployment, action):
    _, _, env = deployment
    assert invoke(deployment, "deploy", "live").returncode == 0
    Path(env["RECORD"]).write_text("")
    result = invoke(deployment, action, "live")
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in Path(env["RECORD"]).read_text().splitlines()]
    mutations = [c for c in calls if c[:2] == ["docker", "compose"] and ("up" in c or "down" in c)]
    assert len(mutations) == 1
    if action == "stop":
        assert mutations[0][-1] == "down"
        assert "--volumes" not in mutations[0]
    else:
        assert "--no-build" in mutations[0]
        assert "--no-deps" not in mutations[0]
        assert mutations[0][-1] == ("--force-recreate" if action == "restart" else "--no-recreate")


@pytest.mark.parametrize("action", ["start", "restart"])
@pytest.mark.parametrize("app", ["database", "data-hub", "research"])
def test_mount_failure_prevents_start_or_restart(deployment, action, app):
    _, _, env = deployment
    assert invoke(deployment, "deploy", app).returncode == 0
    Path(env["RECORD"]).write_text("")
    env["MOUNT_RESULT"] = "1"
    assert invoke(deployment, action, app).returncode != 0
    calls = [json.loads(line) for line in Path(env["RECORD"]).read_text().splitlines()]
    assert not any(c[:2] == ["docker", "compose"] and "up" in c for c in calls)


def test_dependency_failure_prevents_source_transfer_and_application_mutation(deployment):
    _, config, env = deployment
    env["DOCKER_INFO_RESULT"] = "1"
    result = invoke(deployment, "deploy", "live")
    assert result.returncode != 0
    assert not (config.parent / "apps/live/current").exists()
    calls = [json.loads(line) for line in Path(env["RECORD"]).read_text().splitlines()]
    assert len([call for call in calls if call[0] == "ssh"]) == 1
    assert not any("up" in call or "down" in call for call in calls)
