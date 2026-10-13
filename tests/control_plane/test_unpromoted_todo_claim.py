"""The 2.0 cutoff refuses new execution before an explicit canonical import."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.todos import list_goal_todos, update_goal_todo
from loopx.control_plane.coordination.legacy_writer_fence import LegacyCoordinationWriterFenced


@pytest.mark.parametrize("with_note", [False, True])
def test_unpromoted_claim_requires_import_without_source_writes(
    tmp_path: Path, with_note: bool
) -> None:
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text("# Goal\n\n## Agent Todo\n\n- [ ] Historical unclaimed task.\n"
                     "  <!-- loopx:todo todo_id=todo_import_claim status=open -->\n", encoding="utf-8")
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({
        "common_runtime_root": str(runtime),
        "goals": [{"id": "goal-a", "status": "active", "repo": str(tmp_path),
                   "state_file": state.name, "coordination": {
                       "registered_agents": ["agent-a", "agent-b"],
                   }}],
    }), encoding="utf-8")
    request = dict(registry_path=registry, goal_id="goal-a", todo_id="todo_import_claim",
                   claimed_by="agent-a", agent_id="agent-a", claim_only=True)
    if with_note:
        request["note"] = "A note cannot authorize a new source claim"
    before = state.read_bytes()
    with pytest.raises(ValueError, match="requires promoted canonical authority"):
        update_goal_todo(**request, claim_operation_id="explicit-retry")
    assert state.read_bytes() == before
    before_todos = list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"]
    for dry_run in (True, False):
        with pytest.raises(LegacyCoordinationWriterFenced) as rejected:
            update_goal_todo(**request, dry_run=dry_run)
        assert rejected.value.code == "canonical_authority_required"
        assert state.read_bytes() == before
        assert list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"] == before_todos
    assert not (runtime / "authority" / "file-v0").exists()
