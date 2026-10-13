from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

from loopx.control_plane.todos.completion_policy import CompletionPolicy
from loopx.control_plane.testing.canary_harness import (
    run_json_cli,
    run_json_cli_result,
    write_fixture_registry,
)


GOAL_ID = "completion-policy-fixture"
AGENT_ID = "codex-quality-qualification"


def test_completion_policy_does_not_duplicate_runtime_model_authority() -> None:
    assert "agent_model" not in {field.name for field in fields(CompletionPolicy)}


@pytest.fixture(params=["file", "sqlite"])
def canonical_policy_goal(tmp_path: Path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project = tmp_path / "project"
    runtime = tmp_path / "runtime"
    state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "---\nstatus: active\n---\n\n# Active Goal State\n\n## Agent Todo\n",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        domain="loopx-platform",
        adapter_kind="harness_self_improvement",
        registered_agents=[AGENT_ID, "codex-main-control"],
        quota_allowed_slots=None,
    )
    initialize_canonical_authority(
        runtime, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=[], handoff_mode="soft_claim"),
        state_path=state_file, provider=request.param,
    )
    source = run_json_cli(
        "todo",
        "add",
        "--goal-id",
        GOAL_ID,
        "--role",
        "agent",
        "--text",
        "Validate the completion boundary.",
        "--claimed-by",
        AGENT_ID,
        registry_path=registry_path,
    )
    return registry_path, state_file, source["todo_id"]


def test_completion_rejects_unknown_runtime_model_before_write(canonical_policy_goal) -> None:
    registry_path, state_file, todo_id = canonical_policy_goal
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["goals"][0]["coordination"]["agent_model"] = "hierarchy_v2"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    before = state_file.read_text(encoding="utf-8")

    returncode, result = run_json_cli_result(
        "todo",
        "complete",
        "--goal-id",
        GOAL_ID,
        "--todo-id",
        todo_id,
        "--claimed-by",
        AGENT_ID,
        "--agent-id",
        AGENT_ID,
        "--evidence",
        "must remain atomic",
        "--no-follow-up",
        registry_path=registry_path,
    )

    assert returncode == 1
    assert "coordination.agent_model must be peer_v1" in result["error"]
    assert state_file.read_text(encoding="utf-8") == before


def test_self_merge_policy_rejection_preserves_authority_and_retry(canonical_policy_goal) -> None:
    registry_path, state_file, todo_id = canonical_policy_goal

    def listing():
        return run_json_cli("todo", "list", "--goal-id", GOAL_ID, registry_path=registry_path)

    def complete(evidence):
        return run_json_cli_result(
            "todo", "complete", "--goal-id", GOAL_ID, "--todo-id", todo_id,
            "--agent-id", AGENT_ID, "--claimed-by", AGENT_ID,
            "--self-merged", "--evidence", evidence, "--no-follow-up",
            "--note", "No further work remains.",
            registry_path=registry_path,
        )

    before = listing()
    display = state_file.read_bytes()
    # Python whitespace includes NEL; BOM remains non-whitespace in this contract.
    for evidence in ("", "\u0085", " \t\n"):
        returncode, result = complete(evidence)
        assert returncode == 1, result
        assert "--self-merged requires --evidence" in result["error"]
        after = listing()
        assert after["authority_read"] == before["authority_read"]
        assert after["todos"] == before["todos"]
        assert state_file.read_bytes() == display

    returncode, accepted = complete("\ufeff")
    assert returncode == 0, accepted
    assert accepted["completed"] is True
    assert accepted["completion_policy"]["self_merged"] is True
    committed = listing()
    returncode, replay = complete("\ufeff")
    assert returncode == 0, replay
    assert replay["idempotent_replay"] is True
    assert listing()["authority_read"] == committed["authority_read"]
