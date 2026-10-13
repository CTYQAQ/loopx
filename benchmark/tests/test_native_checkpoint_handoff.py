"""Characterize live native forks before choosing a midwake capture transport.

A local deterministic provider replaces inference; the actual Host owns fork,
history and tool execution. This does not qualify a joint research checkpoint.
"""
import json
import os
import shlex
import subprocess
import time

import pytest

from benchmark.runtime.native_checkpoint import native_session_checkpoint
from benchmark.tests.test_native_checkpoint import real_native_source
from loopx.capabilities.benchmark_toolkit.native_codex_goal import (
    NativeGoalProtocolError, StdioNativeGoalTransport,
)


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"),
                   reason="Requires an explicit real native Host; no model service used")
def test_live_fork_preserves_context_without_quiescing_source(tmp_path):
    source_work, branch_work = tmp_path / "source", tmp_path / "branch"
    source_work.mkdir()
    branch_work.mkdir()
    requests = []
    # A bounded tool withholds its output until the test Host releases it.
    # This models transport ordering only, not a LoopX guard or writer barrier.
    code = """from pathlib import Path
import time
Path('waiting').write_text('ready')
deadline = time.monotonic() + 20
while not Path('release').exists() and time.monotonic() < deadline:
    time.sleep(.01)
assert Path('release').exists(), 'fixture release missing'
Path('source-result').write_text('one original execution')
print('WITHHELD_RESULT')
"""

    def response(request, index, message):
        requests.append(request)
        if index == 2:
            return {"type": "function_call", "id": "fc_boundary", "call_id": "call_boundary",
                    "name": "exec_command", "arguments": json.dumps({
                        "cmd": "python3 -c " + shlex.quote(code),
                        "yield_time_ms": 10000, "max_output_tokens": 100})}
        message["content"][0]["text"] = "RETAINED_RESEARCH" if index == 1 else "Continued."
        return message

    def history(host, session):
        return host.request("thread/turns/list", {"threadId": session,
            "sortDirection": "asc", "itemsView": "full", "limit": 100})["data"]

    def terminal(host, session, turn):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            rows = history(host, session)
            if rows and rows[-1]["id"] == turn and rows[-1]["status"] == "completed":
                return rows
            time.sleep(.02)
        raise AssertionError("Native fixture did not complete")

    binary = os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"]
    with real_native_source(tmp_path / "native", binary, source_work, close_source=True,
            response_builder=response, sandbox="workspace-write") as (env, source, _, calls):
        with StdioNativeGoalTransport.spawn([binary, "app-server", "--listen", "stdio://"],
                cwd=str(source_work), env=env, stderr=subprocess.DEVNULL, response_timeout_sec=15) as host:
            host.request("initialize", {"clientInfo": {"name": "handoff_fixture", "version": "0.1"},
                                        "capabilities": {"experimentalApi": True}})
            host.notify("initialized", {})
            host.request("thread/resume", {"threadId": source, "cwd": str(source_work),
                "sandbox": "workspace-write", "approvalPolicy": "never", "persistExtendedHistory": True})
            turn = host.request("turn/start", {"threadId": source, "input": [
                {"type": "text", "text": "Reach the synthetic withheld-output boundary."}]})["turn"]["id"]
            try:
                deadline = time.monotonic() + 10
                while not (source_work / "waiting").exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                assert (source_work / "waiting").exists() and len(calls) == 2
                source_history = history(host, source)
                assert source_history[-1]["status"] == "inProgress"
                with pytest.raises(NativeGoalProtocolError, match="thread/fork"):
                    host.request("thread/fork", {"threadId": source, "cwd": str(branch_work),
                        "lastTurnId": turn, "persistExtendedHistory": True})
                fork = host.request("thread/fork", {"threadId": source, "cwd": str(branch_work),
                    "persistExtendedHistory": True})["thread"]["id"]
                fork_history = history(host, fork)
                assert fork != source and len(calls) == 2
                assert fork_history[:-1] == source_history[:-1]
                assert fork_history[-1]["id"] == turn and fork_history[-1]["status"] == "interrupted"
                assert history(host, source)[-1]["status"] == "inProgress"
                # Native fork success grants neither a complete cursor nor a
                # writer barrier. Keep the production capture rejection intact.
                with pytest.raises(ValueError, match="completed final cursor"):
                    native_session_checkpoint({**env, "LOOPX_PROJECT": str(branch_work)}, fork, timeout=15)
                branch_turn = host.request("turn/start", {"threadId": fork, "input": [
                    {"type": "text", "text": "Fresh fixture admission: retain context; do not replay the source tool."}]})["turn"]["id"]
                terminal(host, fork, branch_turn)
                assert len(calls) == 3
                items = requests[2]["input"]
                assert "RETAINED_RESEARCH" in json.dumps(items)
                assert any(item.get("type") == "function_call_output" and
                           item.get("call_id") == "call_boundary" and item.get("output") == "aborted"
                           for item in items)
                assert history(host, source)[-1]["status"] == "inProgress"
                assert not (source_work / "source-result").exists()
                assert not (branch_work / "waiting").exists()  # No automatic tool replay.
            finally:
                (source_work / "release").write_text("release original tool")
                terminal(host, source, turn)
            assert (source_work / "source-result").read_text() == "one original execution"
            assert not (branch_work / "source-result").exists()
            assert len(calls) == 4 and "WITHHELD_RESULT" in json.dumps(requests[3])


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN") or os.name != "posix",
                   reason="Requires a real native Host and local Unix transport")
def test_real_quota_output_is_held_across_native_fork(tmp_path):
    """The real guard executes once; neither branch observes it before release."""
    import hashlib
    import socket
    import sys
    import tempfile
    from pathlib import Path

    from benchmark.runtime.checkpoint import held_quota_decision
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID, GOAL_ID, TURN_ID, _write_fixture, _configure_autonomous_replan_fixture,
        _heartbeat_receipt_count,
    )

    project, runtime, registry = _write_fixture(tmp_path / "authority")
    _configure_autonomous_replan_fixture(project, runtime, registry)
    capture_root = project / "captures"
    capture_root.mkdir(mode=0o700)
    requests = []
    with tempfile.TemporaryDirectory(prefix="handoff-", dir="/tmp") as short:
        socket_path = Path(short).resolve() / "host.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(socket_path))
            listener.listen(1)
            listener.settimeout(30)
            argv = [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
                    "--runtime-root", str(runtime), "quota", "should-run", "--goal-id", GOAL_ID,
                    "--agent-id", AGENT_ID, "--turn-instance-id", TURN_ID,
                    "--runtime-profile", "generic_cli", "--available-capability", "shell",
                    "--available-capability", "filesystem_read", "--decision-output-root", str(capture_root),
                    "--decision-handoff-socket", str(socket_path)]
            command = "PYTHONPATH=" + shlex.quote(str(Path(__file__).resolve().parents[2])) + " " + shlex.join(argv)

            def response(request, index, message):
                requests.append(request)
                if index == 2:
                    return {"type": "function_call", "id": "fc_guard", "call_id": "call_guard",
                            "name": "exec_command", "arguments": json.dumps({
                                "cmd": command, "yield_time_ms": 10000, "max_output_tokens": 200})}
                message["content"][0]["text"] = "RESEARCH_BEFORE_GUARD" if index == 1 else "Continued."
                return message

            binary = os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"]
            with real_native_source(tmp_path / "native", binary, project, close_source=True,
                    response_builder=response, sandbox="danger-full-access") as (env, source, _, calls):
                with StdioNativeGoalTransport.spawn([binary, "app-server", "--listen", "stdio://"],
                        cwd=str(project), env=env, stderr=subprocess.DEVNULL, response_timeout_sec=30) as host:
                    host.request("initialize", {"clientInfo": {"name": "quota_handoff_fixture", "version": "0.1"},
                                                "capabilities": {"experimentalApi": True}})
                    host.notify("initialized", {})
                    host.request("thread/resume", {"threadId": source, "cwd": str(project),
                        "sandbox": "danger-full-access", "approvalPolicy": "never", "persistExtendedHistory": True})
                    turn = host.request("turn/start", {"threadId": source, "input": [
                        {"type": "text", "text": "Execute the original synthetic guard."}]})["turn"]["id"]
                    with held_quota_decision(listener, capture_root, goal_id=GOAL_ID,
                            agent_id=AGENT_ID, turn_instance_id=TURN_ID) as held:
                        assert held["quota"]["ok"] is True and len(calls) == 2
                        assert held["intent"]["capture"] is True
                        assert held["sha256"] == hashlib.sha256(held["path"].read_bytes()).hexdigest()
                        receipts = _heartbeat_receipt_count(runtime, TURN_ID)
                        assert receipts >= 1
                        # Native events, rather than the incomplete history projection,
                        # witness the actual in-flight tool and its exact command.
                        pending = None
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            event = host.next_event(timeout_sec=.1)
                            if event and event.get("method") == "item/started":
                                params = event["params"]
                                item = params.get("item", {})
                                if item.get("type") == "commandExecution":
                                    pending = params
                                    break
                        assert pending and pending["threadId"] == source and pending["turnId"] == turn
                        assert shlex.split(pending["item"]["command"])[-1] == command
                        fork = host.request("thread/fork", {"threadId": source,
                            "cwd": str(project), "persistExtendedHistory": True})["thread"]["id"]
                        rows = host.request("thread/turns/list", {"threadId": fork,
                            "sortDirection": "asc", "itemsView": "full", "limit": 100})["data"]
                        assert rows[-1]["status"] == "interrupted" and rows[-1]["id"] == turn
                        assert len(calls) == 2  # No post-guard inference while held.
                        assert _heartbeat_receipt_count(runtime, TURN_ID) == receipts
                    deadline = time.monotonic() + 10
                    while len(calls) < 3 and time.monotonic() < deadline:
                        time.sleep(.02)
                    assert len(calls) == 3 and "heartbeat_receipt" in json.dumps(requests[2])
                    assert _heartbeat_receipt_count(runtime, TURN_ID) == receipts
                    assert held["path"].read_bytes()  # Original receipt stays recoverable.


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN") or os.name != "posix",
                   reason="Requires a real native Host and local Unix transport")
@pytest.mark.parametrize("outcome", ["resume", "capture_failure", "changed_packet"])
def test_native_cli_interruption_retains_guard_beyond_tool_yield(tmp_path, outcome):
    """A real native CLI stops, then explicitly resumes the one committed guard."""
    import socket
    import sys
    import tempfile
    from pathlib import Path

    from benchmark.runtime.checkpoint import held_quota_decision
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID, GOAL_ID, TURN_ID, _write_fixture, _configure_autonomous_replan_fixture,
        _heartbeat_receipt_count,
    )

    project, runtime, registry = _write_fixture(tmp_path / "authority")
    _configure_autonomous_replan_fixture(project, runtime, registry)
    capture_root = project / "captures"
    capture_root.mkdir(mode=0o700)
    requests = []
    with tempfile.TemporaryDirectory(prefix="handoff-", dir="/tmp") as short:
        socket_path = Path(short).resolve() / "host.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(socket_path))
            listener.listen(1)
            listener.settimeout(30)
            argv = [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
                    "--runtime-root", str(runtime), "quota", "should-run", "--goal-id", GOAL_ID,
                    "--agent-id", AGENT_ID, "--turn-instance-id", TURN_ID,
                    "--runtime-profile", "generic_cli", "--available-capability", "shell",
                    "--available-capability", "filesystem_read", "--decision-output-root", str(capture_root),
                    "--decision-handoff-socket", str(socket_path)]
            command = "PYTHONPATH=" + shlex.quote(str(Path(__file__).resolve().parents[2])) + " " + shlex.join(argv)

            def response(request, index, message):
                requests.append(request)
                if index == 2:
                    return {"type": "function_call", "id": "fc_guard", "call_id": "call_guard",
                            "name": "exec_command", "arguments": json.dumps({
                                "cmd": command, "yield_time_ms": 10000, "max_output_tokens": 200})}
                message["content"][0]["text"] = "RESEARCH_BEFORE_GUARD" if index == 1 else "Continued."
                return message

            binary = os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"]
            with real_native_source(tmp_path / "native", binary, project, close_source=True,
                    response_builder=response, sandbox="danger-full-access") as (env, source, _, calls):
                from types import SimpleNamespace
                from benchmark.runtime.worker import child_process, native_command
                from benchmark.runtime.codex import Execution
                env.update(MODEL_NAME="fixture", REASONING_EFFORT="high")
                wake = tmp_path / "cli-wake"
                wake.mkdir()
                cli_command = native_command(env, Execution(context="resume", sandbox="danger-full-access"),
                                         "execute", wake, SimpleNamespace(session_id=source))
                with (wake / "stdout.jsonl").open("w") as output, (wake / "stderr.log").open("w") as error:
                    with child_process(cli_command, env=env, stdout=output, stderr=error) as process:
                        process.stdin.write("Execute the original synthetic guard.\n")
                        process.stdin.flush()
                        process.stdin.close()
                        from contextlib import nullcontext
                        expected = (nullcontext() if outcome == "resume" else
                                    pytest.raises(ValueError, match="capture failed|decision changed"))
                        with expected:
                            with held_quota_decision(listener, capture_root, goal_id=GOAL_ID,
                                    agent_id=AGENT_ID, turn_instance_id=TURN_ID,
                                    native_process=process) as held:
                                assert held["intent"]["capture"] and len(calls) == 2
                                assert process.poll() is not None
                                assert "continuation_body" not in held
                                receipts = _heartbeat_receipt_count(runtime, TURN_ID)
                                if outcome == "capture_failure":
                                    raise ValueError("capture failed")
                                if outcome == "changed_packet":
                                    held["path"].write_text("{}")
                                else:
                                    time.sleep(12)  # Exceeds the real tool's 10-second yield.
                                assert len(calls) == 2
                                assert _heartbeat_receipt_count(runtime, TURN_ID) == receipts
                if outcome != "resume":
                    assert "continuation_body" not in held
                    assert process.poll() is not None and len(calls) == 2
                    assert _heartbeat_receipt_count(runtime, TURN_ID) == receipts
                    return
                original = held["path"].read_text()
                assert held["continuation_body"].endswith(original)
                result = subprocess.run(cli_command, env=env, cwd=project,
                    input=held["continuation_body"],
                    text=True,capture_output=True, timeout=30)
                assert result.returncode == 0, result.stderr
                assert len(calls) == 3 and "heartbeat_receipt" in json.dumps(requests[2])
                assert _heartbeat_receipt_count(runtime, TURN_ID) == receipts
                assert "RESEARCH_BEFORE_GUARD" in json.dumps(requests[2])
                assert any(item.get("type") == "function_call_output" and
                           item.get("call_id") == "call_guard" and
                           "aborted" in item.get("output", "")
                           for item in requests[2]["input"])


@pytest.mark.skipif(os.name != "posix", reason="Requires owned Unix process groups")
def test_checkpoint_interrupt_rejects_shared_group_and_invalid_timeout(tmp_path):
    import sys
    from benchmark.runtime.worker import interrupt_checkpoint_process

    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"]) as process:
        try:
            with pytest.raises(ValueError, match="positive timeout"):
                interrupt_checkpoint_process(process, timeout=float("nan"))
            with pytest.raises(ValueError, match="owned child process group"):
                interrupt_checkpoint_process(process)
            assert process.poll() is None  # No signal to the caller's own group.
        finally:
            process.terminate()
            process.wait(timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="Requires owned Unix process groups")
def test_checkpoint_interrupt_timeout_reaps_without_qualifying_capture(tmp_path):
    import sys
    from benchmark.runtime.worker import child_process, interrupt_checkpoint_process

    ready = tmp_path / "ready"
    code = ("import signal,time; from pathlib import Path; "
            "signal.signal(signal.SIGINT, signal.SIG_IGN); "
            f"Path({str(ready)!r}).touch(); time.sleep(30)")
    with child_process([sys.executable, "-c", code],
            env={**os.environ, "LOOPX_PROJECT": str(tmp_path)},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists()
        with pytest.raises(subprocess.TimeoutExpired):
            interrupt_checkpoint_process(process, timeout=.1)
        assert process.poll() is not None
