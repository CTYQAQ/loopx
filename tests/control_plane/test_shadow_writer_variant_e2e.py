"""Public writer combinations with real persistence and scheduling-only seams."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import select
import subprocess
import sys

import pytest

from shadow_e2e_fixture import ShadowWorkspace, workspace as make_workspace
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from historical_shadow_fixture import HistoricalShadowWorkspace, original_1x_source  # noqa: F401
from loopx.control_plane.coordination.coordination_state_contract_generated import TASK_LEASE_ACQUIRE_REQUEST_SCHEMA
from loopx.control_plane.coordination.legacy_writer_fence import legacy_coordination_writer_fence_path

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.stage2c_e2e


def workspace(path: Path) -> ShadowWorkspace:
    result = make_workspace(path, bootstrap=False)
    result.state.write_text(result.state.read_text() + "\n## Progress Ledger\n\n## Next Action\n\n- Inspect.\n")
    return result


def command(ws: ShadowWorkspace, *args: str, goal: str | None = None) -> list[str]:
    return [sys.executable, "-m", "loopx.cli", "--registry", str(ws.registry),
            "--runtime-root", str(ws.runtime), "--format", "json", *args, "--goal-id", goal or ws.goal]


def public(ws: ShadowWorkspace, *args: str, goal: str | None = None) -> dict:
    result = subprocess.run(command(ws, *args, goal=goal), cwd=REPO, capture_output=True, text=True, timeout=30)
    assert "Traceback" not in result.stderr, result.stderr
    return json.loads(result.stdout)


def start(args: list[str], *, cwd: Path = REPO) -> subprocess.Popen[str]:
    return subprocess.Popen(args, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def barrier(child: subprocess.Popen[str], phase: str) -> None:
    assert child.stdout is not None
    ready, _, _ = select.select([child.stdout], [], [], 10)
    assert ready, "public writer did not reach its real scheduling boundary"
    line = child.stdout.readline().strip()
    assert line == "BARRIER " + phase, line


def finish(child: subprocess.Popen[str], *, resume: bool = False) -> dict:
    output, error = child.communicate("continue\n" if resume else None, timeout=30)
    assert "Traceback" not in error, output + error
    return json.loads(output)


def stop(child: subprocess.Popen[str] | None) -> None:
    if child is not None:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


NATIVE = r"""
import fs from 'node:fs';
import {syncBuiltinESMExports} from 'node:module';
import {once} from 'node:events';
const input = JSON.parse(process.argv[1]);
if (input.wait_lock || input.pause_lock) {
  const actual = fs.promises.open;
  let notified = false;
  fs.promises.open = async (path, flags, ...args) => {
    if (String(path) === input.wait_lock + '.ts-effect.lock' && flags === 'wx' && !notified) {
      notified = true; process.stdout.write('BARRIER native-lock\n');
    }
    const handle = await actual(path, flags, ...args);
    if (String(path) === input.pause_lock + '.ts-effect.lock' && flags === 'wx') {
      const write = handle.writeFile.bind(handle);
      handle.writeFile = async (...args) => {
        await write(...args);
        process.stdout.write('BARRIER native-held\n'); await once(process.stdin, 'data');
      };
    }
    return handle;
  };
  syncBuiltinESMExports();
}
const owner = await import(input.module);
const dependencies = input.pause_write ? {beforeWrite:async () => {
  process.stdout.write('BARRIER native-write\n'); await once(process.stdin, 'data');
}} : {};
process.stdout.write(JSON.stringify(await owner[input.function](input.request, dependencies)) + '\n');
"""


def native(module: str, function: str, request: dict, *, source: Path = REPO, **options: object) -> list[str]:
    return ["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", NATIVE,
            json.dumps({"module": (source / module).as_uri(), "function": function, "request": request, **options})]


def fence_request(ws: ShadowWorkspace) -> dict:
    return {"schema_version": "loopx_legacy_coordination_writer_fence_engage_request_v0",
            "runtime_root": str(ws.runtime), "goal_id": ws.goal, "state_path": str(ws.state),
            "fence": {"schema_version": "loopx_legacy_coordination_writer_fence_v0", "state": "engaged",
                "goal_id": ws.goal, "fence_id": "variant-fence", "source_version": "variant-source",
                "source_projection_sha256": "a" * 64,
                "expected_shadow_provider_revision": "file:1:aaaaaaaaaaaaaaaaaaaaaaaa"}}


def fence_command(ws: ShadowWorkspace, **options: object) -> list[str]:
    return native("loopx/control_plane/coordination/legacy_writer_fence.ts",
                  "engageLegacyCoordinationWriterFence", fence_request(ws), **options)


@pytest.mark.parametrize("authority", ["fence", "active_capture"])
def test_other_goal_cannot_write_a_protected_goal_source_via_state_override(tmp_path: Path, authority: str) -> None:
    ws = workspace(tmp_path)
    other_state = tmp_path / "OTHER_GOAL_STATE.md"
    other_state.write_text("---\ngoal_id: goal-other\n---\n\n## Agent Todo\n")
    registry = json.loads(ws.registry.read_text())
    registry["goals"].append({"id": "goal-other", "repo": str(tmp_path), "state_file": other_state.name,
                              "coordination": {"registered_agents": ["agent-a"]}})
    ws.registry.write_text(json.dumps(registry))
    # An unbound source no longer grants normal writes, even before capture or a fence.
    before = ws.state.read_bytes(), other_state.read_bytes()
    legacy = public(ws, "todo", "add", "--state-file", str(ws.state), "--role", "agent",
                    "--text", "An unbound shared-state write requires import.",
                    "--evidence", "Canonical import requirement.", goal="goal-other")
    assert legacy["ok"] is False and legacy["added"] is False, legacy
    assert legacy["error_code"] == "canonical_authority_required", legacy
    assert (ws.state.read_bytes(), other_state.read_bytes()) == before
    if authority == "active_capture":
        # A registered source stays protected even without a frontmatter owner.
        ws.state.write_text(ws.state.read_text().replace(f"goal_id: {ws.goal}\n", ""))
        assert public(ws, "coordination-shadow", "bootstrap", "--execute")["bootstrap"]["status"] == "applied"
    else:
        result = subprocess.run(fence_command(ws), cwd=REPO, capture_output=True, text=True, check=True, timeout=20)
        assert json.loads(result.stdout)["status"] == "applied"
    before = ws.state.read_bytes(), other_state.read_bytes()
    result = public(ws, "todo", "add", "--state-file", str(ws.state), "--role", "agent",
                    "--text", "Must not bypass another goal's source authority.",
                    "--evidence", "Cross-goal source boundary.", goal="goal-other")
    assert not result.get("ok"), json.dumps(result, indent=2)
    # Current admission rejects before any legacy source writer is selected.
    assert result["error_code"] == "canonical_authority_required", result
    assert (ws.state.read_bytes(), other_state.read_bytes()) == before
    assert not (ws.runtime / "authority-shadow" / "outbox" / "goal-other").exists()
    if authority == "active_capture":
        unknown = public(ws, "todo", "add", "--state-file", str(ws.state), "--role", "agent",
                         "--text", "An unregistered goal cannot bypass source ownership.",
                         "--evidence", "Unregistered goal control.", goal="unregistered-goal")
        assert not unknown.get("ok"), unknown
        assert (ws.state.read_bytes(), other_state.read_bytes()) == before


PUBLIC_WORKER = r"""
import sys
from contextlib import contextmanager
from pathlib import Path
stage = sys.argv[1]
def pause(label):
    print('BARRIER ' + label, flush=True)
    sys.stdin.readline()
if stage == 'reward_plan':
    from loopx import feedback
    original = feedback.plan_active_state_update
    def planned(*args, **kwargs):
        result = original(*args, **kwargs)
        pause('reward-plan')
        return result
    feedback.plan_active_state_update = planned
elif stage == 'reward_write':
    from loopx import feedback
    original = feedback.atomic_write_state_text
    def write(*args, **kwargs):
        pause('reward-write')
        return original(*args, **kwargs)
    feedback.atomic_write_state_text = write
elif stage == 'handoff_write':
    from loopx.control_plane.runtime import document_io
    original = document_io.atomic_write_state_text
    def write(*args, **kwargs):
        pause('handoff-write')
        return original(*args, **kwargs)
    document_io.atomic_write_state_text = write
elif stage == 'handoff_k':
    from loopx import file_lock
    original = file_lock.exclusive_cross_runtime_file_lock
    @contextmanager
    def lock(path, *args, **kwargs):
        if Path(path).name == '.task-leases':
            print('BARRIER handoff-lock', flush=True)
        with original(path, *args, **kwargs) as held:
            yield held
    file_lock.exclusive_cross_runtime_file_lock = lock
from loopx.entrypoint import main
raise SystemExit(main(sys.argv[2:]))
"""


def paused_public(ws: ShadowWorkspace, stage: str, *args: str) -> subprocess.Popen[str]:
    return start([sys.executable, "-c", PUBLIC_WORKER, stage, *command(ws, *args)[3:]], cwd=ws.cwd)


@pytest.mark.parametrize("first", ["lease", "handoff"])
def test_current_drain_recovers_original_handoff_acquire_races(
    tmp_path: Path, first: str, original_source: Path,
) -> None:
    current = workspace(tmp_path)
    ws = HistoricalShadowWorkspace(**vars(current), original_source=original_source).original()
    # The original writers establish authentic 1.x receipts. All recovery below
    # uses HEAD; no current normal writer is redirected to a compatibility path.
    added = ws.add("Unclaimed work eligible for a lease.")
    todo_id = added["todo_id"]
    assert ws.cli("coordination-shadow", "bootstrap", "--execute")["bootstrap"]["status"] == "applied"
    facts = subprocess.run([sys.executable, "-c",
        "import json,sys; from pathlib import Path; "
        "from loopx.control_plane.work_items.task_lease_acquire_adapter import task_lease_acquire_authority_facts; "
        "print(json.dumps(task_lease_acquire_authority_facts(registry_path=Path(sys.argv[1]), "
        "goal_id=sys.argv[2], todo_id=sys.argv[3])))", str(ws.registry), ws.goal, todo_id],
        cwd=original_source, capture_output=True, text=True, check=True, timeout=30)
    request = {"schema_version": TASK_LEASE_ACQUIRE_REQUEST_SCHEMA, "runtime_root": str(ws.runtime), "goal_id": ws.goal,
        "todo_id": todo_id, "owner": "agent-a", "idempotency_key": "native-variant-acquire", "ttl_seconds": 120,
        "write_scopes": [], "expected_version": None,
        "authority": json.loads(facts.stdout)}
    lease_dir = ws.runtime / "goals" / ws.goal / "task-leases"
    def lease_command(**options: object) -> list[str]:
        return native("loopx/control_plane/work_items/task_lease_acquire.ts",
                      "executeTaskLeaseAcquire", request, source=original_source, **options)
    handoff_args = ("handoff-mode", "set", "--mode", "soft_claim")
    lease = handoff = None
    original = ws.state.read_bytes()
    try:
        if first == "lease":
            lease = start(lease_command(pause_write=True))
            barrier(lease, "native-write")
            handoff = paused_public(ws, "handoff_k", *handoff_args)
            barrier(handoff, "handoff-lock")
            assert handoff.poll() is None
            acquired = finish(lease, resume=True)
            refused = finish(handoff)
            assert acquired["acquired"] is True, acquired
            assert refused["error_code"] == "handoff_mode_not_quiescent", refused
            assert ws.state.read_bytes() == original
            assert json.loads((lease_dir / f"{todo_id}.json").read_text())["owner"] == "agent-a"
        else:
            handoff = paused_public(ws, "handoff_write", *handoff_args)
            barrier(handoff, "handoff-write")
            lease = start(lease_command(wait_lock=str(lease_dir / ".task-leases")))
            barrier(lease, "native-lock")
            assert lease.poll() is None
            changed = finish(handoff, resume=True)
            refused = finish(lease)
            assert changed["changed"] is True, changed
            assert refused["error_code"] == "authority_source_changed", refused
            assert "handoff_mode: soft_claim" in ws.state.read_text()
            assert not (lease_dir / f"{todo_id}.json").exists()
        before = ws.state.read_bytes()
        refused = public(current, "todo", "add", "--role", "agent", "--text", "Fresh legacy intent")
        assert refused["error_code"] == "canonical_authority_required", refused
        assert ws.state.read_bytes() == before
        drained = public(current, "authority-shadow", "drain")
        assert drained["ok"] is True, drained
        inspected = public(current, "coordination-shadow", "inspect")
        assert inspected["inspection"]["status"] == "matched", inspected
        assert inspected["inspection"]["cursor"] == "2", inspected
    finally:
        stop(lease)
        stop(handoff)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("first", ["lease", "handoff"])
def test_canonical_handoff_acquire_race_uses_one_provider_head(tmp_path, monkeypatch, provider, first):
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.coordination.coordination_state_contract import (
        TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION, TODO_DOMAIN_RECORD_FIELDS,
    )
    from loopx.control_plane.coordination.coordination_state_contract_generated import TASK_LEASE_CANONICAL_ACQUIRE_REQUEST_SCHEMA
    from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
    from loopx.control_plane.work_items.task_lease_acquire_adapter import _canonical_lease_authority_facts

    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    ws = workspace(tmp_path)
    projection = build_todo_runtime_shadow_projection(goal_id=ws.goal, todos=[], handoff_mode="hard_lease")
    projection["todo_read_model"].update(schema_version=TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
                                         contract_fields=list(TODO_DOMAIN_RECORD_FIELDS))
    initialize_canonical_authority(ws.runtime, ws.goal, projection, state_path=ws.state, provider=provider)
    todo = ws.add("Race mode change against execution admission.")["todo_id"]
    original = ws.state.read_bytes()
    def read():
        return read_canonical_todos_if_promoted(runtime_root=ws.runtime, goal_id=ws.goal, include_leases=True)
    initial = read()
    acquire = {"schema_version": TASK_LEASE_CANONICAL_ACQUIRE_REQUEST_SCHEMA,
        "runtime_root": str(ws.runtime), "goal_id": ws.goal, "todo_id": todo, "owner": "agent-a",
        "idempotency_key": "canonical-race", "expected_version": 0, "ttl_seconds": 120, "write_scopes": [],
        "authority": _canonical_lease_authority_facts(ws.registry, ws.goal)}
    handoff = {"schema_version": "loopx_coordination_handoff_mode_set_request_v0",
        "runtime_root": str(ws.runtime), "goal_id": ws.goal, "operation_id": "canonical-race-mode",
        "requested_mode": "soft_claim", "dry_run": False}
    # A real clock makes the just-acquired lease time-active for the mode decision.
    from datetime import datetime, timezone
    handoff["observed_at"] = datetime.now(timezone.utc).isoformat()
    digest = hashlib.sha256(ws.goal.encode()).hexdigest()[:16]
    lock = str(ws.runtime / "authority-transition/file-v0" / ("shadow-management-" + digest) / "maintenance")
    lease = mode = None
    try:
        if first == "lease":
            lease = start(native("loopx/control_plane/work_items/task_lease_acquire.ts",
                                 "executeTaskLeaseAcquire", acquire, pause_write=True))
            barrier(lease, "native-write")
            mode = start(native("loopx/control_plane/coordination/handoff_mode_runtime.ts",
                                "setLocalHandoffMode", handoff, wait_lock=lock))
            barrier(mode, "native-lock")
            assert mode.poll() is None
            assert finish(lease, resume=True)["acquired"] is True
            refused = finish(mode)
            assert refused["reason_code"] == "handoff_mode_not_quiescent", refused
            final = read()
            assert final["handoff_mode"] == "hard_lease"
            assert len(final["leases"]) == 1 and final["leases"][0]["owner"] == "agent-a"
        else:
            mode = start(native("loopx/control_plane/coordination/handoff_mode_runtime.ts",
                                "setLocalHandoffMode", handoff, pause_lock=lock))
            barrier(mode, "native-held")
            lease = start(native("loopx/control_plane/work_items/task_lease_acquire.ts",
                                 "executeTaskLeaseAcquire", acquire, wait_lock=lock))
            barrier(lease, "native-lock")
            assert lease.poll() is None
            assert finish(mode, resume=True)["changed"] is True
            refused = finish(lease)
            assert refused["error_code"] == "handoff_mode_forbids_lease", refused
            final = read()
            assert final["handoff_mode"] == "soft_claim" and final["leases"] == []
        assert final["todos"] == initial["todos"]
        assert int(final["cursor"]) == int(initial["cursor"]) + 1
        assert ws.state.read_bytes() == original
        assert not list((ws.runtime / "goals" / ws.goal / "task-leases").glob("*.json"))
    finally:
        stop(lease)
        stop(mode)


def prepare_rewards(ws: ShadowWorkspace) -> Path:
    index = ws.runtime / "goals" / ws.goal / "runs" / "index.jsonl"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(json.dumps({"generated_at": "2026-09-05T00:00:00Z", "json_path": "run.json",
                                "markdown_path": "run.md", "classification": "continue"}) + "\n")
    return index


def reward_args(reason: str, timestamp: str) -> tuple[str, ...]:
    return ("reward", "--actor-kind", "owner", "--decision", "continue", "--reward", "positive",
            "--reason-summary", reason, "--recorded-at", timestamp, "--write-active-state-summary")


def test_concurrent_public_rewards_preserve_both_summaries_and_run_overlays(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    index = prepare_rewards(ws)
    assert public(ws, "coordination-shadow", "bootstrap", "--execute")["bootstrap"]["status"] == "applied"
    first = paused_public(ws, "reward_plan", *reward_args("First independent review.", "2026-09-05T00:01:00Z"))
    try:
        barrier(first, "reward-plan")
        second = public(ws, *reward_args("Second independent review.", "2026-09-05T00:02:00Z"))
        assert second["ok"] is True, second
        result = finish(first, resume=True)
        assert result["ok"] is True, result
        text = ws.state.read_text()
        assert text.count("First independent review.") == 1
        assert text.count("Second independent review.") == 1
        overlays = [json.loads(line)["human_reward"]["reason_summary"] for line in index.read_text().splitlines()[1:]]
        assert sorted(overlays) == ["First independent review.", "Second independent review."]
        inspection = public(ws, "coordination-shadow", "inspect")["inspection"]
        assert inspection["status"] == "matched", inspection
        assert inspection["cursor"] == "1", inspection
    finally:
        stop(first)


def test_native_fence_waits_for_public_prose_and_then_blocks_todo_writes(tmp_path: Path) -> None:
    ws = workspace(tmp_path)
    index = prepare_rewards(ws)
    prose = paused_public(ws, "reward_write", *reward_args("Prose committed before the fence.", "2026-09-05T00:01:00Z"))
    fence = None
    try:
        barrier(prose, "reward-write")
        fence = start(fence_command(ws, wait_lock=str(ws.state)))
        barrier(fence, "native-lock")
        fence_path = legacy_coordination_writer_fence_path(runtime_root=ws.runtime, goal_id=ws.goal)
        assert not fence_path.exists()
        assert fence.poll() is None
        assert finish(prose, resume=True)["ok"] is True
        assert finish(fence)["status"] == "applied"
        before = ws.state.read_bytes()
        result = public(
            ws, "todo", "add", "--role", "agent", "--text", "Must now be fenced.",
            "--evidence", "Boundary check.",
        )
        # The fence selects native create; its owner reads the complete head,
        # rather than the retired Python list preflight. Missing authority must
        # reject without falling through to this still-writable source document.
        assert result["ok"] is False and result["added"] is False, result
        assert result["error_code"] == "local_authority_todo_list_unavailable", result
        assert result["error"] == "canonical Todo authority is unavailable", result
        assert result["status"] == "missing", result
        assert result["source_authority"] == "file_v0", result
        assert result["decision_read_from_provider"] is True, result
        assert result["legacy_fallback_used"] is False, result
        assert result["recovery"] == {
            "action": "restore_canonical_authority",
            "runtime_root": str(ws.runtime.resolve()),
            "goal_id": ws.goal,
            "legacy_markdown_fallback_allowed": False,
            "retry_after": "canonical_provider_readback_loaded",
        }, result
        assert ws.state.read_bytes() == before
        assert "Prose committed before the fence." in ws.state.read_text()
        assert len(index.read_text().splitlines()) == 2
    finally:
        stop(prose)
        stop(fence)
