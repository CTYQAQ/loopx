"""2.0 normal writes refuse a cold source without changing its history."""
import json

import pytest

from loopx.control_plane.coordination.legacy_writer_fence import LegacyCoordinationWriterFenced
from loopx.control_plane.todos.mutation_api import (
    add_goal_todo, update_goal_todo, complete_goal_todo,
    supersede_goal_todo, archive_completed_todos,
)
from loopx.control_plane.todos.contract import format_todo_metadata_line
from loopx.control_plane.todos.list_readback import list_goal_todos
from loopx.control_plane.todos.handoff_mode import set_goal_handoff_mode


@pytest.fixture
def cold_goal(tmp_path):
    runtime = tmp_path / "runtime"
    state = tmp_path / "state.md"
    state.write_text('---\ngoal_id: old-goal\nhandoff_mode: soft_claim\n---\n# 原始目标\n\n## Agent Todo\n\n'
                     '- [ ] 保留原内容\n' + format_todo_metadata_line(
                         todo_id="todo_original", status="open", task_class="advancement_task",
                         action_kind="implement") + '\n', encoding="utf-8")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": "old-goal", "repo": str(tmp_path), "state_file": str(state),
        "coordination": {"registered_agents": ["alpha"], "agent_model": "peer_v1"}}]}))
    return registry, runtime, state


@pytest.mark.parametrize("operation", ["add", "update", "claim", "complete", "supersede", "archive", "handoff"])
def test_cold_source_normal_writes_require_import_before_effect(cold_goal, operation):
    registry, runtime, state = cold_goal
    before = state.read_bytes(), registry.read_bytes()
    common = dict(registry_path=registry, goal_id="old-goal", runtime_root_arg=str(runtime))
    calls = {
        "add": lambda: add_goal_todo(**common, role="agent", text="New work", agent_id="alpha"),
        "update": lambda: update_goal_todo(**common, todo_id="todo_original", text="Changed", agent_id="alpha"),
        "claim": lambda: update_goal_todo(**common, todo_id="todo_original", claimed_by="alpha", agent_id="alpha"),
        "complete": lambda: complete_goal_todo(**common, todo_id="todo_original", agent_id="alpha"),
        "supersede": lambda: supersede_goal_todo(**common, todo_id="todo_original", agent_id="alpha", reason="replacement"),
        "archive": lambda: archive_completed_todos(**common, dry_run=False),
        "handoff": lambda: set_goal_handoff_mode(**common, mode="hard_lease"),
    }
    with pytest.raises(LegacyCoordinationWriterFenced, match="canonical authority.*import"):
        calls[operation]()
    assert (state.read_bytes(), registry.read_bytes()) == before
    assert not list(runtime.rglob("*outbox*"))
    # Inspection is still an import/recovery entrypoint.
    assert list_goal_todos(**common, role="agent")["todos"][0]["text"] == "保留原内容"
