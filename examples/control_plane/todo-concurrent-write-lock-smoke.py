#!/usr/bin/env python3
"""Canonical writers commit independently of a contended display lock."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.file_lock import exclusive_cross_runtime_file_lock  # noqa: E402
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted  # noqa: E402
from tests.control_plane.canonical_authority_fixture import initialize_canonical_registry_fixture  # noqa: E402
from loopx.status import parse_active_state_todos  # noqa: E402


GOAL_ID = "todo-concurrent-write-lock-goal"
CHILD_ADD_TODO = "Child add preserves another canonical task while display delivery waits."
PARENT_ADD_TODO = "Parent task commits while child add waits for display delivery."
UPDATE_TARGET_TODO = "Update target todo should keep unrelated concurrent additions."
PARENT_UPDATE_TODO = "Parent task commits while child update waits for display delivery."


def write_fixture(root: Path, *, provider: str = "file") -> tuple[Path, Path]:
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
        "# Active Goal State\n\n"
        "## Objective\n\n"
        "Exercise todo writer locking.\n\n"
        "## Agent Todo\n\n"
        "- [ ] Initial visible work item.\n",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "todo-concurrency-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md",
                        "adapter": {"kind": "generic_project_goal_v0", "status": "connected"},
                        "authority_sources": [],
                        "coordination": {
                            "registered_agents": ["codex-main-control", "codex-side-bypass"],
                            "agent_model": "peer_v1",
                        },
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    initialize_canonical_registry_fixture(registry_path, provider=provider)
    return registry_path, state_file


def cli_args(registry_path: Path, *args: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry_path),
        "--format",
        "json",
        *args,
    ]


def run_cli(registry_path: Path, *args: str) -> dict[str, Any]:
    result = subprocess.run(
        cli_args(registry_path, *args),
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def start_cli(registry_path: Path, *args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        cli_args(registry_path, *args),
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def wait_json(process: subprocess.Popen[str]) -> dict[str, Any]:
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, stderr
    return json.loads(stdout)


def wait_for_commit(registry: Path, process: subprocess.Popen[str], text: str,
                    *, status: str = "open") -> None:
    runtime = Path(json.loads(registry.read_text())["common_runtime_root"])
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        readback = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
        assert readback and readback["legacy_fallback_used"] is False, readback
        if any(row["text"] == text and row["status"] == status for row in readback["todos"]):
            # The provider committed, but display delivery must wait for the
            # shared cross-runtime lock, not overwrite the parent's narrative.
            assert process.poll() is None
            return
        assert process.poll() is None, process.communicate()
        time.sleep(0.05)
    raise AssertionError(f"canonical commit did not become visible: {text}")


def agent_items(state_file: Path) -> list[dict[str, Any]]:
    parsed = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
    return parsed["agent_todos"]["items"]


def find_item(state_file: Path, text: str) -> dict[str, Any]:
    return next(item for item in agent_items(state_file) if item.get("text") == text)


def main(*, provider: str = "file") -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-todo-lock-smoke-") as tmp:
        registry_path, state_file = write_fixture(Path(tmp), provider=provider)

        with exclusive_cross_runtime_file_lock(state_file, operation="fixture_narrative_edit"):
            add_process = start_cli(
                registry_path,
                "todo",
                "add",
                "--goal-id",
                GOAL_ID,
                "--role",
                "agent",
                "--text",
                CHILD_ADD_TODO,
            )
            wait_for_commit(registry_path, add_process, CHILD_ADD_TODO)
            parent_process = start_cli(
                registry_path, "todo", "add", "--goal-id", GOAL_ID,
                "--role", "agent", "--text", PARENT_ADD_TODO,
            )
            wait_for_commit(registry_path, parent_process, PARENT_ADD_TODO)
            state_file.write_text(state_file.read_text().replace(
                "Exercise todo writer locking.", "Narrative edited while both displays wait."))
        parent_result = wait_json(parent_process)
        assert parent_result["added"] is True, parent_result
        add_result = wait_json(add_process)
        assert add_result["added"] is True, add_result
        assert "Narrative edited while both displays wait." in state_file.read_text()
        assert find_item(state_file, CHILD_ADD_TODO), state_file.read_text(encoding="utf-8")
        assert find_item(state_file, PARENT_ADD_TODO), state_file.read_text(encoding="utf-8")

        target = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            UPDATE_TARGET_TODO,
        )
        target_id = target["todo_id"]

        with exclusive_cross_runtime_file_lock(state_file, operation="fixture_narrative_edit"):
            update_process = start_cli(
                registry_path,
                "todo",
                "update",
                "--goal-id",
                GOAL_ID,
                "--todo-id",
                target_id,
                "--agent-id",
                "codex-side-bypass",
                "--status",
                "blocked",
                "--reason",
                "waiting-on-concurrent-state",
                "--claimed-by",
                "codex-side-bypass",
            )
            wait_for_commit(registry_path, update_process, UPDATE_TARGET_TODO, status="blocked")
            parent_process = start_cli(
                registry_path, "todo", "add", "--goal-id", GOAL_ID,
                "--role", "agent", "--text", PARENT_UPDATE_TODO,
            )
            wait_for_commit(registry_path, parent_process, PARENT_UPDATE_TODO)
            state_file.write_text(state_file.read_text().replace(
                "Narrative edited while both displays wait.", "Narrative preserved across update and add."))
        parent_result = wait_json(parent_process)
        assert parent_result["added"] is True, parent_result
        update_result = wait_json(update_process)
        assert update_result["changed"] is True, update_result
        assert "Narrative preserved across update and add." in state_file.read_text()
        updated_target = find_item(state_file, UPDATE_TARGET_TODO)
        assert updated_target["status"] == "blocked", updated_target
        assert updated_target["reason"] == "waiting-on-concurrent-state", updated_target
        assert updated_target["claimed_by"] == "codex-side-bypass", updated_target
        assert find_item(state_file, PARENT_UPDATE_TODO), state_file.read_text(encoding="utf-8")

        final_read = read_canonical_todos_if_promoted(
            runtime_root=Path(json.loads(registry_path.read_text())["common_runtime_root"]),
            goal_id=GOAL_ID,
        )
        assert final_read and final_read["legacy_fallback_used"] is False, final_read
        projected = {item["todo_id"]: (item["text"], item["status"], item.get("claimed_by"))
                     for item in agent_items(state_file)}
        native = {item["todo_id"]: (item["text"], item["status"], item.get("claimed_by"))
                  for item in final_read["todos"]}
        assert len(native) == 5, native
        assert native == projected, (native, projected)

    print("todo-concurrent-write-lock-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
