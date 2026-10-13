"""Public completion/continuation journeys against disposable real providers."""
from __future__ import annotations

import runpy
from pathlib import Path

import pytest

from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_canonical_completion_journeys(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    root = Path(__file__).resolve().parents[2]
    lifecycle = runpy.run_path(str(root / "examples/control_plane/todo-lifecycle-cli-smoke.py"))
    # Select a real backend for the existing CLI scenarios, without replacing
    # any production decisions, transport responses or assertions.
    import todo_lifecycle_fixtures as fixtures
    original = fixtures.write_fixture
    def write_fixture(path):
        return original(path, provider=provider)
    monkeypatch.setattr(fixtures, "write_fixture", write_fixture)
    lifecycle["assert_peer_monitor_no_followup"].__globals__["write_fixture"] = write_fixture
    for name in (
        "assert_configured_peer_handoff",
        "assert_no_followup_cli_metadata",
        "assert_complete_links_existing_successor",
        "assert_same_title_completion_creates_fresh_successor",
        "assert_peer_monitor_no_followup",
    ):
        lifecycle[name]()
    continuation = runpy.run_path(str(root / "examples/control_plane/todo-continuation-policy-smoke.py"))
    assert continuation["main"](provider=provider) == 0
