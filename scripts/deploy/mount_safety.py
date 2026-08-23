"""Fail-closed mount-subtree checks for privileged deployment traversal.

``rm --one-file-system`` and ``find -xdev`` do not protect against a bind
mount on the same device.  Before a root deployment script recursively seals
or removes a release tree, this helper inspects the kernel mount table and
rejects a mount at the tree root or anywhere below it.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import sys
from typing import Iterable


_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")


class MountSafetyError(ValueError):
    """Raised when a privileged recursive traversal cannot be proven safe."""


def _decode_mountinfo_path(value: str) -> str:
    """Decode Linux mountinfo's octal-escaped mountpoint field."""

    return _OCTAL_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)


def iter_mountpoints(mountinfo: Iterable[str]) -> Iterable[Path]:
    """Yield normalized mount points from Linux ``/proc/*/mountinfo`` lines."""

    for line in mountinfo:
        fields = line.rstrip("\n").split()
        if len(fields) < 7:
            raise MountSafetyError("mountinfo contains an incomplete record")
        yield Path(os.path.normpath(_decode_mountinfo_path(fields[4])))


def assert_tree_has_no_mounts(
    tree: Path,
    *,
    mountinfo: Iterable[str] | None = None,
) -> None:
    """Reject a non-directory, a symlink, or a mount at/below ``tree``."""

    try:
        metadata = tree.lstat()
    except OSError as exc:
        raise MountSafetyError(f"cannot inspect deployment tree {tree}: {exc}") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise MountSafetyError(f"deployment tree must be a non-symlink directory: {tree}")

    canonical_tree = Path(os.path.normpath(os.path.realpath(tree)))
    source = mountinfo
    if source is None:
        try:
            source = Path("/proc/self/mountinfo").open(encoding="utf-8")
        except OSError as exc:
            raise MountSafetyError(f"cannot read Linux mount table: {exc}") from exc

    try:
        for mountpoint in iter_mountpoints(source):
            try:
                mountpoint.relative_to(canonical_tree)
            except ValueError:
                continue
            raise MountSafetyError(
                f"deployment tree contains a mount point and cannot be traversed: {mountpoint}"
            )
    finally:
        close = getattr(source, "close", None)
        if callable(close):
            close()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tree", type=Path, help="root-owned tree to inspect")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        assert_tree_has_no_mounts(args.tree)
    except MountSafetyError as exc:
        print(f"mount safety error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by deployment scripts.
    raise SystemExit(main())
