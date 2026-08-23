"""Real PostgreSQL-client restore-drill coverage."""

from __future__ import annotations

import os
from pathlib import Path
import shutil

import pytest
from sqlalchemy import create_engine, text

from northstar_quant.platform.backup.restore_drill import run_test_postgresql_restore_drill
from tests.helpers.postgresql import postgresql_test_url


@pytest.mark.integration
@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows workstation CI does not run the PostgreSQL CLI restore drill",
)
def test_real_pg_dump_and_pg_restore_drill_rolls_back_to_preserved_source(
    tmp_path: Path,
) -> None:
    search_path = (
        None
        if os.name == "nt"
        else "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    )
    missing = [
        name
        for name in ("pg_dump", "pg_restore", "psql")
        if shutil.which(name, path=search_path) is None
    ]
    assert not missing, "Linux CI must provide restore-drill clients: " + ", ".join(missing)

    database_url = postgresql_test_url(tmp_path)
    workspace = tmp_path / "restore-drills"
    workspace.mkdir(mode=0o700)

    result = run_test_postgresql_restore_drill(database_url, workspace_dir=workspace)

    assert result.archive_path.is_file()
    assert len(result.archive_sha256) == 64
    engine = create_engine(database_url, future=True)
    try:
        with engine.connect() as connection:
            count = connection.scalar(
                text(f'SELECT count(*) FROM "{result.schema_name}".sentinel')
            )
            marker = connection.scalar(
                text(
                    f'SELECT marker FROM "{result.schema_name}".sentinel '
                    "WHERE id = 1"
                )
            )
        assert count == 1
        assert marker == "northstar"
    finally:
        engine.dispose()
