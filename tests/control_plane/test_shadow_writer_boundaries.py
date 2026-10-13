from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane.coordination.legacy_writer_fence import (
    LegacyCoordinationWriterFenced,
    legacy_coordination_writer_fence_path,
    legacy_todo_write_transaction,
)
from loopx.control_plane.todos.handoff_mode import set_goal_handoff_mode
from loopx.todos import add_goal_todo


GOAL = "writer-boundary"
REPO = Path(__file__).resolve().parents[2]


pytestmark = pytest.mark.stage2c_e2e


def fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "---\ngoal_id: writer-boundary\nhandoff_mode: legacy\n"
        "updated_at: 2026-09-05T00:00:00Z\n---\n\n"
        "## Agent Todo\n\n## Progress Ledger\n\n## Next Action\n\n- Review.\n",
        encoding="utf-8",
    )
    root = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(root), "goals": [{
        "id": GOAL, "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"agent_model": "peer_v1", "registered_agents": ["agent-a"]},
    }]}), encoding="utf-8")
    return registry, state, root


def seed_historical_todo(state: Path, *, text: str, **metadata) -> None:
    """Persist a synthetic pre-cutoff source for unchanged prose/recovery checks."""
    from loopx.control_plane.todos.contract import format_todo_metadata_line
    block = "- [ ] " + text + "\n" + format_todo_metadata_line(todo_id="todo_retained", **metadata) + "\n"
    state.write_text(state.read_text().replace("## Agent Todo\n", "## Agent Todo\n\n" + block, 1))


@pytest.mark.parametrize("enabled", [True, False])
def test_cutoff_preserves_active_shadow_history_when_configuration_changes(tmp_path, enabled):
    from shadow_e2e_fixture import ShadowWorkspace, seed_legacy_todo_capture
    registry, state, runtime = fixture(tmp_path)
    value = json.loads(registry.read_text())
    value["goals"][0]["coordination"]["runtime_shadow"] = {
        "enabled": True, "schema_version": "loopx_coordination_runtime_shadow_config_v0", "provider": "file_v0"}
    registry.write_text(json.dumps(value))
    cli(registry, "coordination-shadow", "bootstrap", "--goal-id", GOAL, "--execute")
    seed_legacy_todo_capture(ShadowWorkspace(registry, runtime, state, goal=GOAL), "Original pending work", "todo_pending")
    value["goals"][0]["coordination"]["runtime_shadow"]["enabled"] = enabled
    registry.write_text(json.dumps(value))
    original = state.read_bytes(), registry.read_bytes()
    def history():
        return {str(p.relative_to(runtime)): p.read_bytes() for p in runtime.rglob("*.json")}
    retained = history()
    for override in [None, str(tmp_path / "override")]:
        with pytest.raises(LegacyCoordinationWriterFenced, match="canonical authority"):
            add_goal_todo(registry_path=registry, goal_id=GOAL, runtime_root_arg=override,
                          role="agent", text="Must import first", agent_id="agent-a")
        assert (state.read_bytes(), registry.read_bytes()) == original
        assert history() == retained
    assert ShadowWorkspace(registry, runtime, state, goal=GOAL).drain()["outcome"] == "drained"


@pytest.mark.parametrize("registry_directory", [".loopx", "standalone"])
@pytest.mark.parametrize("override", [None, "", "override", Path("path-override")])
def test_runtime_route_keeps_project_relative_precedence_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry_directory: str, override,
) -> None:
    from loopx.paths import effective_runtime_root
    from loopx.control_plane.coordination.local_authority_shadow_adapter import effective_runtime_root as compatibility_route

    assert compatibility_route is effective_runtime_root
    project = tmp_path / "project"
    registry = project / registry_directory / "registry.json"
    registry.parent.mkdir(parents=True)
    original = json.dumps({"common_runtime_root": "declared"}).encode()
    registry.write_bytes(original)
    monkeypatch.chdir(tmp_path)
    owner = project if registry_directory == ".loopx" else registry.parent
    assert effective_runtime_root(registry, override) == owner / str(override or "declared")
    assert registry.read_bytes() == original
    assert list(registry.parent.iterdir()) == [registry]
    assert not (owner / "declared").exists()


def test_handoff_writer_refuses_a_fence_before_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, state, root = fixture(tmp_path)
    fence = legacy_coordination_writer_fence_path(runtime_root=root, goal_id=GOAL)
    fence.parent.mkdir(parents=True)
    fence.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "loopx.control_plane.coordination.legacy_writer_fence.effect_runtime_result",
        lambda *_args, **_kwargs: {
            "status": "blocked", "reason_code": "legacy_coordination_writer_fenced",
        },
    )
    before = state.read_bytes()
    with pytest.raises(LegacyCoordinationWriterFenced):
        set_goal_handoff_mode(registry_path=registry, goal_id=GOAL, mode="soft_claim")
    assert state.read_bytes() == before
    assert not (root / "authority-shadow").exists()


def test_handoff_mode_rechecks_legacy_fence_when_canonical_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider outage must not bypass an already-present legacy fence."""

    registry, state, root = fixture(tmp_path)
    fence = legacy_coordination_writer_fence_path(runtime_root=root, goal_id=GOAL)
    fence.parent.mkdir(parents=True)
    fence.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "loopx.control_plane.todos.provider_handoff_mode.effect_runtime_result",
        lambda *_args, **_kwargs: {
            "status": "unavailable",
            "reason_code": "canonical_provider_unavailable",
            "reason": "canonical provider is unavailable",
        },
    )
    monkeypatch.setattr(
        "loopx.control_plane.coordination.legacy_writer_fence.effect_runtime_result",
        lambda *_args, **_kwargs: {
            "status": "blocked",
            "reason_code": "legacy_coordination_writer_fenced",
        },
    )
    before = state.read_bytes()
    with pytest.raises(LegacyCoordinationWriterFenced) as error:
        set_goal_handoff_mode(registry_path=registry, goal_id=GOAL, mode="soft_claim")
    assert error.value.code == "legacy_coordination_writer_fenced"
    assert error.value.payload["write_check"]["reason_code"] == "legacy_coordination_writer_fenced"
    assert state.read_bytes() == before
    assert not (root / "authority-shadow").exists()


def test_corrupt_management_state_blocks_before_transaction_body(tmp_path: Path) -> None:
    registry, state, root = fixture(tmp_path)
    digest = hashlib.sha256(GOAL.encode()).hexdigest()[:16]
    management = root / "authority-transition" / "file-v0" / f"shadow-management-{digest}" / "state.json"
    management.parent.mkdir(parents=True)
    management.write_text("{corrupt", encoding="utf-8")
    reached = False
    try:
        with legacy_todo_write_transaction(registry, GOAL, state, None, "test", False):
            reached = True
    except RuntimeError as error:
        assert getattr(error, "reason_code", "")
    assert not reached, "maintenance errors must not fall through to a primary write"


def test_atomic_state_writer_keeps_original_on_failed_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx.control_plane.runtime import document_io

    write = getattr(document_io, "atomic_write_state_text", None)
    assert callable(write), "all state writers need the shared durable text primitive"
    state = tmp_path / "state.md"
    state.write_bytes(b"original\r\n")
    state.chmod(0o640)
    def fail_replace(*_args: object) -> None:
        raise OSError("replacement unavailable")
    monkeypatch.setattr(document_io.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement unavailable"):
        write(state, "replacement\r\n")
    assert state.read_bytes() == b"original\r\n"
    assert state.stat().st_mode & 0o777 == 0o640
    assert list(tmp_path.iterdir()) == [state]


def reward_fixture(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    registry, state, root = fixture(tmp_path)
    index = root / "goals" / GOAL / "runs" / "index.jsonl"
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps({
        "generated_at": "2026-09-05T00:00:00Z", "json_path": "run.json",
        "markdown_path": "run.md", "classification": "continue",
    }) + "\n", encoding="utf-8")
    reward = {"recorded_at": "2026-09-05T00:00:01Z", "decision": "continue",
              "reward": "positive", "reason_summary": "Review accepted."}
    return registry, state, index, reward


def test_reward_summary_cannot_inject_a_canonical_todo(tmp_path: Path) -> None:
    from loopx.feedback import append_human_reward
    from loopx.control_plane.coordination.runtime_shadow_writer_adapter import ActiveStateAuthorityMutationError

    registry, state, index, reward = reward_fixture(tmp_path)
    # Insert before the first recognized Todo source, rather than after it.
    state.write_text("---\nhandoff_mode: legacy\n---\n\n## Progress Ledger\n\n## Agent Todo\n", encoding="utf-8")
    reward["reason_summary"] = "Review.\n## Agent Todo\n- [ ] Injected canonical task."
    before = state.read_bytes(), index.read_bytes()
    with pytest.raises(ActiveStateAuthorityMutationError):
        append_human_reward(registry_path=registry, runtime_root_override=None,
            goal_id=GOAL, run_generated_at=None, reward=reward,
            actor_kind="owner",
            write_active_state_summary=True)
    assert (state.read_bytes(), index.read_bytes()) == before


def test_reward_rebases_its_owned_paragraph_after_a_concurrent_todo_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx import feedback

    registry, state, _index, reward = reward_fixture(tmp_path)
    plan = feedback.plan_active_state_update
    def plan_then_edit(**kwargs: object):
        result = plan(**kwargs)
        state.write_text(state.read_text().replace("## Agent Todo\n", "## Agent Todo\n\n- [ ] Concurrent task.\n"))
        return result
    monkeypatch.setattr(feedback, "plan_active_state_update", plan_then_edit)
    feedback.append_human_reward(registry_path=registry, runtime_root_override=None,
        goal_id=GOAL, run_generated_at=None, reward=reward,
        actor_kind="owner",
        write_active_state_summary=True)
    assert "Concurrent task." in state.read_text()
    assert "Review accepted." in state.read_text()


@pytest.mark.parametrize("preserve", [False, True])
def test_force_bootstrap_cannot_erase_an_active_shadow_binding(tmp_path: Path, preserve: bool) -> None:
    from loopx.bootstrap import bootstrap_project

    registry, state, root = fixture(tmp_path)
    value = json.loads(registry.read_text())
    value["goals"][0]["coordination"]["runtime_shadow"] = {
        "enabled": True, "schema_version": "loopx_coordination_runtime_shadow_config_v0", "provider": "file_v0",
    }
    registry.write_text(json.dumps(value))
    before = registry.read_bytes(), state.read_bytes()
    with pytest.raises(RuntimeError, match="shadow"):
        bootstrap_project(project=tmp_path, registry_path=registry, runtime_root=root,
            goal_id=GOAL, objective="Rebuild safely.", domain="test", role="primary",
            parent_goal_id=None, state_file=state, goal_doc=None, adapter_kind="generic_project_goal_v0",
            adapter_status="connected", next_probe=None, spawn_allowed=False, max_children=0,
            allowed_domains=[], write_scope=[],
            force=True, preserve_todos=preserve, dry_run=False, sync_global=False)
    assert (registry.read_bytes(), state.read_bytes()) == before


def cli(registry: Path, *args: str) -> dict:
    result = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry), "--format", "json", *args],
        cwd=REPO, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("phase", ["before", "after"])
def test_real_process_kill_around_state_replace_preserves_complete_bytes(tmp_path: Path, phase: str) -> None:
    state = tmp_path / "state.md"
    state.write_bytes(b"original\r\n")
    code = """
import sys
from pathlib import Path
from loopx.control_plane.runtime import document_io as editing
original = editing.os.replace
def replace(source, target):
    if sys.argv[2] == 'after': original(source, target)
    print('replace-cut', flush=True)
    sys.stdin.readline()
    if sys.argv[2] == 'before': original(source, target)
editing.os.replace = replace
editing.atomic_write_state_text(Path(sys.argv[1]), 'replacement\\r\\n')
"""
    child = subprocess.Popen([sys.executable, "-c", code, str(state), phase], cwd=REPO,
        text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "replace-cut"
        child.kill()
        child.communicate(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)
    assert state.read_bytes() == (b"original\r\n" if phase == "before" else b"replacement\r\n")


def test_prose_only_reward_remains_allowed_under_a_legacy_fence(tmp_path: Path) -> None:
    from loopx.feedback import append_human_reward
    registry, state, _index, reward = reward_fixture(tmp_path)
    root = tmp_path / "runtime"
    fence = legacy_coordination_writer_fence_path(runtime_root=root, goal_id=GOAL)
    fence.parent.mkdir(parents=True)
    # Prose never reads this authority marker, even if invalid; its projection
    # comparison proves that no Todo/lease field is changed.
    fence.write_text("{invalid", encoding="utf-8")
    result = append_human_reward(registry_path=registry, runtime_root_override=None,
        goal_id=GOAL, run_generated_at=None, reward=reward, actor_kind="owner",
        write_active_state_summary=True)
    assert result["appended"] is True
    assert "Review accepted." in state.read_text()
    assert not (root / "authority-shadow").exists()


def test_prose_guard_ignores_resume_evaluation_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx.control_plane.coordination.runtime_shadow_writer_adapter import (
        require_prose_state_write_allowed,
    )

    registry, state, root = fixture(tmp_path)
    seed_historical_todo(state,
        text="Resume the bounded inspection.",
        status="deferred",
        resume_when="resume_at:2099-01-01T00:00:00Z",
    )
    original = state.read_text(encoding="utf-8")
    planned = original + "\nA bounded narrative observation.\n"

    from loopx.control_plane.coordination.local_authority_shadow_adapter import (
        todo_partition_projector,
    )
    from loopx.control_plane.coordination.local_authority_shadow_projection import (
        partition_comparison_view,
    )

    timestamps = ("2026-09-21T00:00:00Z", "2026-09-21T00:00:01Z")
    ticks = iter(timestamps * 2)

    def ticking_projector(*args, **kwargs):
        project = todo_partition_projector(*args, **kwargs)

        def project_at_distinct_time(text):
            projection = project(text)
            projection["todos"][0]["resume_condition"]["evaluated_at"] = next(ticks)
            return projection

        return project_at_distinct_time

    monkeypatch.setattr(
        "loopx.control_plane.coordination.authority_source_capture.todo_partition_projector",
        ticking_projector,
    )
    goal = json.loads(registry.read_text(encoding="utf-8"))["goals"][0]
    projector = ticking_projector(goal, state_path=state)
    before, after = projector(original), projector(planned)
    assert before != after
    assert before["todos"][0]["resume_condition"]["evaluated_at"] == timestamps[0]
    assert after["todos"][0]["resume_condition"]["evaluated_at"] == timestamps[1]
    assert partition_comparison_view(before) == partition_comparison_view(after)
    # Prove the raw projections differ ONLY in the read-time clock.
    after["todos"][0]["resume_condition"]["evaluated_at"] = timestamps[0]
    assert before == after

    # The real guard gets the same forced one-second difference. Restoring
    # its old raw comparison must fail deterministically, without sleeping.
    require_prose_state_write_allowed(
        registry_path=registry,
        runtime_root=root,
        goal_id=GOAL,
        state_path=state,
        original_text=original,
        planned_text=planned,
    )


@pytest.mark.parametrize("original_value,planned_value", [(True, 1), (False, 0)])
def test_prose_guard_preserves_json_scalar_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    original_value: bool, planned_value: int,
) -> None:
    """A full source projection must retain JSON types even if Python equates them."""
    from loopx.control_plane.coordination import authority_source_capture as adapter
    from loopx.control_plane.coordination.local_authority_shadow_projection import (
        todo_partition_projection,
    )
    from loopx.control_plane.coordination.runtime_shadow_writer_adapter import (
        ActiveStateAuthorityMutationError, require_prose_state_write_allowed,
    )

    registry, state, root = fixture(tmp_path)
    seed_historical_todo(state, text="Retain result facts.")
    original = state.read_text(encoding="utf-8")
    planned = original + "\nA narrative observation.\n"
    real_projector = adapter.todo_partition_projector

    def source_projector(*args, **kwargs):
        project = real_projector(*args, **kwargs)

        def project_result(text):
            projection = project(text)
            # Exercise the complete-record adapter contract through the real TS
            # assembler. This injected result fact models an adapter output,
            # not an assertion that the current Markdown codec emits this shape.
            projection["todos"][0]["completion_result"] = {
                "facts": [{"value": original_value if text == original else planned_value}],
            }
            return todo_partition_projection(**projection)

        return project_result

    monkeypatch.setattr(adapter, "todo_partition_projector", source_projector)
    with pytest.raises(ActiveStateAuthorityMutationError, match="would change canonical"):
        require_prose_state_write_allowed(
            registry_path=registry, runtime_root=root, goal_id=GOAL,
            state_path=state, original_text=original, planned_text=planned,
        )
    assert state.read_text(encoding="utf-8") == original


def test_prose_only_reward_holds_before_index_append_during_maintenance(tmp_path: Path) -> None:
    from loopx.feedback import append_human_reward
    from loopx.control_plane.coordination.shadow_management import ShadowManagementError, shadow_management_state_path
    registry, state, index, reward = reward_fixture(tmp_path)
    path = shadow_management_state_path(tmp_path / "runtime", GOAL)
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    before = state.read_bytes(), index.read_bytes()
    with pytest.raises(ShadowManagementError):
        append_human_reward(registry_path=registry, runtime_root_override=None,
            goal_id=GOAL, run_generated_at=None, reward=reward, actor_kind="owner",
            write_active_state_summary=True)
    assert (state.read_bytes(), index.read_bytes()) == before


def test_prose_guard_rejects_a_handoff_change(tmp_path: Path) -> None:
    from loopx.control_plane.coordination.runtime_shadow_writer_adapter import (
        ActiveStateAuthorityMutationError, require_prose_state_write_allowed,
    )
    registry, state, root = fixture(tmp_path)
    original = state.read_text()
    with pytest.raises(ActiveStateAuthorityMutationError, match="would change canonical"):
        require_prose_state_write_allowed(
            registry_path=registry, runtime_root=root, goal_id=GOAL, state_path=state,
            original_text=original, planned_text=original.replace("handoff_mode: legacy", "handoff_mode: hard_lease"),
        )
    assert state.read_text() == original


@pytest.mark.parametrize("maintenance", [False, True])
def test_prose_projection_failure_preserves_source_and_reward_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, maintenance: bool,
) -> None:
    from loopx.feedback import append_human_reward
    from loopx.control_plane.coordination import authority_source_capture as source
    from loopx.control_plane.coordination.runtime_shadow_writer_adapter import ActiveStateAuthorityMutationError
    from loopx.control_plane.coordination.shadow_management import ShadowManagementError, shadow_management_state_path

    registry, state, index, reward = reward_fixture(tmp_path)
    called = []

    def unavailable(*_args, **_kwargs):
        called.append(True)
        raise ValueError("source projection unavailable")

    monkeypatch.setattr(source, "todo_partition_projector", unavailable)
    if maintenance:
        path = shadow_management_state_path(tmp_path / "runtime", GOAL)
        path.parent.mkdir(parents=True)
        path.write_text("{}")
    before = state.read_bytes(), index.read_bytes(), registry.read_bytes()
    error = ShadowManagementError if maintenance else ActiveStateAuthorityMutationError
    with pytest.raises(error):
        append_human_reward(registry_path=registry, runtime_root_override=None,
            goal_id=GOAL, run_generated_at=None, reward=reward, actor_kind="owner",
            write_active_state_summary=True)
    assert called == ([] if maintenance else [True])
    assert (state.read_bytes(), index.read_bytes(), registry.read_bytes()) == before


def test_configuration_and_prose_guard_load_without_capture_modules(tmp_path, monkeypatch):
    from test_cold_source_import_cli import workspace

    _, state, _, _, runtime, receiver, env = workspace(tmp_path, monkeypatch)
    for name in ("runtime_shadow.py", "runtime_shadow_writer_adapter.py",
                 "local_authority_shadow_adapter.py", "local_authority_shadow_outbox.py"):
        (receiver / "loopx/control_plane/coordination" / name).unlink()
    registry = tmp_path / "project/.loopx/registry.json"
    child = subprocess.run([sys.executable, "-c", """
import sys,loopx
from pathlib import Path
import loopx.presentation.configuration_api
from loopx.feedback import require_prose_state_write_allowed
from loopx.control_plane.coordination.legacy_writer_fence import ActiveStateAuthorityMutationError
registry,runtime,state,package = map(Path,sys.argv[1:])
assert Path(loopx.__file__) == package / 'loopx/__init__.py'
original = state.read_text()
args = dict(registry_path=registry,runtime_root=runtime,goal_id='cold',state_path=state,original_text=original)
require_prose_state_write_allowed(**args,planned_text=original+'\\nA narrative observation.\\n')
try:
    require_prose_state_write_allowed(**args,planned_text=original.replace('handoff_mode: legacy','handoff_mode: hard_lease'))
except ActiveStateAuthorityMutationError:
    pass
else:
    raise AssertionError('handoff mutation was accepted')
assert state.read_text() == original
""", str(registry), str(runtime), str(state), str(receiver)], cwd=tmp_path,
        env=env, capture_output=True, text=True, timeout=60)
    assert child.returncode == 0, child.stdout + child.stderr


@pytest.mark.parametrize("writer", ["todo", "prose"])
def test_override_root_cannot_bypass_registry_source_maintenance(tmp_path: Path, writer: str) -> None:
    from loopx.control_plane.coordination.shadow_management import ShadowManagementError, shadow_management_state_path
    from loopx.feedback import append_human_reward
    registry, state, index, reward = reward_fixture(tmp_path)
    root = tmp_path / "runtime"
    override = tmp_path / "override"
    override_index = override / "goals" / GOAL / "runs" / "index.jsonl"
    override_index.parent.mkdir(parents=True)
    override_index.write_bytes(index.read_bytes())
    management = shadow_management_state_path(root, GOAL)
    management.parent.mkdir(parents=True)
    management.write_text("{}")
    before = state.read_bytes()
    with pytest.raises(LegacyCoordinationWriterFenced if writer == "todo" else ShadowManagementError):
        if writer == "todo":
            add_goal_todo(
                registry_path=registry, goal_id=GOAL, runtime_root_arg=str(override),
                role="agent", text="Cannot bypass source maintenance.",
            )
        else:
            append_human_reward(registry_path=registry, runtime_root_override=str(override),
                goal_id=GOAL, run_generated_at=None, reward=reward, actor_kind="owner",
                write_active_state_summary=True)
    assert state.read_bytes() == before


def test_override_root_keeps_prose_writable_with_an_active_source_binding(tmp_path: Path) -> None:
    from loopx.feedback import append_human_reward
    registry, state, index, reward = reward_fixture(tmp_path)
    value = json.loads(registry.read_text())
    value["goals"][0]["coordination"]["runtime_shadow"] = {
        "enabled": True, "schema_version": "loopx_coordination_runtime_shadow_config_v0", "provider": "file_v0"}
    registry.write_text(json.dumps(value))
    cli(registry, "coordination-shadow", "bootstrap", "--goal-id", GOAL, "--execute")
    override = tmp_path / "override"
    override_index = override / "goals" / GOAL / "runs" / "index.jsonl"
    override_index.parent.mkdir(parents=True)
    override_index.write_bytes(index.read_bytes())
    refreshed = append_human_reward(registry_path=registry, runtime_root_override=str(override),
        goal_id=GOAL, run_generated_at=None, reward=reward, actor_kind="owner",
        write_active_state_summary=True)
    assert refreshed["appended"] is True
    assert "Review accepted." in state.read_text()
    assert not (override / "authority-shadow" / "outbox" / GOAL).exists()


def test_migration_refuses_overwriting_a_managed_target_before_copy(tmp_path: Path) -> None:
    from loopx.state_migration import migrate_legacy_state
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    source_registry, source_state, source_root = fixture(source)
    target_registry, target_state, target_root = fixture(target)
    target_state.write_text(target_state.read_text() + "\nProtected target history.\n")
    value = json.loads(target_registry.read_text())
    value["goals"][0]["coordination"]["runtime_shadow"] = {"enabled": True}
    target_registry.write_text(json.dumps(value))
    before = target_state.read_bytes(), target_registry.read_bytes(), source_state.read_bytes()
    with pytest.raises(RuntimeError, match="shadow source"):
        migrate_legacy_state(legacy_registry_path=source_registry, target_registry_path=target_registry,
            legacy_runtime_root=source_root, target_runtime_root=target_root, goal_ids=[GOAL],
            goal_id_map={}, path_map={str(source): str(target)}, copy_active_state=True,
            copy_runtime=True, execute=True)
    assert (target_state.read_bytes(), target_registry.read_bytes(), source_state.read_bytes()) == before


def test_registry_missing_state_reconstruction_respects_maintenance(tmp_path: Path) -> None:
    from loopx.control_plane.projects.registry import register_project_goal
    from loopx.control_plane.coordination.shadow_management import ShadowManagementError, shadow_management_state_path
    registry = tmp_path / "registry.json"
    root = tmp_path / "runtime"
    args = dict(registry_path=registry, runtime_root=root, project_id="project-a", project_kind="work",
        knowledge_root=tmp_path, goal_id=GOAL, objective="Keep the source intact.", non_goals=[],
        acceptance=["State exists."], unknowns=[], next_effect="Inspect state.", stop_condition="Done.",
        repository_bindings=[], external_locator_bindings=[])
    created = register_project_goal(**args)
    state = Path(created["state_file"])
    state.unlink()
    before = registry.read_bytes()
    management = shadow_management_state_path(root, GOAL)
    management.parent.mkdir(parents=True, exist_ok=True)
    management.write_text("{}")
    with pytest.raises(ShadowManagementError):
        register_project_goal(**args)
    assert not state.exists()
    assert registry.read_bytes() == before


def test_public_refresh_step_does_not_write_owned_paragraph(tmp_path: Path) -> None:
    registry, state, root = fixture(tmp_path)
    seed_historical_todo(state, text="Inspect the bounded result.", task_class="advancement_task", claimed_by="agent-a")
    before = state.read_bytes()
    result = cli(registry, "refresh-state", "--goal-id", GOAL, "--agent-id", "agent-a",
        "--next-action", "Compare the current evidence.", "--no-global-sync")
    assert result["ok"] is True
    assert result["recommended_action_resolution"]["recommended_action_source"] == "agent_lane_step"
    assert state.read_bytes() == before
    assert "Compare the current evidence." not in state.read_text()
    assert not (root / "authority-shadow").exists()


def test_retained_transaction_rechecks_fence_after_lock_acquisition(tmp_path, monkeypatch):
    """Bootstrap/registration still use this transaction; normal Todo writing is retired."""
    from contextlib import contextmanager
    from loopx.control_plane.coordination import legacy_writer_fence as owner
    from test_shadow_writer_variant_e2e import fence_command
    from shadow_e2e_fixture import ShadowWorkspace

    registry, state, runtime = fixture(tmp_path)
    ws = ShadowWorkspace(registry, runtime, state, goal=GOAL)
    result = subprocess.run(fence_command(ws), cwd=REPO, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["status"] == "applied"
    fence = owner.legacy_coordination_writer_fence_path(runtime_root=runtime, goal_id=GOAL)
    fence_bytes = fence.read_bytes()
    fence.unlink()
    lock = owner.exclusive_cross_runtime_file_lock
    acquired = []

    @contextmanager
    def raced_lock(path, *args, **kwargs):
        # Scheduling seam only: another holder published a real fence before S.
        if path == state:
            fence.write_bytes(fence_bytes)
        with lock(path, *args, **kwargs) as held:
            acquired.append(path)
            yield held

    monkeypatch.setattr(owner, "exclusive_cross_runtime_file_lock", raced_lock)
    before = state.read_bytes()
    with pytest.raises(owner.LegacyCoordinationWriterFenced) as error:
        with owner.legacy_todo_write_transaction(registry, GOAL, state, None, "retained-recovery", False):
            pass  # The owning guard must raise before the transaction body.
    assert error.value.code == "legacy_coordination_writer_fenced"
    assert acquired[-1] == state
    assert state.read_bytes() == before


def test_retained_transaction_rechecks_source_binding_after_lock_acquisition(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from loopx.control_plane.coordination import legacy_writer_fence as owner
    from loopx.control_plane.coordination.shadow_management import ShadowManagementError
    from shadow_e2e_fixture import workspace

    ws = workspace(tmp_path)
    # Bootstrap owns this real source path; remove frontmatter to test registry ownership.
    ws.state.write_text(ws.state.read_text().replace(f"goal_id: {ws.goal}\n", ""))
    registry = json.loads(ws.registry.read_text())
    protected = registry["goals"][0]["state_file"]
    registry["goals"][0]["state_file"] = "elsewhere.md"
    ws.registry.write_text(json.dumps(registry))
    lock = owner.exclusive_cross_runtime_file_lock
    acquired = []

    @contextmanager
    def raced_lock(path, *args, **kwargs):
        if path == ws.state:
            registry["goals"][0]["state_file"] = protected
            ws.registry.write_text(json.dumps(registry))
        with lock(path, *args, **kwargs) as held:
            acquired.append(path)
            yield held

    monkeypatch.setattr(owner, "exclusive_cross_runtime_file_lock", raced_lock)
    before = ws.state.read_bytes()
    with pytest.raises(ShadowManagementError) as error:
        with owner.legacy_todo_write_transaction(ws.registry, "other-goal", ws.state, None, "retained-recovery", False):
            pass  # The owning guard must raise before the transaction body.
    assert error.value.code == "shadow_source_goal_mismatch"
    assert acquired[-1] == ws.state
    assert ws.state.read_bytes() == before


def test_retained_transaction_rejects_an_unbound_state_path(tmp_path):
    from loopx.control_plane.coordination.shadow_management import ShadowManagementError
    from shadow_e2e_fixture import workspace

    ws = workspace(tmp_path)
    alternate = tmp_path / "alternate.md"
    original = ws.state.read_bytes()
    alternate.write_bytes(original)
    with pytest.raises(ShadowManagementError) as error:
        with legacy_todo_write_transaction(ws.registry, ws.goal, alternate, None, "retained-recovery", False):
            pass
    assert error.value.code == "shadow_source_state_path_mismatch"
    assert ws.state.read_bytes() == alternate.read_bytes() == original
