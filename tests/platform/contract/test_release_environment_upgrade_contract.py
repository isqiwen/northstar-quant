from __future__ import annotations

from tests.helpers.paths import PROJECT_ROOT


def test_candidate_upgrade_validates_the_existing_release_environment_chain_first() -> None:
    """A new candidate cannot bypass the rollback configuration precondition."""

    release_script = (
        PROJECT_ROOT / "scripts" / "deploy" / "install-release.sh"
    ).read_text(encoding="utf-8")

    active_validation = release_script.index('ACTIVE_ENV_SNAPSHOT=""')
    candidate_selection = release_script.index('if [ -z "${CANDIDATE_ENV_FILE}" ]; then')
    candidate_normalization = release_script.index(
        'CANDIDATE_ENV_FILE="$(realpath -m -- "${CANDIDATE_ENV_FILE}")"'
    )
    stage_creation = release_script.index('STAGE_DIR="$(deploy_as_root mktemp -d')

    assert active_validation < candidate_selection < candidate_normalization < stage_creation
    assert "deploy_resolve_managed_active_environment_snapshot" in release_script[
        active_validation:candidate_selection
    ]
    assert 'PREVIOUS_RELEASE="$(deploy_as_root readlink -- "${CURRENT_LINK}")"' in release_script[
        active_validation:candidate_selection
    ]
    assert "only when both public pointers are absent" in release_script
    assert 'PREVIOUS_RELEASE="$(readlink -f "${CURRENT_LINK}"' not in release_script
