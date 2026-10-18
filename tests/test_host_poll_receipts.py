from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.quota.host_poll_receipts import (
    HOST_POLL_RECEIPT_DIRNAME,
    HOST_POLL_RECEIPT_SCHEMA_VERSION,
    receipt_path_for_goal,
    read_host_poll_receipt,
    read_host_poll_receipt_observation,
    read_host_worker_observations,
    record_host_poll_receipt,
    stale_host_poll_risk,
)


def terminal_decision() -> dict[str, Any]:
    return {
        "should_run": False,
        "effective_action": "terminal_no_followup",
        "goal_frontier_projection": {
            "terminal_state": {"kind": "no_followup"},
        },
    }


def run_now_decision() -> dict[str, Any]:
    return {"should_run": True, "effective_action": "normal_run"}


def test_receipt_path_sits_beside_the_goal_state_file() -> None:
    goal = {"id": "goal/one", "state_file": "state/goal-one.md", "repo": "/repo"}
    path = receipt_path_for_goal(goal, Path("/repo/state/goal-one.md"))
    assert path is not None
    assert path.parent.name == HOST_POLL_RECEIPT_DIRNAME
    assert path.parent.parent == Path("/repo/state")
    assert "goal_one" in path.name


def test_receipt_path_fails_closed_without_state_path() -> None:
    assert receipt_path_for_goal({"id": "goal-1"}, None) is None
    assert receipt_path_for_goal({"state_file": "x.md"}, Path("/tmp/x.md")) is None


def test_record_and_read_receipt_round_trip(tmp_path: Path) -> None:
    state_path = tmp_path / "goals" / "goal-1.md"
    goal = {"id": "goal-1", "state_file": "goals/goal-1.md", "repo": str(tmp_path)}
    now = datetime(2026, 8, 13, 10, 0, 0, tzinfo=timezone.utc)
    receipt = record_host_poll_receipt(
        goal,
        state_path,
        agent_id="agent-1",
        decision=run_now_decision(),
        now=now,
    )
    assert receipt is not None
    assert receipt["schema_version"] == HOST_POLL_RECEIPT_SCHEMA_VERSION
    assert receipt["expected_continuation"] is True
    assert receipt["poll_count"] == 1
    path = receipt_path_for_goal(goal, state_path)
    assert path is not None and path.exists()
    reread = read_host_poll_receipt(path)
    assert reread == receipt


def test_receipt_increments_poll_count_and_marks_terminal_stops(tmp_path: Path) -> None:
    state_path = tmp_path / "goals" / "goal-1.md"
    goal = {"id": "goal-1", "state_file": "goals/goal-1.md", "repo": str(tmp_path)}
    now = datetime(2026, 8, 13, 10, 0, 0, tzinfo=timezone.utc)
    first = record_host_poll_receipt(
        goal, state_path, agent_id=None, decision=run_now_decision(), now=now
    )
    second = record_host_poll_receipt(
        goal,
        state_path,
        agent_id=None,
        decision=terminal_decision(),
        now=now + timedelta(minutes=1),
    )
    assert first is not None and second is not None
    assert second["poll_count"] == 2
    assert second["expected_continuation"] is False
    assert second["last_decision_action"] == "terminal_no_followup"


def test_read_receipt_rejects_foreign_schemas(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps({"schema_version": "other"}), encoding="utf-8")
    assert read_host_poll_receipt(path) is None
    path.write_text("{broken", encoding="utf-8")
    assert read_host_poll_receipt(path) is None
    assert read_host_poll_receipt(tmp_path / "missing.json") is None


def test_read_receipt_observation_distinguishes_missing_and_unreadable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "receipt.json"
    assert read_host_poll_receipt_observation(path) == {
        "read_status": "missing",
        "receipt": None,
    }

    path.write_text("{broken", encoding="utf-8")
    assert read_host_poll_receipt_observation(path) == {
        "read_status": "unreadable",
        "receipt": None,
    }

    path.write_text(
        json.dumps(
            {
                "schema_version": HOST_POLL_RECEIPT_SCHEMA_VERSION,
                "goal_id": "goal-1",
                "last_poll_at": "not-a-timestamp",
            }
        ),
        encoding="utf-8",
    )
    assert read_host_poll_receipt_observation(path) == {
        "read_status": "unreadable",
        "receipt": None,
    }


def test_stale_host_poll_risk_only_for_quiet_expecting_loops() -> None:
    now = datetime(2026, 8, 13, 10, 0, 0, tzinfo=timezone.utc)
    stale = {
        "schema_version": HOST_POLL_RECEIPT_SCHEMA_VERSION,
        "goal_id": "goal-1",
        "agent_id": "agent-1",
        "poll_count": 7,
        "last_poll_at": (now - timedelta(hours=6)).isoformat(),
        "expected_continuation": True,
    }
    risk = stale_host_poll_risk(stale, now=now)
    assert risk is not None
    assert risk["kind"] == "stale_host_poll"
    assert risk["age_minutes"] == 360.0
    fresh = {**stale, "last_poll_at": (now - timedelta(minutes=5)).isoformat()}
    assert stale_host_poll_risk(fresh, now=now) is None
    stopped = {**stale, "expected_continuation": False}
    assert stale_host_poll_risk(stopped, now=now) is None
    assert stale_host_poll_risk({"expected_continuation": True, "last_poll_at": "not-a-date"}, now=now) is None


def test_worker_observations_survive_polls_and_only_their_agent_can_clear(tmp_path):
    goal, state = {"id": "goal-1"}, tmp_path / "state.md"
    runtime = tmp_path / "runtime"
    observed = {
        "status": "replan_outcome_unknown", "obligation_id": "replan-original",
        "turn_instance_id": "original-turn", "registry_goal_digest": "current-goal",
        "runtime_root": str(runtime.resolve()),
    }
    first = record_host_poll_receipt(goal, state, agent_id="agent-1", decision=run_now_decision(),
                                     worker_observation=observed)
    assert first["expected_continuation"] is False
    record_host_poll_receipt(goal, state, agent_id="agent-2", decision=run_now_decision(),
                             worker_observation={**observed, "status": "replan_paused"})
    record_host_poll_receipt(goal, state, agent_id="agent-1", decision=run_now_decision())
    def read():
        return read_host_worker_observations(goal, state, registry_goal_digest="current-goal", runtime_root=runtime)

    rows = read()["rows"]
    assert [row["agent_id"] for row in rows] == ["agent-1", "agent-2"]
    assert rows[0]["observed_at"] == first["last_poll_at"]
    assert all("runtime_root" not in row and "registry_goal_digest" not in row for row in rows)
    record_host_poll_receipt(goal, state, agent_id="agent-1", decision=run_now_decision(), worker_observation=None)
    assert [row["agent_id"] for row in read()["rows"]] == ["agent-2"]
    record_host_poll_receipt(goal, state, agent_id="agent-2", decision=run_now_decision(), worker_observation=None)
    assert read() is None


@pytest.mark.parametrize("damage", ["wrong-goal", "wrong-runtime", "wrong-digest", "bad-status", "bad-map", "bad-time", "invalid-utf8"])
def test_worker_observation_scope_and_damage_remain_visible_unknown(tmp_path, damage):
    goal, state = {"id": "goal-1"}, tmp_path / "state.md"
    runtime = tmp_path / "runtime"
    record_host_poll_receipt(goal, state, agent_id="agent-1", decision=run_now_decision(),
        worker_observation={"status": "replan_paused", "obligation_id": "replan-original",
            "turn_instance_id": None, "registry_goal_digest": "current-goal", "runtime_root": str(runtime.resolve())})
    path = receipt_path_for_goal(goal, state)
    receipt = json.loads(path.read_text())
    row = receipt["worker_observations"]["agent-1"]
    expected = "unreadable"
    if damage == "wrong-goal":
        receipt["goal_id"] = "different-goal"
    elif damage == "wrong-runtime":
        row["runtime_root"] = str(tmp_path / "different-runtime")
        expected = "stale"
    elif damage == "wrong-digest":
        row["registry_goal_digest"] = "old-goal"
        expected = "stale"
    elif damage == "bad-status":
        row["status"] = ["replan_paused"]
    elif damage == "bad-map":
        receipt["worker_observations"] = ["damaged"]
    elif damage == "bad-time":
        row["observed_at"] = "2026-01-01T00:00:00"
    path.write_bytes(b"\xff" if damage == "invalid-utf8" else json.dumps(receipt).encode())
    read = read_host_worker_observations(goal, state, registry_goal_digest="current-goal", runtime_root=runtime)
    assert read == {"goal_id": "goal-1", "read_status": expected, "rows": []}
    if damage == "bad-map":
        record_host_poll_receipt(goal, state, agent_id="agent-1", decision=run_now_decision())
        assert read_host_worker_observations(goal, state, registry_goal_digest="current-goal", runtime_root=runtime) == read
