"""开发工具 bootstrap 与本地初始化安全门禁。"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

import pytest

from scripts.dev import check_env, setup, sync_env_schema, tool_bootstrap


def test_windows_bootstrap_plan_only_includes_docker_when_explicitly_requested() -> None:
    missing = {"uv", "just", "git", "docker", "docker-compose-v2"}

    core_steps = tool_bootstrap.build_install_plan(
        missing_tools=missing,
        install_docker=False,
        system_name="Windows",
    )
    docker_steps = tool_bootstrap.build_install_plan(
        missing_tools=missing,
        install_docker=True,
        system_name="Windows",
    )

    assert all("Docker.DockerDesktop" not in step.command for step in core_steps)
    assert [step.command[3] for step in docker_steps] == [
        "astral-sh.uv",
        "Casey.Just",
        "Git.Git",
        "Docker.DockerDesktop",
    ]
    assert all(step.command[:3] == ("winget", "install", "--id") for step in docker_steps)


def test_linux_bootstrap_refuses_unknown_distribution(tmp_path: Path) -> None:
    release = tmp_path / "os-release"
    release.write_text("ID=fedora\nVERSION_CODENAME=forty-two\n", encoding="utf-8")

    with pytest.raises(tool_bootstrap.BootstrapPlanError, match="仅正式支持 Ubuntu/Debian"):
        tool_bootstrap.build_install_plan(
            missing_tools={"just"},
            install_docker=False,
            system_name="Linux",
            os_release_path=release,
        )


def test_linux_docker_plan_never_uses_shell_or_usermod(tmp_path: Path) -> None:
    release = tmp_path / "os-release"
    release.write_text("ID=ubuntu\nVERSION_CODENAME=noble\n", encoding="utf-8")

    steps = tool_bootstrap.build_install_plan(
        missing_tools={"docker", "docker-compose-v2"},
        install_docker=True,
        system_name="Linux",
        os_release_path=release,
        docker_apt_source=tmp_path / "docker.sources",
        docker_apt_keyring=tmp_path / "docker.asc",
    )

    commands = [step.command for step in steps]
    flattened = "\n".join(" ".join(command) for command in commands)
    assert "shell" not in flattened
    assert "usermod" not in flattened
    assert "systemctl" not in flattened
    assert any(command[:2] == ("sudo", "tee") for command in commands)


def test_linux_docker_plan_refuses_to_overwrite_existing_system_repository(tmp_path: Path) -> None:
    release = tmp_path / "os-release"
    release.write_text("ID=debian\nVERSION_CODENAME=trixie\n", encoding="utf-8")
    existing_source = tmp_path / "docker.sources"
    existing_source.write_text("custom source\n", encoding="utf-8")

    with pytest.raises(tool_bootstrap.BootstrapPlanError, match="既有 Docker APT 源"):
        tool_bootstrap.build_install_plan(
            missing_tools={"docker"},
            install_docker=True,
            system_name="Linux",
            os_release_path=release,
            docker_apt_source=existing_source,
            docker_apt_keyring=tmp_path / "docker.asc",
        )


def test_linux_docker_repository_inspection_has_auditable_state_matrix(tmp_path: Path) -> None:
    """只将 source-first 留下的严格匹配 source-only 状态视为可恢复。"""

    source = tmp_path / "docker.sources"
    keyring = tmp_path / "docker.asc"
    expected_source = tool_bootstrap._docker_source_contents(
        release={"id": "ubuntu", "codename": "noble"},
        keyring=keyring,
    )

    absent = tool_bootstrap._docker_repository_state(
        source_path=source,
        keyring_path=keyring,
        expected_source=expected_source,
    )
    assert absent.state is tool_bootstrap.DockerRepositoryState.ABSENT

    source.write_text(expected_source, encoding="utf-8")
    recoverable = tool_bootstrap._docker_repository_state(
        source_path=source,
        keyring_path=keyring,
        expected_source=expected_source,
    )
    assert recoverable.state is tool_bootstrap.DockerRepositoryState.RECOVERABLE_SOURCE_ONLY

    keyring.write_bytes(b"existing-keyring")
    compatible = tool_bootstrap._docker_repository_state(
        source_path=source,
        keyring_path=keyring,
        expected_source=expected_source,
    )
    assert compatible.state is tool_bootstrap.DockerRepositoryState.COMPATIBLE

    original_source = "unexpected source\n"
    source.write_text(original_source, encoding="utf-8")
    conflict = tool_bootstrap._docker_repository_state(
        source_path=source,
        keyring_path=keyring,
        expected_source=expected_source,
    )
    assert conflict.state is tool_bootstrap.DockerRepositoryState.CONFLICT
    assert conflict.conflict_reason is not None
    assert source.read_text(encoding="utf-8") == original_source


def test_linux_docker_plan_reuses_matching_repository_state_idempotently(
    tmp_path: Path,
) -> None:
    release = tmp_path / "os-release"
    release.write_text("ID=ubuntu\nVERSION_CODENAME=noble\n", encoding="utf-8")
    source = tmp_path / "docker.sources"
    keyring = tmp_path / "docker.asc"
    source.write_text(
        tool_bootstrap._docker_source_contents(
            release={"id": "ubuntu", "codename": "noble"},
            keyring=keyring,
        ),
        encoding="utf-8",
    )
    keyring.write_bytes(b"previously-installed-keyring")

    first_plan = tool_bootstrap.build_install_plan(
        missing_tools={"docker", "docker-compose-v2"},
        install_docker=True,
        system_name="Linux",
        os_release_path=release,
        docker_apt_source=source,
        docker_apt_keyring=keyring,
    )
    second_plan = tool_bootstrap.build_install_plan(
        missing_tools={"docker", "docker-compose-v2"},
        install_docker=True,
        system_name="Linux",
        os_release_path=release,
        docker_apt_source=source,
        docker_apt_keyring=keyring,
    )

    assert first_plan == second_plan
    assert not any(step.command[:2] == ("sudo", "tee") for step in first_plan)
    assert not any("curl" in step.command for step in first_plan)
    assert any("docker-ce" in step.command for step in first_plan)


def test_linux_docker_plan_resumes_matching_source_after_interruption(tmp_path: Path) -> None:
    release = tmp_path / "os-release"
    release.write_text("ID=debian\nVERSION_CODENAME=trixie\n", encoding="utf-8")
    source = tmp_path / "docker.sources"
    keyring = tmp_path / "docker.asc"
    source.write_text(
        tool_bootstrap._docker_source_contents(
            release={"id": "debian", "codename": "trixie"},
            keyring=keyring,
        ),
        encoding="utf-8",
    )

    steps = tool_bootstrap.build_install_plan(
        missing_tools={"docker"},
        install_docker=True,
        system_name="Linux",
        os_release_path=release,
        docker_apt_source=source,
        docker_apt_keyring=keyring,
    )

    assert not any(step.command[:2] == ("sudo", "tee") for step in steps)
    assert any("curl" in step.command for step in steps)


def test_linux_docker_plan_recovers_keyring_permission_after_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = tmp_path / "os-release"
    release.write_text("ID=ubuntu\nVERSION_CODENAME=noble\n", encoding="utf-8")
    source = tmp_path / "docker.sources"
    keyring = tmp_path / "docker.asc"
    source.write_text(
        tool_bootstrap._docker_source_contents(
            release={"id": "ubuntu", "codename": "noble"},
            keyring=keyring,
        ),
        encoding="utf-8",
    )
    keyring.write_bytes(b"partially-configured-keyring")
    monkeypatch.setattr(tool_bootstrap.stat, "S_IMODE", lambda _: 0o600)

    steps = tool_bootstrap.build_install_plan(
        missing_tools={"docker"},
        install_docker=True,
        system_name="Linux",
        os_release_path=release,
        docker_apt_source=source,
        docker_apt_keyring=keyring,
    )

    assert any(step.label == "恢复 Docker APT 签名密钥读取权限" for step in steps)


def test_linux_docker_plan_refuses_ambiguous_keyring_only_state(tmp_path: Path) -> None:
    release = tmp_path / "os-release"
    release.write_text("ID=ubuntu\nVERSION_CODENAME=noble\n", encoding="utf-8")
    keyring = tmp_path / "docker.asc"
    keyring.write_bytes(b"unknown-keyring")

    with pytest.raises(tool_bootstrap.BootstrapPlanError, match="签名密钥但没有匹配"):
        tool_bootstrap.build_install_plan(
            missing_tools={"docker"},
            install_docker=True,
            system_name="Linux",
            os_release_path=release,
            docker_apt_source=tmp_path / "docker.sources",
            docker_apt_keyring=keyring,
        )


def test_execute_install_plan_passes_arguments_without_shell() -> None:
    captured: list[tuple[list[str], dict[str, object]]] = []

    def fake_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[object]:
        captured.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    tool_bootstrap.execute_install_plan(
        [tool_bootstrap.InstallStep("测试", ("installer", "--safe"), input_text="content\n")],
        runner=fake_runner,
    )

    assert captured == [
        (
            ["installer", "--safe"],
            {"input": "content\n", "text": True, "check": True},
        )
    ]


def test_bootstrap_requires_supported_python_before_system_installation() -> None:
    assert setup._bootstrap_python_error((3, 10)) is not None
    assert setup._bootstrap_python_error((3, 11)) is None


def test_environment_check_refuses_remote_docker_host_without_daemon_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCKER_HOST", "ssh://production.example")

    result = check_env._check_docker_daemon(require_docker=True)

    assert result["status"] == "error"
    assert "DOCKER_HOST" in result["message"]


def _bootstrap_args(*, apply: bool, install_docker: bool, tool_confirmation: str = "", docker_confirmation: str = "") -> argparse.Namespace:
    return argparse.Namespace(
        apply=apply,
        install_docker=install_docker,
        confirm_tool_install=tool_confirmation,
        confirm_docker_install=docker_confirmation,
    )


def test_setup_bootstrap_default_only_renders_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    step = tool_bootstrap.InstallStep("安装 just", ("winget", "install", "--id", "Casey.Just"))
    executed = False
    monkeypatch.setattr(setup, "_missing_bootstrap_tools", lambda **_: {"just"})
    monkeypatch.setattr(setup, "build_install_plan", lambda **_: [step])

    def fake_execute(_: object) -> None:
        nonlocal executed
        executed = True

    monkeypatch.setattr(setup, "execute_install_plan", fake_execute)

    assert setup._bootstrap_tools(_bootstrap_args(apply=False, install_docker=False)) == 0
    assert not executed


def test_setup_bootstrap_requires_two_confirmations_for_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    step = tool_bootstrap.InstallStep("安装 Docker", ("winget", "install", "--id", "Docker.DockerDesktop"))
    executed = False
    monkeypatch.setattr(setup, "_missing_bootstrap_tools", lambda **_: {"docker"})
    monkeypatch.setattr(setup, "build_install_plan", lambda **_: [step])

    def fake_execute(_: object) -> None:
        nonlocal executed
        executed = True

    monkeypatch.setattr(setup, "execute_install_plan", fake_execute)

    assert (
        setup._bootstrap_tools(
            _bootstrap_args(apply=True, install_docker=True, tool_confirmation="YES")
        )
        == 2
    )
    assert not executed
    assert (
        setup._bootstrap_tools(
            _bootstrap_args(
                apply=True,
                install_docker=True,
                tool_confirmation="YES",
                docker_confirmation="YES",
            )
        )
        == 0
    )
    assert executed


def test_existing_live_or_kill_switch_configuration_requires_explicit_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    app_config = tmp_path / "app.yaml"
    env_file.write_text(
        "NORTHSTAR_ENV=production\n"
        "NORTHSTAR_BROKER=ctp\n"
        "NORTHSTAR_LIVE_TRADING_ENABLED=true\n"
        "NORTHSTAR_KILL_SWITCH_ENABLED=true\n",
        encoding="utf-8",
    )
    app_config.write_text("runtime: {}\nlogging: {}\n", encoding="utf-8")
    monkeypatch.setattr(setup, "ENV_FILE", env_file)
    monkeypatch.setattr(setup, "APP_CONFIG", app_config)

    with pytest.raises(RuntimeError, match="已拒绝写入"):
        setup._guard_local_development_configuration(allow_reset=False)
    setup._guard_local_development_configuration(allow_reset=True)


def test_development_runtime_url_encodes_password_and_strips_inherited_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NORTHSTAR_DATABASE_URL", "postgresql+psycopg://remote.invalid/prod")
    monkeypatch.setenv("NORTHSTAR_PROJECT_ROOT", "/outside")
    monkeypatch.setenv("DOCKER_HOST", "ssh://remote.example")
    monkeypatch.setenv("POSTGRES_PASSWORD", "ambient-password")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "ambient-project")
    monkeypatch.setenv("COMPOSE_FILE", "/outside/compose.yaml")
    monkeypatch.setenv("COMPOSE_PROFILES", "external-services")

    runtime_url, test_url = setup._local_database_urls("p@ss:/?#", 5432)
    environment = setup._safe_development_environment("p@ss:/?#", 5432)

    assert "p%40ss%3A%2F%3F%23" in runtime_url
    assert test_url.endswith("/northstar_test")
    assert environment["NORTHSTAR_DATABASE_URL"] == runtime_url
    assert environment["NORTHSTAR_TEST_DATABASE_URL"] == test_url
    assert environment["NORTHSTAR_PROJECT_ROOT"] == str(setup.PROJECT_ROOT)
    assert environment["NORTHSTAR_BROKER"] == "paper"
    assert environment["NORTHSTAR_LIVE_TRADING_ENABLED"] == "false"
    assert "DOCKER_HOST" not in environment
    assert environment["POSTGRES_PASSWORD"] == "p@ss:/?#"
    assert environment["POSTGRES_PORT"] == "5432"
    assert environment["COMPOSE_PROJECT_NAME"] == setup.DEVELOPMENT_COMPOSE_PROJECT_NAME
    assert "COMPOSE_FILE" not in environment
    assert "COMPOSE_PROFILES" not in environment
    assert setup._compose_command("config")[:4] == [
        "docker",
        "compose",
        "--project-name",
        setup.DEVELOPMENT_COMPOSE_PROJECT_NAME,
    ]


def test_local_docker_target_uses_the_same_safe_environment_as_compose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = setup._safe_development_environment("local-password", 5432)
    calls: list[dict[str, object]] = []
    results = [
        subprocess.CompletedProcess(["docker", "context", "show"], 0, "default\n", ""),
        subprocess.CompletedProcess(
            ["docker", "context", "inspect"], 0, "ssh://remote.example\n", ""
        ),
    ]
    monkeypatch.setattr(setup.shutil, "which", lambda _: "/usr/bin/docker")

    def fake_run(*_: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        return results.pop(0)

    monkeypatch.setattr(setup.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="不是本机"):
        setup._assert_local_docker_target(environment=environment)

    assert calls and all(call["env"] == environment for call in calls)
    assert "DOCKER_CONTEXT" not in environment


def test_ambient_remote_docker_context_is_rejected_with_safe_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCKER_CONTEXT", "production-remote")
    environment = setup._safe_development_environment("local-password", 5432)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(setup.shutil, "which", lambda _: "/usr/bin/docker")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        assert command[:3] == ["docker", "context", "inspect"]
        assert command[3] == "production-remote"
        return subprocess.CompletedProcess(command, 0, "ssh://production.example\n", "")

    monkeypatch.setattr(setup.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="DOCKER_CONTEXT.*不是本机"):
        setup._assert_local_docker_target(environment=environment)

    assert calls and all(call["env"] == environment for call in calls)


def test_existing_postgres_volume_without_password_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = setup._safe_development_environment("", 5432)
    monkeypatch.setattr(setup, "_assert_local_docker_target", lambda **_: None)

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert command[:3] == ["docker", "volume", "inspect"]
        return subprocess.CompletedProcess(command, 0, setup.DEVELOPMENT_POSTGRES_VOLUME_NAME, "")

    monkeypatch.setattr(setup.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="POSTGRES_PASSWORD 为空"):
        setup._guard_existing_postgres_volume_without_password(
            password="",
            environment=environment,
        )


def test_database_creation_race_converges_when_another_setup_created_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = setup._safe_development_environment("local-password", 5432)
    query_results = iter((False, True))
    monkeypatch.setattr(
        setup,
        "_postgresql_database_exists",
        lambda *_args, **_kwargs: next(query_results),
    )

    def competing_create(*_: object, **__: object) -> None:
        raise subprocess.CalledProcessError(1, ["createdb", "northstar_test"])

    monkeypatch.setattr(setup, "_run", competing_create)

    setup._ensure_postgresql_database("northstar_test", environment=environment)


def test_repeated_safe_environment_initialization_does_not_rewrite_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = "repeatable-password"  # secret-scan: allow; reason: disposable test fixture
    runtime_url, test_url = setup._local_database_urls(password, 5432)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                f"POSTGRES_PASSWORD={password}",
                "POSTGRES_PORT=5432",
                f"NORTHSTAR_DATABASE_URL={runtime_url}",
                f"NORTHSTAR_TEST_DATABASE_URL={test_url}",
                "NORTHSTAR_ENV=dev",
                "NORTHSTAR_BROKER=paper",
                "NORTHSTAR_LIVE_TRADING_ENABLED=false",
                "NORTHSTAR_KILL_SWITCH_ENABLED=false",
                "",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(setup, "ENV_FILE", env_file)
    invoked = False

    def unexpected_run(*_: object, **__: object) -> None:
        nonlocal invoked
        invoked = True

    monkeypatch.setattr(setup, "_run", unexpected_run)

    assert setup._set_safe_development_values() == (password, 5432)
    assert not invoked


def test_active_app_config_creation_is_atomic_and_existing_empty_file_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_config = tmp_path / "app.yaml"
    setup._create_file_atomically(app_config, "runtime: {}\n", mode=0o600)

    assert app_config.read_text(encoding="utf-8") == "runtime: {}\n"

    app_config.write_text("", encoding="utf-8")
    monkeypatch.setattr(setup, "APP_CONFIG", app_config)
    with pytest.raises(RuntimeError, match="app.yaml 为空"):
        setup._ensure_existing_app_config_is_usable()

    app_config.write_text("runtime: [\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="YAML 格式无效"):
        setup._ensure_existing_app_config_is_usable()

    app_config.write_text("- not-an-app-config\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="顶层必须是 YAML 对象"):
        setup._ensure_existing_app_config_is_usable()


def test_configuration_initialization_rejects_broken_app_before_env_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_template = tmp_path / ".env.example"
    env_file = tmp_path / ".env"
    app_template = tmp_path / "app.example.yaml"
    app_config = tmp_path / "app.yaml"
    env_template.write_text("FIRST=\n", encoding="utf-8")
    app_template.write_text("runtime: {}\nlogging: {}\n", encoding="utf-8")
    app_config.write_text("runtime: [\n", encoding="utf-8")
    monkeypatch.setattr(setup, "ENV_TEMPLATE", env_template)
    monkeypatch.setattr(setup, "ENV_FILE", env_file)
    monkeypatch.setattr(setup, "APP_TEMPLATE", app_template)
    monkeypatch.setattr(setup, "APP_CONFIG", app_config)
    invoked = False

    def unexpected_run(*_: object, **__: object) -> None:
        nonlocal invoked
        invoked = True

    monkeypatch.setattr(setup, "_run", unexpected_run)

    with pytest.raises(RuntimeError, match="YAML 格式无效"):
        setup._initialize_active_configuration(allow_reset=False)

    assert not invoked
    assert not env_file.exists()


def test_repeated_configuration_initialization_preserves_env_and_app_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_template = tmp_path / ".env.example"
    env_file = tmp_path / ".env"
    app_template = tmp_path / "app.example.yaml"
    app_config = tmp_path / "app.yaml"
    env_template.write_text("FIRST=\nSECOND=\n", encoding="utf-8")
    app_template.write_text("runtime: {}\nlogging: {}\n", encoding="utf-8")
    monkeypatch.setattr(setup, "ENV_TEMPLATE", env_template)
    monkeypatch.setattr(setup, "ENV_FILE", env_file)
    monkeypatch.setattr(setup, "APP_TEMPLATE", app_template)
    monkeypatch.setattr(setup, "APP_CONFIG", app_config)

    def sync_environment(*_: object, **__: object) -> None:
        sync_env_schema.sync_environment_schema(env_template, env_file, apply=True)

    monkeypatch.setattr(setup, "_run", sync_environment)

    setup._initialize_active_configuration(allow_reset=False)
    first_env = env_file.read_text(encoding="utf-8")
    first_app_config = app_config.read_text(encoding="utf-8")
    setup._initialize_active_configuration(allow_reset=False)

    assert env_file.read_text(encoding="utf-8") == first_env
    assert app_config.read_text(encoding="utf-8") == first_app_config
    assert not list(tmp_path.glob(".env.before-schema-migration-*"))


def test_environment_schema_refuses_active_symlink(tmp_path: Path) -> None:
    target = tmp_path / "outside.env"
    target.write_text("FIRST=secret\n", encoding="utf-8")
    active = tmp_path / ".env"
    try:
        active.symlink_to(target)
    except OSError:
        pytest.skip("当前 Windows 权限不允许创建符号链接。")
    template = tmp_path / ".env.example"
    template.write_text("FIRST=\n", encoding="utf-8")

    with pytest.raises(ValueError, match="不能是符号链接"):
        sync_env_schema.sync_environment_schema(template, active, apply=True)
    with pytest.raises(ValueError, match="不能是符号链接"):
        sync_env_schema.update_environment_values(active, {"FIRST": "updated"})

    assert target.read_text(encoding="utf-8") == "FIRST=secret\n"
