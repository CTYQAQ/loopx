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
        "assert_open_parent_successor_advisory",
    ):
        lifecycle[name]()
    continuation = runpy.run_path(str(root / "examples/control_plane/todo-continuation-policy-smoke.py"))
    assert continuation["main"](provider=provider) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_update_response_preserves_original_facts_after_later_edit(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    root = Path(__file__).resolve().parents[2]
    runpy.run_path(str(root / "examples/control_plane/todo-lifecycle-cli-smoke.py"))
    from todo_lifecycle_fixtures import GOAL_ID, run_cli, write_fixture
    registry, _ = write_fixture(tmp_path / "fixture", provider=provider)
    added = run_cli(registry, "todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
        "--text", "Original response task", "--claimed-by", "codex-main-control")
    base = ("todo", "update", "--goal-id", GOAL_ID, "--todo-id", added["todo_id"],
        "--agent-id", "codex-main-control")
    first = (*base, "--text", "First accepted wording", "--update-operation-id", "first-update")
    preview = run_cli(registry, *first, "--dry-run")
    assert preview["status"] == "open"
    assert preview["text_changed"] is True
    assert preview["changed"] is False
    result = run_cli(registry, *first)
    assert result["todo"] == "First accepted wording"
    assert result["status"] == "open"
    assert result["text_changed"] is True
    assert result["status_changed"] is False
    assert result["metadata_updated"] is False
    noop = run_cli(registry, *base, "--text", "First accepted wording")
    assert noop["changed"] is False
    assert noop["metadata_updated"] is False
    later = run_cli(registry, *base, "--text", "Later accepted wording")
    assert later["todo"] == "Later accepted wording"
    replay = run_cli(registry, *first)
    for field in ("todo", "status", "text_changed", "status_changed", "metadata_updated", "todo_update"):
        assert replay[field] == result[field]
    current = run_cli(registry, "todo", "list", "--goal-id", GOAL_ID, "--todo-id", added["todo_id"])
    assert current["todo"]["text"] == "Later accepted wording"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_user_completion_update_projects_combined_edit(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    root = Path(__file__).resolve().parents[2]
    runpy.run_path(str(root / "examples/control_plane/todo-lifecycle-cli-smoke.py"))
    from todo_lifecycle_fixtures import GOAL_ID, run_cli, write_fixture
    registry, _ = write_fixture(tmp_path / "fixture", provider=provider)
    added = run_cli(registry, "todo", "add", "--goal-id", GOAL_ID, "--role", "user",
        "--text", "Confirm bounded change", "--task-class", "user_action", "--bound-agent", "codex-main-control")
    result = run_cli(registry, "todo", "update", "--goal-id", GOAL_ID, "--todo-id", added["todo_id"],
        "--agent-id", "codex-main-control", "--status", "done", "--text", "Confirmed bounded change")
    assert result["status"] == "done"
    assert result["todo"] == "Confirmed bounded change"
    assert result["status_changed"] is True
    assert result["text_changed"] is True
