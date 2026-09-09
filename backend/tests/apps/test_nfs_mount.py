"""Prevent a local directory or a wrong/read-write share from passing deployment admission."""

import pytest

from northstar_quant.data_management.storage_identity import verify_mount


def test_nfs_guard_rejects_wrong_mounts_and_research_write_access():
    expected = {
        "mount": "/mnt/northstar",
        "server": "192.0.2.8",
        "export": "/verified-export",
        "version": "4",
        "mode": "ro",
    }
    actual = {
        "target": "/mnt/northstar",
        "source": "192.0.2.8:/verified-export",
        "fstype": "nfs4",
        "options": "ro,hard,vers=4.1,proto=tcp",
    }
    verify_mount(expected, actual)
    for changed in (
        {},
        {**actual, "fstype": "ext4"},
        {**actual, "target": "/mnt"},
        {**actual, "source": "192.0.2.8:/other"},
        {**actual, "options": "rw,hard,vers=4.1"},
        {**actual, "options": "ro,soft,vers=4.1"},
        {**actual, "options": "ro,hard,vers=3"},
    ):
        with pytest.raises(ValueError):
            verify_mount(expected, changed)
