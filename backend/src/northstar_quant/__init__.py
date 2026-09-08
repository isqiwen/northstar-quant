"""Northstar package version and Git provenance; no source fingerprinting."""

import re
import subprocess
from functools import lru_cache
from pathlib import Path

__version__ = "0.1.0"


def _checkout_revision(root: Path) -> str:
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], text=True, stderr=subprocess.PIPE
        ).strip()

    commit = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain", "--untracked-files=all"))
    return commit + ("-dirty" if dirty else "")


def validate_code_revision(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}(?:-dirty)?", value) is None:
        raise ValueError("code revision must be a full Git SHA, optionally followed by -dirty")
    return value


@lru_cache(maxsize=1)
def code_revision() -> str:
    """Freeze process provenance. Installed artifacts never require Git.

    Dirty identifies development work only, not its exact uncommitted contents.
    Restart source applications after editing; formal artifacts require a clean tree.
    """
    package = Path(__file__).resolve().parent
    embedded = package / "_git_revision.txt"
    if embedded.is_file():
        return validate_code_revision(embedded.read_text().strip())
    root = package.parents[2]
    if package == root / "backend" / "src" / "northstar_quant" and (root / ".git").exists():
        return validate_code_revision(_checkout_revision(root))
    raise ValueError("application is missing its build-time Git revision")
