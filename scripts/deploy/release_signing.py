"""Detached OpenSSH signatures for immutable release manifests.

The SSH deployment identity is deliberately not a release authority.  The
Linux gate verifies a detached signature against a root-admin-installed
``allowed_signers`` file before it accepts any runtime or control bytes.  The
controller uses the same OpenSSH primitive to sign canonical manifest bytes
without putting a private key, token, or signing material in the repository.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import tempfile
import re
from pathlib import Path
from typing import Final


class ReleaseSigningError(RuntimeError):
    """A manifest signature cannot be safely created or verified."""


SIGNATURE_NAMESPACE: Final = "northstar-quant-release-v1"
ENVIRONMENT_SIGNATURE_NAMESPACE: Final = "northstar-quant-release-environment-v1"
SIGNER_PRINCIPAL: Final = "northstar-release"
_MAX_SIGNATURE_BYTES: Final = 64 * 1024
_MAX_ENVIRONMENT_BYTES: Final = 1024 * 1024
_SIGNATURE_PREAMBLE: Final = b"-----BEGIN SSH SIGNATURE-----"
_RELEASE_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _ssh_keygen() -> str:
    executable = shutil.which("ssh-keygen")
    if executable is None:
        raise ReleaseSigningError("OpenSSH ssh-keygen is required for release signing")
    return executable


def _require_regular_private_key(path: Path) -> Path:
    resolved = path.resolve()
    try:
        metadata = resolved.stat()
    except OSError as exc:
        raise ReleaseSigningError("release signing key is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ReleaseSigningError("release signing key must be a regular non-symlink file")
    return resolved


def _write_private_file(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def sign_manifest(*, manifest_bytes: bytes, signing_key: Path) -> bytes:
    """Sign exact canonical manifest bytes with an operator-provided key."""

    if not manifest_bytes:
        raise ReleaseSigningError("release manifest cannot be empty")
    return _sign_payload(
        payload=manifest_bytes,
        signing_key=signing_key,
        namespace=SIGNATURE_NAMESPACE,
        label="release manifest",
    )


def environment_signature_payload(*, release_id: str, environment: bytes) -> bytes:
    """Bind a private environment file to one signed release identifier."""

    if not _RELEASE_ID_PATTERN.fullmatch(release_id):
        raise ReleaseSigningError("release environment signature has an invalid release identifier")
    if not environment or len(environment) > _MAX_ENVIRONMENT_BYTES:
        raise ReleaseSigningError("release environment payload size is invalid")
    return b"northstar-release-environment-v1\x00" + release_id.encode("ascii") + b"\x00" + environment


def sign_environment(*, release_id: str, environment_path: Path, signing_key: Path) -> bytes:
    """Sign an opaque environment file without publishing any secret digest."""

    try:
        metadata = environment_path.stat()
    except OSError as exc:
        raise ReleaseSigningError("release environment file is unavailable") from exc
    if environment_path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ReleaseSigningError("release environment file must be a regular non-symlink file")
    if metadata.st_size <= 0 or metadata.st_size > _MAX_ENVIRONMENT_BYTES:
        raise ReleaseSigningError("release environment file size is invalid")
    try:
        environment = environment_path.read_bytes()
    except OSError as exc:
        raise ReleaseSigningError("release environment file cannot be read") from exc
    return _sign_payload(
        payload=environment_signature_payload(release_id=release_id, environment=environment),
        signing_key=signing_key,
        namespace=ENVIRONMENT_SIGNATURE_NAMESPACE,
        label="release environment",
    )


def _sign_payload(*, payload: bytes, signing_key: Path, namespace: str, label: str) -> bytes:
    """Use OpenSSH signing for one bounded byte stream."""

    key = _require_regular_private_key(signing_key)
    ssh_keygen = _ssh_keygen()
    with tempfile.TemporaryDirectory(prefix="northstar-release-sign-") as temporary:
        manifest_path = Path(temporary) / "release-manifest.json"
        _write_private_file(manifest_path, payload)
        result = subprocess.run(
            [
                ssh_keygen,
                "-Y",
                "sign",
                "-f",
                str(key),
                "-n",
                namespace,
                str(manifest_path),
            ],
            check=False,
            capture_output=True,
        )
        signature_path = manifest_path.with_suffix(f"{manifest_path.suffix}.sig")
        if result.returncode != 0 or not signature_path.is_file() or signature_path.is_symlink():
            raise ReleaseSigningError(f"OpenSSH could not sign the {label}")
        try:
            signature = signature_path.read_bytes()
        except OSError as exc:
            raise ReleaseSigningError(f"{label} signature is unavailable") from exc
    _validate_signature_bytes(signature)
    return signature


def verify_manifest_signature(
    *,
    manifest_bytes: bytes,
    signature: bytes,
    allowed_signers: Path,
) -> None:
    """Verify a detached signature using the root-owned release authority."""

    if not manifest_bytes:
        raise ReleaseSigningError("release manifest cannot be empty")
    _validate_signature_bytes(signature)
    try:
        metadata = allowed_signers.stat()
    except OSError as exc:
        raise ReleaseSigningError("trusted release signer file is unavailable") from exc
    if allowed_signers.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ReleaseSigningError("trusted release signer file must be a regular non-symlink file")
    ssh_keygen = _ssh_keygen()
    with tempfile.TemporaryDirectory(prefix="northstar-release-verify-") as temporary:
        signature_path = Path(temporary) / "release-manifest.sig"
        _write_private_file(signature_path, signature)
        result = subprocess.run(
            [
                ssh_keygen,
                "-Y",
                "verify",
                "-f",
                str(allowed_signers),
                "-I",
                SIGNER_PRINCIPAL,
                "-n",
                SIGNATURE_NAMESPACE,
                "-s",
                str(signature_path),
            ],
            input=manifest_bytes,
            check=False,
            capture_output=True,
        )
    if result.returncode != 0:
        raise ReleaseSigningError("release manifest signature is not trusted")


def _validate_signature_bytes(signature: bytes) -> None:
    if not signature or len(signature) > _MAX_SIGNATURE_BYTES:
        raise ReleaseSigningError("release manifest signature size is invalid")
    if not signature.startswith(_SIGNATURE_PREAMBLE) or b"\x00" in signature:
        raise ReleaseSigningError("release manifest signature format is invalid")
