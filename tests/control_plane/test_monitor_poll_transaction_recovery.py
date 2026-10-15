"""Historical Markdown Monitor receipt replay after canonical cutover."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority
from loopx.control_plane.coordination.local_authority import (
    CanonicalAuthorityRequired,
    read_canonical_todos_if_promoted,
)
from loopx.control_plane.coordination.local_authority_shadow_projection import (
    canonical_bytes,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.scheduler.monitor_poll_writeback import (
    write_monitor_poll_todo_state,
)
from loopx.control_plane.testing.canary_harness import write_fixture_registry


GOAL = "synthetic-monitor-recovery"
AGENT = "codex-quality-qualification"
OPERATION = "historical-monitor-effect"
MONITOR_ID = "todo_historical_monitor"
AGENT_SUCCESSOR = "todo_historical_agent"
USER_SUCCESSOR = "todo_historical_user"
AGENT_TASK = "Validate observed synthetic head"
USER_TASK = "Review observed synthetic head"


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    runtime = tmp_path / "runtime"
    state = project / ".codex" / "goals" / GOAL / "ACTIVE_GOAL_STATE.md"
    registry = project / ".loopx" / "registry.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        "---\nstatus: active\n---\n\n# Active Goal State\n\n## Agent Todo\n",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry,
        goal_id=GOAL,
        domain="loopx-platform",
        adapter_kind="harness_self_improvement",
        registered_agents=[AGENT],
        quota_allowed_slots=None,
    )
    return registry, runtime, state


def _request() -> tuple[dict, dict]:
    observation = {
        "todo_id": MONITOR_ID,
        "target_key": "synthetic-target",
        "result_hash": "synthetic-head-1",
        "material_change": True,
        "generated_at": "2026-09-01T00:00:00Z",
        "cadence": None,
        "next_due_at": None,
        "reason_summary": None,
    }
    intent = {
        "next_agent_todo": AGENT_TASK,
        "next_action_kind": "validate",
        "next_task_repository": None,
        "next_required_capabilities": [],
        "next_continuation_policy": None,
        "next_target_key": None,
        "next_claimed_by": None,
        "next_user_todo": USER_TASK,
        "next_user_task_class": "user_action",
    }
    return observation, intent


def _historical_receipt() -> dict:
    observation, intent = _request()
    request_sha256 = hashlib.sha256(
        canonical_bytes(
            {
                "goal_id": GOAL,
                "observation": observation,
                "intent": intent,
                "actor_agent_id": AGENT,
                "dry_run": False,
            }
        )
    ).hexdigest()
    next_todos = [
        {"todo_id": AGENT_SUCCESSOR, "role": "agent", "todo": AGENT_TASK},
        {"todo_id": USER_SUCCESSOR, "role": "user", "todo": USER_TASK},
    ]
    successor_receipts = [
        {"todo_id": AGENT_SUCCESSOR, "role": "agent"},
        {"todo_id": USER_SUCCESSOR, "role": "user"},
    ]
    return {
        "schema_version": "loopx_monitor_batch_receipt_v0",
        "goal_id": GOAL,
        "operation_id": OPERATION,
        "request_sha256": request_sha256,
        "writeback": {
            "schema_version": "monitor_poll_todo_writeback_v0",
            "dry_run": False,
            "goal_id": GOAL,
            "todo_id": MONITOR_ID,
            "monitor_effect_id": OPERATION,
            "result_hash": observation["result_hash"],
            "material_change": True,
            "last_checked_at": observation["generated_at"],
            "todo_update": {"ok": True},
            "next_todos": next_todos,
            "successor_receipts": successor_receipts,
            "provider_replayed": False,
        },
    }


def _seed_receipt(state: Path, receipt: object) -> None:
    encoded = base64.b64encode(
        json.dumps(receipt, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    digest = hashlib.sha256(OPERATION.encode("utf-8")).hexdigest()
    with state.open("a", encoding="utf-8") as handle:
        handle.write(f"\n<!-- loopx:monitor-batch:{digest} {encoded} -->\n")


def _promote(runtime: Path, state: Path) -> None:
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL,
        todos=[],
        handoff_mode="soft_claim",
    )
    initialize_canonical_authority(
        runtime,
        GOAL,
        projection,
        state_path=state,
    )


def _replay(registry: Path, runtime: Path, **overrides: object) -> dict:
    observation, intent = _request()
    arguments = {
        "registry_path": registry,
        "runtime_root": runtime,
        "goal_id": GOAL,
        "generated_at": observation["generated_at"],
        "execute": True,
        "monitor_effect_id": OPERATION,
        "todo_id": observation["todo_id"],
        "target_key": observation["target_key"],
        "result_hash": observation["result_hash"],
        "material_change": True,
        "cadence": None,
        "next_due_at": None,
        "reason_summary": None,
        "next_agent_todo": intent["next_agent_todo"],
        "next_action_kind": intent["next_action_kind"],
        "next_task_repository": None,
        "next_required_capabilities": [],
        "next_continuation_policy": None,
        "next_target_key": None,
        "next_claimed_by": None,
        "next_user_todo": intent["next_user_todo"],
        "next_user_task_class": intent["next_user_task_class"],
        "agent_id": AGENT,
    }
    arguments.update(overrides)
    result = write_monitor_poll_todo_state(**arguments)
    assert isinstance(result, dict)
    return result


def test_historical_receipt_replays_without_mutating_canonical_authority(
    tmp_path: Path,
) -> None:
    registry, runtime, state = _fixture(tmp_path)
    _seed_receipt(state, _historical_receipt())
    _promote(runtime, state)
    state_before = state.read_bytes()
    provider_before = read_canonical_todos_if_promoted(
        runtime_root=runtime,
        goal_id=GOAL,
    )

    replayed = _replay(registry, runtime)

    assert replayed["provider_replayed"] is True
    assert [item["todo_id"] for item in replayed["next_todos"]] == [
        AGENT_SUCCESSOR,
        USER_SUCCESSOR,
    ]
    assert state.read_bytes() == state_before
    assert read_canonical_todos_if_promoted(
        runtime_root=runtime,
        goal_id=GOAL,
    ) == provider_before


def test_historical_receipt_rejects_changed_intent(tmp_path: Path) -> None:
    registry, runtime, state = _fixture(tmp_path)
    _seed_receipt(state, _historical_receipt())
    _promote(runtime, state)
    state_before = state.read_bytes()
    provider_before = read_canonical_todos_if_promoted(
        runtime_root=runtime,
        goal_id=GOAL,
    )

    with pytest.raises(ValueError, match="request mismatch"):
        _replay(registry, runtime, next_user_todo="Request a different review")

    assert state.read_bytes() == state_before
    assert read_canonical_todos_if_promoted(
        runtime_root=runtime,
        goal_id=GOAL,
    ) == provider_before


def test_malformed_historical_receipt_is_not_treated_as_absent(
    tmp_path: Path,
) -> None:
    registry, runtime, state = _fixture(tmp_path)
    _seed_receipt(state, None)
    _promote(runtime, state)
    state_before = state.read_bytes()

    with pytest.raises(ValueError, match="receipt must be an object"):
        _replay(registry, runtime)

    assert state.read_bytes() == state_before


def test_unpromoted_goal_cannot_replay_historical_receipt(
    tmp_path: Path,
) -> None:
    registry, runtime, state = _fixture(tmp_path)
    _seed_receipt(state, _historical_receipt())
    state_before = state.read_bytes()

    with pytest.raises(CanonicalAuthorityRequired):
        _replay(registry, runtime)

    assert state.read_bytes() == state_before
    assert not (runtime / "goals" / GOAL / "authority").exists()
