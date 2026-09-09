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
        source.write_text(
            source.read_text()
            .replace("/opt/northstar", str(tmp_path))
            .replace('Path.home() / ".ssh/', f'Path({str(tmp_path)!r}) / ".ssh/')
        )
    for app in ("database", "data_hub", "research", "live"):
        folder = repo / "deploy" / app
        folder.mkdir(parents=True)
        (folder / ".env").write_text("PASSWORD=initial-private-value\n")
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
            f'[{app.replace("-", "_")}]\nhost="example.invalid"\nuser="root"\n'
            for app in ("database", "data-hub", "research", "live")
        )
    )
    for share in ("source", "market", "research", "backup"):
        (tmp_path / "files" / share).mkdir(parents=True)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    stub = f"""#!{sys.executable}
import json, os, subprocess, sys
from pathlib import Path
name = Path(sys.argv[0]).name
with open(os.environ['RECORD'], 'a') as file:
    file.write(json.dumps([name, *sys.argv[1:]]) + '\\n')
if name == 'sudo':
    if os.environ.get('ASK_SUDO'):
        import getpass
        if '-n' in sys.argv: sys.exit(1)
        if getpass.getpass('sudo password: ') != os.environ['ASK_SUDO']: sys.exit(1)
    args = sys.argv[1:]
    while args and args[0] in ('-n', '--'): args.pop(0)
    sys.exit(subprocess.run(args).returncode)
if name == 'ssh':
    if '-l' in sys.argv and sys.argv[sys.argv.index('-l') + 1] in ('root', 'bootstrap-admin'):
        sys.exit(int(os.environ.get('INIT_HOST_RESULT', '0')))
    sys.exit(subprocess.run(sys.argv[-1], shell=True).returncode)
if name == 'docker' and sys.argv[1:] == ['info']:
    sys.exit(int(os.environ.get('DOCKER_INFO_RESULT', '0')))
if name == 'uv':
    if 'scripts/operations/check_storage.py' in sys.argv: print('{{}}')
    sys.exit(int(os.environ.get('MOUNT_RESULT', '0')))
if name == 'docker' and 'up' in sys.argv: sys.exit(int(os.environ.get('DEPLOY_UP_RESULT', '0')))
"""
    for tool in ("ssh", "sudo", "docker", "make", "uv", "curl"):
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


def test_private_configuration_permissions_are_fixed_without_changing_content(deployment):
    _, config, _ = deployment
    private = config.parent / "private.env"
    private.chmod(0o644)
    result = invoke(deployment, "deploy", "database")
    assert result.returncode == 0, result.stderr
    assert private.stat().st_mode & 0o777 == 0o600
    assert private.read_text() == "PASSWORD=not-for-output\n"
    assert "not-for-output" not in result.stdout + result.stderr


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


@pytest.mark.skipif(os.geteuid() == 0, reason="root does not request sudo authentication")
def test_interactive_elevation_keeps_password_out_of_transport_and_bundle(deployment):
    import pty
    import select
    import signal
    import time

    repo, config, env = deployment
    env["ASK_SUDO"] = "synthetic-sudo-secret"
    pid, terminal = pty.fork()
    if pid == 0:
        os.execve(
            sys.executable,
            [
                sys.executable,
                str(repo / "scripts/northstarctl.py"),
                "deploy",
                "research",
                "--config",
                str(config),
            ],
            env,
        )
    output = b""
    answered = False
    status = None
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if select.select([terminal], [], [], 0.1)[0]:
                try:
                    chunk = os.read(terminal, 65536)
                except OSError:
                    chunk = b""
                output += chunk
                if b"sudo password:" in output and not answered:
                    os.write(terminal, (env["ASK_SUDO"] + "\n").encode())
                    answered = True
            done, result = os.waitpid(pid, os.WNOHANG)
            if done:
                status = result
                break
        assert status == 0, output.decode(errors="replace")
        assert answered
        assert env["ASK_SUDO"].encode() not in output
        calls = Path(env["RECORD"]).read_text()
        assert env["ASK_SUDO"] not in calls
        ssh_calls = [
            json.loads(line) for line in calls.splitlines() if json.loads(line)[0] == "ssh"
        ]
        assert "-tt" in ssh_calls[0] and "-T" in ssh_calls[1]
        assert (config.parent / "apps/research/successful-revision").is_file()
    finally:
        if status is None:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        os.close(terminal)


@pytest.mark.skipif(os.geteuid() == 0, reason="root does not require sudo permission")
def test_unattended_deployment_without_sudo_permission_stops_before_transfer(deployment):
    _, config, env = deployment
    env["ASK_SUDO"] = "synthetic-sudo-secret"
    result = invoke(deployment, "deploy", "research")
    assert result.returncode != 0
    assert not (config.parent / "apps/research/current").exists()
    assert env["ASK_SUDO"] not in result.stdout + result.stderr
    calls = [json.loads(line) for line in Path(env["RECORD"]).read_text().splitlines()]
    assert len([call for call in calls if call[0] == "ssh"]) == 1
    assert not any(call[0] == "docker" for call in calls)


def test_first_deployment_uploads_private_config_and_redeploy_preserves_edits(deployment):
    _, config, env = deployment
    private = config.parent / "config/research.env"
    private.unlink()
    result = invoke(deployment, "deploy", "research")
    assert result.returncode == 0, result.stderr
    assert private.read_text() == "PASSWORD=initial-private-value\n"
    assert private.stat().st_mode & 0o777 == 0o600
    private.write_text("PASSWORD=host-private-value\n")
    result2 = invoke(deployment, "deploy", "research")
    assert result2.returncode == 0, result2.stderr
    assert private.read_text() == "PASSWORD=host-private-value\n"
    evidence = result.stdout + result.stderr + result2.stdout + result2.stderr
    evidence += Path(env["RECORD"]).read_text()
    assert "initial-private-value" not in evidence
    assert "host-private-value" not in evidence


@pytest.mark.parametrize("bootstrap_user", ["root", "bootstrap-admin"])
def test_init_host_deduplicates_targets_and_verifies_fixed_deployment_account(
    deployment, tmp_path, bootstrap_user
):
    repo, config, env = deployment
    config.write_text(
        config.read_text().replace(
            '[research]\nhost="example.invalid"', '[research]\nhost="research.invalid"'
        )
    )
    config.write_text(config.read_text().replace('user="root"', f'user="{bootstrap_user}"'))
    (tmp_path / ".ssh").mkdir()
    key = tmp_path / ".ssh/id_ed25519"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    command = [
        sys.executable,
        str(repo / "scripts/northstarctl.py"),
        "init-host",
        "--config",
        str(config),
    ]
    result = subprocess.run(command, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in Path(env["RECORD"]).read_text().splitlines()]
    users = [call[call.index("-l") + 1] for call in calls if call[0] == "ssh"]
    assert users == [bootstrap_user, "northstar", bootstrap_user, "northstar"]
    import shlex

    init_calls = [
        call for call in calls if call[0] == "ssh" and call[call.index("-l") + 1] == bootstrap_user
    ]
    expected = ["sudo", "-n", "--", "python3"] if bootstrap_user != "root" else ["python3"]
    assert all(shlex.split(call[-1])[: len(expected)] == expected for call in init_calls)
    assert key.read_text() not in result.stdout + result.stderr + Path(env["RECORD"]).read_text()
    Path(env["RECORD"]).unlink()
    env["INIT_HOST_RESULT"] = "9"
    result = subprocess.run(command, env=env, text=True, capture_output=True)
    assert result.returncode != 0
    calls = [json.loads(line) for line in Path(env["RECORD"]).read_text().splitlines()]
    assert len([call for call in calls if call[0] == "ssh"]) == 1


def test_init_host_dry_run_does_not_access_keys_or_connect(deployment):
    repo, config, env = deployment
    result = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts/northstarctl.py"),
            "init-host",
            "--config",
            str(config),
            "--dry-run",
        ],
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("root@example.invalid") == 1
    assert not Path(env["RECORD"]).exists()


@pytest.mark.parametrize("app", ["database", "data-hub", "research", "live"])
def test_explicit_environment_updates_only_selected_app_without_disclosing_values(
    deployment, tmp_path, app
):
    _, config, env = deployment
    custom = tmp_path / "private.env.input"
    content = b"PASSWORD=custom-secret-$literal\nNOTE='spaces stay intact'\n"
    custom.write_bytes(content)
    result = invoke(deployment, "deploy", app, "--env-file", str(custom))
    assert result.returncode == 0, result.stderr
    target = config.parent / "config" / f"{app}.env"
    assert target.read_bytes() == content
    assert target.stat().st_mode & 0o777 == 0o600
    for other in ("database", "data-hub", "research", "live"):
        if other != app:
            assert (
                config.parent / "config" / f"{other}.env"
            ).read_text() == "PASSWORD=not-for-output\n"
    result2 = invoke(deployment, "deploy", app)
    assert result2.returncode == 0, result2.stderr
    assert target.read_bytes() == content
    evidence = (
        result.stdout
        + result.stderr
        + result2.stdout
        + result2.stderr
        + Path(env["RECORD"]).read_text()
    )
    assert "custom-secret" not in evidence
    assert custom.read_bytes() == content


def test_missing_custom_environment_fails_before_connecting(deployment):
    _, _, env = deployment
    result = invoke(deployment, "deploy", "research", "--env-file", "/missing/northstar.env")
    assert result.returncode != 0
    assert not Path(env["RECORD"]).exists()
