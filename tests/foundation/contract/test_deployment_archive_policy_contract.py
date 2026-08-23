from __future__ import annotations

from tests.helpers.paths import PROJECT_ROOT


DEPLOY_DIR = PROJECT_ROOT / "scripts" / "deploy"


def test_root_release_extraction_is_preceded_by_a_bounded_archive_policy() -> None:
    """Root never begins extraction until the complete artifact policy passes."""

    release_script = (DEPLOY_DIR / "install-release.sh").read_text(encoding="utf-8")
    policy = (DEPLOY_DIR / "archive_policy.py").read_text(encoding="utf-8")

    policy_invocation = release_script.index(
        '/usr/bin/python3 -I "${SCRIPT_DIR}/archive_policy.py"'
    )
    policy_argument = release_script.index(
        '--validate-deployment-artifact "${ARTIFACT_TARBALL}"'
    )
    policy_call = release_script.rindex("\nvalidate_artifact_extraction_policy\n")
    legacy_path_listing = release_script.rindex("\nvalidate_artifact\n")
    legacy_content_listing = release_script.rindex("\nvalidate_artifact_contents\n")
    extraction = release_script.index('tar --extract --gzip --file="${ARTIFACT_TARBALL}"')

    assert policy_invocation < policy_argument < policy_call < legacy_path_listing
    assert legacy_path_listing < legacy_content_listing < extraction
    assert "env -i" in release_script[policy_invocation - 160 : policy_invocation]
    assert "MAX_DEPLOYMENT_ARTIFACT_MEMBERS" in policy
    assert "MAX_DEPLOYMENT_ARTIFACT_MEMBER_BYTES" in policy
    assert "MAX_DEPLOYMENT_ARTIFACT_UNPACKED_BYTES" in policy
    assert "member.issparse()" in policy
    assert "member.issym() or member.islnk()" in policy
    assert "not member.isreg() and not member.isdir()" in policy
    assert 'tarfile.open(archive_path, mode="r:gz")' in policy
