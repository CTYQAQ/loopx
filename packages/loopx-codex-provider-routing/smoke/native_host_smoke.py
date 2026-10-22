"""Offline smoke for native App shutdown during an exhausted-account handoff."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT / "src"))

native_host = importlib.import_module("loopx_codex_provider_routing.native_host")

EXE = "/Applications/ChatGPT.app/Contents/MacOS/ChatGPT"


class Apps:
    def __init__(self, pids):
        self._pids = iter(pids)

    def executable(self, _bundle):
        return EXE

    def app_bundle(self, _spec, _role, _home):
        return Path("/Applications/ChatGPT.app")

    def running(self, _exe, _spec):
        return next(self._pids, None)


def adapter(pids):
    value = native_host.NativeHost.__new__(native_host.NativeHost)
    value.apps = Apps(pids)
    value.spec = {}
    value.home = Path("/tmp/native-host-smoke")
    value.expected = "revision"
    value.not_after = None
    value.revision = lambda: "revision"
    return value


def close_with(launch_time, *, force):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((list(command), kwargs.get("input", "")))

        class Result:
            stdout = json.dumps({"launch_time": launch_time, "accepted": True})
            returncode = 0

        return Result()

    original = native_host.subprocess.run
    native_host.subprocess.run = fake_run
    try:
        # Ownership is checked before the quit, after the identity probe and
        # once while waiting for the App root to exit.
        closed = adapter([4242, 4242, 4242, None]).close_client("revision", force=force)
    finally:
        native_host.subprocess.run = original
    return closed, calls


# An App started outside Launch Services reports a nil launchDate. The force
# path must still terminate it instead of failing the handoff as changed.
closed, calls = close_with(None, force=True)
assert closed is True
assert len(calls) == 2, calls
assert "launch_time" in calls[0][1] and "terminate" not in calls[0][1], calls[0]
assert "forceTerminate" in calls[1][1], calls[1]
assert calls[1][0][-1] == "unknown", calls[1][0]

# A registered App keeps the launch-time fence.
closed, calls = close_with(123.0, force=True)
assert closed is True
assert len(calls) == 2, calls
assert "forceTerminate" in calls[1][1], calls[1]
assert calls[1][0][-1] == "123.0", calls[1][0]

# The default path still asks the App to quit normally.
closed, calls = close_with(None, force=False)
assert closed is True
assert len(calls) == 1, calls
assert "terminate" in calls[0][1] and "forceTerminate" not in calls[0][1], calls[0]

print("native host force handoff smoke passed")
