"""Public update invariants: a partial edit must not invalidate an armed wait."""
import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.todos import add_goal_todo, list_goal_todos, update_goal_todo
from loopx.control_plane.testing.canary_harness import run_json_cli_result
from tests.control_plane.test_monitor_followthrough_contract import (
    AGENT_ID, GOAL_ID, _write_fixture,
)


def waiting_goal(tmp_path, *, provider="file"):
    registry, runtime, state = _write_fixture(tmp_path)
    initialize_canonical_authority(runtime, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=[], handoff_mode="soft_claim"),
        state_path=state, provider=provider)
    # Initialize historical evidence through the create/import boundary;
    # ordinary configuration cannot manufacture an observation generation.
    monitor = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="agent",
        text="Observe fixture", task_class="continuous_monitor", claimed_by=AGENT_ID,
        monitor_metadata={"target_key": "fixture", "cadence": "1h", "watch_only": "true",
                          "material_change_generation": "3"})
    def add(text):
        return add_goal_todo(registry_path=registry, goal_id=GOAL_ID,
            role="agent", task_class="advancement_task", text=text, claimed_by=AGENT_ID)
    waiting, successor = add("Await observation"), add("Independent work")
    update_goal_todo(registry_path=registry, goal_id=GOAL_ID,
        todo_id=waiting["todo_id"], agent_id=AGENT_ID,
        resume_when=f"monitor_changed:{monitor['todo_id']}",
        successor_todo_ids=[successor["todo_id"]])
    return registry, runtime, state, waiting, successor


def _canonical_todo(registry, todo_id):
    return list_goal_todos(registry_path=registry, goal_id=GOAL_ID,
                           todo_id=todo_id)["todo"]


@pytest.mark.parametrize("edit", [{"successor_todo_ids": []}, {"status": "blocked"},
                                    {"task_class": "continuous_monitor"}])
def test_partial_edit_cannot_invalidate_retained_monitor_wait(tmp_path, edit):
    registry, _, state, waiting, _ = waiting_goal(tmp_path)
    before = state.read_bytes()
    with pytest.raises(ValueError, match="external-wait") as rejected:
        update_goal_todo(registry_path=registry, goal_id=GOAL_ID,
            todo_id=waiting["todo_id"], agent_id=AGENT_ID, **edit)
    assert rejected.value.authoring_contract["waiting_todo"]["resume_when"].startswith("monitor_changed:")
    assert state.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_clear_condition_and_successors_together_is_valid(tmp_path, monkeypatch, provider):
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, _, state, waiting, _ = waiting_goal(tmp_path, provider=provider)
    result = update_goal_todo(registry_path=registry, goal_id=GOAL_ID,
        todo_id=waiting["todo_id"], agent_id=AGENT_ID,
        clear_resume_when=True, successor_todo_ids=[])
    assert result["status"] == "applied"
    updated = _canonical_todo(registry, waiting["todo_id"])
    assert updated.get("resume_when") is None
    assert updated.get("resume_monitor_generation") is None
    assert updated.get("resume_condition") is None
    assert updated.get("resume_ready") is None
    assert updated.get("successor_todo_ids", []) == []


def test_copy_edit_preserves_existing_wait_fence(tmp_path):
    registry, _, _, waiting, _ = waiting_goal(tmp_path)
    result = update_goal_todo(registry_path=registry, goal_id=GOAL_ID,
        todo_id=waiting["todo_id"], agent_id=AGENT_ID, note="Clarify context")
    assert result["status"] == "applied"
    updated = _canonical_todo(registry, waiting["todo_id"])
    assert updated["resume_when"].startswith("monitor_changed:")
    assert updated["resume_monitor_generation"] == 3
    assert updated["note"] == "Clarify context"


def test_real_cli_rejects_partial_wait_edit_and_supports_explicit_repair(tmp_path):
    registry, runtime, state, waiting, _ = waiting_goal(tmp_path)
    common = ("todo", "update", "--goal-id", GOAL_ID, "--todo-id", waiting["todo_id"],
              "--agent-id", AGENT_ID, "--status", "blocked")
    before = state.read_bytes()
    code, rejected = run_json_cli_result(*common, registry_path=registry, runtime_root=runtime)
    assert code != 0, rejected
    assert rejected["error_code"] == "external_wait_todo_status_must_remain_open"
    assert rejected["authoring_contract"]["waiting_todo"]["resume_when"].startswith("monitor_changed:")
    assert state.read_bytes() == before
    code, accepted = run_json_cli_result(*common, "--clear-resume-when", "--dry-run",
        registry_path=registry, runtime_root=runtime)
    assert code == 0, accepted
    assert state.read_bytes() == before


def test_public_update_uses_one_scope_wait_field_crossing(tmp_path, monkeypatch):
    from loopx.control_plane.todos import provider_update
    registry, _, _, waiting, successor = waiting_goal(tmp_path)
    actual = provider_update.effect_runtime_result
    calls = []
    def traced(method, params, **kwargs):
        calls.append(method)
        return actual(method, params, **kwargs)
    monkeypatch.setattr(provider_update, "effect_runtime_result", traced)
    result = update_goal_todo(registry_path=registry, goal_id=GOAL_ID,
        todo_id=waiting["todo_id"], agent_id=AGENT_ID,
        successor_todo_ids=[successor["todo_id"]])
    assert result["status"] in {"applied", "no_change"}
    assert _canonical_todo(registry, waiting["todo_id"])["resume_monitor_generation"] == 3
    assert calls == ["coordination.local_authority.todo_update"]
