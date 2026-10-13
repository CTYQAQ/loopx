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
