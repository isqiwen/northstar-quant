"""Linux x86_64 runtime host-policy tests."""

from __future__ import annotations

import pytest

from northstar_quant.foundation.platform_support import (
    PlatformSupportError,
    canonical_machine,
    require_linux_x86_64,
)


def test_linux_x86_64_host_policy_accepts_the_supported_machine_aliases() -> None:
    require_linux_x86_64(system_name="Linux", machine="x86_64")
    require_linux_x86_64(system_name="Linux", machine="AMD64")
    assert canonical_machine("X86-64") == "x86_64"


@pytest.mark.parametrize(
    ("system_name", "machine"),
    (("Windows", "AMD64"), ("Darwin", "arm64"), ("Linux", "aarch64")),
)
def test_linux_x86_64_host_policy_fails_closed_for_every_other_host(
    system_name: str,
    machine: str,
) -> None:
    with pytest.raises(PlatformSupportError, match="仅支持 Linux x86_64"):
        require_linux_x86_64(system_name=system_name, machine=machine)
