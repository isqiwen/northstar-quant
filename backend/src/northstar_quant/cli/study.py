"""Read CLI study input; business configuration is validated by its owner."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import cast


def read(path: Path) -> tuple[Path, dict[str, object], dict[str, object], dict[str, object]]:
    if path.stat().st_size > 65536:
        raise ValueError("study settings exceed 64 KiB")
    with path.open("rb") as stream:
        document = tomllib.load(stream)
    if not {"source", "research"} <= set(document) <= {"source", "research", "archive"}:
        raise ValueError("study requires [source], [research] and optional import-only [archive]")
    source = document["source"]
    research = document["research"]
    archive = document.get("archive", {})
    if (
        not isinstance(source, dict)
        or not isinstance(research, dict)
        or (not isinstance(archive, dict))
    ):
        raise ValueError("source, research and archive must be TOML tables")
    source = dict(source)
    file_name = source.pop("file", None)
    if not isinstance(file_name, str) or not file_name.strip():
        raise ValueError("source.file must name a CSV file relative to the study")
    return (
        (path.parent / file_name).resolve(),
        cast(dict[str, object], source),
        cast(dict[str, object], research),
        cast(dict[str, object], archive),
    )
