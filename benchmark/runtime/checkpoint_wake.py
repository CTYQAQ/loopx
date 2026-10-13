"""Opt-in native CLI rendezvous inside an existing heartbeat wake.

This adapter owns process/transport ordering, not replan admission or complete
research-point qualification. Typed quota/artifact owners retain those rules.
"""
from __future__ import annotations

import hashlib
import json
import select
import socket
import subprocess
import tempfile
import time
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

from loopx.control_plane.effect_runtime import effect_runtime_result
from .checkpoint import _mark_replan_attempt, held_quota_decision, read_checkpoint_request


class CheckpointWake:
    """One private listener survives all source-session resumptions in a wake."""

    def __init__(self, env, receipt):
        self.env, self.receipt = env, receipt
        self.stack = ExitStack()
        self.listener = None
        self.socket_path = None
        self.request = None
        self.raw = None
        self.root = Path(env["LOOPX_WAKE_LOG_DIR"]).parent / "checkpoints"
        self.capture_root = Path(env["LOOPX_WAKE_LOG_DIR"]) / receipt["turn_id"] / "decisions"

    def __enter__(self):
        path = self.env.get("LOOPX_CHECKPOINT_REQUEST")
        if not path or self.receipt["mode"] != "heartbeat" or self.receipt["stage"] != "execute":
            return self
        try:
            self.request, self.raw = read_checkpoint_request(path)
        except (OSError, ValueError):
            return self  # The existing preflight records declaration failure.
        if self.request.get("trigger") != "fresh_replan":
            return self
        if Path(self.request["destination"]) != self.root or self.root.resolve() != self.root:
            raise ValueError("fresh_replan destination must be this trial's checkpoints directory")
        try:
            # AF_UNIX paths are short; trial roots need not be. No ambient socket
            # or socket authority is inherited from the caller's environment.
            directory = self.stack.enter_context(tempfile.TemporaryDirectory(prefix="loopx-ckpt-", dir="/tmp"))
            self.socket_path = str(Path(directory).resolve() / "host.sock")
            self.listener = self.stack.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
            self.listener.bind(self.socket_path)
            self.listener.listen(1)
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def _first_replan(self, intent):
        return intent["capture"] and _mark_replan_attempt(
            self.root, intent["obligation_id"], self.receipt["turn_id"],
            hashlib.sha256(self.raw).hexdigest())

    def _capture(self, held, deadline):
        """Capture declared inert files; never downgrade a full-state request."""
        if any(self.request.get(key) for key in ("native_session", "loopx_state", "evaluation_opportunity")):
            raise ValueError("Interrupted full-state capture requires qualified native pending history and the controller writer barrier")
        destination = self.root / held["intent"]["obligation_id"]
        metadata = {"operator_metadata": self.request["point_metadata"],
                    "request_sha256": hashlib.sha256(self.raw).hexdigest(),
                    "session": self.receipt["session"], "replan": held["intent"],
                    "decision_sha256": held["sha256"],
                    "capture_order": "after_native_interruption",
                    "writer_barrier": "owned_native_process_group_only"}
        window = min(time.time() + 30, time.time() + deadline - time.monotonic())
        if window <= time.time():
            raise TimeoutError("original wake budget expired before capture")
        components = self.request["components"]
        probe = effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "probe", "components": components, "deadline_epoch_ms": window * 1000,
        }, timeout=max(.1, window - time.time()), retry_safe=False)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        return effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "capture", "components": components, "destination": str(destination),
            "expected_source_sha256": probe["source_sha256"], "point_metadata": metadata,
            "deadline_epoch_ms": window * 1000,
        }, timeout=max(.1, window - time.time()), retry_safe=False)

    def run(self, execution, body, wake, session_wake, stdout, stderr, timeout):
        from .worker import child_process, native_command
        from .sessions import BenchmarkSessionWake

        deadline = time.monotonic() + timeout if timeout is not None else float("inf")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("checkpoint source wake", timeout)
            command = native_command(self.env, execution, "execute", wake, session_wake)
            continuation = None
            with child_process(command, env=self.env, stdout=stdout, stderr=stderr) as process:
                process.stdin.write(body)
                process.stdin.close()
                while process.poll() is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    if not select.select([self.listener], [], [], min(.1, remaining))[0]:
                        continue
                    with held_quota_decision(self.listener, self.capture_root,
                            goal_id=self.env["LOOPX_GOAL_ID"], agent_id=self.env["LOOPX_AGENT_ID"],
                            turn_instance_id=None, native_process=process,
                            interrupt_timeout=min(10, remaining), interrupt_when=self._first_replan,
                            deadline=deadline) as held:
                        if "native_process_return_code" not in held:
                            continue
                        stdout.flush()
                        session_wake.observe(wake / "stdout.jsonl")
                        event = {"obligation_id": held["intent"]["obligation_id"],
                                 "decision_path": str(held["path"]), "decision_sha256": held["sha256"],
                                 "captured": False, "research_point_qualified": False}
                        self.receipt.setdefault("checkpoint_handoffs", []).append(event)
                        try:
                            event.update(self._capture(held, deadline), captured=True)
                        except Exception as error:
                            # A missed optional point cannot discard the original
                            # committed guard or turn a later retry into its capture.
                            event.update(error_kind=type(error).__name__, error=str(error)[:500])
                    continuation = held.get("continuation_body")
                    if continuation is not None:
                        break
                return_code = process.wait(timeout=max(.1, deadline - time.monotonic()) if timeout is not None else None)
            if continuation is None:
                return return_code
            # A fresh-context profile still resumes THIS source session after an
            # interruption. Only the next ordinary Host wake uses its own policy.
            source_session_id = self.receipt["session"]["session_id"]
            execution = replace(execution, context="resume")
            session_wake = BenchmarkSessionWake(self.env, execution, self.receipt)
            if not source_session_id or session_wake.session_id != source_session_id:
                raise ValueError("checkpoint source continuation lost its native session")
            body = continuation
