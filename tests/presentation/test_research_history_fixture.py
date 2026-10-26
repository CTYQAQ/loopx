"""Captured reader fixtures preserve source bytes and launch only synthetic Python."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from threading import Thread
from types import SimpleNamespace
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from loopx.capabilities.benchmark_toolkit.trajectory_assessment_store import (
    PackUnavailable, _verify_sources,
)

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "research_history_fixture", ROOT / "examples/personal-workspace-browser/research-history-backend.py",
)
assert SPEC and SPEC.loader
backend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backend)


@pytest.mark.parametrize("fixture", ["research-native-readback.json", "research-synthesis-readback.json"])
def test_captured_analysis_materialization_preserves_declared_bytes_and_excerpts(tmp_path, fixture):
    capture = json.loads((ROOT / "apps/presentation/dashboard/src/data/fixtures" / fixture).read_bytes())
    analysis = capture["analyses"][0]
    source_root = tmp_path / "analysis"
    input_path = backend.materialize_analysis(source_root, analysis)
    assert json.loads(input_path.read_bytes()) == analysis["input"]
    for artifact, text in analysis["artifacts"].items():
        assert (source_root / artifact).read_bytes() == text.encode("utf-8")
    record = {"source_root": str(source_root), "input": analysis["input"]}
    for source in record["input"]["sources"]:
        assert hashlib.sha256((source_root / source["artifact"]).read_bytes()).hexdigest() == source["artifact_sha256"]
    _verify_sources(record)
    source = source_root / record["input"]["sources"][0]["artifact"]
    original = source.read_bytes()
    source.write_bytes(b"X" + original[1:])
    with pytest.raises(PackUnavailable, match="source_changed"):
        _verify_sources(record)
    source.write_bytes(original)
    changed_excerpt = copy.deepcopy(record)
    changed_excerpt["input"]["sources"][0]["text"] = "An unsupported excerpt"
    with pytest.raises(PackUnavailable, match="excerpt_mismatch"):
        _verify_sources(changed_excerpt)


def test_synthetic_provider_uses_selected_python_with_real_arguments_pipes_and_exit(tmp_path):
    provider = backend.write_python_host_fixture(
        tmp_path / "synthetic provider",
        ("#!python\n" if sys.platform == "win32" else f"#!{sys.executable}\n")
        + "import json,os,sys\n"
        "print(json.dumps([sys.argv[1:],sys.stdin.read(),os.getcwd(),sys.executable]))\n"
        "sys.stderr.write('Synthetic diagnostic')\nraise SystemExit(7)\n",
    )
    arguments = ["exec", "argument with spaces", "-o", "result path.json"]
    result = subprocess.run([str(provider), *arguments], cwd=tmp_path, input="Synthetic input",
                            capture_output=True, text=True, encoding="utf-8", timeout=10, check=False)
    assert result.returncode == 7
    assert result.stderr == "Synthetic diagnostic"
    assert json.loads(result.stdout) == [arguments, "Synthetic input", str(tmp_path), sys.executable]


def test_captured_analysis_fixture_serves_real_saved_results_without_source_mutation(tmp_path, monkeypatch):
    fixture = ROOT / "apps/presentation/dashboard/src/data/fixtures/research-synthesis-readback.json"
    capture = json.loads(fixture.read_bytes())

    class InspectingServer(backend.ChatHTTPServer):
        def serve_forever(self, poll_interval=0.5):
            worker = Thread(target=super().serve_forever, daemon=True)
            worker.start()
            base = f"http://127.0.0.1:{self.server_port}"

            def read(path):
                with urlopen(base + path, timeout=10) as response:
                    return json.load(response)

            try:
                log = self.runtime_root / "goals" / capture["goal_id"] / "explore-result-log.jsonl"
                events = [json.loads(line) for line in log.read_bytes().decode("utf-8").splitlines()]
                assert events == capture["events"]
                history = read("/api/chat/explore-results?" + urlencode({
                    "goal_id": capture["goal_id"], "view": "history",
                }))
                assert {event["title"] for event in history["items"]} == {event["title"] for event in capture["events"]}
                for language in capture["analyses"][0]["outputs"]:
                    view = read("/api/chat/research-analysis?" + urlencode({
                        "goal_id": capture["goal_id"], "output_language": language,
                    }))["pack"]
                    assert view["availability"] == "available"
                    assert view["job"]["status"] == "succeeded"
                    assert view["result"]["output_language"] == language
                    assert view["result"]["assessments"]
                readback = read("/fixture/readback")
                assert readback["source_unchanged"] is True
                assert readback["mutation_attempts"] == 0
                assert readback["fixture_sha256"] == hashlib.sha256(fixture.read_bytes()).hexdigest()
                evidence = self.runtime_root.parent / f"analysis-{capture['goal_id']}-0" / "facts.txt"
                original = evidence.read_bytes()
                evidence.write_bytes(b"X" + original[1:])
                try:
                    with pytest.raises(PackUnavailable, match="source_changed"):
                        backend.execute_pack(runtime_root=self.runtime_root, owner_goal_id=capture["goal_id"],
                            pack_id=view["pack_id"], analysis_key=view["analysis_key"], output_language=language,
                            codex="must-not-launch-for-changed-source")
                finally:
                    evidence.write_bytes(original)
                for method in ("POST", "PUT", "DELETE"):
                    with pytest.raises(HTTPError) as rejected:
                        urlopen(Request(base + "/api/chat/turns", data=b"{}", method=method), timeout=10)
                    assert rejected.value.code == 405
                assert read("/fixture/readback")["mutation_attempts"] == 3
                assert read("/fixture/readback")["source_unchanged"] is True
            finally:
                self.shutdown()
                worker.join(timeout=10)

    monkeypatch.setattr(backend, "ChatHTTPServer", InspectingServer)
    # Pytest's session TEMP adds a long prefix unrelated to this byte/process
    # contract. Keep only this disposable fixture in the native temp namespace.
    parent = Path(os.environ["LOCALAPPDATA"]) / "Temp" if os.name == "nt" else tmp_path
    monkeypatch.setattr(backend, "tempfile", SimpleNamespace(
        TemporaryDirectory=lambda **_: tempfile.TemporaryDirectory(prefix="lxr-", dir=parent),
    ))
    monkeypatch.setattr(backend, "signal", SimpleNamespace(SIGTERM=backend.signal.SIGTERM, signal=lambda *_: None))
    monkeypatch.setattr(sys, "argv", [str(SPEC.origin), "--fixture", str(fixture)])
    backend.main()
