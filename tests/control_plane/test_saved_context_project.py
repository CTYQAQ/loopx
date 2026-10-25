"""Saved quota presentation never reevaluates the Goal or renews authority."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys

import pytest

from tests.control_plane.test_quota_settlement_cli import _run_cli


@pytest.mark.parametrize("failure", [None, "digest", "goal", "agent", "turn", "symlink", "object"])
def test_saved_projection_checks_original_before_any_publication(tmp_path, failure):
    original = {"ok": True, "goal_id": "saved-goal", "agent_identity": {"agent_id": "saved-agent"},
        "heartbeat_receipt": {"turn_instance_id": "saved-turn"}, "requirement": "保留完整要求🙂" * 100}
    path = tmp_path / "original.json"
    path.write_text(json.dumps(original, ensure_ascii=False))
    if failure == "object":
        path.write_text("[]")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if failure == "symlink":
        source = tmp_path / "source.json"
        path.rename(source)
        path.symlink_to(source)
    args = ["quota", "context-project", "--capture-file", str(path), "--capture-sha256", digest,
        "--goal-id", "saved-goal", "--agent-id", "saved-agent", "--turn-instance-id", "saved-turn"]
    for name in ("digest", "goal", "agent", "turn"):
        if failure == name:
            option = {"digest": "--capture-sha256", "goal": "--goal-id", "agent": "--agent-id", "turn": "--turn-instance-id"}[name]
            args[args.index(option) + 1] = "0" * 64 if name == "digest" else "foreign"
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
    code, result = _run_cli(registry, runtime, *args, cwd=tmp_path)
    assert not registry.exists() and not runtime.exists()
    if failure:
        assert code != 0 and not result["ok"] and "command" not in result
    else:
        assert code == 0 and result["ok"] and result["mode"] == "full"
        assert result["path"] == str(path) and result["sha256"] == digest
        assert result["recovery"]["path"] == str(path)
        command = shlex.split(result["command"])
        code, page = _run_cli(registry, runtime, *command[3:], cwd=tmp_path)
        assert code == 0 and page["content"].encode() == path.read_bytes()[:page["next_offset"]]


@pytest.mark.parametrize("root_kind", ["unsafe", "symlink", "missing"])
def test_enabled_cache_preparation_failure_returns_error_with_original_reference(tmp_path, monkeypatch, root_kind):
    from loopx.cli import build_parser
    from loopx.cli_commands.quota_capture import project_saved_context_capture

    path, root, context = tmp_path / "original.json", tmp_path / "cache", tmp_path / "native"
    raw = json.dumps({"ok": True, "goal_id": "saved-goal", "agent_identity": {"agent_id": "saved-agent"},
        "heartbeat_receipt": {"turn_instance_id": "saved-turn"}}).encode()
    path.write_bytes(raw)
    context.mkdir(mode=0o700)
    stale = context / "session.json"
    stale.write_text(json.dumps({"session_id": "native-session", "cwd": str(tmp_path),
        "recovery": {"goal_id": "saved-goal", "agent_id": "saved-agent",
            "path": str(tmp_path / "prior.json"), "sha256": "a" * 64}}))
    stale_bytes = stale.read_bytes()
    monkeypatch.setenv("CODEX_THREAD_ID", "native-session")
    monkeypatch.chdir(tmp_path)
    if root_kind == "unsafe":
        root.mkdir(mode=0o755)
        root.chmod(0o755)
    elif root_kind == "symlink":
        root.symlink_to(context, target_is_directory=True)
    digest = hashlib.sha256(raw).hexdigest()
    args = build_parser().parse_args(["--format", "json", "quota", "context-project",
        "--capture-file", str(path), "--capture-sha256", digest, "--goal-id", "saved-goal",
        "--agent-id", "saved-agent", "--turn-instance-id", "saved-turn",
        "--decision-output-root", str(root), "--context-delivery", "on", "--codex-app", "--codex-context-dir", str(context)])
    result = project_saved_context_capture(args)
    assert result["ok"] is False and result["error_code"] == "quota_decision_transport_failed"
    assert result["recovery"]["path"] == str(path) and result["recovery"]["sha256"] == digest
    assert "prior.json" not in json.dumps(result)
    assert path.read_bytes() == raw and stale.read_bytes() == stale_bytes


def test_saved_projection_wire_failure_keeps_committed_current_recovery(tmp_path, monkeypatch):
    from loopx.cli import build_parser
    from loopx.cli_commands.quota_capture import project_saved_context_capture
    from loopx.control_plane.turn_driver import codex_context, decision_capture

    path, root, context = tmp_path / "original.json", tmp_path / "cache", tmp_path / "native"
    original = {"ok": True, "goal_id": "saved-goal", "agent_identity": {"agent_id": "saved-agent"},
        "heartbeat_receipt": {"turn_instance_id": "saved-turn"}, "requirement": "Current complete requirements."}
    raw = json.dumps(original).encode()
    path.write_bytes(raw)
    root.mkdir(mode=0o700)
    context.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", "native-session")
    monkeypatch.chdir(tmp_path)
    codex_context.codex_context_hook(str(context), {"hook_event_name": "SessionStart", "source": "startup",
        "session_id": "native-session", "cwd": str(tmp_path)})
    write_capture = decision_capture.write_decision_capture

    def fail_optional_wire(path, payload, **kwargs):
        if path.name == "delivery.json":
            raise OSError("optional wire unavailable")
        return write_capture(path, payload, **kwargs)

    monkeypatch.setattr(decision_capture, "write_decision_capture", fail_optional_wire)
    digest = hashlib.sha256(raw).hexdigest()
    args = build_parser().parse_args(["--format", "json", "quota", "context-project",
        "--capture-file", str(path), "--capture-sha256", digest, "--goal-id", "saved-goal",
        "--agent-id", "saved-agent", "--turn-instance-id", "saved-turn", "--context-delivery", "on",
        "--codex-app", "--codex-context-dir", str(context), "--decision-output-root", str(root)])
    result = project_saved_context_capture(args)
    assert result["ok"] and result["mode"] == "full"
    assert result["path"] == str(path) and result["sha256"] == digest
    recovered_path, recovered_digest = codex_context.read_codex_context_reference(str(context),
        goal_id="saved-goal", agent_id="saved-agent")
    recovered = Path(recovered_path).read_bytes()
    assert hashlib.sha256(recovered).hexdigest() == recovered_digest
    assert json.loads(recovered) == original and path.read_bytes() == raw


def test_saved_projection_recovery_failure_returns_typed_error_and_current_reference(
    tmp_path, monkeypatch, capsys,
):
    from loopx.cli import main
    from loopx.control_plane.quota.error_codes import QuotaDecisionTransportError
    from loopx.control_plane.turn_driver import codex_context

    original = {"ok": True, "goal_id": "saved-goal", "agent_identity": {"agent_id": "saved-agent"},
        "heartbeat_receipt": {"turn_instance_id": "saved-turn", "status": "committed"},
        "requirement": "Use the current captured decision, never the prior session reference."}
    raw = json.dumps(original).encode()
    path, root, context = tmp_path / "original.json", tmp_path / "cache", tmp_path / "native"
    path.write_bytes(raw)
    root.mkdir(mode=0o700)
    context.mkdir(mode=0o700)
    stale = context / "session.json"
    stale.write_text(json.dumps({"recovery": {"path": "prior-capture.json"}}))
    stale_bytes = stale.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()

    def refuse_recovery(payload, **kwargs):
        assert payload == original
        raise QuotaDecisionTransportError(
            'Current context recovery could not be published or safely invalidated. '
            'Read the saved original decision/receipt; do not rerun admission.'
        )

    monkeypatch.setattr(codex_context, "project_codex_context", refuse_recovery)
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
    code = main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "quota", "context-project", "--capture-file", str(path), "--capture-sha256", digest,
        "--goal-id", "saved-goal", "--agent-id", "saved-agent", "--turn-instance-id", "saved-turn",
        "--decision-output-root", str(root), "--context-delivery", "on", "--codex-app",
        "--codex-context-dir", str(context)])
    result = json.loads(capsys.readouterr().out)
    assert code == 1 and result["ok"] is False
    assert result["error_code"] == "quota_decision_transport_failed"
    assert result["heartbeat_receipt"] == original["heartbeat_receipt"]
    assert result["capture_sha256"] == digest
    assert result["recovery"]["path"] == str(path) and result["recovery"]["sha256"] == digest
    assert "prior-capture" not in json.dumps(result)
    assert not list(root.glob("*/delivery.json"))
    assert path.read_bytes() == raw and stale.read_bytes() == stale_bytes
    assert not registry.exists() and not runtime.exists()
    code, recovered = _run_cli(registry, runtime, "quota", "context-read",
        "--capture-file", result["recovery"]["path"], "--capture-sha256", result["recovery"]["sha256"])
    assert code == 0 and recovered["eof"] and recovered["content"].encode() == raw
    assert not registry.exists() and not runtime.exists()


def test_preflight_instruction_pins_both_projection_and_full_fallback(tmp_path):
    from benchmark.runtime.context_delivery import preflight_context_instruction

    source = tmp_path / "saved packet.json"
    env = {"LOOPX_CLI": "/fixture/bin/loopx", "LOOPX_GOAL_ID": "goal",
        "LOOPX_AGENT_ID": "agent", "LOOPX_CODEX_CONTEXT_DIR": str(tmp_path / "private native")}
    body = preflight_context_instruction(env, source, "a" * 64, "original-turn")
    commands = [shlex.split(block.split("\n```", 1)[0]) for block in body.split("```sh\n")[1:]]
    assert len(commands) == 2
    assert commands[0][6] == "context-project" and commands[1][6] == "context-read"
    for argv in commands:
        assert argv[:3] == [env["LOOPX_CLI"], "--cli-bin", env["LOOPX_CLI"]]
        assert argv[argv.index("--capture-file") + 1] == str(source)
        assert argv[argv.index("--capture-sha256") + 1] == "a" * 64
        assert "should-run" not in argv and "spend-slot" not in argv
    assert commands[0][commands[0].index("--turn-instance-id") + 1] == "original-turn"


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"), reason="Explicit native Codex; loopback inference only")
@pytest.mark.parametrize("host", ["app_server", "benchmark"])
def test_native_saved_preflight_uses_current_input_without_guard_replay(tmp_path, monkeypatch, host):
    from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime, read_fixture_todos
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.quota.turn_envelope import prepare_context_delivery, restore_context_delivery
    from tests.control_plane.codex_checkpoint_fixture import local_inference_provider
    from tests.control_plane.test_codex_context_boundary import _app_server, _configure_provider, _native_fixture, _input_text
    from tests.control_plane.test_codex_context_capture import _delivered
    from tests.control_plane.test_quota_settlement_cli import AGENT_ID, GOAL_ID, TODO_ID, _write_fixture, _spend_run_count, _heartbeat_receipt_count

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    binary, env, workspace, home = _native_fixture(tmp_path)
    env.update(NODE_OPTIONS="--experimental-sqlite", LOOPX_USAGE_PING="0", CODEX_ROLLOUT_TRACE_ROOT=str(workspace / "traces"))
    project, runtime, registry = _write_fixture(workspace)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    todos = read_fixture_todos(state)
    todos[0]["text"] = todos[0]["title"] = "Exact requirement with retained provenance.\n" * 240
    initialize_canonical_authority(runtime, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=todos, handoff_mode="soft_claim"),
        state_path=state, provider="sqlite")
    sources = []
    for name in (("cold", "warm") if host == "app_server" else ()):
        code, decision = _run_cli(registry, runtime, "quota", "should-run", "--goal-id", GOAL_ID,
            "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", "saved-native-turn", "--codex-app", cwd=project)
        assert code == 0, decision
        source = workspace / (name + "-source.json")
        source.write_text(json.dumps(decision, ensure_ascii=False))
        sources.append((source, source.read_bytes()))
    receipts = _heartbeat_receipt_count(runtime, "saved-native-turn")
    context, cache = workspace / "context", workspace / "cache"
    context.mkdir(mode=0o700)
    cache.mkdir(mode=0o700)
    program = os.environ.get("LOOPX_NATIVE_CONTEXT_CLI") or str(Path(sys.executable).with_name("loopx"))
    cli = [program, "--cli-bin", program]
    from loopx.control_plane.turn_driver.codex_context import codex_context_configuration
    configuration = codex_context_configuration(str(context), goal_id=GOAL_ID, agent_id=AGENT_ID, cli_argv=cli, scenario="codex_app_heartbeat")
    if host == "app_server":
        (home / "hooks.json").write_text(json.dumps({"hooks": configuration["hooks"]}))
    phase, queue, observed, errors = "cold", [], {}, []
    actual_turn = None

    def response(request, number, message):
        nonlocal phase, queue, actual_turn, receipts
        try:
            if queue:
                command = queue.pop(0)
            elif phase in ("cold", "warm"):
                if host == "benchmark":
                    command = [line for line in _input_text(request).splitlines() if " quota context-project " in line][-1]
                    argv = shlex.split(command)
                    actual_turn = argv[argv.index("--turn-instance-id") + 1]
                    source = Path(argv[argv.index("--capture-file") + 1])
                    sources.append((source, source.read_bytes()))
                    receipts = _heartbeat_receipt_count(runtime, actual_turn)
                    assert hashlib.sha256(source.read_bytes()).hexdigest() == argv[argv.index("--capture-sha256") + 1]
                else:
                    source, raw = sources[0 if phase == "cold" else 1]
                    command = shlex.join([*cli, "--format", "json", "quota", "context-project",
                        "--capture-file", str(source), "--capture-sha256", hashlib.sha256(raw).hexdigest(),
                        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", "saved-native-turn",
                        "--context-delivery", "on", "--codex-app", "--codex-context-dir", str(context), "--decision-output-root", str(cache)])
                phase += "_ready"
            elif phase.endswith("_ready"):
                name = phase.split("_")[0]
                output = [item["output"] for item in request["input"] if item.get("type") == "function_call_output"][-1]
                descriptor = json.loads(output.split("\nOutput:\n", 1)[-1])
                assert descriptor["ok"] and descriptor["mode"] == ("full" if name == "cold" else "delta")
                wire = json.loads((Path(descriptor["path"]).parent / "presentation.json").read_text()) if name == "cold" else json.loads(Path(descriptor["path"]).read_text())
                assert descriptor["target"] == wire["target"]
                observed[name] = (wire, descriptor)
                data = Path(descriptor["path"]).read_bytes()
                for offset in range(0, len(data), 4096):
                    # Synthetic captures are ASCII JSON; public reader owns every byte.
                    queue.append(shlex.join([*cli, "--format", "json", "quota", "context-read",
                        "--capture-file", descriptor["path"], "--capture-sha256", descriptor["sha256"],
                        "--offset", str(offset)]))
                phase = name + "_read"
                command = queue.pop(0)
            else:
                name = phase.split("_")[0]
                descriptor = observed[name][1]
                assert _delivered(request, descriptor["sha256"]) == Path(descriptor["path"]).read_text().rstrip("\n")
                if name == "warm":
                    phase = "done"
                    return message
                # Full wire context is observed exactly, then reuse in the next call.
                phase = "warm"
                if host == "benchmark":
                    return message  # A new worker wake owns its own original guard.
                return response(request, number, message)
            return {"type": "function_call", "id": f"fc_{number}", "call_id": f"call_{number}",
                "name": "exec_command", "arguments": json.dumps({"cmd": command, "max_output_tokens": 3000})}
        except Exception as error:
            errors.append(error)
            return message

    with local_inference_provider(response) as (url, _, _):
        if host == "benchmark":
            from benchmark.runtime.worker import run_once
            skills = workspace / "skills"
            skills.mkdir()
            declaration = workspace / "checkpoint-request.json"
            declaration.write_text(json.dumps({"trigger": "fresh_replan", "destination": str(workspace / "checkpoints"),
                "components": [{"id": "task", "source_path": str(state)}], "point_metadata": {}}))
            env.update(LOOPX_CONTEXT_DELIVERY="on", LOOPX_EXECUTION_MODE="heartbeat", LOOPX_ITERATION_CONTEXT="resume",
                LOOPX_TASK_ENTRY="seeded-todo", LOOPX_TASK_STAGE="execute", LOOPX_CHECKPOINT_REQUEST=str(declaration),
                LOOPX_CODEX_HOME=str(home), LOOPX_PROJECT=str(project), LOOPX_SHARED_SKILLS=str(skills),
                LOOPX_REGISTRY=str(registry), LOOPX_RUNTIME_ROOT=str(runtime), LOOPX_GOAL_ID=GOAL_ID, LOOPX_AGENT_ID=AGENT_ID,
                LOOPX_WAKE_LOG_DIR=str(workspace / "wakes"), LOOPX_CLI=program, CODEX_BIN=binary,
                MODEL_NAME="fixture", REASONING_EFFORT="high", OPENAI_BASE_URL=url, OPENAI_API_KEY="synthetic-loopback-only",
                LOOPX_CODEX_TURN_TIMEOUT_SEC="60")
            result = run_once(env)
            assert result["ok"] and result["context_transport"] == {"available": True}, result
            session_id = result["session"]["session_id"]
            assert phase == "warm" and sources[0][0].read_bytes() == sources[0][1]
            # Settle this isolated, independently verified readback once before
            # a new wake. Presentation itself still performs no debit or guard.
            cold_turn = json.loads(sources[0][1])["heartbeat_receipt"]["turn_instance_id"]
            binding = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
                "--todo-id", TODO_ID, "--turn-instance-id", cold_turn)
            code, written = _run_cli(registry, runtime, "refresh-state", *binding,
                "--classification", "saved_context_readback_verified", "--delivery-batch-scale", "single_surface",
                "--delivery-outcome", "outcome_progress", "--delivery-boundary", "in_flight_continuation",
                "--no-global-sync", "--suppress-external-sinks", cwd=project)
            assert code == 0, written
            code, spent = _run_cli(registry, runtime, "quota", "spend-slot", *binding,
                "--slots", "1", "--source", "heartbeat", "--execute", cwd=project)
            assert code == 0 and spent["appended"], spent
            result = run_once(env)
            assert result["ok"] and result["context_transport"] == {"available": True}, result
            assert result["session"]["session_id"] == session_id
        else:
            _configure_provider(home, url)
            with (home / "config.toml").open("a") as config:
                config.write("[sandbox_workspace_write]\nnetwork_access=true\n")
            _app_server(binary, env, workspace, home, True, sandbox="workspace-write")
    assert not errors and phase == "done", errors
    if host == "benchmark":
        turns = [json.loads(raw)["heartbeat_receipt"]["turn_instance_id"] for _, raw in sources]
        assert len(set(turns)) == 2
        assert all(_heartbeat_receipt_count(runtime, turn) == 1 for turn in turns)
        assert _spend_run_count(runtime) == 1  # only the explicit inter-wake settlement above
    else:
        assert _heartbeat_receipt_count(runtime, "saved-native-turn") == receipts and _spend_run_count(runtime) == 0
    cold, warm = observed["cold"][0], observed["warm"][0]
    assert cold["context"] == json.loads(sources[0][1])
    original = json.loads(sources[1][1])
    current = prepare_context_delivery(original,
        scope={key: warm["target"][key] for key in ("goal_id", "agent_id", "session_id", "context_epoch")},
        capture_id=warm["target"]["capture_id"], surface="quota_packet")
    assert restore_context_delivery(warm, baseline=cold, current=current)["context"] == original
    assert all(path.read_bytes() == raw for path, raw in sources)


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"), reason="Explicit native Codex; loopback inference only")
def test_native_discovery_keeps_plugins_out_of_probe_without_changing_solver(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from benchmark.runtime.context_delivery import prepare_context_delivery
    from loopx.control_plane.turn_driver.codex_stdio import StdioNativeGoalTransport
    from tests.control_plane.codex_checkpoint_fixture import local_inference_provider
    from tests.control_plane.test_codex_context_boundary import _configure_provider, _native_fixture

    binary, env, workspace, home = _native_fixture(tmp_path)
    observed = []
    original_spawn = StdioNativeGoalTransport.spawn

    @contextmanager
    def inspect_probe(argv, **kwargs):
        with original_spawn(argv, **kwargs) as native:
            def request(method, params):
                result = native.request(method, params)
                if method == "hooks/list":
                    effective = native.request("config/read", {"cwd": str(workspace), "includeLayers": False})
                    observed.append(effective["config"]["features"])
                return result
            from types import SimpleNamespace
            yield SimpleNamespace(request=request, notify=native.notify)

    monkeypatch.setattr(StdioNativeGoalTransport, "spawn", inspect_probe)
    with local_inference_provider() as (url, calls, _):
        _configure_provider(home, url)
        config = home / "config.toml"
        config.write_text(config.read_text().replace("plugins=false", "plugins=true")
                          .replace("remote_plugin=false", "remote_plugin=true")
                          .replace("plugin_sharing=false", "plugin_sharing=true"))
        original_config = config.read_bytes()
        env.update(CODEX_BIN=binary, LOOPX_PROJECT=str(workspace), LOOPX_GOAL_ID="fixture",
                   LOOPX_AGENT_ID="agent", LOOPX_CLI=str(Path(sys.executable).with_name("loopx")))
        prepare_context_delivery(env, home)
        first_trust = env["LOOPX_CODEX_CONTEXT_TRUST_ARGS"]
        prepare_context_delivery(env, home)
        assert env["LOOPX_CODEX_CONTEXT_TRUST_ARGS"] == first_trust
        assert calls == []  # Hook identity lookup never starts inference.
    assert len(observed) == 2
    for features in observed:
        assert all(features[key] is False for key in ("plugins", "remote_plugin", "plugin_sharing"))
    assert config.read_bytes() == original_config
    assert not list(home.rglob("*.pack")), "discovery must not clone a plugin catalog"


def test_saved_projection_default_off_ignores_available_native_cache(tmp_path, monkeypatch):
    from loopx.cli import build_parser
    from loopx.cli_commands.quota_capture import project_saved_context_capture
    from loopx.control_plane.turn_driver import codex_context

    path, root = tmp_path / "original.json", tmp_path / "uncreated"
    raw = json.dumps({"ok": True, "goal_id": "g", "agent_identity": {"agent_id": "a"},
        "heartbeat_receipt": {"turn_instance_id": "t"}}).encode()
    path.write_bytes(raw)
    def unexpected(*args, **kwargs):
        pytest.fail("default-off saved presentation must not inspect/create native state")
    monkeypatch.setattr(codex_context, "codex_context_directory", unexpected)
    args = build_parser().parse_args(["quota", "context-project", "--capture-file", str(path),
        "--capture-sha256", hashlib.sha256(raw).hexdigest(), "--goal-id", "g", "--agent-id", "a",
        "--turn-instance-id", "t", "--codex-context-dir", str(root), "--decision-output-root", str(root)])
    result = project_saved_context_capture(args)
    assert result["ok"] and result["mode"] == "full" and result["path"] == str(path)
    assert not root.exists() and path.read_bytes() == raw
