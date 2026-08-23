from pathlib import Path

import pytest

from scripts.deploy.inventory import InventoryError, load_inventory


def _load(tmp_path: Path, deploy_host: str, *, service_user: str = "northstar"):
    inventory_file = tmp_path / "deploy.env"
    inventory_file.write_text(
        f"DEPLOY_HOST={deploy_host}\nSERVICE_USER={service_user}\n",
        encoding="utf-8",
    )
    return load_inventory(inventory_file)


def _load_runtime_inventory(tmp_path: Path, **overrides: str):
    values = {"DEPLOY_HOST": "deployer@host.example.test"}
    values.update(overrides)
    inventory_file = tmp_path / "deploy.env"
    inventory_file.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )
    return load_inventory(inventory_file)


@pytest.mark.parametrize(
    ("deploy_host", "expected"),
    (
        ("prod-cn-1", "prod-cn-1"),
        ("deployer@prod-cn-1", "deployer@prod-cn-1"),
        ("deployer@prod.example.test", "deployer@prod.example.test"),
        ("deployer@192.0.2.10", "deployer@192.0.2.10"),
        ("deployer@[2001:0db8::10]", "deployer@[2001:db8::10]"),
    ),
)
def test_inventory_accepts_only_explicit_safe_ssh_target_forms(
    tmp_path: Path,
    deploy_host: str,
    expected: str,
) -> None:
    inventory = _load(tmp_path, deploy_host)

    assert inventory.deploy_host == expected
    assert inventory.ssh_target.authority == expected


@pytest.mark.parametrize(
    "deploy_host",
    (
        "",
        "root@host.example.test",
        "northstar@host.example.test",
        "host:22",
        "deployer@host:22",
        "deployer@host@other",
        "deployer@",
        "@host",
        "deployer@[host]",
        "deployer@[]",
        "deployer@[2001:db8::1",
        "deployer@2001:db8::1",
        "host..example.test",
        "-host",
        ".host",
        "host.",
        "deployer@999.999.999.999",
        "deployer@host;ProxyCommand=unexpected",
    ),
)
def test_inventory_rejects_ambiguous_or_privileged_ssh_target_forms(
    tmp_path: Path,
    deploy_host: str,
) -> None:
    with pytest.raises(InventoryError):
        _load(tmp_path, deploy_host)


def test_inventory_rejects_root_service_identity(tmp_path: Path) -> None:
    with pytest.raises(InventoryError, match="Linux"):
        _load(tmp_path, "deployer@host.example.test", service_user="root")


def test_inventory_uses_the_fixed_linux_production_layout(tmp_path: Path) -> None:
    inventory = _load(tmp_path, "deployer@host.example.test")

    assert inventory.app_root == "/opt/northstar"
    assert inventory.config_dir == "/etc/northstar"
    assert inventory.state_dir == "/var/lib/northstar"
    assert inventory.service_home == "/var/lib/northstar"
    assert inventory.cache_dir == "/var/cache/northstar"
    assert inventory.log_dir == "/var/log/northstar"
    assert inventory.environment_file == "/etc/northstar/northstar-quant.env"


def test_inventory_accepts_direct_runtime_leaves_under_dedicated_external_root(
    tmp_path: Path,
) -> None:
    inventory = _load_runtime_inventory(
        tmp_path,
        RUNTIME_STORAGE_DIR="/mnt/northstar-quant/storage",
        RUNTIME_DOWNLOADS_DIR="/mnt/northstar-quant/downloads",
        RUNTIME_REPORTS_DIR="/data/northstar-quant/reports",
        RUNTIME_LOG_DIR="/mnt/northstar-quant/logs",
        RUNTIME_CACHE_DIR="/mnt/northstar-quant/cache",
        RUNTIME_MATPLOTLIB_DIR="/mnt/northstar-quant/matplotlib",
    )

    assert inventory.values["RUNTIME_STORAGE_DIR"] == "/mnt/northstar-quant/storage"
    assert inventory.values["RUNTIME_DOWNLOADS_DIR"] == "/mnt/northstar-quant/downloads"


@pytest.mark.parametrize(
    ("key", "value"),
    (
        ("RUNTIME_STORAGE_DIR", "/var/lib/northstar/storage/downloads"),
        ("RUNTIME_DOWNLOADS_DIR", "/mnt/northstar-quant/storage/downloads"),
        ("RUNTIME_REPORTS_DIR", "/data/northstar-quant/reports/weekly"),
        ("RUNTIME_LOG_DIR", "/var/log/northstar/app/archive"),
        ("RUNTIME_CACHE_DIR", "/var/cache/northstar/runtime/dashboard"),
        ("RUNTIME_MATPLOTLIB_DIR", "/mnt/northstar-quant/cache/matplotlib"),
        ("RUNTIME_STORAGE_DIR", "/mnt/northstar-quant"),
        ("RUNTIME_STORAGE_DIR", "/mnt/northstar-quant//storage"),
        ("RUNTIME_STORAGE_DIR", "/mnt/northstar-quant/storage/"),
    ),
)
def test_inventory_rejects_nested_or_parent_runtime_paths(
    tmp_path: Path,
    key: str,
    value: str,
) -> None:
    with pytest.raises(InventoryError):
        _load_runtime_inventory(tmp_path, **{key: value})


@pytest.mark.parametrize(
    "overrides",
    (
        {
            "RUNTIME_STORAGE_DIR": "/mnt/northstar-quant/storage",
            "RUNTIME_DOWNLOADS_DIR": "/mnt/northstar-quant/storage",
        },
        {
            "RUNTIME_CACHE_DIR": "/data/northstar-quant/cache",
            "RUNTIME_MATPLOTLIB_DIR": "/data/northstar-quant/cache",
        },
    ),
)
def test_inventory_rejects_overlapping_runtime_leaves(
    tmp_path: Path,
    overrides: dict[str, str],
) -> None:
    with pytest.raises(InventoryError, match="重叠"):
        _load_runtime_inventory(tmp_path, **overrides)


@pytest.mark.parametrize(
    ("key", "value"),
    (
        ("RUNTIME_CACHE_DIR", "/var/cache/northstar/dashboard"),
        ("RUNTIME_STORAGE_DIR", "/var/cache/northstar/venv-build"),
        ("RUNTIME_MATPLOTLIB_DIR", "/var/cache/northstar/uv-cache"),
    ),
)
def test_inventory_reserves_system_managed_runtime_leaves(
    tmp_path: Path,
    key: str,
    value: str,
) -> None:
    with pytest.raises(InventoryError, match="受系统管理"):
        _load_runtime_inventory(tmp_path, **{key: value})


@pytest.mark.parametrize(
    "entry",
    (
        "APP_NAME=other-service",
        "SERVICE_USER=other-service",
        "SYSTEMD_SERVICE_NAME=ssh",
        "SERVICE_HOME=/srv/northstar",
    ),
)
def test_inventory_rejects_legacy_or_unrelated_linux_service_identity(
    tmp_path: Path,
    entry: str,
) -> None:
    inventory_file = tmp_path / "deploy.env"
    inventory_file.write_text(
        f"DEPLOY_HOST=deployer@host.example.test\n{entry}\n",
        encoding="utf-8",
    )

    with pytest.raises(InventoryError):
        load_inventory(inventory_file)


def _load_ntfy_inventory(tmp_path: Path, **overrides: str):
    values = {
        "DEPLOY_HOST": "deployer@host.example.test",
        "NTFY_DEPLOY_ENABLED": "1",
        "NTFY_PUBLIC_HOST": "ntfy.example.test",
        "NTFY_ACME_EMAIL": "ops@example.test",
    }
    values.update(overrides)
    inventory_file = tmp_path / "deploy.env"
    inventory_file.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )
    return load_inventory(inventory_file)


@pytest.mark.parametrize(
    "overrides",
    (
        {"NTFY_CONFIG_DIR": "/etc/northstar"},
        {"NTFY_CONFIG_DIR": "/etc"},
        {"NTFY_DATA_DIR": "/var/lib/northstar"},
        {"NTFY_DATA_DIR": "/var/lib/northstar/storage"},
        {
            "RUNTIME_STORAGE_DIR": "/mnt/northstar-quant/storage",
            "NTFY_DATA_DIR": "/mnt/northstar-quant/storage/ntfy",
        },
    ),
)
def test_inventory_rejects_ntfy_paths_overlapping_northstar_boundaries(
    tmp_path: Path,
    overrides: dict[str, str],
) -> None:
    # NTFY data roots outside /var/lib are now rejected before an overlap
    # check; both paths are fail-closed production-boundary outcomes.
    with pytest.raises(InventoryError, match="受保护|/var/lib"):
        _load_ntfy_inventory(tmp_path, **overrides)


def test_inventory_permits_separate_ntfy_paths(tmp_path: Path) -> None:
    inventory = _load_ntfy_inventory(
        tmp_path,
        NTFY_CONFIG_DIR="/etc/northstar-ntfy",
        NTFY_DATA_DIR="/var/lib/northstar-ntfy",
    )

    assert inventory.values["NTFY_CONFIG_DIR"] == "/etc/northstar-ntfy"
    assert inventory.values["NTFY_DATA_DIR"] == "/var/lib/northstar-ntfy"


@pytest.mark.parametrize(
    "overrides",
    (
        {"NTFY_CONFIG_DIR": "/etc/ssh"},
        {"NTFY_DATA_DIR": "/var/lib/postgresql"},
        {"NTFY_CONFIG_DIR": "/etc/northstar-ntfy-other"},
        {"NTFY_DATA_DIR": "/srv/northstar-ntfy"},
    ),
)
def test_inventory_requires_fixed_dedicated_ntfy_directories(
    tmp_path: Path,
    overrides: dict[str, str],
) -> None:
    # The fixed FHS prefix is checked before exact-directory equality.
    with pytest.raises(InventoryError, match="固定|/var/lib"):
        _load_ntfy_inventory(tmp_path, **overrides)
