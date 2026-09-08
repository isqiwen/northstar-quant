"""Installed provenance must survive Git removal and never hide uncommitted code."""

import os
import runpy
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path


def test_clean_and_dirty_artifacts_embed_honest_git_provenance(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    repository = tmp_path / "checkout"
    checkout = repository / "backend"
    checkout.mkdir(parents=True)
    shutil.copyfile(root.parent / ".gitignore", repository / ".gitignore")
    for name in (
        "pyproject.toml",
        "README.md",
        "scripts/build_identity.py",
        "src/northstar_quant/__init__.py",
    ):
        target = checkout / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)

    def git(*arguments: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(repository), *arguments], text=True
        ).strip()

    git("init", "-q")
    git("add", ".")
    git(
        "-c",
        "user.name=Build acceptance",
        "-c",
        "user.email=build@example.invalid",
        "commit",
        "-qm",
        "Build fixture",
    )
    commit = git("rev-parse", "HEAD")
    env = dict(os.environ)
    env.pop("NORTHSTAR_DEVELOPMENT_BUILD", None)
    env.pop("NORTHSTAR_GIT_REVISION", None)
    uv = shutil.which("uv")
    assert uv is not None

    def build(source: Path, out: str, target: str = "--wheel") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [uv, "build", "--offline", target, str(source), "--out-dir", str(tmp_path / out)],
            env=env,
            text=True,
            capture_output=True,
        )

    built = build(checkout, "clean")
    assert built.returncode == 0, built.stderr
    installed = tmp_path / "installed"
    with zipfile.ZipFile(next((tmp_path / "clean").glob("*.whl"))) as wheel:
        wheel.extractall(installed)
    # There is no .git in the installed package or its parents.
    identity = runpy.run_path(str(installed / "northstar_quant/__init__.py"))
    assert identity["code_revision"]() == commit
    built = build(checkout, "sdist", "--sdist")
    assert built.returncode == 0, built.stderr
    with tarfile.open(next((tmp_path / "sdist").glob("*.tar.gz"))) as archive:
        archive.extractall(tmp_path / "unpacked", filter="data")
    built = build(next((tmp_path / "unpacked").iterdir()), "from-sdist")
    assert built.returncode == 0, built.stderr
    with zipfile.ZipFile(next((tmp_path / "from-sdist").glob("*.whl"))) as wheel:
        assert wheel.read("northstar_quant/_git_revision.txt").decode().strip() == commit
    (checkout / "README.md").write_text("Uncommitted change\n")
    refused = build(checkout, "refused")
    assert refused.returncode != 0 and "clean Git worktree" in refused.stderr
    env["NORTHSTAR_DEVELOPMENT_BUILD"] = "1"
    built = build(checkout, "development")
    assert built.returncode == 0, built.stderr
    with zipfile.ZipFile(next((tmp_path / "development").glob("*.whl"))) as wheel:
        assert wheel.read("northstar_quant/_git_revision.txt").decode().strip() == commit + "-dirty"
