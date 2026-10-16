"""Planned benchmark entry can lock an owner-confirmed acceptance gate.

The real bootstrap, Todo, lease and ``goal-acceptance`` owners are exercised;
only the model is replaced. Feature-off parity keeps the previous closure rules.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from canonical_authority_fixture import isolate_sqlite_runtime

from benchmark.runtime.acceptance import (
    ACCEPTANCE_DOCUMENT_ENV,
    apply_planned_acceptance,
    reconfirm_planned_acceptance,
)
from benchmark.runtime.codex import Execution
from benchmark.runtime.planning import render_planning_prompt
from benchmark.runtime.worker import loopx_command

REPO_ROOT = Path(__file__).resolve().parents[2]
GOAL_ID, AGENT_ID = "benchmark-goal", "benchmark-agent"


def test_acceptance_contract_is_an_explicit_planned_entry_opt_in():
    assert Execution().acceptance_contract == "off"
    assert Execution(mode="heartbeat", acceptance_contract="planned").task_entry == "loopx-planned"
    for overrides in (dict(mode="heartbeat", task_entry="seeded-todo"), dict(mode="plain"),
                      dict(mode="native-goal")):
        with pytest.raises(ValueError, match="loopx-planned"):
            Execution(**overrides, acceptance_contract="planned")
    with pytest.raises(ValueError, match="acceptance contract"):
        Execution(acceptance_contract="required")


def test_planning_prompt_feature_off_parity_and_opt_in_guidance():
    packet = {"schema_version": "loopx_task_planning_v0", "input_digest": "d"}
    off = render_planning_prompt(packet, {"UNRELATED": "1"}, execution_reserve_seconds=150)
    assert off == render_planning_prompt(packet, {}, execution_reserve_seconds=150)
    assert "acceptance document" not in off
    # An explicit off keeps the original prompt even when a stale locator leaks in.
    legacy = render_planning_prompt(
        packet, {"LOOPX_ACCEPTANCE_CONTRACT": "off", ACCEPTANCE_DOCUMENT_ENV: "/control/acceptance.json"},
        execution_reserve_seconds=150)
    assert legacy == off
    with pytest.raises(ValueError, match="requires LOOPX_ACCEPTANCE_DOCUMENT"):
        render_planning_prompt(packet, {"LOOPX_ACCEPTANCE_CONTRACT": "planned"}, execution_reserve_seconds=150)
    on = render_planning_prompt(
        packet, {"LOOPX_ACCEPTANCE_CONTRACT": "planned", ACCEPTANCE_DOCUMENT_ENV: "/control/acceptance.json"},
        execution_reserve_seconds=150)
    assert on.endswith(off.removeprefix("$loopx\n\n")) and on.startswith("$loopx\n\n")
    for required in ("/control/acceptance.json", '"selected_work"', "must not read",
                     "fails this planning phase", "25 seconds in total", "do not add validation_files"):
        assert required in on


def _bootstrap_goal(tmp_path: Path) -> tuple[dict[str, str], list[str]]:
    """Mirror the Harbor adapter's bootstrap, registration and seeded Todo."""
    project, control, runtime = tmp_path / "project", tmp_path / "control", tmp_path / "runtime"
    project.mkdir()
    control.mkdir()
    (control / "task.md").write_text("# Task\nProduce outputs/result.txt containing ok.\n")
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "LOOPX_PROJECT": str(project),
           "LOOPX_REGISTRY": str(control / "registry.json"), "LOOPX_RUNTIME_ROOT": str(runtime),
           "LOOPX_GOAL_ID": GOAL_ID, "LOOPX_AGENT_ID": AGENT_ID,
           "LOOPX_TASK_DOC": str(control / "task.md"),
           "LOOPX_CLI": str(Path(sys.executable).parent / "loopx"),
           ACCEPTANCE_DOCUMENT_ENV: str(control / "acceptance-phase-001.json")}
    cli = loopx_command(env)

    def run(*args, ok=True):
        proc = subprocess.run(cli + list(args), cwd=project, env=env, text=True, capture_output=True)
        payload = json.loads(proc.stdout)
        assert (proc.returncode == 0) is ok, payload
        return payload

    run("bootstrap", "--project", ".", "--goal-id", GOAL_ID, "--objective", "Complete the task",
        "--goal-doc", str(control / "task.md"), "--adapter-kind", "read_only_project_map_v0",
        "--adapter-status", "connected-read-only", "--write-scope", "**", "--no-global-sync")
    run("configure-goal", "--goal-id", GOAL_ID, "--registered-agent", AGENT_ID,
        "--boundary-authority-scope", "**", "--boundary-authority-source", "harbor-task-workspace",
        "--boundary-authority-decision-id", "trial-workspace", "--execution-replan-after-turns", "6",
        "--agent-work-mode", f"{AGENT_ID}=active", "--execute")
    todo_id = run("todo", "add", "--goal-id", GOAL_ID, "--role", "agent", "--text",
                  "[P0] Deliver outputs/result.txt", "--task-class", "advancement_task",
                  "--action-kind", "benchmark_task", "--claimed-by", AGENT_ID, "--status", "open",
                  "--execute")["todo_id"]
    env["TEST_TODO_ID"] = todo_id
    return env, cli


def _document(todo_id: str) -> dict:
    return {
        "objective": "Deliver outputs/result.txt", "non_goals": ["Reading hidden data"],
        "scope": {"kind": "selected_work", "todo_ids": [todo_id]},
        "criteria": [{"id": "result-present", "description": "result file says ok",
                      "validation_argv": [sys.executable, "-c",
                                          "from pathlib import Path; assert Path('outputs/result.txt').read_text().strip() == 'ok'"],
                      "validation_timeout_seconds": 5}],
        "bindings": [{"todo_id": todo_id, "criterion_ids": ["result-present"]}],
    }


def test_real_goal_holds_selected_todo_until_criteria_pass_and_reconfirms_edits(tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    env, cli = _bootstrap_goal(tmp_path)
    todo_id, project = env["TEST_TODO_ID"], Path(env["LOOPX_PROJECT"])
    document_path = Path(env[ACCEPTANCE_DOCUMENT_ENV])
    goal = ["--goal-id", GOAL_ID]

    def run(*args):
        proc = subprocess.run(cli + list(args), cwd=project, env=env, text=True, capture_output=True)
        return proc.returncode, json.loads(proc.stdout)

    def tasks():
        return [(row["state"], row["applicable"])
                for row in run("goal-acceptance", "inspect", *goal)[1]["goal_acceptance_contract"]["tasks"]]

    planning = {"todo_ids": [todo_id], "input_digest": "digest-1", "status": "ready"}
    with pytest.raises(ValueError, match="did not write"):
        apply_planned_acceptance(env, planning, cli)
    document_path.write_text(json.dumps(_document("todo_elsewhere")))
    with pytest.raises(ValueError, match="outside the planning result"):
        apply_planned_acceptance(env, planning, cli)
    unbound = _document(todo_id)
    unbound["bindings"] = []
    document_path.write_text(json.dumps(unbound))
    with pytest.raises(ValueError, match="binding"):
        apply_planned_acceptance(env, planning, cli)
    bare_pins = _document(todo_id)
    bare_pins["criteria"][0]["validation_files"] = ["outputs/result.txt"]
    document_path.write_text(json.dumps(bare_pins))
    with pytest.raises(ValueError, match="validation_files must be"):
        apply_planned_acceptance(env, planning, cli)
    assert run("goal-acceptance", "inspect", *goal)[1]["goal_acceptance_contract"] == {"enabled": False}

    document_path.write_text(json.dumps(_document(todo_id)))
    applied = apply_planned_acceptance(env, planning, cli)
    assert applied["status"] == "applied" and applied["selected_todo_ids"] == [todo_id]
    assert applied["criterion_ids"] == ["result-present"] and "validation_argv" not in json.dumps(applied)
    assert tasks() == [("ready", True)]
    assert reconfirm_planned_acceptance(env, cli) == {"reconfirmed": [], "revision": 1}

    # The model edits its own selected Todo under a real lease: the association
    # goes stale, and the host re-confirms it without changing any criterion.
    code, lease = run("task-lease", "acquire", *goal, "--todo-id", todo_id, "--owner", AGENT_ID,
                      "--idempotency-key", "exec-1", "--expected-version", "0", "--ttl-seconds", "600")
    assert code == 0 and lease["lease"]["version"] == 1
    binding = ["--todo-id", todo_id, "--agent-id", AGENT_ID,
               "--task-lease-idempotency-key", "exec-1", "--task-lease-expected-version", "1"]
    code, updated = run("todo", "update", *goal, *binding, "--text", "[P0] Deliver outputs/result.txt (ok)", "--execute")
    assert code == 0 and updated["ok"], updated
    assert tasks() == [("stale", True)]
    # The model rewrites its draft to a vacuous check; re-confirmation must keep
    # the host-accepted criterion instead of adopting the draft.
    weakened = _document(todo_id)
    weakened["criteria"][0]["validation_argv"] = [sys.executable, "-c", "pass"]
    document_path.write_text(json.dumps(weakened))
    assert reconfirm_planned_acceptance(env, cli) == {"reconfirmed": [todo_id], "revision": 2}
    assert tasks() == [("ready", True)]
    assert json.loads(document_path.read_text()) == weakened

    code, rejected = run("todo", "complete", *goal, *binding, "--evidence", "local run", "--no-follow-up")
    assert code == 1 and rejected["error_code"] == "goal_acceptance_validation_rejected", rejected
    assert rejected["goal_acceptance_validation_failure"]["criterion_id"] == "result-present"
    assert run("todo", "list", *goal, "--role", "agent")[1]["todos"][0]["status"] == "open"
    (project / "outputs").mkdir()
    (project / "outputs" / "result.txt").write_text("ok\n")
    code, completed = run("todo", "complete", *goal, *binding, "--evidence", "local run", "--no-follow-up")
    assert code == 0 and completed["changed"] is True, completed
    assert run("todo", "list", *goal, "--role", "agent")[1]["todos"][0]["status"] == "done"
    assert reconfirm_planned_acceptance(env, cli)["reconfirmed"] == []


def test_reconfirm_refuses_a_contract_changed_outside_the_host(tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    env, cli = _bootstrap_goal(tmp_path)
    todo_id, document_path = env["TEST_TODO_ID"], Path(env[ACCEPTANCE_DOCUMENT_ENV])
    planning = {"todo_ids": [todo_id], "input_digest": "digest-1", "status": "ready"}
    with pytest.raises(ValueError, match="no host-accepted"):
        reconfirm_planned_acceptance(env, cli)
    document_path.write_text(json.dumps(_document(todo_id)))
    apply_planned_acceptance(env, planning, cli)
    weakened = _document(todo_id)
    weakened["criteria"][0]["validation_argv"] = [sys.executable, "-c", "pass"]
    other = Path(env["LOOPX_PROJECT"]).parent / "control" / "out-of-band.json"
    other.write_text(json.dumps(weakened))
    goal = ["--goal-id", GOAL_ID]
    basis = json.loads(subprocess.run(cli + ["goal-acceptance", "inspect", *goal], cwd=env["LOOPX_PROJECT"],
                                      env=env, text=True, capture_output=True).stdout)
    configured = subprocess.run(cli + ["goal-acceptance", "configure", *goal, "--document", str(other),
                                       "--expected-provider-revision", basis["provider_revision"],
                                       "--operation-id", "out-of-band", "--execute"],
                                cwd=env["LOOPX_PROJECT"], env=env, text=True, capture_output=True)
    assert configured.returncode == 0, configured.stdout
    with pytest.raises(ValueError, match="changed outside the planned host"):
        reconfirm_planned_acceptance(env, cli)


_SYNTHETIC_CODEX = """#!{python}
import json, os, pathlib, sys
argv = sys.argv
body = sys.stdin.read()
packet = json.loads(body.split("Host-supplied planning checkpoint:\\n", 1)[1])
result = {{"input_digest": packet["input_digest"], "status": "ready",
          "todo_ids": packet["runnable_todo_ids"]}}
pathlib.Path(argv[argv.index("--output-last-message") + 1]).write_text(json.dumps(result))
pathlib.Path("planning-body.txt").write_text(body)
if os.environ.get("LOOPX_ACCEPTANCE_DOCUMENT") and os.environ.get("SYNTHETIC_WRITE_DOCUMENT") == "1":
    todo_id = packet["runnable_todo_ids"][0]
    pathlib.Path(os.environ["LOOPX_ACCEPTANCE_DOCUMENT"]).write_text(json.dumps({{
        "objective": "Deliver outputs/result.txt", "non_goals": [],
        "scope": {{"kind": "selected_work", "todo_ids": [todo_id]}},
        "criteria": [{{"id": "result-present", "description": "result file says ok",
                      "validation_argv": [sys.executable, "-c", "from pathlib import Path; assert Path('outputs/result.txt').read_text().strip() == 'ok'"],
                      "validation_timeout_seconds": 5}}],
        "bindings": [{{"todo_id": todo_id, "criterion_ids": ["result-present"]}}]}}))
print(json.dumps({{"type": "thread.started", "thread_id": "synthetic-planning-session"}}))
"""


@pytest.mark.parametrize("contract", ["off", "off-legacy-locator", "planned", "planned-missing-document"])
def test_worker_planning_stage_configures_or_requires_the_document(tmp_path, monkeypatch, contract):
    from benchmark.runtime.worker import run_once

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    env, cli = _bootstrap_goal(tmp_path)
    binary = tmp_path / "codex"
    binary.write_text(_SYNTHETIC_CODEX.format(python=sys.executable))
    binary.chmod(0o755)
    (tmp_path / "skills").mkdir()
    env.update({"CODEX_BIN": str(binary), "LOOPX_CODEX_HOME": str(tmp_path / "home"),
                "LOOPX_WAKE_LOG_DIR": str(tmp_path / "wakes"), "LOOPX_SHARED_SKILLS": str(tmp_path / "skills"),
                "MODEL_NAME": "fixture", "REASONING_EFFORT": "high", "OPENAI_BASE_URL": "http://localhost:8123/v1",
                "OPENAI_API_KEY": "fixture-key", "LOOPX_TASK_ENTRY": "loopx-planned", "LOOPX_TASK_STAGE": "plan",
                "LOOPX_PLANNING_RESULT": str(tmp_path / "control" / "planning-phase-001.json"),
                "LOOPX_PHASE_DEADLINE_EPOCH": str(time.time() + 600),
                "LOOPX_ACCEPTANCE_CONTRACT": "off" if contract.startswith("off") else "planned",
                "SYNTHETIC_WRITE_DOCUMENT": "1" if contract == "planned" else "0"})
    if contract == "off":
        env.pop(ACCEPTANCE_DOCUMENT_ENV)
    document_path = tmp_path / "control" / "acceptance-phase-001.json"
    if contract == "planned-missing-document":
        with pytest.raises(ValueError, match="did not write"):
            run_once(env)
        assert not Path(env["LOOPX_PLANNING_RESULT"]).exists()
        (receipt_path,) = (tmp_path / "wakes").glob("*/receipt.json")
        saved = json.loads(receipt_path.read_text())
        assert saved["ok"] is False and saved["planning"]["acceptance"]["status"] == "failed"
        assert "did not write" in saved["planning"]["acceptance"]["error"]
        return
    receipt = run_once(env)
    assert receipt["ok"] and receipt["stage"] == "plan", receipt
    planning = json.loads(Path(env["LOOPX_PLANNING_RESULT"]).read_text())
    assert planning["status"] == "ready" and planning["todo_ids"] == [env["TEST_TODO_ID"]]
    body = (Path(env["LOOPX_PROJECT"]) / "planning-body.txt").read_text()
    inspect = subprocess.run(cli + ["goal-acceptance", "inspect", "--goal-id", GOAL_ID], cwd=env["LOOPX_PROJECT"],
                             env=env, text=True, capture_output=True)
    contract_state = json.loads(inspect.stdout)["goal_acceptance_contract"]
    if contract.startswith("off"):
        assert "acceptance" not in planning and "acceptance_contract" not in receipt
        assert "acceptance document" not in body and not document_path.exists()
        assert contract_state == {"enabled": False}
    else:
        assert receipt["acceptance_contract"] == "planned" and str(document_path) in body
        assert planning["acceptance"]["status"] == "applied"
        assert planning["acceptance"]["selected_todo_ids"] == [env["TEST_TODO_ID"]]
        assert contract_state["enabled"] and contract_state["tasks"] == [
            {**contract_state["tasks"][0], "state": "ready", "applicable": True}]


def test_render_config_discloses_the_default_and_forwards_the_opt_in(tmp_path):
    pytest.importorskip("yaml")
    base = [sys.executable, str(REPO_ROOT / "benchmark/LHTB/scripts/render_config.py"), "--template",
            str(REPO_ROOT / "benchmark/LHTB/configs/heartbeat-generic-cli.yaml"), "--job-name", "smoke",
            "--jobs-dir", str(tmp_path), "--concurrency", "1", "--model", "openai/fixture", "--effort", "max",
            "--timeout", "5400", "--task", "tabular-data-feature-covshift"]
    import yaml
    for extra, expected in (([], "off"), (["--acceptance-contract", "planned"], "planned")):
        output = tmp_path / f"{expected}.yaml"
        subprocess.run(base + ["--output", str(output), *extra], check=True, env={**os.environ, "PYTHONPATH": str(REPO_ROOT)})
        kwargs = yaml.safe_load(output.read_text())["agents"][0]["kwargs"]
        assert kwargs["acceptance_contract"] == expected and kwargs["task_entry"] == "loopx-planned"
    failed = subprocess.run(base + ["--output", str(tmp_path / "bad.yaml"), "--task-entry", "seeded-todo",
                                    "--acceptance-contract", "planned"], capture_output=True, text=True,
                            env={**os.environ, "PYTHONPATH": str(REPO_ROOT)})
    assert failed.returncode != 0 and "loopx-planned" in failed.stderr
