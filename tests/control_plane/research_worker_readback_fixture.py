"""Disposable real worker/CLI authority for the packaged dashboard smoke."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shlex
import signal
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def serve(root: Path, mode: str) -> None:
    if mode not in {"paused", "unknown"}:
        raise ValueError("unknown smoke mode")
    patch = pytest.MonkeyPatch()
    # This helper also runs directly from the browser smoke, outside pytest's
    # autouse fixtures. Resolve default device settings inside its disposable
    # home before importing any runtime owner, as the live readiness smoke does.
    isolated_home = root / "home"
    isolated_home.mkdir()
    patch.setenv("HOME", str(isolated_home))
    patch.setenv("USERPROFILE", str(isolated_home))
    patch.delenv("CODEX_HOME", raising=False)
    from test_external_scheduler_worker import _args, worker
    from test_replan_successor_guard_reentry import AGENT, GOAL, _fixture
    from managed_effect_runtime_fixture import stop_disposable_effect_runtimes
    from loopx.chat_server import ChatHTTPServer, default_chat_assets_dir, serve_chat
    from loopx.control_plane.quota.host_poll_receipts import receipt_path_for_goal

    try:
        _, runtime, index = _fixture(root, patch, "file", future_monitor=False)
        before = index.read_bytes()
        host = root / "controlled_host.py"
        dispatches = root / "dispatches.json"
        host.write_text(
            "import json, pathlib, sys\n"
            "path = pathlib.Path(sys.argv[1])\n"
            "count = json.loads(path.read_text()) if path.exists() else 0\n"
            "path.write_text(json.dumps(count + 1))\n"
            "print('Controlled model boundary: one request; no semantic delta.')\n"
            "raise SystemExit(int(sys.argv[2]))\n"
        )
        args = _args(fake_cli=Path(sys.executable).parent / "loopx", state_file=root / "worker.json",
            wake_cmd=shlex.join([sys.executable, str(host), str(dispatches), "1" if mode == "unknown" else "0"]),
            quota_timeout_seconds=60, wake_timeout_seconds=15)
        args.registry, args.runtime_root = str(root / "registry.json"), str(runtime)
        args.goal_id, args.agent_id, args.once = GOAL, AGENT, mode == "unknown"
        with redirect_stdout(io.StringIO()):
            returned = worker.run_worker(args, sleep=lambda _: pytest.fail("unexpected timer"))
        assert returned == (1 if mode == "unknown" else 0)
        assert json.loads(dispatches.read_text()) == 1
        assert index.read_bytes() == before
        state = json.loads(Path(args.state_file).read_text())
        original = state["replan_attempts"][0]["obligation_id"]
        receipt = receipt_path_for_goal({"id": GOAL}, root / "project" / "ACTIVE_GOAL_STATE.md")
        output = sys.stdout

        class ObservableChatServer(ChatHTTPServer):
            def serve_forever(self, poll_interval=0.5):
                print(json.dumps({"port": self.server_address[1], "registry": args.registry,
                    "runtime": args.runtime_root, "goal_id": GOAL, "agent_id": AGENT,
                    "obligation_id": original, "receipt": str(receipt), "dispatches": str(dispatches),
                    "history": str(index)}),
                    file=output, flush=True)
                return super().serve_forever(poll_interval)

        patch.setattr("loopx.chat_server.ChatHTTPServer", ObservableChatServer)
        for variable in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            patch.delenv(variable, raising=False)

        def terminate(*_):
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, terminate)
        with redirect_stdout(sys.stderr):
            serve_chat(registry_path=Path(args.registry), runtime_root_override=runtime,
                scan_roots=[root / "project"], goal_id=GOAL, host="127.0.0.1", port=0,
                codex_bin=str(root / "no-provider-codex"), claude_bin=str(root / "no-provider-claude"),
                kiro_cli_bin=str(root / "no-provider-kiro"), assets_dir=default_chat_assets_dir(), open_browser=False)
    finally:
        # Source changes during a local run can create more than one runtime
        # fingerprint. Retire every verified owner in this private namespace
        # before the Node harness removes it, not only the latest fingerprint.
        stop_disposable_effect_runtimes(root)
        patch.undo()


if __name__ == "__main__":
    serve(Path(sys.argv[1]), sys.argv[2])
