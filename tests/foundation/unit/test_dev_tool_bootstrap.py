"""开发工具 bootstrap 与本地初始化安全门禁。"""

from __future__ import annotations

import argparse
import io
from pathlib import Path
import stat
import subprocess
import tarfile
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

import pytest

from scripts.dev import (
    bootstrap_just,
    check_env,
    project_tools,
    run_just,
    run_uv,
    setup,
    sync_env_schema,
    tool_bootstrap,
)


def test_windows_bootstrap_plan_installs_requested_git_and_repository_local_tools() -> None:
    missing = {"uv", "just", "git"}
    tool_root = Path("C:/workspace/northstar-quant/.northstar")

    steps = tool_bootstrap.build_install_plan(
        missing_tools=missing,
        system_name="Windows",
        project_tool_root=tool_root,
        python_executable="python",
    )

    winget_steps = [step.command for step in steps if step.command[0] == "winget"]
    assert [step[3] for step in winget_steps] == ["Git.Git"]
    assert all(step[:3] == ("winget", "install", "--id") for step in winget_steps)
    assert steps[1].command == (
        "python",
        str(tool_bootstrap.JUST_BOOTSTRAP_SCRIPT),
        "--tool-root",
        str(tool_root),
    )
    assert [step.command for step in steps if step.command[:2] == ("python", "-m")] == [
        (
            "python",
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--target",
            str(tool_root / "bootstrap" / "pipx"),
            "--upgrade",
            "pipx",
        ),
        ("python", "-m", "pipx", "install", "--force", "uv"),
    ]


def test_local_just_plan_uses_the_pinned_stdlib_installer_without_system_package_manager(
    tmp_path: Path,
) -> None:
    release = tmp_path / "os-release"
    release.write_text("ID=debian\nVERSION_CODENAME=trixie\n", encoding="utf-8")
    tool_root = tmp_path / ".northstar"

    steps = tool_bootstrap.build_install_plan(
        missing_tools={"just"},
        system_name="Linux",
        os_release_path=release,
        project_tool_root=tool_root,
        python_executable="/usr/bin/python3",
    )

    assert [step.command for step in steps] == [
        (
            "/usr/bin/python3",
            str(tool_bootstrap.JUST_BOOTSTRAP_SCRIPT),
            "--tool-root",
            str(tool_root),
        )
    ]
    assert "apt-get" not in " ".join(steps[0].command)
    assert "winget" not in " ".join(steps[0].command)
    assert "cargo" not in " ".join(steps[0].command)


def test_linux_bootstrap_refuses_unknown_distribution(tmp_path: Path) -> None:
    release = tmp_path / "os-release"
    release.write_text("ID=fedora\nVERSION_CODENAME=forty-two\n", encoding="utf-8")

    with pytest.raises(tool_bootstrap.BootstrapPlanError, match="仅正式支持 Ubuntu/Debian"):
        tool_bootstrap.build_install_plan(
            missing_tools={"just"},
            system_name="Linux",
            os_release_path=release,
        )


def test_native_postgresql_plan_is_limited_to_debian_packages_and_default_service(
    tmp_path: Path,
) -> None:
    release = tmp_path / "os-release"
    release.write_text("ID=debian\nVERSION_CODENAME=trixie\n", encoding="utf-8")

    steps = tool_bootstrap.build_native_postgresql_plan(
        install_packages=True,
        system_name="Linux",
        os_release_path=release,
    )

    assert [step.command for step in steps] == [
        ("sudo", "apt-get", "update"),
        (
            "sudo",
            "apt-get",
            "install",
            "--yes",
            "--no-install-recommends",
            "postgresql",
            "postgresql-client",
        ),
        ("sudo", "systemctl", "enable", "--now", "postgresql"),
    ]
    assert "stop" not in "\n".join(" ".join(step.command) for step in steps)
    assert "restart" not in "\n".join(" ".join(step.command) for step in steps)
    assert "drop" not in "\n".join(" ".join(step.command) for step in steps)


def test_native_postgresql_service_plan_does_not_reinstall_packages(
    tmp_path: Path,
) -> None:
    release = tmp_path / "os-release"
    release.write_text("ID=ubuntu\nVERSION_CODENAME=noble\n", encoding="utf-8")

    steps = tool_bootstrap.build_native_postgresql_plan(
        install_packages=False,
        system_name="Linux",
        os_release_path=release,
    )

    assert [step.command for step in steps] == [
        ("sudo", "systemctl", "enable", "--now", "postgresql")
    ]


def test_native_postgresql_plan_refuses_non_linux_platform() -> None:
    with pytest.raises(tool_bootstrap.BootstrapPlanError, match="仅支持 Ubuntu/Debian Linux"):
        tool_bootstrap.build_native_postgresql_plan(
            install_packages=True,
            system_name="Windows",
        )


def test_linux_uv_plan_installs_pipx_and_uv_under_the_repository_without_bypassing_pep668(
    tmp_path: Path,
) -> None:
    release = tmp_path / "os-release"
    release.write_text("ID=debian\nVERSION_CODENAME=trixie\n", encoding="utf-8")
    tool_root = tmp_path / ".northstar"

    steps = tool_bootstrap.build_install_plan(
        missing_tools={"uv"},
        system_name="Linux",
        os_release_path=release,
        project_tool_root=tool_root,
        python_executable="/usr/bin/python3",
    )

    commands = [step.command for step in steps]
    assert commands == [
        (
            "/usr/bin/python3",
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--target",
            str(tool_root / "bootstrap" / "pipx"),
            "--upgrade",
            "pipx",
        ),
        ("/usr/bin/python3", "-m", "pipx", "install", "--force", "uv"),
    ]
    flattened = "\n".join(" ".join(command) for command in commands)
    assert "--user" not in flattened
    assert "--break-system-packages" not in flattened
    assert "ensurepath" not in flattened
    assert "sudo apt-get" not in flattened
    assert steps[0].environment == steps[1].environment
    assert steps[0].environment is not None
    assert steps[0].environment["PIPX_BIN_DIR"] == str(tool_root / "bin")
    assert steps[0].environment["PIPX_DEFAULT_PYTHON"] == "/usr/bin/python3"
    assert steps[0].environment["PYTHONPATH"] == str(tool_root / "bootstrap" / "pipx")


def test_linux_uv_plan_is_independent_of_any_system_pipx(tmp_path: Path) -> None:
    release = tmp_path / "os-release"
    release.write_text("ID=ubuntu\nVERSION_CODENAME=noble\n", encoding="utf-8")
    tool_root = tmp_path / "repository" / ".northstar"

    steps = tool_bootstrap.build_install_plan(
        missing_tools={"uv"},
        system_name="Linux",
        os_release_path=release,
        project_tool_root=tool_root,
        python_executable="/usr/bin/python3",
    )

    assert [step.command for step in steps] == [
        (
            "/usr/bin/python3",
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--target",
            str(tool_root / "bootstrap" / "pipx"),
            "--upgrade",
            "pipx",
        ),
        ("/usr/bin/python3", "-m", "pipx", "install", "--force", "uv"),
    ]


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


def test_execute_install_plan_merges_explicit_environment_without_shell() -> None:
    captured: list[tuple[list[str], dict[str, object]]] = []

    def fake_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[object]:
        captured.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    tool_bootstrap.execute_install_plan(
        [tool_bootstrap.InstallStep("测试", ("installer", "--safe"))],
        runner=fake_runner,
        environment={"PIPX_HOME": "/safe/pipx", "XDG_STATE_HOME": "/safe/state"},
    )

    assert captured[0][0] == ["installer", "--safe"]
    environment = captured[0][1]["env"]
    assert isinstance(environment, dict)
    assert environment["PIPX_HOME"] == "/safe/pipx"
    assert environment["XDG_STATE_HOME"] == "/safe/state"


def test_execute_install_plan_merges_step_local_environment_without_shell() -> None:
    captured: list[tuple[list[str], dict[str, object]]] = []

    def fake_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[object]:
        captured.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    tool_bootstrap.execute_install_plan(
        [
            tool_bootstrap.InstallStep(
                "仓库本地工具",
                ("python", "-m", "pipx", "install", "uv"),
                environment={
                    "PIPX_HOME": "/safe/.northstar/pipx",
                    "PIPX_BIN_DIR": "/safe/.northstar/bin",
                },
            )
        ],
        runner=fake_runner,
        environment={"XDG_STATE_HOME": "/safe/.northstar/state"},
    )

    environment = captured[0][1]["env"]
    assert isinstance(environment, dict)
    assert environment["PIPX_HOME"] == "/safe/.northstar/pipx"
    assert environment["PIPX_BIN_DIR"] == "/safe/.northstar/bin"
    assert environment["XDG_STATE_HOME"] == "/safe/.northstar/state"


def test_bootstrap_requires_supported_python_before_system_installation() -> None:
    assert setup._bootstrap_python_error((3, 10)) is not None
    assert setup._bootstrap_python_error((3, 11)) is None


def test_missing_bootstrap_tools_requires_repository_local_uv_and_just(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    locations = {"git": "/usr/bin/git"}
    monkeypatch.setattr(setup.shutil, "which", lambda command: locations.get(command))

    def missing_uv() -> Path:
        raise project_tools.ProjectToolError("missing")

    def missing_just() -> Path:
        raise project_tools.ProjectToolError("missing")

    monkeypatch.setattr(setup, "repository_uv_executable", missing_uv)
    monkeypatch.setattr(setup, "repository_just_executable", missing_just)
    assert setup._missing_bootstrap_tools() == {"just", "uv"}

    monkeypatch.setattr(
        setup,
        "repository_uv_executable",
        lambda: Path("/workspace/northstar-quant/.northstar/bin/uv"),
    )
    monkeypatch.setattr(
        setup,
        "repository_just_executable",
        lambda: Path("/workspace/northstar-quant/.northstar/bin/just"),
    )
    assert setup._missing_bootstrap_tools() == set()


def test_repository_tool_root_for_bootstrap_requires_an_owned_repository_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tool_root = tmp_path / ".northstar"
    monkeypatch.setattr(setup, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(setup, "repository_tool_root", lambda: tool_root)

    assert setup._repository_tool_root_for_bootstrap() == tool_root
    assert not tool_root.exists()


def test_repository_tool_root_check_refuses_symbolic_link() -> None:
    path = Mock(spec=Path)
    path.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFLNK)

    with pytest.raises(RuntimeError, match="符号链接"):
        setup._require_owned_writable_directory(path, label="仓库工具目录")


@pytest.mark.skipif(project_tools.os.name == "nt", reason="Windows symlink permissions vary by runner")
def test_repository_tool_root_for_bootstrap_rejects_a_nested_symbolic_link(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tool_root = tmp_path / ".northstar"
    tool_root.mkdir()
    (tool_root / "bootstrap").symlink_to(tmp_path)
    monkeypatch.setattr(setup, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(setup, "repository_tool_root", lambda: tool_root)

    with pytest.raises(RuntimeError, match="bootstrap 目录不能是符号链接"):
        setup._repository_tool_root_for_bootstrap()


@pytest.mark.skipif(project_tools.os.name == "nt", reason="Windows symlink permissions vary by runner")
def test_repository_tool_root_for_bootstrap_rejects_download_directory_symbolic_link(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tool_root = tmp_path / ".northstar"
    tool_root.mkdir()
    (tool_root / "downloads").symlink_to(tmp_path)
    monkeypatch.setattr(setup, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(setup, "repository_tool_root", lambda: tool_root)

    with pytest.raises(RuntimeError, match="downloads 目录不能是符号链接"):
        setup._repository_tool_root_for_bootstrap()


def test_uv_bootstrap_passes_repository_root_to_the_plan_and_keeps_environment_per_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = tool_bootstrap.InstallStep(
        "安装 uv",
        ("python", "-m", "pipx", "install", "--force", "uv"),
    )
    tool_root = Path("/safe/.northstar")
    planner_arguments: list[dict[str, object]] = []
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(setup, "_missing_bootstrap_tools", lambda **_: {"uv"})
    monkeypatch.setattr(setup, "_repository_tool_root_for_bootstrap", lambda: tool_root)

    def fake_plan(**kwargs: object) -> list[tool_bootstrap.InstallStep]:
        planner_arguments.append(kwargs)
        return [step]

    monkeypatch.setattr(setup, "build_install_plan", fake_plan)

    def fake_execute(_: object, **kwargs: object) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(setup, "execute_install_plan", fake_execute)

    assert (
        setup._bootstrap_tools(
            _bootstrap_args(apply=True, tool_confirmation="YES")
        )
        == 0
    )
    assert planner_arguments == [
        {
            "missing_tools": {"uv"},
            "project_tool_root": tool_root,
            "python_executable": setup.sys.executable,
        }
    ]
    assert captured == [{}]


def test_repository_uv_resolver_requires_an_executable_under_dot_northstar(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    executable_name = "uv.exe" if project_tools.os.name == "nt" else "uv"
    executable = repository / ".northstar" / "bin" / executable_name
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)

    assert project_tools.repository_uv_executable(project_root=repository) == executable


def test_repository_just_resolver_requires_an_executable_under_dot_northstar(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    executable_name = "just.exe" if project_tools.os.name == "nt" else "just"
    executable = repository / ".northstar" / "bin" / executable_name
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)

    assert project_tools.repository_just_executable(project_root=repository) == executable


@pytest.mark.skipif(project_tools.os.name == "nt", reason="Windows symlink permissions vary by runner")
def test_repository_uv_resolver_rejects_a_launcher_that_escapes_dot_northstar(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    tool_root = repository / ".northstar"
    launcher = tool_root / "bin" / "uv"
    outside = tmp_path / "outside-uv"
    launcher.parent.mkdir(parents=True)
    outside.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    outside.chmod(0o755)
    launcher.symlink_to(outside)

    with pytest.raises(project_tools.ProjectToolError, match=".northstar 外部"):
        project_tools.repository_uv_executable(project_root=repository)


@pytest.mark.skipif(project_tools.os.name == "nt", reason="Windows symlink permissions vary by runner")
def test_repository_just_resolver_rejects_a_launcher_that_escapes_dot_northstar(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    tool_root = repository / ".northstar"
    launcher = tool_root / "bin" / "just"
    outside = tmp_path / "outside-just"
    launcher.parent.mkdir(parents=True)
    outside.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    outside.chmod(0o755)
    launcher.symlink_to(outside)

    with pytest.raises(project_tools.ProjectToolError, match=".northstar 外部"):
        project_tools.repository_just_executable(project_root=repository)


def test_run_uv_executes_the_verified_repository_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = Path("/safe/.northstar/bin/uv")
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(run_uv, "repository_uv_executable", lambda: launcher)
    monkeypatch.setattr(
        run_uv,
        "repository_uv_cache_directory",
        lambda: Path("/safe/.northstar/cache/uv"),
    )
    monkeypatch.setenv("UV_NO_CACHE", "1")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[object]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(run_uv.subprocess, "run", fake_run)

    assert run_uv.main(["lock", "--check", "--offline"]) == 0
    command, options = calls[0]
    assert command == [str(launcher), "lock", "--check", "--offline"]
    assert options["cwd"] == run_uv.PROJECT_ROOT
    assert options["check"] is False
    environment = options["env"]
    assert isinstance(environment, dict)
    assert environment["UV_CACHE_DIR"] == "/safe/.northstar/cache/uv"
    assert "UV_NO_CACHE" not in environment


def test_run_just_executes_the_verified_repository_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = Path("/safe/.northstar/bin/just")
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(run_just, "repository_just_executable", lambda: launcher)

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[object]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(run_just.subprocess, "run", fake_run)

    assert run_just.main(["check"]) == 0
    assert calls == [
        (
            [str(launcher), "check"],
            {"cwd": run_just.PROJECT_ROOT, "check": False},
        )
    ]


def test_just_release_assets_are_pinned_for_tier_one_workstations() -> None:
    linux = bootstrap_just.release_asset_for_platform(system_name="Linux", machine="x86_64")
    windows = bootstrap_just.release_asset_for_platform(system_name="Windows", machine="AMD64")

    assert linux.filename == "just-1.57.0-x86_64-unknown-linux-musl.tar.gz"
    assert linux.archive_format == "tar.gz"
    assert linux.executable_name == "just"
    assert windows.filename == "just-1.57.0-x86_64-pc-windows-msvc.zip"
    assert windows.archive_format == "zip"
    assert windows.executable_name == "just.exe"
    assert len(linux.sha256) == len(windows.sha256) == 64
    with pytest.raises(bootstrap_just.JustBootstrapError, match="仅支持"):
        bootstrap_just.release_asset_for_platform(system_name="Darwin", machine="arm64")


def test_just_bootstrap_extracts_only_the_expected_zip_executable(tmp_path: Path) -> None:
    asset = bootstrap_just.release_asset_for_platform(system_name="Windows", machine="x86_64")
    archive_path = tmp_path / asset.filename
    destination = tmp_path / "bin" / asset.executable_name
    destination.parent.mkdir()
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("just-1.57.0-x86_64-pc-windows-msvc/just.exe", b"windows-just")
        archive.writestr("just-1.57.0-x86_64-pc-windows-msvc/README.md", b"ignored")

    bootstrap_just._install_from_zip(archive_path, asset=asset, destination=destination)

    assert destination.read_bytes() == b"windows-just"


def test_just_bootstrap_extracts_only_the_expected_tar_executable(tmp_path: Path) -> None:
    asset = bootstrap_just.release_asset_for_platform(system_name="Linux", machine="x86_64")
    archive_path = tmp_path / asset.filename
    destination = tmp_path / "bin" / asset.executable_name
    destination.parent.mkdir()
    with tarfile.open(archive_path, "w:gz") as archive:
        executable = tarfile.TarInfo("just-1.57.0-x86_64-unknown-linux-musl/just")
        executable.size = len(b"linux-just")
        archive.addfile(executable, io.BytesIO(b"linux-just"))
        readme = tarfile.TarInfo("just-1.57.0-x86_64-unknown-linux-musl/README.md")
        readme.size = len(b"ignored")
        archive.addfile(readme, io.BytesIO(b"ignored"))

    bootstrap_just._install_from_tar(archive_path, asset=asset, destination=destination)

    assert destination.read_bytes() == b"linux-just"
    assert destination.stat().st_mode & stat.S_IXUSR


def test_just_bootstrap_rejects_unsafe_archive_member_paths(tmp_path: Path) -> None:
    asset = bootstrap_just.release_asset_for_platform(system_name="Windows", machine="x86_64")
    archive_path = tmp_path / asset.filename
    destination = tmp_path / "bin" / asset.executable_name
    destination.parent.mkdir()
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../just.exe", b"unsafe")

    with pytest.raises(bootstrap_just.JustBootstrapError, match="不安全"):
        bootstrap_just._install_from_zip(archive_path, asset=asset, destination=destination)


def test_just_bootstrap_rejects_a_digest_mismatch_without_leaking_temp_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    asset = bootstrap_just.JustReleaseAsset(
        filename="just.tar.gz",
        sha256="0" * 64,
        archive_format="tar.gz",
        executable_name="just",
    )

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        @staticmethod
        def geturl() -> str:
            return "https://github.com/casey/just/releases/download/1.57.0/just.tar.gz"

        def read(self, _: int) -> bytes:
            if getattr(self, "_read", False):
                return b""
            self._read = True
            return b"untrusted-download"

    monkeypatch.setattr(bootstrap_just, "urlopen", lambda *_args, **_kwargs: Response())

    with pytest.raises(bootstrap_just.JustBootstrapError, match="SHA-256"):
        bootstrap_just._download_asset(asset, directory=tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_environment_check_requires_native_loopback_postgres_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(check_env, "_configured_postgres_port", lambda: 5544)
    monkeypatch.setattr(
        check_env.shutil,
        "which",
        lambda command: "/usr/bin/pg_isready" if command == "pg_isready" else None,
    )

    def accepting_service(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "accepting connections\n", "")

    monkeypatch.setattr(check_env, "_command_result", accepting_service)

    result = check_env._check_local_postgres_service(required=True)

    assert result["status"] == "ok"
    assert "127.0.0.1:5544" in result["message"]
    assert commands == [
        [
            "pg_isready",
            "--host",
            "127.0.0.1",
            "--port",
            "5544",
            "--dbname",
            "postgres",
        ]
    ]


def test_environment_check_fails_closed_for_an_invalid_native_postgres_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(check_env, "_configured_postgres_port", lambda: None)
    monkeypatch.setattr(
        check_env,
        "_command_result",
        lambda *_args, **_kwargs: pytest.fail("无效端口时不得探测服务"),
    )

    result = check_env._check_local_postgres_service(required=True)

    assert result["status"] == "error"
    assert "POSTGRES_PORT" in result["message"]


def _bootstrap_args(
    *,
    apply: bool,
    tool_confirmation: str = "",
) -> argparse.Namespace:
    return argparse.Namespace(
        apply=apply,
        confirm_tool_install=tool_confirmation,
    )


def _workstation_args(
    *,
    tool_confirmation: str = "",
) -> argparse.Namespace:
    return argparse.Namespace(
        confirm_tool_install=tool_confirmation,
    )


def test_setup_bootstrap_default_only_renders_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    step = tool_bootstrap.InstallStep("安装 just", ("python", "bootstrap_just.py"))
    executed = False
    monkeypatch.setattr(setup, "_missing_bootstrap_tools", lambda **_: {"just"})
    monkeypatch.setattr(setup, "build_install_plan", lambda **_: [step])

    def fake_execute(_: object, **__: object) -> None:
        nonlocal executed
        executed = True

    monkeypatch.setattr(setup, "execute_install_plan", fake_execute)

    assert setup._bootstrap_tools(_bootstrap_args(apply=False)) == 0
    assert not executed


def test_setup_bootstrap_requires_one_explicit_tool_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = tool_bootstrap.InstallStep("安装 uv", ("pipx", "install", "--force", "uv"))
    executed = False
    monkeypatch.setattr(setup, "_missing_bootstrap_tools", lambda: {"uv"})
    monkeypatch.setattr(
        setup,
        "_repository_tool_root_for_bootstrap",
        lambda: Path("/safe/.northstar"),
    )
    monkeypatch.setattr(setup, "build_install_plan", lambda **_: [step])

    def fake_execute(_: object, **__: object) -> None:
        nonlocal executed
        executed = True

    monkeypatch.setattr(setup, "execute_install_plan", fake_execute)

    assert (
        setup._bootstrap_tools(_bootstrap_args(apply=True))
        == 2
    )
    assert not executed
    assert (
        setup._bootstrap_tools(_bootstrap_args(apply=True, tool_confirmation="YES"))
        == 0
    )
    assert executed


def test_workstation_initializer_continues_after_repository_uv_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = tool_bootstrap.InstallStep("安装 uv", ("pipx", "install", "--force", "uv"))
    executed = False
    missing_states = iter(({"uv", "just"}, set()))
    calls: list[tuple[list[str], dict[str, object]]] = []
    events: list[str] = []
    monkeypatch.setattr(setup, "_missing_bootstrap_tools", lambda: next(missing_states))
    monkeypatch.setattr(
        setup,
        "_repository_tool_root_for_bootstrap",
        lambda: Path("/safe/.northstar"),
    )
    monkeypatch.setattr(setup, "build_install_plan", lambda **_: [step])

    def fake_execute(_: object, **__: object) -> None:
        nonlocal executed
        executed = True

    monkeypatch.setattr(setup, "execute_install_plan", fake_execute)
    monkeypatch.setattr(
        setup,
        "repository_just_executable",
        lambda: Path("/safe/.northstar/bin/just"),
    )
    monkeypatch.setattr(
        setup,
        "_guard_local_development_configuration",
        lambda **_: events.append("guard"),
    )
    monkeypatch.setattr(
        setup,
        "_ensure_native_postgresql_for_workstation",
        lambda **_: events.append("postgres"),
    )
    monkeypatch.setattr(
        setup,
        "_initialize_active_configuration",
        lambda **_: events.append("config"),
    )
    monkeypatch.setattr(
        setup,
        "_ensure_northstar_postgresql_role",
        lambda: events.append("role") or "local-password",
    )
    monkeypatch.setattr(
        setup,
        "_set_safe_development_values",
        lambda: events.append("safe-values") or ("local-password", 5432),
    )
    monkeypatch.setattr(setup, "_read_env_value", lambda _key: "")

    def available_environment(**kwargs: object) -> list[check_env.CheckResult]:
        events.append("environment-check")
        assert kwargs == {
            "require_config": False,
            "require_postgres": True,
            "require_just": True,
            "require_git": True,
        }
        return []

    monkeypatch.setattr(setup, "check_environment", available_environment)
    monkeypatch.setattr(
        setup,
        "_run",
        lambda command, **kwargs: (
            events.append(f"run:{command[-1]}"),
            calls.append((command, kwargs)),
        ),
    )

    assert (
        setup._initialize_workstation(_workstation_args(tool_confirmation="YES")) == 0
    )
    assert executed
    assert events == [
        "guard",
        "postgres",
        "environment-check",
        "run:env-bootstrap",
        "config",
        "role",
        "safe-values",
        "run:dev-postgres",
    ]
    assert calls == [
        (["/safe/.northstar/bin/just", "env-bootstrap"], {}),
        (["/safe/.northstar/bin/just", "dev-postgres"], {}),
    ]


def test_workstation_initializer_waits_only_when_a_host_tool_is_still_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    step = tool_bootstrap.InstallStep("安装 Git", ("winget", "install", "--id", "Git.Git"))
    missing_states = iter(({"git"}, {"git"}))
    monkeypatch.setattr(setup, "_missing_bootstrap_tools", lambda: next(missing_states))
    monkeypatch.setattr(setup, "build_install_plan", lambda **_: [step])
    monkeypatch.setattr(setup, "execute_install_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        setup,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("不可定位的 Git 不应继续初始化"),
    )

    assert setup._initialize_workstation(_workstation_args(tool_confirmation="YES")) == 2
    assert "当前进程仍无法定位：git" in capsys.readouterr().err


def test_workstation_initializer_delegates_to_just_when_tools_are_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    bootstrap_checks: list[dict[str, object]] = []

    def missing_bootstrap_tools(**kwargs: object) -> set[str]:
        bootstrap_checks.append(kwargs)
        return set()

    monkeypatch.setattr(setup, "_missing_bootstrap_tools", missing_bootstrap_tools)
    monkeypatch.setattr(setup, "check_environment", lambda **_: [])
    monkeypatch.setattr(setup, "_guard_local_development_configuration", lambda **_: None)
    monkeypatch.setattr(setup, "_read_env_value", lambda _key: "")
    monkeypatch.setattr(
        setup,
        "_ensure_native_postgresql_for_workstation",
        lambda **_: None,
    )
    monkeypatch.setattr(setup, "_initialize_active_configuration", lambda **_: None)
    monkeypatch.setattr(
        setup,
        "_ensure_northstar_postgresql_role",
        lambda: "local-password",
    )
    monkeypatch.setattr(
        setup,
        "_set_safe_development_values",
        lambda: ("local-password", 5432),
    )
    monkeypatch.setattr(
        setup,
        "repository_just_executable",
        lambda: Path("/safe/.northstar/bin/just"),
    )

    def fake_run(command: list[str], **kwargs: object) -> None:
        calls.append((command, kwargs))

    monkeypatch.setattr(setup, "_run", fake_run)

    assert setup._initialize_workstation(_workstation_args()) == 0
    assert bootstrap_checks == [{}]
    assert calls == [
        (["/safe/.northstar/bin/just", "env-bootstrap"], {}),
        (["/safe/.northstar/bin/just", "dev-postgres"], {}),
    ]


def test_workstation_initializer_stops_before_dependency_sync_when_default_postgres_provisioning_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provisioning_attempts: list[dict[str, object]] = []
    monkeypatch.setattr(setup, "_missing_bootstrap_tools", lambda: set())
    monkeypatch.setattr(setup, "_guard_local_development_configuration", lambda **_: None)
    monkeypatch.setattr(setup, "_read_env_value", lambda _key: "")

    def unavailable_native_postgres(**kwargs: object) -> None:
        provisioning_attempts.append(kwargs)
        raise RuntimeError("默认服务无法启动。")

    monkeypatch.setattr(
        setup,
        "_ensure_native_postgresql_for_workstation",
        unavailable_native_postgres,
    )
    monkeypatch.setattr(
        setup,
        "check_environment",
        lambda **_: pytest.fail("默认 PostgreSQL provision 失败后不得继续环境检查"),
    )
    monkeypatch.setattr(
        setup,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("PostgreSQL provision 失败后不得同步依赖或初始化数据库"),
    )

    assert setup._initialize_workstation(_workstation_args()) == 1
    assert provisioning_attempts == [{"port": 5432}]
    assert "默认服务无法启动" in capsys.readouterr().err


def test_workstation_initializer_rejects_unsafe_configuration_before_postgres_provisioning(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(setup, "_missing_bootstrap_tools", lambda: set())
    monkeypatch.setattr(
        setup,
        "_guard_local_development_configuration",
        lambda **_: (_ for _ in ()).throw(RuntimeError("检测到 production .env")),
    )
    monkeypatch.setattr(
        setup,
        "_ensure_native_postgresql_for_workstation",
        lambda **_: pytest.fail("不安全 .env 不得触发 PostgreSQL 系统修改"),
    )

    assert setup._initialize_workstation(_workstation_args()) == 1
    assert "production .env" in capsys.readouterr().err


def test_workstation_initializer_rejects_redundant_postgres_switch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        setup.sys,
        "argv",
        ["setup.py", "--initialize-workstation", "--with-postgres"],
    )

    assert setup.main() == 2
    assert "已包含本地 PostgreSQL 初始化与迁移" in capsys.readouterr().err


def test_workstation_initializer_refuses_noninteractive_tool_install_without_yes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class NonInteractiveInput:
        @staticmethod
        def isatty() -> bool:
            return False

    step = tool_bootstrap.InstallStep("安装 uv", ("pipx", "install", "--force", "uv"))
    monkeypatch.setattr(setup, "_missing_bootstrap_tools", lambda **_: {"uv"})
    monkeypatch.setattr(setup, "build_install_plan", lambda **_: [step])
    monkeypatch.setattr(setup.sys, "stdin", NonInteractiveInput())
    monkeypatch.setattr(
        setup,
        "execute_install_plan",
        lambda _, **__: pytest.fail("没有确认时不能执行开发工具安装"),
    )
    monkeypatch.setattr(
        setup,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("没有确认时不能继续项目初始化"),
    )

    assert setup._initialize_workstation(_workstation_args()) == 2
    assert "无法交互确认" in capsys.readouterr().err


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


def test_development_runtime_url_uses_fixed_loopback_native_postgres_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NORTHSTAR_DATABASE_URL", "postgresql+psycopg://remote.invalid/prod")
    monkeypatch.setenv("NORTHSTAR_PROJECT_ROOT", "/outside")
    monkeypatch.setenv("POSTGRES_PASSWORD", "ambient-password")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("PGSERVICE", "external-service")
    monkeypatch.setenv("PGHOSTADDR", "198.51.100.50")

    runtime_url, test_url = setup._local_database_urls("p@ss:/?#", 5432)
    environment = setup._safe_development_environment("p@ss:/?#", 5432)

    assert "p%40ss%3A%2F%3F%23" in runtime_url
    assert test_url.endswith("/northstar_test")
    assert environment["NORTHSTAR_DATABASE_URL"] == runtime_url
    assert environment["NORTHSTAR_TEST_DATABASE_URL"] == test_url
    assert environment["NORTHSTAR_PROJECT_ROOT"] == str(setup.PROJECT_ROOT)
    assert environment["NORTHSTAR_BROKER"] == "paper"
    assert environment["NORTHSTAR_LIVE_TRADING_ENABLED"] == "false"
    assert environment["POSTGRES_PASSWORD"] == "p@ss:/?#"
    assert environment["POSTGRES_PORT"] == "5432"
    assert environment["PGHOST"] == "127.0.0.1"
    assert environment["PGHOSTADDR"] == "127.0.0.1"
    assert environment["PGPORT"] == "5432"
    assert environment["PGUSER"] == "northstar"
    assert environment["PGDATABASE"] == "postgres"
    assert "PGSERVICE" not in environment
    assert setup._native_postgres_command(
        "psql",
        port=5432,
        arguments=("--dbname", "postgres", "--command", "SELECT 1"),
    ) == [
        "psql",
        "--host",
        "127.0.0.1",
        "--port",
        "5432",
        "--username",
        "northstar",
        "--dbname",
        "postgres",
        "--command",
        "SELECT 1",
    ]


def test_workstation_native_postgres_installs_missing_clients_then_rechecks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = tool_bootstrap.InstallStep("安装 PostgreSQL", ("sudo", "apt-get", "install"))
    missing_states = iter(({"psql", "pg_dump"}, set()))
    plan_requests: list[bool] = []
    executed: list[list[tool_bootstrap.InstallStep]] = []
    monkeypatch.setattr(
        setup,
        "_missing_native_postgresql_client_tools",
        lambda: next(missing_states),
    )
    monkeypatch.setattr(
        setup,
        "_native_postgresql_service_is_ready",
        lambda **_: False,
    )
    monkeypatch.setattr(setup, "_wait_for_native_postgresql_service", lambda **_: True)
    monkeypatch.setattr(setup.shutil, "which", lambda _command: "/usr/bin/tool")

    def build_plan(*, install_packages: bool) -> list[tool_bootstrap.InstallStep]:
        plan_requests.append(install_packages)
        return [step]

    monkeypatch.setattr(setup, "build_native_postgresql_plan", build_plan)
    monkeypatch.setattr(
        setup,
        "execute_install_plan",
        lambda steps: executed.append(list(steps)),
    )

    setup._ensure_native_postgresql_for_workstation(port=5432)

    assert plan_requests == [True]
    assert executed == [[step]]


def test_workstation_native_postgres_starts_only_service_when_clients_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_requests: list[bool] = []
    monkeypatch.setattr(setup, "_missing_native_postgresql_client_tools", lambda: set())
    monkeypatch.setattr(
        setup,
        "_native_postgresql_service_is_ready",
        lambda **_: False,
    )
    monkeypatch.setattr(setup, "_wait_for_native_postgresql_service", lambda **_: True)
    monkeypatch.setattr(setup.shutil, "which", lambda _command: "/usr/bin/tool")
    monkeypatch.setattr(
        setup,
        "build_native_postgresql_plan",
        lambda *, install_packages: plan_requests.append(install_packages)
        or [tool_bootstrap.InstallStep("启动 PostgreSQL", ("sudo", "systemctl"))],
    )
    monkeypatch.setattr(setup, "execute_install_plan", lambda _steps: None)

    setup._ensure_native_postgresql_for_workstation(port=5432)

    assert plan_requests == [False]


def test_workstation_native_postgres_never_mutates_a_nondefault_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        setup,
        "_missing_native_postgresql_client_tools",
        lambda: {"psql"},
    )
    monkeypatch.setattr(
        setup,
        "_native_postgresql_service_is_ready",
        lambda **_: False,
    )
    monkeypatch.setattr(
        setup,
        "build_native_postgresql_plan",
        lambda **_: pytest.fail("非默认端口不得生成系统安装计划"),
    )

    with pytest.raises(RuntimeError, match="仅管理 5432 端口"):
        setup._ensure_native_postgresql_for_workstation(port=55432)


def test_workstation_native_postgres_refuses_unsupported_platform_before_sudo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        setup,
        "_missing_native_postgresql_client_tools",
        lambda: {"psql"},
    )
    monkeypatch.setattr(
        setup,
        "_native_postgresql_service_is_ready",
        lambda **_: False,
    )
    monkeypatch.setattr(
        setup,
        "build_native_postgresql_plan",
        lambda **_: (_ for _ in ()).throw(
            tool_bootstrap.BootstrapPlanError("仅支持 Ubuntu/Debian Linux")
        ),
    )
    monkeypatch.setattr(
        setup.shutil,
        "which",
        lambda _command: pytest.fail("不受支持的平台不得先检查或调用 sudo/systemctl"),
    )

    with pytest.raises(RuntimeError, match="仅支持 Ubuntu/Debian Linux"):
        setup._ensure_native_postgresql_for_workstation(port=5432)


def test_workstation_native_postgres_reuses_a_ready_service_without_sudo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup, "_missing_native_postgresql_client_tools", lambda: set())
    monkeypatch.setattr(
        setup,
        "_native_postgresql_service_is_ready",
        lambda **_: True,
    )
    monkeypatch.setattr(
        setup,
        "build_native_postgresql_plan",
        lambda **_: pytest.fail("已就绪的服务不得生成系统安装计划"),
    )

    setup._ensure_native_postgresql_for_workstation(port=5432)


def test_new_northstar_role_sql_uses_stdin_and_never_exposes_password_in_argv(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: list[tuple[list[str], dict[str, object]]] = []
    password = "fresh-local-password"  # secret-scan: allow; reason: disposable test fixture

    def successful_create(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("PGHOST", "db.example.invalid")
    monkeypatch.setenv("PGSERVICE", "remote-service")
    monkeypatch.setenv("NORTHSTAR_DATABASE_URL", "postgresql+psycopg://remote.invalid/db")
    monkeypatch.setattr(setup.subprocess, "run", successful_create)

    setup._create_northstar_postgresql_role(password)

    assert captured[0][0][:5] == ["sudo", "-n", "-u", "postgres", "psql"]
    assert password not in " ".join(captured[0][0])
    assert password in str(captured[0][1]["input"])
    assert "CREATE ROLE northstar" in str(captured[0][1]["input"])
    assert "PGHOST" not in captured[0][1]["env"]
    assert "PGSERVICE" not in captured[0][1]["env"]
    assert "NORTHSTAR_DATABASE_URL" not in captured[0][1]["env"]
    assert password not in capsys.readouterr().out


def test_fresh_northstar_role_generates_and_persists_a_local_password_only_after_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "POSTGRES_PASSWORD=\nPOSTGRES_PORT=5432\n",  # secret-scan: allow; reason: disposable test fixture
        encoding="utf-8",
    )
    events: list[tuple[str, str | None]] = []
    generated_password = "generated-local-password"  # secret-scan: allow; reason: disposable test fixture
    monkeypatch.setattr(setup, "ENV_FILE", env_file)
    monkeypatch.setattr(
        setup,
        "_ensure_postgres_administrator_access",
        lambda: events.append(("sudo", None)),
    )
    monkeypatch.setattr(setup, "_northstar_postgresql_role_exists", lambda: False)
    monkeypatch.setattr(setup.secrets, "token_hex", lambda _size: generated_password)
    monkeypatch.setattr(
        setup,
        "_create_northstar_postgresql_role",
        lambda value: events.append(("create", value)),
    )
    monkeypatch.setattr(
        setup,
        "_write_generated_local_postgres_password",
        lambda value: events.append(("write", value)),
    )

    assert setup._ensure_northstar_postgresql_role() == generated_password
    assert events == [
        ("sudo", None),
        ("create", generated_password),
        ("write", generated_password),
    ]


def test_existing_northstar_role_without_password_fails_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "POSTGRES_PASSWORD=\nPOSTGRES_PORT=5432\n",  # secret-scan: allow; reason: disposable test fixture
        encoding="utf-8",
    )
    monkeypatch.setattr(setup, "ENV_FILE", env_file)
    monkeypatch.setattr(setup, "_ensure_postgres_administrator_access", lambda: None)
    monkeypatch.setattr(setup, "_northstar_postgresql_role_exists", lambda: True)
    monkeypatch.setattr(
        setup,
        "_create_northstar_postgresql_role",
        lambda *_: pytest.fail("既有角色不得 CREATE/ALTER"),
    )
    monkeypatch.setattr(
        setup,
        "_write_generated_local_postgres_password",
        lambda *_: pytest.fail("既有角色不得写入新密码"),
    )

    with pytest.raises(RuntimeError, match="不会生成覆盖它的密码"):
        setup._ensure_northstar_postgresql_role()


def test_existing_valid_northstar_role_never_requires_sudo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = "known-local-password"  # secret-scan: allow; reason: disposable test fixture
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"POSTGRES_PASSWORD={password}\nPOSTGRES_PORT=5432\n",  # secret-scan: allow; reason: disposable test fixture
        encoding="utf-8",
    )
    monkeypatch.setattr(setup, "ENV_FILE", env_file)
    monkeypatch.setattr(setup, "_assert_native_postgres_ready", lambda **_: None)
    monkeypatch.setattr(
        setup,
        "_ensure_postgres_administrator_access",
        lambda: pytest.fail("可验证既有角色不需要 sudo"),
    )

    assert setup._ensure_northstar_postgresql_role() == password


def test_missing_northstar_role_uses_existing_local_password_without_rewriting_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = "known-local-password"  # secret-scan: allow; reason: disposable test fixture
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"POSTGRES_PASSWORD={password}\nPOSTGRES_PORT=5432\n",  # secret-scan: allow; reason: disposable test fixture
        encoding="utf-8",
    )
    created: list[str] = []
    monkeypatch.setattr(setup, "ENV_FILE", env_file)
    monkeypatch.setattr(
        setup,
        "_assert_native_postgres_ready",
        lambda **_: (_ for _ in ()).throw(RuntimeError("authentication failed")),
    )
    monkeypatch.setattr(setup, "_ensure_postgres_administrator_access", lambda: None)
    monkeypatch.setattr(setup, "_northstar_postgresql_role_exists", lambda: False)
    monkeypatch.setattr(
        setup,
        "_create_northstar_postgresql_role",
        lambda value: created.append(value),
    )
    monkeypatch.setattr(
        setup,
        "_write_generated_local_postgres_password",
        lambda *_: pytest.fail("已有密码不得被重新写入"),
    )

    assert setup._ensure_northstar_postgresql_role() == password
    assert created == [password]


def test_native_postgres_ready_fails_before_credential_probe_when_service_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = setup._safe_development_environment("local-password", 5432)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def unavailable_service(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 2, "", "no response")

    monkeypatch.setattr(setup.subprocess, "run", unavailable_service)

    with pytest.raises(RuntimeError, match="未在 127.0.0.1:5432 就绪"):
        setup._assert_native_postgres_ready(environment=environment, port=5432)

    assert [command for command, _ in calls] == [
        [
            "pg_isready",
            "--host",
            "127.0.0.1",
            "--port",
            "5432",
            "--dbname",
            "postgres",
        ]
    ]
    assert calls[0][1]["env"] == environment


def test_native_postgres_ready_validates_env_credentials_on_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = setup._safe_development_environment("local-password", 5432)
    calls: list[tuple[list[str], dict[str, object]]] = []
    responses = iter(
        (
            subprocess.CompletedProcess(["pg_isready"], 0, "accepting connections\n", ""),
            subprocess.CompletedProcess(["psql"], 0, "1\n", ""),
        )
    )

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        response = next(responses)
        return subprocess.CompletedProcess(command, response.returncode, response.stdout, response.stderr)

    monkeypatch.setattr(setup.subprocess, "run", fake_run)

    setup._assert_native_postgres_ready(environment=environment, port=5432)

    assert [command[0] for command, _ in calls] == ["pg_isready", "psql"]
    psql_command, psql_kwargs = calls[1]
    assert psql_command[:7] == [
        "psql",
        "--host",
        "127.0.0.1",
        "--port",
        "5432",
        "--username",
        "northstar",
    ]
    assert psql_kwargs["env"] == environment
    assert all(command[0] != "docker" for command, _ in calls)


def test_prepare_native_postgres_only_checks_the_two_local_databases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = setup._safe_development_environment("local-password", 5432)
    readiness: list[tuple[dict[str, str], int]] = []
    databases: list[tuple[str, dict[str, str], int]] = []
    monkeypatch.setattr(
        setup,
        "_assert_native_postgres_ready",
        lambda *, environment, port: readiness.append((dict(environment), port)),
    )
    monkeypatch.setattr(
        setup,
        "_ensure_postgresql_database",
        lambda name, *, environment, port: databases.append((name, dict(environment), port)),
    )

    setup._prepare_native_postgres(environment=environment, port=5432)

    assert readiness == [(environment, 5432)]
    assert databases == [
        ("northstar", environment, 5432),
        ("northstar_test", environment, 5432),
    ]


def test_database_creation_refuses_any_target_outside_the_two_local_databases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = setup._safe_development_environment("local-password", 5432)
    monkeypatch.setattr(
        setup.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("未授权数据库不得查询或创建"),
    )

    with pytest.raises(RuntimeError, match="只允许"):
        setup._ensure_postgresql_database("postgres", environment=environment, port=5432)


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

    setup._ensure_postgresql_database("northstar_test", environment=environment, port=5432)


@pytest.mark.parametrize("password", ("", "CHANGE_ME"))
def test_native_postgres_setup_requires_an_existing_env_role_password(
    password: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"POSTGRES_PASSWORD={password}\nPOSTGRES_PORT=5432\n",  # secret-scan: allow; reason: disposable test fixture
        encoding="utf-8",
    )
    monkeypatch.setattr(setup, "ENV_FILE", env_file)
    monkeypatch.setattr(
        setup,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("凭据缺失时不得改写 .env 或调用 PostgreSQL"),
    )

    with pytest.raises(RuntimeError, match="必须先在 .env 中设置"):
        setup._set_safe_development_values()


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
                f"POSTGRES_PASSWORD={password}",  # secret-scan: allow; reason: disposable test fixture
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
