"""The shipped native projector and CLI recover exact scoped capture bytes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.cli_commands.quota_capture import native_context_capture_view, read_context_capture
from loopx.control_plane.turn_driver.codex_context import codex_context_hook, project_codex_context
from loopx.control_plane.turn_driver.decision_capture import capture_page_range

GOAL, AGENT = "scope-goal", "author"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@pytest.fixture
def packet():
    # Import the shared prose owner instead of copying an instruction fixture.
    module = Path(__file__).resolve().parents[2] / "loopx/control_plane/work_items/outcome_read_guidance.ts"
    completed = subprocess.run(["node", "--input-type=module", "-e",
        f"import {{outcomeReadGuidance}} from {json.dumps(module.as_uri())}; process.stdout.write(outcomeReadGuidance());"],
        capture_output=True, text=True, check=True, timeout=20)
    reason = completed.stdout
    outcomes = [{"id": owner, "owner_agent_id": owner, "objective": f"Keep {owner} intent.", "revision": 1,
        "todo_ids": [f"todo_{owner}"], "work": [{"todo_id": f"todo_{owner}", "status": "open"}],
        "basis_digest": owner, "sources": [{"ref": f"message:{owner}", "summary": "Original requirements."}],
        "requirements": [{"id": f"r{i}", "description": (f"{owner} exact requirement {i}. " * 12).strip(),
            "status": "unverified", "planning_satisfied": False} for i in range(20)]}
        for owner in (AGENT, "peer")]
    acceptance = {"outcome_requirements": {"revision": 1, "current_lifetime": True, "outcomes": outcomes}}
    plan = {"schema_version": "outcome_context_read_plan_v0", "goal_id": GOAL, "agent_id": AGENT,
        "todo_id": "todo_author", "provider_revision": "source-revision", "outcome_revision": 1,
        "acceptance_digest": digest(acceptance), "complete": True, "fallback_reasons": [],
        "required_outcome_ids": [AGENT]}
    return {"ok": True, "goal_id": GOAL, "agent_identity": {"agent_id": AGENT},
        "selected_todo": {"todo_id": "todo_author"}, "goal_boundary": {"stop": "Do not publish."},
        "interaction_contract": {"agent_channel": {"must_attempt": True, "delivery_allowed": True,
            "required_reads": [], "work_context": {"complete": True, "instruction": "Read the complete required input.",
                "sources": [{"source": "goal_acceptance", "reason": reason, "content": {"status": "loaded",
                    "provider_revision": "source-revision", "goal_acceptance_contract": acceptance,
                    "outcome_context": plan}}]}},
            "cli_channel": {"settlement_plan": {"identity": {"turn_instance_id": "original-turn"}, "ordered_steps": []}}}}


def read_args(path, sha, **changes):
    return argparse.Namespace(capture_file=str(path), capture_sha256=sha, offset=0,
        page_bytes=4096, max_page_output_bytes=8192, format="json", **changes)


def project(packet, tmp_path, monkeypatch):
    directory = tmp_path / "native"
    directory.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", "native-session")
    codex_context_hook(str(directory), {"hook_event_name": "SessionStart", "session_id": "native-session",
        "cwd": str(tmp_path), "source": "startup"})
    capture = tmp_path / "capture"
    capture.mkdir(mode=0o700)
    result = project_codex_context(packet, directory=str(directory), capture_directory=capture,
        goal_id=GOAL, agent_id=AGENT, surface="quota_packet", capture_id="capture")
    assert result["mode"] == "full"
    assert result["context"]["schema_version"] == "outcome_context_projection_v0"
    assert result["recovery"]["path"].endswith("required-context.json")
    assert json.loads((capture / "projection.json").read_text()) == packet
    return result, directory, capture


def test_scoped_recovery_and_original_detail_are_digest_bound(tmp_path, monkeypatch, packet):
    result, directory, _ = project(packet, tmp_path, monkeypatch)
    recovery = result["recovery"]
    raw = Path(recovery["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == recovery["sha256"]
    first = read_context_capture(read_args(recovery["path"], recovery["sha256"], outcome_ref=""))
    assert first["ok"] and first["eof"]
    index = json.loads(first["content"])
    peer = next(row for row in index["outcomes"] if row["id"] == "peer")
    assert peer["required_in_scope"] is False
    args = read_args(recovery["path"], recovery["sha256"], outcome_ref=peer["outcome_ref"])
    body = []
    while True:
        page = read_context_capture(args)
        assert page["ok"], page
        assert capture_page_range(page, raw=raw, digest=recovery["sha256"]) is None
        body.append(page["content"])
        if page["eof"]:
            break
        args.offset = page["next_offset"]
        args.selection_sha256 = page["selection_sha256"]
        assert "--selection-sha256" in page["next_command"]
    detail = json.loads("".join(body))
    original = packet["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]["content"]
    assert detail["outcome"] == original["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][1]
    before = (directory / "session.json").read_bytes()
    args.selection_sha256 = "f" * 64
    failed = read_context_capture(args)
    assert failed["ok"] is False and "digest mismatch" in failed["reason"]
    assert failed["recovery_command"]
    assert (directory / "session.json").read_bytes() == before


def test_complete_native_pages_then_compaction_recover_same_required_capture(tmp_path, monkeypatch, packet):
    result, directory, capture = project(packet, tmp_path, monkeypatch)
    view = native_context_capture_view(result, capture, context_directory=str(directory))
    assert view["eof"], "fixture fits the supported native page profile"
    codex_context_hook(str(directory), {"hook_event_name": "PostToolUse", "session_id": "native-session",
        "cwd": str(tmp_path), "tool_response": json.dumps(view)})
    current = json.loads((directory / "session.json").read_text())
    assert current.get("candidate"), current
    assert current["candidate"]["capture_file"].endswith("required-context.json")
    codex_context_hook(str(directory), {"hook_event_name": "PreCompact", "session_id": "native-session", "cwd": str(tmp_path)})
    after = json.loads((directory / "session.json").read_text())
    assert not after.get("candidate")
    assert after["recovery"]["path"] == current["recovery"]["path"]
    raw = Path(after["recovery"]["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == after["recovery"]["sha256"]
    # Recovery is read-only even when the source has not become a new admission.
    recovered = read_context_capture(read_args(after["recovery"]["path"], after["recovery"]["sha256"]))
    assert recovered["ok"]
    assert json.loads((directory / "session.json").read_text()) == after


def test_real_cli_original_detail_preserves_unicode_and_source_identity(tmp_path, monkeypatch, packet):
    result, _, _ = project(packet, tmp_path, monkeypatch)
    recovery = result["recovery"]
    command = [sys.executable, "-m", "loopx.cli", "--format", "json", "quota", "context-read",
        "--capture-file", recovery["path"], "--capture-sha256", recovery["sha256"], "--outcome-ref"]
    completed = subprocess.run(command, cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    page = json.loads(completed.stdout)
    assert page["ok"] and page["eof"]
    assert json.loads(page["content"])["goal_id"] == GOAL
    wrong = subprocess.run(command[:-2] + ["0" * 64, "--outcome-ref"],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert wrong.returncode != 0


def test_failed_optional_scope_transport_keeps_original_full_recovery(tmp_path, monkeypatch, packet):
    # Fault only the optional coarse call; the ordinary codec remains the owner.
    from loopx.control_plane.quota import turn_envelope
    original = turn_envelope._turn_envelope_result

    def unavailable(operation, **params):
        if operation == "native_context_delivery":
            raise ValueError("Synthetic projection failure")
        return original(operation, **params)

    directory = tmp_path / "native"
    directory.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", "native-session")
    codex_context_hook(str(directory), {"hook_event_name": "SessionStart", "session_id": "native-session",
        "cwd": str(tmp_path), "source": "startup"})
    capture = tmp_path / "capture"
    capture.mkdir(mode=0o700)
    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", unavailable)
    result = project_codex_context(packet, directory=str(directory), capture_directory=capture,
        goal_id=GOAL, agent_id=AGENT, surface="quota_packet", capture_id="capture")
    assert result["context"] == packet
    assert json.loads(Path(result["recovery"]["path"]).read_text()) == packet


def test_failed_scoped_capture_write_returns_original_not_unrecoverable_scope(tmp_path, monkeypatch, packet):
    from loopx.control_plane.turn_driver import codex_context

    write = codex_context.capture_context_projection

    def fail_scoped(*args, **kwargs):
        if kwargs.get("filename") == "required-context.json":
            raise OSError("Synthetic scoped publication failure")
        return write(*args, **kwargs)

    directory = tmp_path / "native"
    directory.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", "native-session")
    codex_context_hook(str(directory), {"hook_event_name": "SessionStart", "session_id": "native-session",
        "cwd": str(tmp_path), "source": "startup"})
    capture = tmp_path / "capture"
    capture.mkdir(mode=0o700)
    monkeypatch.setattr(codex_context, "capture_context_projection", fail_scoped)
    result = project_codex_context(packet, directory=str(directory), capture_directory=capture,
        goal_id=GOAL, agent_id=AGENT, surface="quota_packet", capture_id="capture")
    assert result == packet
    state = json.loads((directory / "session.json").read_text())
    assert json.loads(Path(state["recovery"]["path"]).read_text()) == packet
    assert state["pending"]["ranges"] == {}
