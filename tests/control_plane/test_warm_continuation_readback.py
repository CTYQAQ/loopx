"""Continuation readiness uses live Goal owners, not old plan or clock prose."""
from __future__ import annotations

import time

import pytest

from benchmark.runtime.sforge_warm_start import _PREPARATION_TASK, _read_preparation, restore_workspace
from loopx.control_plane.effect_runtime import effect_runtime_result
from test_replan_successor_guard_reentry import AGENT, GOAL, _fixture


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_current_preparation_readback_is_idempotent_and_respects_user_gate(tmp_path, monkeypatch, provider):
    call, _, _ = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    project = tmp_path / "project"
    candidate = project / "candidate.txt"
    candidate.write_text("retained candidate; old Host deadline belongs to the prior phase")
    component = {"id": "workspace", "source_path": str(project)}
    probe = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe", "components": [component]})
    point = tmp_path / "captured-point"
    sealed = effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "capture", "destination": str(point), "components": [component],
        "expected_source_sha256": probe["source_sha256"], "point_metadata": {
            "native_session": {"guest_workspace": str(project)},
            "worker_file_snapshot": {"components": [component]},
        },
    })
    (project / "image-only-file").write_text("not part of the retained work")
    restore_workspace({"root": str(point), "sha256": sealed["sha256"],
                       "deadline_epoch": int(time.time()) + 600}, project)
    assert candidate.read_text().startswith("retained candidate")
    assert not (project / "image-only-file").exists()
    created = call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--text", _PREPARATION_TASK, "--task-class", "advancement_task", "--required-capability", "shell",
        "--operation-id", "checkpoint-preparation")
    todo = created["todo_id"]
    deadline = int(time.time()) + 600
    declaration = {"deadline_epoch": deadline}
    state = project / "ACTIVE_GOAL_STATE.md"
    state_before = state.read_bytes()
    readback = _read_preparation(call, GOAL, AGENT, todo, declaration)
    repeated = _read_preparation(call, GOAL, AGENT, todo, declaration)
    assert readback == repeated
    assert readback["preparation_todo_id"] == todo
    assert readback["authority_read"]["provider_revision"]
    assert readback["selected_todo_id"] == todo or readback["current_replan_required"]
    assert state.read_bytes() == state_before
    assert declaration["deadline_epoch"] == deadline
    listed = call("todo", "list", "--goal-id", GOAL, "--todo-id", todo)["todo"]
    assert listed["status"] == "open" and listed["claimed_by"] == AGENT
    assert "Earlier Host deadlines" in listed["text"]
    assert "Original task time constraints" in listed["text"]
    assert "No additional time or authority" in listed["text"]
    call("todo", "add", "--goal-id", GOAL, "--role", "user", "--task-class", "user_gate",
        "--global-gate", "--text", "Authorize the separate destination operation.")
    with pytest.raises(ValueError, match="no actionable"):
        _read_preparation(call, GOAL, AGENT, todo, declaration)


def test_expired_preparation_does_not_read_or_renew_a_phase():
    declaration = {"deadline_epoch": 1}
    calls = []
    with pytest.raises(ValueError, match="expired"):
        _read_preparation(lambda *args: calls.append(args), "goal", "agent", "todo", declaration)
    assert calls == [] and declaration == {"deadline_epoch": 1}


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("boundary", ["before_guard", "after_guard"])
@pytest.mark.parametrize("change", ["complete", "transfer"])
def test_changed_preparation_cannot_borrow_an_unrelated_current_review(
    tmp_path, monkeypatch, provider, boundary, change,
):
    call, _, _ = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    todo = call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--text", _PREPARATION_TASK, "--task-class", "advancement_task")["todo_id"]
    changed = False

    def interleaved(*args):
        nonlocal changed
        result = call(*args)
        trigger = ("todo", "list") if boundary == "before_guard" else ("quota", "should-run")
        if not changed and args[:2] == trigger:
            changed = True
            if change == "complete":
                call("todo", "complete", "--goal-id", GOAL, "--agent-id", AGENT, "--todo-id", todo)
            else:
                call("todo", "update", "--goal-id", GOAL, "--agent-id", AGENT, "--todo-id", todo,
                     "--claimed-by", "different-agent")
        return result

    with pytest.raises(ValueError, match="preparation.*changed|not open"):
        _read_preparation(interleaved, GOAL, AGENT, todo, {"deadline_epoch": int(time.time()) + 600})
    assert changed
    final = call("todo", "list", "--goal-id", GOAL, "--todo-id", todo)["todo"]
    assert final["status"] == "done" if change == "complete" else final["claimed_by"] == "different-agent"


@pytest.mark.parametrize("todo", [
    {"status": "done", "claimed_by": "agent"},
    {"status": "open", "claimed_by": "another-agent"},
])
def test_closed_or_foreign_preparation_cannot_use_a_saved_ready_plan(todo):
    calls = []

    def read(*args):
        calls.append(args)
        return {"todo": todo}

    with pytest.raises(ValueError, match="not open"):
        _read_preparation(read, "goal", "agent", "todo", {"deadline_epoch": int(time.time()) + 60})
    assert len(calls) == 1 and calls[0][:2] == ("todo", "list")


def test_expiry_during_readback_cannot_report_continuation_ready(tmp_path, monkeypatch):
    from benchmark.runtime import sforge_warm_start as warm

    call, _, _ = _fixture(tmp_path, monkeypatch, "file", future_monitor=False)
    todo = call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--text", _PREPARATION_TASK, "--task-class", "advancement_task")["todo_id"]
    clock = [time.time()]
    deadline = int(clock[0]) + 600
    monkeypatch.setattr(warm, "time", type("Clock", (), {"time": staticmethod(lambda: clock[0])}))

    def expires_after_actual_guard(*args):
        result = call(*args)
        if args[:2] == ("quota", "should-run"):
            clock[0] = deadline
        return result

    with pytest.raises(ValueError, match="expired"):
        _read_preparation(expires_after_actual_guard, GOAL, AGENT, todo, {"deadline_epoch": deadline})
