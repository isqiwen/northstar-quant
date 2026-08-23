"""Static contracts for the out-of-band root release-gate trust anchor."""

from __future__ import annotations

from tests.helpers.paths import PROJECT_ROOT


DEPLOY_DIR = PROJECT_ROOT / "scripts" / "deploy"


def test_release_gate_bootstrap_has_fixed_root_targets_and_no_normal_deploy_integration() -> None:
    bootstrap = (DEPLOY_DIR / "release_gate_bootstrap.py").read_text(encoding="utf-8")
    deploy_control = (DEPLOY_DIR / "deploy.py").read_text(encoding="utf-8")
    provision = (DEPLOY_DIR / "provision.sh").read_text(encoding="utf-8")

    assert 'Path("/usr/local/libexec/northstar-quant")' in bootstrap
    assert 'ROOT_GATE_WRAPPER_PATH: Final = ROOT_GATE_DIRECTORY / "release-gate"' in bootstrap
    assert 'Path("/etc/northstar")' in bootstrap
    assert 'ALLOWED_SIGNERS_PATH: Final = ROOT_CONFIG_DIRECTORY / "release-allowed-signers"' in bootstrap
    assert "--confirm-root-gate-bootstrap" in bootstrap
    assert 'ROOT_CONFIRMATION: Final = "INSTALL_ROOT_RELEASE_GATE"' in bootstrap
    assert "--expected-gate-sha256" in bootstrap
    assert "--expected-allowed-signers-sha256" in bootstrap
    assert "SSH staging or a temporary directory" in bootstrap
    assert "os.link(" in bootstrap
    assert "refusing to overwrite" in bootstrap

    assert "release_gate_bootstrap" not in deploy_control
    assert "release_gate_bootstrap" not in provision


def test_release_gate_sudoers_template_grants_only_the_fixed_wrapper() -> None:
    template = (
        PROJECT_ROOT / "infra" / "systemd" / "sudoers.d" / "northstar-quant-release-gate"
    ).read_text(encoding="utf-8")

    assert "/usr/local/libexec/northstar-quant/release-gate identity" in template
    assert "/usr/local/libexec/northstar-quant/release-gate submit" in template
    assert "release-gate *" not in template
    assert "NOPASSWD: NOSETENV: NORTHSTAR_RELEASE_GATE" in template
    assert "/usr/bin/python3" not in template
    assert "/tmp" not in template
    assert "scripts/deploy" not in template
