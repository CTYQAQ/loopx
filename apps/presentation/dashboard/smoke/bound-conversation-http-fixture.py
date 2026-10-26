"""Packaged UI against real Chat/Core; only provider identity/model are synthetic.

Everything lives in a disposable SQLite runtime. No personal App, source, Goal,
credentials or model account is read or contacted. Stdin controls source events.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import socket
import sys
import tempfile
import threading

import pytest

REPO = Path(__file__).resolve().parents[4]
BACKEND = Path(os.environ.get("LOOPX_CHAT_FIXTURE_BACKEND_ROOT", REPO)).resolve()
sys.path[:0] = [str(BACKEND), str(REPO), str(REPO / "tests/control_plane")]
from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from loopx.chat_server import serve_chat


class SyntheticPrivateSource:
    transport_ref = "synthetic-private"
    cancelled = staticmethod(lambda: False)

    def __init__(self, root, *, steward=False, peer=False):
        self.root, self.verified = root, True
        self.steward, self.peer = steward, peer
        self.peer_provider = None
        if peer:
            self.transport_ref = "synthetic-peer"

    def observe(self):
        return {"transport_ref": self.transport_ref, "provider_ref": ("e" if self.peer else "c") * 24,
                "operator_ref": ("f" if self.peer else "d") * 24, "verified": self.verified}

    def health_snapshot(self):
        return {"status": "listening"}

    def close(self):
        pass

    def start(self, server):
        runtime = server.runtime_controller
        runtime.codex_home = self.root / "synthetic-model-home"
        runtime.codex_home.mkdir(exist_ok=True)
        bindings = runtime.project_contexts.conversation_bindings
        binding = bindings.configure(transport_ref=self.transport_ref,
            project_ref=runtime.project_contexts.available()[0]["project_ref"],
            executor_endpoint_id="codex", context_kind="steward" if self.steward else "project", project_grant="workspace_read")
        source = {"source_ref": ("b" if self.peer else "a") * 24,
                  "sender_ref": ("f" if self.peer else "d") * 24, "private_human_message": True}
        original, _ = runtime.open_session(goal_id=None, agent_id="codex", objective="",
            work_dir=self.root / "project", mode="resume_latest",
            conversation_binding_id=binding["binding_id"], source_context=source)
        sid = original["session_id"]
        if self.peer:
            server.chat_store.append_message(sid, role="agent", text="Different private steward: keep this source separate.")
            return
        server.chat_store.append_message(sid, role="user", text="Synthetic private conversation: review the research notes.")
        server.chat_store.append_message(sid, role="agent", text="The original conversation and its research notes are ready.")
        other, _ = runtime.open_session(goal_id=None, agent_id="codex", objective="", mode="new",
            work_dir=self.root / "project", project_ref=binding["project_ref"])
        server.chat_store.append_message(other["session_id"], role="agent", text="Unrelated desktop conversation — must remain separate.")
        print(json.dumps({"origin": f"http://127.0.0.1:{server.server_port}",
            "bindingId": binding["binding_id"], "sessionId": sid, "ordinarySessionId": other["session_id"]}), flush=True)
        settings_handler = server.RequestHandlerClass._private_conversations

        def control():
            for line in sys.stdin:
                command = line.strip()
                if command in {"deny", "recover"}:
                    self.verified = command == "recover"
                    result = {"source_verified": self.verified}
                elif command == "source":
                    accepted, _ = runtime.enqueue_turn(session_id=sid, message="A new synthetic message from the original private source.",
                        client_turn_id="synthetic-source-update", origin="lark", work_dir=self.root / "project", objective="")
                    turn = runtime.wait_for_turn(session_id=sid, turn_id=accepted["turn_id"], timeout_sec=15)
                    result = {"source_turn_status": turn["status"]}
                elif command == "close":
                    server.chat_store.update_session(sid, status="closed")
                    result = {"closed": True}
                elif command == "replace-peer" and self.peer_provider is not None:
                    old = next(row for row in bindings.read()["bindings"] if row["transport_ref"] == self.peer_provider.transport_ref)
                    bindings.disconnect(old["binding_id"], expected_revision=bindings.read()["revision"])
                    self.peer_provider.start(server)
                    fresh = next(row for row in bindings.read()["bindings"] if row["transport_ref"] == self.peer_provider.transport_ref)
                    result = {"old_peer_binding_id": old["binding_id"], "new_peer_binding_id": fresh["binding_id"]}
                elif command == "revoke":
                    bindings.disconnect(binding["binding_id"], expected_revision=bindings.read()["revision"])
                    result = {"revoked": binding["binding_id"]}
                elif command in {"settings-unavailable", "settings-recover"}:
                    def unavailable(handler):
                        return handler._send_error("Synthetic connection settings are unavailable", status=503)
                    server.RequestHandlerClass._private_conversations = (
                        unavailable if command == "settings-unavailable" else settings_handler)
                    result = {"settings_available": command == "settings-recover"}
                elif command == "populate":
                    for index in range(6):
                        server.chat_store.append_message(sid, role="user", text=f"Synthetic source request {index + 1}: 保留原任务与尚未确认的结果。")
                        server.chat_store.append_message(sid, role="agent", text=f"### 合成报告 {index + 1} / Synthetic report\n\n"
                            + "\n\n".join(f"**工作 {part + 1}**：已验证的结果保留来源；仍在处理的事项继续保留，不把监听状态当作完成。 "
                                "Verified results retain their source. Unconfirmed returns remain open for recovery."
                                for part in range(6)))
                    result = {"populated": True, "messages": len(server.chat_store.messages(sid))}
                elif command == "inspect":
                    saved = server.chat_store.load_session(sid)
                    result = {"sessions": len(server.chat_store.list_sessions()), "original_thread": saved["upstream_thread_id"],
                              "messages": server.chat_store.messages(sid),
                              "native_requests": len((self.root / "synthetic-requests.jsonl").read_text().splitlines())}
                elif command == "stop":
                    server.shutdown()
                    return
                else:
                    result = {"unknown_command": command}
                print(json.dumps(result), flush=True)
        threading.Thread(target=control, daemon=True).start()


def main():
    with tempfile.TemporaryDirectory(prefix="loopx-bound-ui-") as directory, pytest.MonkeyPatch.context() as patch:
        root = Path(directory)
        isolate_sqlite_runtime(root, patch)
        patch.setenv("CODEX_HOME", str(root / "synthetic-model-home"))
        registry, _, _ = promoted_create_fixture(root, provider="sqlite")
        fake = root / "synthetic-codex"
        model = runpy.run_path(str(REPO / "examples/loopx-chat-runtime-smoke.py"))["FAKE_CODEX"]
        model = model.replace('    method = request.get("method")',
            f'    with open({str(root / "synthetic-requests.jsonl")!r}, "a") as output:\n'
            '        output.write(json.dumps(request) + "\\n")\n    method = request.get("method")')
        fake.write_text(model)
        fake.chmod(0o700)
        steward = "--steward" in sys.argv
        source = SyntheticPrivateSource(root, steward=steward)
        peer = SyntheticPrivateSource(root, steward=True, peer=True) if steward else None
        source.peer_provider = peer
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        serve_chat(registry_path=registry, runtime_root_override=root / "runtime", scan_roots=[root / "project"],
            codex_bin=str(fake), lark_cli_bin="missing-synthetic-lark", port=port,
            external_conversation_factories=((lambda _: source, lambda _: peer) if peer else (lambda _: source,)))


if __name__ == "__main__":
    main()
