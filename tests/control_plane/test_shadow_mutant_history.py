"""Frozen mutation oracles retain only their pinned historical producer."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from test_shadow_mutant_locators import HARNESS


@pytest.mark.parametrize("available", [True, False], ids=["pinned-history", "missing-history"])
def test_frozen_history_is_independent_and_missing_history_fails(tmp_path: Path, monkeypatch, available):
    source, frozen = tmp_path / "upstream", tmp_path / "frozen"
    source.mkdir()
    subprocess.run(["git", "init", "--quiet", str(source)], check=True)
    (source / "producer.txt").write_text("original producer bytes\n")
    subprocess.run(["git", "-C", str(source), "add", "producer.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Fixture Author",
                    "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                    "commit", "-qsm", "Original synthetic producer"], check=True)
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    # A dirty present-day source must not become the historical input.
    (source / "producer.txt").write_text("current worktree bytes\n")
    owner = frozen / "loopx/control_plane/testing/authority_e2e_fixtures.py"
    owner.parent.mkdir(parents=True)
    for folder in (owner.parent, owner.parent.parent, owner.parent.parent.parent):
        (folder / "__init__.py").touch()
    owner.write_text(f"ORIGINAL_1X_REVISION = {revision if available else '0' * 40!r}\n")
    # Ambient Git routing must not redirect the frozen repository or its fetch.
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "not-a-repository"))
    if not available:
        with pytest.raises(subprocess.CalledProcessError):
            HARNESS.freeze_historical_git_source(source, frozen)
        return
    assert HARNESS.freeze_historical_git_source(source, frozen) == revision
    monkeypatch.delenv("GIT_DIR")
    # Verify the object remains available without the origin at all.
    source.rename(tmp_path / "detached-upstream")
    content = subprocess.check_output(["git", "-C", str(frozen), "show", f"{revision}:producer.txt"])
    assert content == b"original producer bytes\n"
    assert not (frozen / "producer.txt").exists(), "history fetch must not replace the frozen working tree"
    assert owner.read_text() == f"ORIGINAL_1X_REVISION = {revision!r}\n"
