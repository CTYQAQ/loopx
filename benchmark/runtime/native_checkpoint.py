"""Codex-specific checkpoint inventory and native history readback.

This transport adapter does not decide research validity or adopt authority.
The artifact owner seals its declared native-store members after the Host exits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import time
import tomllib
from pathlib import Path
from typing import Any

from loopx.capabilities.benchmark_toolkit.native_codex_goal import StdioNativeGoalTransport
from .codex import Execution


def _complete_history(request, session_id: str) -> list[dict[str, Any]]:
    turns = []
    cursor = None
    seen = set()
    while True:
        page = request("thread/turns/list", {
            "threadId": session_id, "sortDirection": "asc", "itemsView": "full", "limit": 100,
            **({"cursor": cursor} if cursor else {}),
        })
        if not isinstance(page.get("data"), list):
            raise ValueError("native checkpoint history page is unavailable")
        turns.extend(page["data"])
        cursor = page.get("nextCursor")
        if not cursor:
            break
        if cursor in seen:
            raise ValueError("native checkpoint repeated history cursor")
        seen.add(cursor)
    if not turns or any(t.get("status") not in {"completed", "failed", "interrupted"} or not t.get("id") for t in turns):
        raise ValueError("native checkpoint requires complete retained turns")
    if turns[-1].get("status") != "completed":
        raise ValueError("native checkpoint requires a completed final cursor")
    ids = [t["id"] for t in turns]
    if len(set(ids)) != len(ids):
        raise ValueError("native checkpoint history contains duplicate turns")
    return turns


def native_session_checkpoint(env: dict[str, str], session_id: str, *, timeout: float) -> dict[str, Any]:
    home = Path(env["CODEX_HOME"])
    if not session_id or timeout <= 0:
        raise ValueError("native checkpoint requires a session and an open capture window")
    deadline = time.monotonic() + timeout
    version = subprocess.check_output([env["CODEX_BIN"], "--version"], env=env, text=True,
                                      timeout=timeout).strip()
    # Native storage is opaque. Retain both rollout and paginated/index stores;
    # auth, logs, memories and the symlinked skill installation are not members.
    with StdioNativeGoalTransport.spawn(
        [env["CODEX_BIN"], "-c", "features.goals=false", "app-server", "--listen", "stdio://"],
        cwd=env["LOOPX_PROJECT"], env=env, stderr=subprocess.DEVNULL,
        response_timeout_sec=timeout,
    ) as transport:
        def request(method, params):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("native checkpoint window closed")
            transport.response_timeout_sec = remaining
            return transport.request(method, params)
        request("initialize", {"clientInfo": {"name": "loopx_checkpoint", "version": "0.1.0"},
                                          "capabilities": {"experimentalApi": True}})
        transport.notify("initialized", {})
        readback = request("thread/read", {"threadId": session_id, "includeTurns": False})
        if readback.get("thread", {}).get("id") != session_id:
            raise ValueError("native checkpoint session identity mismatch")
        workspace = Path(env["LOOPX_PROJECT"])
        if (not workspace.is_absolute() or workspace.resolve(strict=True) != workspace or
                readback["thread"].get("cwd") != str(workspace)):
            raise ValueError("native checkpoint session workspace differs from the declared code basis")
        turns = _complete_history(request, session_id)
        ids = [t["id"] for t in turns]
    members = []
    for entry in sorted(home.iterdir()):
        if entry.name in {"sessions", "archived_sessions", "session_index.jsonl"} or re.fullmatch(
            r"(?:state|thread_history)_[0-9]+\.sqlite", entry.name
        ):
            members.append({"id": "native_" + entry.name.replace(".", "_"), "source_path": str(entry)})
    if not any(m["id"].startswith("native_state_") for m in members):
        raise ValueError("native checkpoint session index is unavailable")
    encoded = json.dumps(turns, sort_keys=True, separators=(",", ":")).encode()
    return {"components": members, "metadata": {
        "session_id": session_id, "completed_turn_id": ids[-1], "turn_ids": ids,
        "visible_history_sha256": hashlib.sha256(encoded).hexdigest(), "host_version": version,
        "guest_home": str(home), "guest_workspace": env["LOOPX_PROJECT"],
        "path_profile": "preserve_guest_paths_in_isolated_environment",
        "config_sha256": hashlib.sha256((home / "config.toml").read_bytes()).hexdigest(),
        "store_members": [{"id": c["id"], "name": Path(c["source_path"]).name} for c in members],
        "model_calls": 0, "execution_authority_granted": False,
    }}


def fork_native_checkpoint(env: dict[str, str], unit: Path, expected_sha256: str,
                           config_path: Path, *, timeout: float) -> dict[str, Any]:
    """Reconstruct one native branch in a new, fixed-path isolated environment.

    No model starts and no LoopX binding is registered. Existing session/Goal
    owners must separately admit this new session before useful continuation.
    """
    from loopx.control_plane.effect_runtime import effect_runtime_result

    deadline = time.monotonic() + timeout
    effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "verify", "root": str(unit), "expected_sha256": expected_sha256,
    }, timeout=timeout)
    manifest = json.loads((unit / "checkpoint.json").read_text())
    native = manifest["point_metadata"]["native_session"]
    home = Path(env["CODEX_HOME"])
    if str(home) != native["guest_home"] or env["LOOPX_PROJECT"] != native["guest_workspace"]:
        raise ValueError("native checkpoint requires its qualified fixed guest paths")
    if home.exists() or home.is_symlink():
        raise ValueError("native checkpoint destination home must be new")
    config = config_path.read_bytes()
    if hashlib.sha256(config).hexdigest() != native["config_sha256"]:
        raise ValueError("native checkpoint effective configuration differs")
    version = subprocess.check_output([env["CODEX_BIN"], "--version"], env=env, text=True,
                                      timeout=max(.1, deadline - time.monotonic())).strip()
    if version != native["host_version"]:
        raise ValueError("native checkpoint Host version differs")
    names = set()
    for member in native["store_members"]:
        name = member["name"]
        if (name in names or member["id"] != "native_" + name.replace(".", "_") or
                not (name in {"sessions", "archived_sessions", "session_index.jsonl"} or
                     re.fullmatch(r"(?:state|thread_history)_[0-9]+\.sqlite", name))):
            raise ValueError("native checkpoint member inventory is invalid")
        names.add(name)
    home.mkdir(parents=True, mode=0o700)
    for member in native["store_members"]:
        source, destination = unit / "components" / member["id"], home / member["name"]
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
    (home / "config.toml").write_bytes(config)
    with StdioNativeGoalTransport.spawn(
        [env["CODEX_BIN"], "-c", "features.goals=false", "app-server", "--listen", "stdio://"],
        cwd=env["LOOPX_PROJECT"], env=env, stderr=subprocess.DEVNULL,
        response_timeout_sec=max(.1, deadline - time.monotonic()),
    ) as transport:
        def request(method, params):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("native checkpoint reconstruction window closed")
            transport.response_timeout_sec = remaining
            return transport.request(method, params)
        request("initialize", {"clientInfo": {"name": "loopx_checkpoint", "version": "0.1.0"},
                               "capabilities": {"experimentalApi": True}})
        transport.notify("initialized", {})
        fork = request("thread/fork", {"threadId": native["session_id"],
            "lastTurnId": native["completed_turn_id"], "cwd": env["LOOPX_PROJECT"],
            "sandbox": "read-only", "approvalPolicy": "never", "deferGoalContinuation": True,
            "excludeTurns": True})
        session = fork.get("thread", {}).get("id")
        if not session or session == native["session_id"]:
            raise ValueError("native checkpoint did not create a distinct session")
        turns = _complete_history(request, session)
        encoded = json.dumps(turns, sort_keys=True, separators=(",", ":")).encode()
        if ([t["id"] for t in turns] != native["turn_ids"] or
                hashlib.sha256(encoded).hexdigest() != native["visible_history_sha256"]):
            raise ValueError("native checkpoint reconstructed history differs")
    return {"session_id": session, "completed_turn_id": native["completed_turn_id"],
            "visible_history_sha256": native["visible_history_sha256"], "history_verified": True,
            "model_calls": 0, "execution_authority_granted": False, "continuation_qualified": False}


def admit_native_checkpoint_session(env: dict[str, str], unit: Path, expected_sha256: str,
                                    config_path: Path, *, execution: Execution, timeout: float) -> dict[str, Any]:
    """Explicit controller admission of a verified fork into a destination Goal.

    Existing native fork, Goal lifetime and session owners retain their rules.
    Historical research/effects and evaluator/whole-trial budget are not adopted
    by this participant. Admission alone never launches work or a model.
    """
    from loopx.control_plane.effect_runtime import effect_runtime_result
    from loopx.agent_registry import load_goal_from_registry, require_registered_agent_id
    from loopx.control_plane.projects.registry_codec import (
        project_registry_transaction,
    )
    from loopx.control_plane.turn_driver.codex_sessions import select_codex_cli_session
    from .sessions import BenchmarkSessionWake

    if execution.context != "resume" or execution.mode != "heartbeat":
        raise ValueError("checkpoint session admission requires explicit heartbeat resume")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("checkpoint session admission timeout must be finite and positive")
    deadline = time.monotonic() + timeout
    effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "verify", "root": str(unit), "expected_sha256": expected_sha256,
    }, timeout=timeout)
    metadata = json.loads((unit / "checkpoint.json").read_bytes())["point_metadata"]
    source_profile = (metadata.get("session") or {}).get("session_profile_digest")
    if not isinstance(source_profile, str) or not re.fullmatch(r"[0-9a-f]{64}", source_profile):
        raise ValueError("checkpoint session admission requires its captured source session profile")
    if tomllib.loads(config_path.read_text()).get("features", {}).get("goals") is not False:
        raise ValueError("checkpoint session admission requires native automatic Goals explicitly disabled")
    if hashlib.sha256(config_path.read_bytes()).hexdigest() != metadata["native_session"]["config_sha256"]:
        raise ValueError("native checkpoint effective configuration differs")
    source = metadata.get("loopx_state", {})
    lineage = {"goal_id": env["LOOPX_GOAL_ID"], "agent_id": env["LOOPX_AGENT_ID"]}
    if (not source.get("goal_id") or not source.get("agent_id") or
            any(source[key] == lineage[key] for key in lineage)):
        raise ValueError("checkpoint session admission requires distinct destination Goal and Agent identities")
    registry = Path(env["LOOPX_REGISTRY"])
    # Use the current governed benchmark path. The lifecycle-only source-session
    # profile cannot run governed Turns and is rejected by the registry owner.
    goal = load_goal_from_registry(registry, lineage["goal_id"])
    require_registered_agent_id(registry_path=registry, goal_id=lineage["goal_id"], agent_id=lineage["agent_id"])
    if select_codex_cli_session(Path(env["LOOPX_RUNTIME_ROOT"]), lineage=lineage,
                               session_scope="agent") is not None:
        # The original session binding is also the admission receipt. Only a
        # source-bound committed admission can recover a lost controller reply;
        # an unrelated binding or a partial native fork is never replaced.
        with project_registry_transaction(registry, operation="checkpoint_session_binding", agent_id=lineage["agent_id"]):
            current = load_goal_from_registry(registry, lineage["goal_id"])
            require_registered_agent_id(registry_path=registry, goal_id=lineage["goal_id"], agent_id=lineage["agent_id"])
            if current != goal:
                raise ValueError("destination Goal configuration changed during native checkpoint readback")
            wake = BenchmarkSessionWake(env, execution, {})
            binding = select_codex_cli_session(wake.root, lineage=lineage, session_scope="agent",
                                              goal_admission=wake.admission)
            if not binding or binding.get("checkpoint_artifact_sha256") != expected_sha256:
                raise ValueError("destination session binding already exists without this checkpoint admission")
            if wake.digest != source_profile:
                raise ValueError("checkpoint source session profile changed")
            native = metadata["native_session"]
            observed = native_session_checkpoint(env, binding["session_id"],
                timeout=max(.1, deadline - time.monotonic()))["metadata"]
            keys = ("completed_turn_id", "turn_ids", "visible_history_sha256", "host_version",
                    "guest_home", "guest_workspace", "config_sha256")
            if binding["session_id"] == native["session_id"] or any(observed[key] != native[key] for key in keys):
                raise ValueError("checkpoint admitted native session advanced or differs; use its existing continuation")
            return {"session_id": binding["session_id"], "completed_turn_id": observed["completed_turn_id"],
                "visible_history_sha256": observed["visible_history_sha256"], "history_verified": True,
                "model_calls": 0, "execution_authority_granted": False, "continuation_qualified": False,
                "destination_goal_ref": wake.goal_ref, "destination_agent_id": lineage["agent_id"],
                "session_binding_admitted": True, "session_profile_digest": wake.digest}
    result = fork_native_checkpoint(env, unit, expected_sha256, config_path,
                                    timeout=max(.1, deadline - time.monotonic()))
    # Hold the existing registry owner's fence through the session write. This
    # does not mint a lease, task or work admission from historical state.
    with project_registry_transaction(registry, operation="checkpoint_session_binding", agent_id=lineage["agent_id"]):
        current = load_goal_from_registry(registry, lineage["goal_id"])
        require_registered_agent_id(registry_path=registry, goal_id=lineage["goal_id"], agent_id=lineage["agent_id"])
        if current != goal:
            raise ValueError("destination Goal configuration changed during native checkpoint reconstruction")
        receipt: dict[str, Any] = {}
        wake = BenchmarkSessionWake(env, execution, receipt)
        wake._bind_verified_fork(result["session_id"], source_profile, expected_sha256)
    return {**result, "destination_goal_ref": wake.goal_ref, "destination_agent_id": lineage["agent_id"],
            "session_binding_admitted": True, "session_profile_digest": wake.digest}


def main(argv=None) -> None:
    """Controller entrypoint inside an explicitly prepared isolated guest."""
    import os
    from .codex import SANDBOXES

    parser = argparse.ArgumentParser(description="Admit a checkpoint's native session participant; no model starts.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--config-path", type=Path, required=True)
    parser.add_argument("--sandbox", choices=SANDBOXES, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=30)
    args = parser.parse_args(argv)
    required = ("CODEX_BIN", "CODEX_HOME", "LOOPX_PROJECT", "LOOPX_REGISTRY", "LOOPX_RUNTIME_ROOT",
                "LOOPX_GOAL_ID", "LOOPX_AGENT_ID", "MODEL_NAME", "REASONING_EFFORT")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        parser.error("Explicit destination controller environment is missing: " + ", ".join(missing))
    result = admit_native_checkpoint_session(dict(os.environ), args.root, args.expected_sha256,
        args.config_path, execution=Execution(context="resume", sandbox=args.sandbox), timeout=args.timeout_seconds)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
