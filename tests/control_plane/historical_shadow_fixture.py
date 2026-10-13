"""Immutable 1.x producers for cross-version recovery, never current writes.

Requires the public Git object below (CI checks out history). Only synthetic
originals use this source; inspection, disposition and later writes use HEAD.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import io
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from tests.control_plane.shadow_e2e_fixture import ShadowWorkspace, workspace

# Last pre-cutoff baseline, retained as a public compatibility input.
ORIGINAL_REVISION = "60d833b13c85135587b725013b247e827663ffb9"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(name="original_source", scope="session")
def original_1x_source(tmp_path_factory):
    root = tmp_path_factory.mktemp("loopx-original-1x")
    result = subprocess.run(["git", "archive", ORIGINAL_REVISION, "loopx"],
        cwd=ROOT, capture_output=True, check=False)
    assert result.returncode == 0, (
        f"Historical recovery requires Git object {ORIGINAL_REVISION}; "
        "fetch repository history before running this suite. " + result.stderr.decode())
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
        for member in archive:
            path = root / member.name
            assert path.resolve().is_relative_to(root)
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                assert member.isfile(), member.name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.extractfile(member).read())
    imported = subprocess.run([sys.executable, "-c", "import loopx; print(loopx.__file__)"],
        cwd=root, capture_output=True, text=True, check=True)
    assert Path(imported.stdout.strip()).resolve() == root / "loopx/__init__.py"
    return root


@dataclass
class HistoricalShadowWorkspace(ShadowWorkspace):
    original_source: Path | None = None

    def original(self):
        assert self.original_source is not None
        return replace(self, cwd=self.original_source, package=self.original_source / "loopx")

    def original_cli(self, *args, **kwargs):
        return ShadowWorkspace.cli(self.original(), *args, **kwargs)

    def original_add(self, text):
        return self.original_cli("todo", "add", "--role", "agent", "--text", text)

    def original_crash(self, window, *args):
        return ShadowWorkspace.crash(self.original(), window, *args)


@pytest.fixture(name="historical_workspace")
def historical_workspace_factory(original_source):
    def create(path, **kwargs):
        current = workspace(path, **kwargs)
        return HistoricalShadowWorkspace(**vars(current), original_source=original_source)
    return create
