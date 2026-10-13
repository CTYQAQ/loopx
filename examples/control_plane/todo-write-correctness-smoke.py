#!/usr/bin/env python3
"""Qualify canonical Todo preview, revision and claim boundaries through the CLI."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.coordination.local_authority import (  # noqa: E402
    read_canonical_todos_if_promoted,
)
from loopx.control_plane.testing.canary_harness import write_fixture_registry  # noqa: E402
from tests.control_plane.canonical_authority_fixture import (  # noqa: E402
    initialize_canonical_registry_fixture,
)

GOAL_ID = "todo-write-correctness-goal"
AGENT_ID = "codex-product-capability"
TODO_TEXT = "Preview a canonical Todo before committing it."


def write_fixture(root: Path, *, provider: str = "file") -> tuple[Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Todo Write Correctness Fixture\n\n"
        "## User Todo / Owner Review Reading Queue\n\n"
        "## Agent Todo\n\n"
        "- [ ] Existing todo for update preview.\n"
        "  <!-- loopx:todo todo_id=todo_existing status=open task_class=advancement_task -->\n",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        domain="todo-write-correctness-fixture",
        adapter_kind="generic_project_goal_v0",
        registered_agents=("codex-main-control", AGENT_ID),
        quota_allowed_slots=None,
    )
    initialize_canonical_registry_fixture(registry_path, provider=provider)
    return registry_path, state_file, project



def run_cli(registry: Path, *args: str, succeeds: bool = True) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--registry", str(registry),
         "--format", "json", "todo", *args, "--goal-id", GOAL_ID],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=45,
    )
    assert (result.returncode == 0) is succeeds, result.stdout + result.stderr
    return json.loads(result.stdout)


def main(*, provider: str = "file") -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-todo-write-correctness-") as tmp:
        root = Path(tmp)
        registry, display, _ = write_fixture(root, provider=provider)

        def snapshot():
            return read_canonical_todos_if_promoted(
                runtime_root=root / "runtime", goal_id=GOAL_ID, include_leases=True
            )

        before, original_display = snapshot(), display.read_bytes()
        create = ("add", "--role", "agent", "--text", TODO_TEXT,
                  "--claimed-by", AGENT_ID,
                  "--operation-id", "preview-create")
        preview = run_cli(registry, *create, "--dry-run")
        repeat = run_cli(registry, *create, "--dry-run")
        for result in (preview, repeat):
            assert result["dry_run"] is True and result["changed"] is False, result
            assert result["provider_status"] == "planned", result
            assert result["source_authority"] == f"{provider}_v0", result
            assert result["decision_read_from_provider"] is True, result
            assert result["legacy_fallback_used"] is False, result
            assert "local_state_write_correctness" not in result, result
        assert repeat["todo_id"] == preview["todo_id"]
        assert snapshot() == before and display.read_bytes() == original_display

        # Preview must not consume the operation id or fabricate a commit receipt.
        accepted = run_cli(registry, *create)
        assert accepted["changed"] is True and accepted["provider_status"] == "applied", accepted
        assert accepted["todo_id"] == preview["todo_id"]
        after_create = snapshot()
        assert len(after_create["todos"]) == len(before["todos"]) + 1
        created = next(t for t in after_create["todos"] if t["todo_id"] == accepted["todo_id"])
        assert created["text"] == TODO_TEXT and created["claimed_by"] == AGENT_ID
        assert run_cli(registry, *create)["provider_status"] == "replayed"
        assert snapshot() == after_create

        update = ("update", "--todo-id", accepted["todo_id"], "--agent-id", AGENT_ID,
                  "--note", "Reviewed canonical edit", "--update-operation-id", "preview-update")
        revision = after_create["provider_revision"]
        reviewed = (*update, "--update-expected-provider-revision", revision)
        before_update_display = display.read_bytes()
        planned = run_cli(registry, *reviewed, "--dry-run")
        assert planned["provider_status"] == "planned" and planned["changed"] is False, planned
        assert planned["todo_update"]["todo"]["note"] == "Reviewed canonical edit", planned
        assert snapshot() == after_create and display.read_bytes() == before_update_display

        # A concurrent accepted operation invalidates the reviewed revision.
        run_cli(registry, "add", "--role", "agent", "--text", "Concurrent independent task",
                "--operation-id", "concurrent-create")
        concurrent, concurrent_display = snapshot(), display.read_bytes()
        for suffix in (("--dry-run",), ()):
            rejected = run_cli(registry, *reviewed, *suffix, succeeds=False)
            assert rejected["error_code"] == "provider_revision_mismatch", rejected
            assert snapshot() == concurrent and display.read_bytes() == concurrent_display

        # Current revision is insufficient authority to edit another actor's claim.
        foreign = ("update", "--todo-id", accepted["todo_id"], "--agent-id", "codex-main-control",
                   "--note", "Unauthorized overwrite")
        for suffix in (("--dry-run",), ()):
            rejected = run_cli(registry, *foreign, *suffix, succeeds=False)
            assert rejected["error_code"] == "update_owner_mismatch", rejected
            assert snapshot() == concurrent and display.read_bytes() == concurrent_display

        # Re-inspect and retry the original uncommitted intent at the fresh revision.
        fresh = (*update, "--update-expected-provider-revision", concurrent["provider_revision"])
        assert run_cli(registry, *fresh, "--dry-run")["provider_status"] == "planned"
        assert snapshot() == concurrent
        committed = run_cli(registry, *fresh)
        assert committed["provider_status"] == "applied" and committed["changed"] is True, committed
        final, final_display = snapshot(), display.read_bytes()
        updated = next(t for t in final["todos"] if t["todo_id"] == accepted["todo_id"])
        assert updated["note"] == "Reviewed canonical edit"
        assert updated["claimed_by"] == AGENT_ID
        replay = run_cli(registry, *fresh)
        assert replay["provider_status"] == "replayed" and replay["changed"] is False, replay
        assert snapshot() == final and display.read_bytes() == final_display

    print("todo-write-correctness-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
