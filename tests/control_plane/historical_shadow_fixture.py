"""Immutable 1.x producers for cross-version recovery, never current writes.

Requires the public Git object below (CI checks out history). Only synthetic
originals use this source; inspection, disposition and later writes use HEAD.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from tests.control_plane.shadow_e2e_fixture import ShadowWorkspace, workspace

from loopx.control_plane.testing.authority_e2e_fixtures import (
    original_1x_source as extract_original_source,
)


@pytest.fixture(name="original_source", scope="session")
def original_1x_source(tmp_path_factory):
    return extract_original_source(tmp_path_factory.mktemp("loopx-original-1x"))


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
