"""Private credential writing preserves literal values and survives an interrupted replacement."""

import os
import runpy
import stat
from pathlib import Path

import pytest

from northstar_quant.broker.settings import Credentials, load_credentials

save = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "scripts/operations/setup_simnow.py")
)["save"]


def test_private_credentials_are_literal_atomic_and_loadable(tmp_path, monkeypatch):
    path = tmp_path / "private/simnow.env"
    first = Credentials(
        user_id="123456", password="$(not-executed)`x`=", app_id="test", auth_code="0"
    )
    save(path, first)
    assert load_credentials(path) == first
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    second = Credentials(user_id="654321", password="replacement", app_id="test", auth_code="0")
    replace = os.replace

    def interrupted(*args, **kwargs):
        raise OSError("synthetic interrupted replacement")

    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(OSError):
        save(path, second)
    assert load_credentials(path) == first
    monkeypatch.setattr(os, "replace", replace)
    save(path, second)
    assert load_credentials(path) == second


@pytest.mark.parametrize("directory", [True, False])
def test_private_write_refuses_symlink_destination(tmp_path, directory):
    original = tmp_path / "original"
    original.mkdir()
    file = original / "simnow.env"
    file.write_text("unchanged")
    alias = tmp_path / "alias"
    alias.symlink_to(original if directory else file)
    credentials = Credentials(user_id="123456", password="test", app_id="test", auth_code="0")
    with pytest.raises((OSError, ValueError)):
        save(alias / "simnow.env" if directory else alias, credentials)
    assert file.read_text() == "unchanged"
