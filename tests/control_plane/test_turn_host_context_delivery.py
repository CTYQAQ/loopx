"""Real File/SQLite capture to DSH stdio; the explicit runner invokes no model."""
from __future__ import annotations

from copy import deepcopy
import json
import shlex
import subprocess
import sys

import pytest

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.quota.turn_envelope import build_turn_envelope, turn_envelope_action_signature_document
from loopx.control_plane.turn_driver.host_candidate import _canonical_hash
from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from tests.control_plane.test_agent_context import POLICY
from tests.control_plane.test_quota_settlement_cli import AGENT_ID, GOAL_ID, TODO_ID, _run_cli, _write_fixture


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mode", ["off", "inline", "legacy_reference"])
def test_captured_context_reaches_actual_host_without_readmission(tmp_path, monkeypatch, provider, mode):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace(
        "- [ ] [P1] Validate and settle the selected delivery.",
        "- [ ] [P1] Validate the complete acceptance: " + "preserve original evidence; " * 180,
    ))
    config = json.loads(registry.read_text())
    config["goals"][0]["spawn_policy"] = {**POLICY, "spawn_allowed": mode != "off"}
    registry.write_text(json.dumps(config))
    code, listed = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID)
    assert code == 0, listed
    initialize_canonical_authority(runtime, GOAL_ID, build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID, handoff_mode="soft_claim", todos=listed["todos"],
    ), state_path=state, provider=provider)

    capture = tmp_path / "capture"
    code, envelope = _run_cli(registry, runtime, "quota", "should-run",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
        "--turn-instance-id", "host-context-turn", "--codex-app",
        "--available-capability", "filesystem_read", "--available-capability", "subagent_spawn",
        "--turn-envelope", "--decision-output-dir", str(capture), cwd=project)
    assert code == 0, envelope
    original = (capture / "decision.json").read_bytes()
    source = json.loads(original)
    context = source["interaction_contract"].get("agent_context")
    assert bool(context) is (mode != "off")
    if not context:
        assert "agent_context" not in envelope
    if mode == "legacy_reference":
        envelope["agent_context"] = {
            **{k: context[k] for k in ("schema_version", "phase", "scope", "target", "authority", "delivery")},
            "content_hash": _canonical_hash(context),
            "detail_ref": "full_decision.interaction_contract.agent_context",
            "instruction": "Read original capability context before planning.",
        }
        digest = _canonical_hash(turn_envelope_action_signature_document(envelope))
        envelope["action_signature"].update(source_hash=digest, envelope_hash=digest)

    runner = tmp_path / "runner.py"
    runner.write_text(
        "import json\nfrom pathlib import Path\n"
        "def run_dsh_turn(*, prompt, workspace, **kwargs):\n"
        "    Path(workspace, 'received.txt').write_text(prompt)\n"
        "    return json.dumps({'result_kind':'wait','summary':'Protocol input recorded.'})\n"
    )
    command = [sys.executable, "-m", "loopx.dsh_goal_mode", "--workspace", str(project),
               "--dsh-home", str(tmp_path / "host-home"), "--dsh-runner", str(runner)]
    request = {"schema_version": "loopx_turn_host_request_v0", "turn_key": "sha256:" + "0" * 64,
               "route": "primary_delivery", "turn_envelope": envelope}
    before = {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
    result = subprocess.run(command, input=json.dumps(request), text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["result_kind"] == "wait"
    prompt_path = project / "received.txt"
    prompt = prompt_path.read_text()
    authority = json.loads(prompt.split("Turn authority JSON:\n", 1)[1].split("\n\nWhen finished", 1)[0])
    if context:
        # Independent source oracle after the actual subprocess boundary: two
        # agreeing reduced projections cannot prove delivery of the full source.
        assert authority["agent_context"] == envelope["agent_context"]
        if mode == "legacy_reference":
            assert authority["action_signature"] == envelope["action_signature"]
            detail = authority["detail_ref"]
            assert detail == envelope["detail_ref"]
            read = subprocess.run(shlex.split(detail["full_decision"]), capture_output=True, check=True).stdout
            assert read == original
            # Full decision JSON includes numeric values whose canonical wire
            # is owned by TS (e.g. Python 1.0 and JSON.stringify's 1 differ).
            assert build_turn_envelope(json.loads(read))["action_signature"]["source_decision_hash"] == authority["action_signature"]["source_decision_hash"]
            assert _canonical_hash(json.loads(read)["interaction_contract"]["agent_context"]) == authority["agent_context"]["content_hash"]
        else:
            assert envelope["agent_context"] == context
            assert "detail_ref" not in envelope["agent_context"]
            assert envelope["compaction"]["within_budget"] is False
            assert "detail_ref" not in authority
        invalid = deepcopy(request)
        invalid["turn_envelope"]["agent_context"]["scope"]["agent_id"] = "wrong-agent"
        rejected = subprocess.run(command, input=json.dumps(invalid), text=True, capture_output=True, timeout=30)
        assert rejected.returncode != 0 and "action signature" in rejected.stderr
        assert prompt_path.read_text() == prompt  # Refusal precedes the provider seam.
    else:
        assert "agent_context" not in authority
        assert "detail_ref" not in authority
        assert "action_signature" not in authority
    assert (capture / "decision.json").read_bytes() == original
    assert {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()} == before
