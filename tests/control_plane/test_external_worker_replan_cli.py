"""File/SQLite -> real CLI quota -> external worker, with only Host work faked."""
from copy import deepcopy
import json
from pathlib import Path
import shlex
import sys

import pytest

from test_replan_successor_guard_reentry import AGENT, GOAL, TURN, _add, _fixture, _guard
from test_external_scheduler_worker import _args, worker
from loopx.extensions.process_runtime import CappedProcessResult
from loopx.history import collect_history


def _worker_readback(args):
    history = collect_history(registry_path=Path(args.registry), runtime_root=Path(args.runtime_root),
                              goal_id=GOAL, limit=1)
    return next(goal for goal in history["goals"] if goal["id"] == GOAL)["host_worker_observations"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_unresolved_replan_dispatches_once_across_return_and_restart(tmp_path, monkeypatch, provider):
    _, runtime, index = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    args = _args(fake_cli=Path(sys.executable).parent / "loopx",
                 state_file=tmp_path / "worker-state.json", wake_cmd="controlled-host-boundary",
                 quota_timeout_seconds=90)
    args.registry, args.runtime_root = str(tmp_path / "registry.json"), str(runtime)
    args.goal_id, args.agent_id, args.once = GOAL, AGENT, False
    decisions, requests = [], []
    real_quota = worker.run_quota_should_run

    def observe_quota(*command, **kwargs):
        packet = real_quota(*command, **kwargs)
        decisions.append(deepcopy(packet))
        return packet

    def inconclusive_host(*command, **kwargs):
        requests.append({"dispatch": len(requests) + 1, "provider_requests": 1})
        return CappedProcessResult(returncode=0, stdout=b"No new conclusion.\n")

    monkeypatch.setattr(worker, "run_quota_should_run", observe_quota)
    monkeypatch.setattr(worker, "_run_wake", inconclusive_host)
    before = index.read_bytes()
    assert worker.run_worker(args, sleep=lambda _: pytest.fail("active recheck must be immediate")) == 0
    args.once = True
    for _ in range(2):
        assert worker.run_worker(args) == 0
    assert len(decisions) == 4
    assert len(requests) == 1  # D=1, P=1 in this deterministic Host fixture.
    original = decisions[0]["autonomous_replan_obligation"]["obligation_id"]
    for packet in decisions:
        assert packet["should_run"] is True
        assert packet["effective_action"] == "autonomous_replan_required"
        assert packet["autonomous_replan_obligation"]["obligation_id"] == original
        assert not packet.get("goal_ref"), "the executable legacy entry must not acquire a forged lifetime"
    state = json.loads(Path(args.state_file).read_text())
    assert state["replan_attempts"][0]["obligation_id"] == original
    assert state["replan_attempts"][0]["status"] == "returned"
    assert index.read_bytes() == before  # E=0 and Q=0: rc=0 never became an ACK or spend.
    readback = _worker_readback(args)
    assert readback["read_status"] == "read"
    assert readback["rows"][0]["status"] == "replan_paused"
    assert readback["rows"][0]["obligation_id"] == original
    assert readback["rows"][0]["turn_instance_id"] is None


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_original_receipt_recovery_unblocks_next_work_without_repeating_replan(tmp_path, monkeypatch, provider):
    call, runtime, index = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    args = _args(fake_cli=Path(sys.executable).parent / "loopx", state_file=tmp_path / "worker-state.json",
                 wake_cmd="controlled-original-turn", quota_timeout_seconds=90)
    args.registry, args.runtime_root = str(tmp_path / "registry.json"), str(runtime)
    args.goal_id, args.agent_id = GOAL, AGENT
    real_quota = worker.run_quota_should_run
    current_turn = [TURN]
    decisions, requests, recovery = [], [], {}

    def scoped_quota(command, **kwargs):
        # This Host fixture explicitly preserves its original admitted Turn.
        # An ordinary opaque unbound shell command has no such guarantee.
        packet = real_quota([*command, "--turn-instance-id", current_turn[0]], **kwargs)
        decisions.append(deepcopy(packet))
        return packet

    def original_result_lost(*command, **kwargs):
        identity = decisions[-1]["heartbeat_receipt"]["settlement_identity"]
        requests.append(identity["replan_obligation_id"])
        successor = _add(call, identity["replan_obligation_id"])
        original_pending = _guard(call)
        actions = original_pending["interaction_contract"]["cli_channel"]["next_cli_actions"]
        refreshed = call(*shlex.split(actions[0])[1:], "--no-global-sync", "--suppress-external-sinks")
        assert refreshed["settlement_progress"]["state"] == "spend_required"
        recovery.update(spend=actions[1], successor=successor["todo_id"], identity=identity)
        return CappedProcessResult(returncode=1, stdout=b"")  # Effect happened; outer ACK was lost.

    monkeypatch.setattr(worker, "run_quota_should_run", scoped_quota)
    monkeypatch.setattr(worker, "_run_wake", original_result_lost)
    assert worker.run_worker(args) != 0
    assert len(requests) == 1
    readback = _worker_readback(args)
    assert readback["read_status"] == "read"
    assert readback["rows"][0]["status"] == "replan_outcome_unknown"
    assert readback["rows"][0]["turn_instance_id"] == TURN
    current_turn[0] = "current-work-after-lost-ack"
    # A changed current Turn / frontier cannot resolve the original debt.
    assert worker.run_worker(args) != 0
    assert len(requests) == 1
    # Existing non-model recovery acts on the exact old receipt, never the new Turn.
    spent = call(*shlex.split(recovery["spend"])[1:])
    assert spent["settlement_progress"]["state"] == "settled"
    assert spent["appended"] is True
    assert call(*shlex.split(recovery["spend"])[1:])["appended"] is False
    monkeypatch.setattr(worker, "_run_wake", lambda *a, **kw: requests.append("next-work") or
                        CappedProcessResult(returncode=0, stdout=b""))
    assert worker.run_worker(args) == 0
    assert requests == [recovery["identity"]["replan_obligation_id"], "next-work"]
    assert decisions[-1]["selected_todo"]["todo_id"] == recovery["successor"]
    state = json.loads(Path(args.state_file).read_text())
    assert state["replan_attempts"][0]["recovery"] == "original_settlement_verified"
    assert state["replan_attempts"][0]["settlement_identity"] == recovery["identity"]
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == 1
    assert _worker_readback(args) is None
