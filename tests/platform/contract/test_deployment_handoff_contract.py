"""Contracts for crossing from deploy-user staging into root-owned secrets."""

from __future__ import annotations

import inspect

import pytest

from scripts.deploy import artifact_handoff, secure_handoff
from tests.helpers.paths import PROJECT_ROOT


DEPLOY_DIR = PROJECT_ROOT / "scripts" / "deploy"


def test_secure_handoff_accepts_only_stdin_and_fixed_candidate_kinds() -> None:
    """The root helper must not receive a mutable deploy-user source pathname."""

    helper_source = (DEPLOY_DIR / "secure_handoff.py").read_text(encoding="utf-8")
    signature = inspect.signature(secure_handoff.receive_from_standard_input)

    assert tuple(signature.parameters) == ("kind", "release_id")
    assert "source_path" not in helper_source
    assert 'parent=Path("/etc/northstar")' in helper_source
    assert 'parent=Path("/var/lib/northstar/deploy-state")' in helper_source
    assert "os.read(0, 65_536)" in helper_source
    assert "os.fsync(temporary_fd)" in helper_source
    assert "os.fsync(parent_fd)" in helper_source
    assert "os.link(" in helper_source
    assert "follow_symlinks=False" in helper_source
    assert "os.rename(" not in helper_source
    assert "subprocess" not in helper_source


@pytest.mark.parametrize("release_id", ("", "../escape", "release/escape", "release id"))
def test_secure_handoff_rejects_unsafe_release_identifier(release_id: str) -> None:
    with pytest.raises(secure_handoff.HandoffError, match="release identifier"):
        secure_handoff._candidate_name(
            kind="environment",
            release_id=release_id,
            spec=secure_handoff._HANDOFF_SPECS["environment"],
        )


def test_secure_handoff_fails_closed_without_linux_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secure_handoff.os, "geteuid", lambda: 1, raising=False)

    with pytest.raises(secure_handoff.HandoffError, match="requires Linux root"):
        secure_handoff.receive_from_standard_input(
            kind="environment",
            release_id="release-20260822",
        )


def test_artifact_handoff_accepts_only_stdin_and_publishes_a_fixed_candidate() -> None:
    """Artifact bytes must cross the privilege boundary without a source path."""

    helper_source = (DEPLOY_DIR / "artifact_handoff.py").read_text(encoding="utf-8")
    signature = inspect.signature(artifact_handoff.receive_from_standard_input)

    assert tuple(signature.parameters) == ("release_id", "expected_sha256")
    assert "source_path" not in helper_source
    assert 'Path("/var/lib/northstar/deploy-state")' in helper_source
    assert "_MAX_ARTIFACT_BYTES" in helper_source
    assert "hashlib.sha256()" in helper_source
    assert "hmac.compare_digest(actual_sha256, expected_sha256)" in helper_source
    assert "os.fsync(temporary_fd)" in helper_source
    assert "os.fsync(parent_fd)" in helper_source
    assert "os.link(" in helper_source
    assert "follow_symlinks=False" in helper_source
    assert "os.rename(" not in helper_source
    assert "subprocess" not in helper_source


def test_provision_streams_the_artifact_before_any_root_installer_can_read_it() -> None:
    provision = (DEPLOY_DIR / "provision.sh").read_text(encoding="utf-8")
    release_script = (DEPLOY_DIR / "install-release.sh").read_text(encoding="utf-8")

    assert 'UPLOADED_ARTIFACT_TARBALL="${UPLOADED_ARTIFACT_TARBALL:-}"' in provision
    assert 'EXPECTED_ARTIFACT_PATH="${UPLOAD_DIRECTORY}/${APP_NAME}-${RELEASE_ID}.tar.gz"' in provision
    assert "handoff_unprivileged_artifact" in provision
    assert '/usr/bin/python3 -I "${SCRIPT_DIR}/artifact_handoff.py" \\' in provision
    assert '"${RELEASE_ID}" "${ARTIFACT_SHA256}"' in provision
    assert 'CANDIDATE_ARTIFACT_FILE="${DEPLOY_STATE_DIR}/.artifact.${RELEASE_ID}.candidate.tar.gz"' in provision
    assert 'ARTIFACT_TARBALL="${CANDIDATE_ARTIFACT_FILE}"' in provision
    assert 'ARTIFACT_TARBALL="${UPLOADED_ARTIFACT_TARBALL}"' not in provision
    assert "cleanup_managed_artifact_candidate()" in provision
    assert "trap cleanup_known_failed_handoffs" not in provision

    assert 'ARTIFACT_TARBALL="${ARTIFACT_TARBALL:-${1:-}}"' not in release_script
    assert "assert_managed_artifact_candidate()" in release_script
    assert 'expected_candidate="${DEPLOY_STATE_DIR}/.artifact.${RELEASE_ID}.candidate.tar.gz"' in (
        release_script
    )
    assert '[ "${parent_metadata}" != "0:0:700" ]' in release_script
    assert '[ "${candidate_metadata}" = "0:0:600:1" ]' in release_script
    managed_candidate_check = release_script.index("if ! assert_managed_artifact_candidate; then")
    sha_validation = release_script.index(
        'if [[ ! "${ARTIFACT_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then'
    )
    actual_hash = release_script.index(
        'actual_artifact_sha256="$(sha256sum "${ARTIFACT_TARBALL}"'
    )
    render_snapshot = release_script.index("render_systemd_snapshot() {")
    sha_substitution = release_script.index(
        '-e "s|@ARTIFACT_SHA256@|${ARTIFACT_SHA256}|g"'
    )
    assert managed_candidate_check < sha_validation < actual_hash
    assert sha_validation < render_snapshot
    assert sha_validation < sha_substitution


def test_provision_streams_uploads_before_any_root_consumer() -> None:
    """Both application and ntfy credentials must enter root through stdin."""

    provision = (DEPLOY_DIR / "provision.sh").read_text(encoding="utf-8")

    assert 'if cat <&"${uploaded_fd}" |' in provision
    assert '/usr/bin/python3 -I "${SCRIPT_DIR}/secure_handoff.py" "${handoff_kind}" "${RELEASE_ID}"' in (
        provision
    )
    assert 'APP_ENV_FILE="${ENV_FILE_PATH' not in provision
    assert 'NTFY_BOOTSTRAP_PATH="${NTFY_BOOTSTRAP_PATH}"' not in provision
    assert 'NTFY_BOOTSTRAP_PATH="${CANDIDATE_NTFY_BOOTSTRAP_FILE}"' in provision
    assert 'install -m 0640 -o root -g "${SERVICE_USER}" \\\n    "${ENV_FILE_PATH}"' not in (
        provision
    )

    environment_handoff = provision.index('handoff_unprivileged_upload \\\n    "environment"')
    ntfy_root_consumer = provision.index('if [ "${NTFY_DEPLOY_ENABLED}" = "1" ];')
    assert environment_handoff < ntfy_root_consumer
    assert 'validate_managed_production_environment "${CANDIDATE_ENV_FILE}"' in provision


def test_known_failure_cleanup_does_not_delete_candidates_after_unknown_interruption() -> None:
    provision = (DEPLOY_DIR / "provision.sh").read_text(encoding="utf-8")

    assert "cleanup_known_failed_handoffs()" in provision
    assert "cleanup_known_failed_handoffs_or_retain_lock()" in provision
    assert "local cleanup_failed=0" in provision
    assert "cleanup_failed=1" in provision
    assert "return \"${cleanup_failed}\"" in provision
    assert "trap cleanup_known_failed_handoffs" not in provision
    assert '[ "${exit_status}" -gt 0 ] && [ "${exit_status}" -lt 128 ]' in provision
    assert "if ! cleanup_known_failed_handoffs; then\n      # A failed root-side candidate cleanup" in provision
    assert "retain_deployment_lock\n    deploy_log \"警告：候选文件清理失败" in provision
    assert 'if [ "${ntfy_status}" -lt 128 ]; then' in provision
    assert 'if [ "${release_status}" -lt 128 ]; then' in provision
    assert 'if [ "${handoff_status}" -ge 128 ]; then' in provision
    assert "rm -rf" not in provision


def test_provision_lock_uses_a_fixed_root_owned_atomic_directory() -> None:
    """The mutex is created below root-only deploy state, never /tmp staging."""

    provision = (DEPLOY_DIR / "provision.sh").read_text(encoding="utf-8")
    deploy_control = (DEPLOY_DIR / "deploy.py").read_text(encoding="utf-8")

    assert 'DEPLOY_LOCK_PATH="${DEPLOY_STATE_DIR}/deployment.lock"' in provision
    assert 'DEPLOY_LOCK_PATH="${DEPLOY_LOCK_PATH:-}"' not in provision
    assert 'expected_lock_path="${REMOTE_TMP}/.${APP_NAME}.deployment-lock"' not in provision
    assert "assert_deployment_lock_parent()" in provision
    assert 'deploy_assert_root_controlled_directory_chain "${DEPLOY_STATE_DIR}"' in provision
    assert 'deploy_as_root test -d "${DEPLOY_STATE_DIR}"' in provision
    assert 'deploy_as_root test -L "${DEPLOY_STATE_DIR}"' in provision
    assert "deploy_as_root stat -c '%u:%g:%a' -- \"${DEPLOY_STATE_DIR}\"" in provision
    assert '[ "${parent_metadata}" != "0:0:700" ]' in provision
    assert 'if ! deploy_as_root mkdir -m 0700 -- "${DEPLOY_LOCK_PATH}" 2>/dev/null; then' in (
        provision
    )
    assert "Any pre-existing final object is" in provision
    assert "deploy_as_root stat -c '%u:%g:%a:%d:%i' -- \"${DEPLOY_LOCK_PATH}\"" in provision
    assert '"0:0:700:"*:*)' in provision
    assert "release_deployment_lock()" in provision
    assert 'trap release_deployment_lock EXIT' in provision
    assert "retain_deployment_lock_on_signal" in provision
    assert '[ "${exit_status}" -lt 128' in provision
    assert '[ "${DEPLOY_LOCK_RELEASE_ALLOWED}" = "1" ]' in provision
    assert '[ "${DEPLOY_LOCK_RELEASE_ID}" = "${RELEASE_ID}" ]' in provision
    assert '[ "${current_metadata}" = "${DEPLOY_LOCK_METADATA}" ]' in provision
    assert 'deploy_as_root rmdir -- "${DEPLOY_LOCK_PATH}" || true' in provision
    assert "flock" not in provision
    assert "exec {deployment_lock_fd}" not in provision
    assert "DEPLOY_LOCK_PATH" not in deploy_control
    assert "_deployment_lock_path" not in deploy_control

    runtime_setup = provision.index('if [ "${SETUP_SERVER}" = "1" ]; then')
    identity_check = provision.index("if ! deploy_assert_canonical_service_identity; then")
    lock_acquisition = provision.index("acquire_deployment_lock\ntrap release_deployment_lock EXIT")
    artifact_handoff = provision.index("handoff_unprivileged_artifact", lock_acquisition)
    environment_handoff = provision.index("handoff_unprivileged_upload", artifact_handoff)
    release_install = provision.index('deploy_log "安装应用版本"')
    assert runtime_setup < identity_check < lock_acquisition
    assert lock_acquisition < artifact_handoff < environment_handoff < release_install
    assert "two callers can reach install-runtime" in provision
    assert "receives no uploaded artifact, secret" in provision


def test_ntfy_bootstrap_consumes_only_a_verified_managed_candidate() -> None:
    """The privileged ntfy parser never reopens deploy-user staging paths."""

    provision = (DEPLOY_DIR / "provision.sh").read_text(encoding="utf-8")
    ntfy_provision = (DEPLOY_DIR / "ntfy" / "provision-ntfy.sh").read_text(
        encoding="utf-8"
    )

    assert "ntfy_validate_remote_bootstrap_path" not in ntfy_provision
    assert 'DEPLOY_STATE_DIR="/var/lib/northstar/deploy-state"' in ntfy_provision
    assert "ntfy_assert_managed_bootstrap_candidate()" in ntfy_provision
    assert (
        'expected_candidate="${DEPLOY_STATE_DIR}/.ntfy-bootstrap.${RELEASE_ID}.candidate.env"'
        in ntfy_provision
    )
    assert "stat -c '%u:%g:%a'" in ntfy_provision
    assert "%F" not in ntfy_provision
    assert 'if [ ! -d "${DEPLOY_STATE_DIR}" ] || [ -L "${DEPLOY_STATE_DIR}" ]; then' in (
        ntfy_provision
    )
    assert (
        'if [ ! -f "${NTFY_BOOTSTRAP_PATH}" ] || [ -L "${NTFY_BOOTSTRAP_PATH}" ]; then'
        in ntfy_provision
    )
    assert 'MANAGED_BOOTSTRAP_CANDIDATE="${expected_candidate}"' in ntfy_provision
    assert 'rm -f -- "${MANAGED_BOOTSTRAP_CANDIDATE}"' in ntfy_provision
    assert 'rm -f -- "${NTFY_BOOTSTRAP_PATH}"' not in ntfy_provision
    assert 'RELEASE_ID="${RELEASE_ID}" \\' in provision
    assert 'NTFY_BOOTSTRAP_PATH="${CANDIDATE_NTFY_BOOTSTRAP_FILE}"' in provision
