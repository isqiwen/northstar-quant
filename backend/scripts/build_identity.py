"""Embed Git provenance in wheel/sdist; reject uncommitted formal builds."""

import os
import runpy
import tempfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class GitBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if version == "editable":
            return
        root = Path(self.root)
        package = root / "src" / "northstar_quant"
        provenance = runpy.run_path(str(package / "__init__.py"))
        repository = root.parent
        if root.name == "backend" and (repository / ".git").exists():
            revision = provenance["_checkout_revision"](repository)
        elif (package / "_git_revision.txt").is_file():
            revision = (package / "_git_revision.txt").read_text().strip()
        else:
            # Container builders supply the revision verified on the host.
            revision = os.environ.get("NORTHSTAR_GIT_REVISION", "")
        revision = provenance["validate_code_revision"](revision)
        if revision.endswith("-dirty") and os.environ.get("NORTHSTAR_DEVELOPMENT_BUILD") != "1":
            raise ValueError(
                "formal builds require a clean Git worktree; commit changes first, or set "
                "NORTHSTAR_DEVELOPMENT_BUILD=1 for an explicitly dirty development artifact"
            )
        self._temporary = tempfile.TemporaryDirectory(prefix="northstar-git-")
        stamp = Path(self._temporary.name) / "_git_revision.txt"
        stamp.write_text(revision + "\n")
        destination = "northstar_quant/_git_revision.txt"
        if self.target_name == "sdist":
            destination = "src/" + destination
        build_data["force_include"][str(stamp)] = destination

    def finalize(self, version, build_data, artifact_path):
        if version != "editable":
            self._temporary.cleanup()
