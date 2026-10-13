"""Real Docker writer barrier plus native HTTP capture; no model inference."""
from __future__ import annotations

import io
import json
import os
import tarfile
import threading
import time
import uuid

import pytest

from benchmark.edgebench import checkpoint_controller as transport
from benchmark.edgebench.checkpoint_worker import capture_worker
from benchmark.tests import test_checkpoint_controller as native_fixtures
from benchmark.tests.test_checkpoint_controller import register, submit, wait


controllers = native_fixtures.controllers


pytestmark = pytest.mark.skipif(os.environ.get("LOOPX_EDGEBENCH_DOCKER_SMOKE") != "1",
    reason="Requires explicit isolated Docker writer-barrier qualification")


@pytest.fixture
def writer(tmp_path):
    import docker

    client = docker.from_env(timeout=15)
    image = os.environ.get("LOOPX_CHECKPOINT_WRITER_IMAGE", "python:3.12-slim")
    client.images.get(image)  # Never pull implicitly for this test.
    script = """from pathlib import Path
import time
Path('/data').mkdir()
Path('/phase').write_text(str(int(time.time())+7200))
Path('/link').symlink_to('/data', target_is_directory=True)
count=0
while True:
    count+=1
    Path('/data/next').write_text(str(count))
    Path('/data/next').replace('/data/counter')
    time.sleep(.03)
"""
    container = client.containers.run(image, ["python", "-c", script], detach=True,
        name="loopx-checkpoint-writer-" + uuid.uuid4().hex[:12],
        network_disabled=True, mem_limit="64m", nano_cpus=250_000_000)
    request = tmp_path / "worker.json"
    request.write_text(json.dumps({"container_id": container.id,
        "components": [{"id": "native_capsule", "source_path": "/data"}],
        "phase_deadline_path": "/phase"}))
    try:
        wait(lambda: container.exec_run(["test", "-f", "/data/counter"]).exit_code == 0)
        yield container, request
    finally:
        container.remove(force=True)
        assert not client.containers.list(all=True, filters={"id": container.id})
        client.close()


def counter(container):
    stream, _ = container.get_archive("/data/counter")
    with tarfile.open(fileobj=io.BytesIO(b"".join(stream))) as archive:
        return int(archive.extractfile("counter").read())


def test_controller_cli_captures_frozen_worker_with_original_native_point(
        controllers, writer, tmp_path, monkeypatch, capsys):
    source, destination, first, second, files, grades = controllers
    container, request = writer
    token = register(source, first, max_agent_submissions=3, submission_cooldown=3600)
    assert submit(first, token, 7).status_code == 200
    observed = []
    original = transport._post
    def while_frozen(*args, **kwargs):
        container.reload()
        assert container.attrs["State"]["Paused"]
        before = counter(container)
        result = original(*args, **kwargs)
        time.sleep(.15)
        assert counter(container) == before
        observed.append(before)
        return result
    monkeypatch.setattr(transport, "_post", while_frozen)
    token_file = tmp_path / "token"
    token_file.write_text(token)
    unit = tmp_path / "unit"
    transport.main(["--controller-file", str(files[0]), "capture-worker",
        "--token-file", str(token_file), "--capture-id", "7" * 32,
        "--destination", str(unit), "--worker-request", str(request)])
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["research_point_qualified"] is False
    manifest = json.loads((unit / "checkpoint.json").read_bytes())
    metadata = manifest["point_metadata"]["worker_file_snapshot"]
    assert metadata["native_cursor_qualified"] is False and metadata["process_memory_captured"] is False
    assert metadata["native_capsule_component_id"] == "native_capsule_1"
    assert int((unit / "components/native_capsule/counter").read_text()) == observed[0]
    nested = unit / "components/native_capsule_1"
    capsule = json.loads((nested / "checkpoint.json").read_bytes())
    native = capsule["point_metadata"]["native_reference"]
    assert native["opportunity"]["next_agent"] == 2 and native["incumbent"]["round"] == "agent-1"
    assert 3590 < native["opportunity"]["remaining_cooldown_seconds"] <= 3600
    assert 7190 < capsule["point_metadata"]["host_phase_budget"]["remaining_seconds"] <= 7200
    container.reload()
    assert not container.attrs["State"]["Paused"]
    wait(lambda: counter(container) > observed[0])
    assert int((unit / "components/native_capsule/counter").read_text()) == observed[0]
    for secret in (token, source.state.judge.admin_secret):
        assert all(secret.encode() not in p.read_bytes() for p in unit.rglob("*") if p.is_file())
    # This is an inert joint file export, not a shortcut to activation.
    with pytest.raises(ValueError, match="declared capsule"):
        transport.restore(files[1], root=unit, expected_sha256=receipt["sha256"], run_id="branch")
    with pytest.raises(ValueError, match="later worker"):
        capture_worker(files[0], token=token, capture_id="7" * 32,
                       destination=tmp_path / "later", request_file=request)
    assert not (tmp_path / "later").exists() and destination.state.judge.tokens == {} and len(grades) == 1


@pytest.mark.parametrize("fault", ["missing", "symlink", "judge", "pause_reply", "prepaused", "cancel", "unpause"])
def test_worker_capture_failure_releases_only_its_barrier(
        controllers, writer, tmp_path, monkeypatch, fault):
    source, _, first, _, files, _ = controllers
    container, request = writer
    token = register(source, first)
    from docker.errors import NotFound

    expected_error = {"missing": NotFound, "symlink": ValueError, "judge": ValueError,
        "pause_reply": OSError, "prepaused": ValueError, "cancel": KeyboardInterrupt,
        "unpause": OSError}[fault]
    if fault in {"missing", "symlink"}:
        body = json.loads(request.read_bytes())
        body["components"][0]["source_path"] = "/missing" if fault == "missing" else "/link"
        request.write_text(json.dumps(body))
    elif fault == "judge":
        body = json.loads(files[0].read_bytes())
        body["admin_secret"] = "wrong-synthetic-controller"
        files[0].write_text(json.dumps(body))
    elif fault == "pause_reply":
        original = type(container).pause
        def lost_reply(self):
            original(self)
            raise OSError("injected lost pause reply")
        monkeypatch.setattr(type(container), "pause", lost_reply)
    elif fault == "prepaused":
        container.pause()
    elif fault == "cancel":
        def cancel(*args, **kwargs):
            raise KeyboardInterrupt("injected controller cancellation")
        monkeypatch.setattr(transport, "_post", cancel)
    else:
        def unavailable(self):
            raise OSError("injected daemon unpause failure")
        monkeypatch.setattr(type(container), "unpause", unavailable)
    before = counter(container)
    with pytest.raises(expected_error):
        capture_worker(files[0], token=token, capture_id="8" * 32,
                       destination=tmp_path / "unit", request_file=request)
    assert not (tmp_path / "unit").exists()
    container.reload()
    assert container.attrs["State"]["Paused"] == (fault in {"prepaused", "unpause"})
    if fault not in {"prepaused", "unpause"}:
        wait(lambda: counter(container) > before)
    # No failed attempt is silently replaced by a later research state.
    with pytest.raises((FileExistsError, ValueError)):
        capture_worker(files[0], token=token, capture_id="8" * 32,
                       destination=tmp_path / "later", request_file=request)
    assert not (tmp_path / "later").exists()


@pytest.mark.parametrize("separate_bank", [False, True])
def test_concurrent_capture_does_not_release_another_operation_barrier(
        controllers, writer, tmp_path, monkeypatch, separate_bank):
    from concurrent.futures import ThreadPoolExecutor
    from loopx.file_lock import LockAcquireTimeoutError

    source, _, first, _, files, _ = controllers
    container, request = writer
    token = register(source, first)
    other_controller = files[0]
    if separate_bank:
        bank = tmp_path / "other-bank"
        bank.mkdir()
        config = json.loads(files[0].read_bytes())
        config["bank_root"] = str(bank)
        other_controller = tmp_path / "other-controller.json"
        other_controller.write_text(json.dumps(config))
        other_controller.chmod(0o600)
    entered, release = threading.Event(), threading.Event()
    original = transport._post
    def held(*args, **kwargs):
        entered.set()
        assert release.wait(15)
        return original(*args, **kwargs)
    monkeypatch.setattr(transport, "_post", held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(capture_worker, files[0], token=token, capture_id="1" * 32,
            destination=tmp_path / "first", request_file=request)
        try:
            assert entered.wait(15)
            frozen = counter(container)
            with pytest.raises(LockAcquireTimeoutError):
                capture_worker(other_controller, token=token, capture_id="2" * 32,
                    destination=tmp_path / "second", request_file=request, timeout=.5)
            container.reload()
            assert container.attrs["State"]["Paused"] and counter(container) == frozen
            assert not (tmp_path / "second").exists()
        finally:
            release.set()
        assert pending.result(timeout=20)["research_point_qualified"] is False
    container.reload()
    assert not container.attrs["State"]["Paused"]


@pytest.mark.parametrize("entrypoint", ["provider", "cli"])
def test_rejected_pause_preserves_foreign_barrier(
        controllers, writer, tmp_path, monkeypatch, entrypoint):
    from docker.errors import APIError

    source, _, first, _, files, _ = controllers
    container, request = writer
    token = register(source, first)
    original = type(container).pause
    def raced_pause(self):
        # Another actor pauses after our initial state read; the second RPC
        # reaches the real daemon and is determinately rejected.
        original(container)
        original(self)
    monkeypatch.setattr(type(container), "pause", raced_pause)
    def unexpected_native_call(*args, **kwargs):
        pytest.fail("Rejected writer barrier must not capture evaluator state")
    monkeypatch.setattr(transport, "_post", unexpected_native_call)
    unit = tmp_path / "rejected"
    with pytest.raises(APIError) as error:
        if entrypoint == "provider":
            capture_worker(files[0], token=token, capture_id="3" * 32,
                destination=unit, request_file=request)
        else:
            token_file = tmp_path / "token"
            token_file.write_text(token)
            transport.main(["--controller-file", str(files[0]), "capture-worker",
                "--token-file", str(token_file), "--capture-id", "3" * 32,
                "--destination", str(unit), "--worker-request", str(request)])
    assert error.value.response.status_code == 409
    container.reload()
    assert container.attrs["State"]["Paused"]
    assert not unit.exists()
    with pytest.raises(FileExistsError):
        capture_worker(files[0], token=token, capture_id="3" * 32,
            destination=tmp_path / "retry", request_file=request)
