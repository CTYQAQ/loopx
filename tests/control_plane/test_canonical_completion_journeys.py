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
    def write_fixture(path, **options):
        return original(path, provider=provider, **options)
    monkeypatch.setattr(fixtures, "write_fixture", write_fixture)
    lifecycle["assert_peer_monitor_no_followup"].__globals__["write_fixture"] = write_fixture
    assert lifecycle["main"]() == 0
    deferred = runpy.run_path(str(root / "examples/control_plane/todo-deferred-capacity-cli-smoke.py"))
    assert deferred["main"]() == 0
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


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_create_response_preserves_original_todo_after_later_edit(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    root = Path(__file__).resolve().parents[2]
    runpy.run_path(str(root / "examples/control_plane/todo-lifecycle-cli-smoke.py"))
    from todo_lifecycle_fixtures import GOAL_ID, run_cli, run_cli_error, write_fixture
    registry, _ = write_fixture(tmp_path / "fixture", provider=provider)
    intent = ("todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
        "--text", "Wait for worker capacity", "--claimed-by", "codex-main-control",
        "--status", "deferred",
        "--resume-when", "capacity_available:short_pool", "--operation-id", "original-create")
    preview = run_cli(registry, *intent, "--dry-run")
    assert preview["status"] == "deferred"
    assert preview["provider_status"] == "planned"
    assert preview["changed"] is False
    original = run_cli(registry, *intent)
    assert original["todo"] == "Wait for worker capacity"
    assert original["status"] == "deferred"
    assert original["provider_status"] == "applied"
    assert original["claimed_by"] == "codex-main-control"
    assert original["resume_when"] == "capacity_available:short_pool"
    assert original["added"] is True and original["already_exists"] is False
    run_cli(registry, "todo", "update", "--goal-id", GOAL_ID,
        "--todo-id", original["todo_id"], "--agent-id", "codex-main-control",
        "--text", "Worker capacity available", "--status", "open", "--clear-resume-when")
    replay = run_cli(registry, *intent)
    assert replay["provider_status"] == "replayed"
    assert replay["changed"] is False and replay["updated_at"] is None
    assert original["updated_at"] is not None
    for field in ("todo", "todo_id", "status", "claimed_by", "resume_when"):
        assert replay[field] == original[field]
    rejected = run_cli_error(registry, *intent, "--text", "Different create intent")
    assert rejected["error_code"] == "coordination_operation_identity_mismatch"
    current = run_cli(registry, "todo", "list", "--goal-id", GOAL_ID, "--todo-id", original["todo_id"])
    assert current["todo"]["text"] == "Worker capacity available"
    assert current["todo"]["status"] == "open"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_create_duplicate_projects_existing_open_todo(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    root = Path(__file__).resolve().parents[2]
    runpy.run_path(str(root / "examples/control_plane/todo-lifecycle-cli-smoke.py"))
    from todo_lifecycle_fixtures import GOAL_ID, run_cli, write_fixture
    registry, _ = write_fixture(tmp_path / "fixture", provider=provider)
    intent = ("todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
        "--text", "An existing open task", "--claimed-by", "codex-main-control")
    original = run_cli(registry, *intent)
    duplicate = run_cli(registry, *intent)
    assert duplicate["status"] == "open"
    assert duplicate["todo"] == "An existing open task"
    assert duplicate["todo_id"] == original["todo_id"]
    assert duplicate["claimed_by"] == "codex-main-control"
    assert duplicate["provider_status"] == "no_change"
    assert duplicate["added"] is False and duplicate["already_exists"] is True
    assert duplicate["changed"] is False


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_imported_archive_complete_journey(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    root = Path(__file__).resolve().parents[2]
    archive = runpy.run_path(str(root / "examples/control_plane/todo-archive-completed-smoke.py"))
    assert archive["main"](provider=provider) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_canonical_monitor_writeback_journey(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    root = Path(__file__).resolve().parents[2]
    monitor = runpy.run_path(str(root / "examples/control_plane/monitor-poll-writeback-smoke.py"))
    original = monitor["write_fixture"]

    def write_fixture(path, **options):
        return original(path, provider=provider, **options)

    monkeypatch.setitem(monitor["main"].__globals__, "write_fixture", write_fixture)
    assert monitor["main"]() == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_imported_durability_journey(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    root = Path(__file__).resolve().parents[2]
    durability = runpy.run_path(str(root / "examples/control_plane/todo-durability-fixture-smoke.py"))
    assert durability["main"](provider=provider) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_canonical_todo_cli_journey(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    root = Path(__file__).resolve().parents[2]
    cli = runpy.run_path(str(root / "examples/control_plane/todo-cli-smoke.py"))
    assert cli["main"](provider=provider) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_canonical_concurrent_writer_journey(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    root = Path(__file__).resolve().parents[2]
    concurrency = runpy.run_path(str(root / "examples/control_plane/todo-concurrent-write-lock-smoke.py"))
    assert concurrency["main"](provider=provider) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_canonical_user_gate_scope_journey(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    root = Path(__file__).resolve().parents[2]
    gates = runpy.run_path(str(root / "examples/control_plane/todo-user-gate-scope-smoke.py"))
    assert gates["main"](provider=provider) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_canonical_write_correctness_journey(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    root = Path(__file__).resolve().parents[2]
    preview = runpy.run_path(str(root / "examples/control_plane/todo-write-correctness-smoke.py"))
    assert preview["main"](provider=provider) == 0
