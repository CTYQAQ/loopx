"""Offline smoke for handoff coordination with product-owned clients."""

from __future__ import annotations

import importlib
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT / "src"))

native_host = importlib.import_module("loopx_codex_provider_routing.native_host")

BUNDLE = "/Applications/ChatGPT.app"


class Apps:
    def app_bundle(self, _spec, _role, _home):
        return Path(BUNDLE)


def adapter(home):
    value = native_host.NativeHost.__new__(native_host.NativeHost)
    value.apps = Apps()
    value.spec = {"home": home}
    value.home = home
    return value


with tempfile.TemporaryDirectory() as temporary:
    home = Path(temporary)
    value = adapter(home)

    # Only App-bundle holders are retired; an unrelated CLI owner stays a hold.
    def fake_run(command, **_kwargs):
        class Result:
            returncode = 0
            stdout = ""

        result = Result()
        if command[0] == "/usr/sbin/lsof":
            result.stdout = "p100\np200\np300\n"
        elif command[-1] == "100":
            result.stdout = BUNDLE + "/Contents/Resources/codex\n"
        else:
            result.stdout = "/Users/example/.local/bin/codex\n"
        return result

    killed = []
    with patch.object(native_host.subprocess, "run", side_effect=fake_run), \
            patch.object(native_host.os, "kill", side_effect=lambda pid, _sig: killed.append(pid)):
        value.retire_app_helpers()
    assert killed == [100], killed

    # A loaded product chat agent is paused and restored around the switch.
    launch_agents = home / "Library/LaunchAgents"
    launch_agents.mkdir(parents=True)
    plist = launch_agents / ("com.loopx.personal-bots.chat.plist")
    plist.write_text("<plist/>")
    calls = []

    def fake_launchctl(command, **_kwargs):
        calls.append(list(command))

        class Result:
            returncode = 0
            stdout = ""

        return Result()

    with patch.object(native_host.subprocess, "run", side_effect=fake_launchctl):
        paused = value.pause_chat_agents()
        value.resume_chat_agents(paused)
    assert paused == [plist], paused
    verbs = [command[1] for command in calls if len(command) > 1]
    assert "bootout" in verbs and "bootstrap" in verbs, verbs

print("native host client handoff smoke passed")
