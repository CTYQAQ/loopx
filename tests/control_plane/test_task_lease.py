from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.work_items import task_lease, task_lease_acquire_adapter
from loopx.control_plane.work_items.local_lease_record import read_lease
from loopx.control_plane.work_items.task_lease import (
    MAX_TASK_LEASE_TTL_SECONDS,
    TaskLeaseError,
    assert_expected_version,
    normalize_idempotency_key,
    normalize_ttl_seconds,
    release_task_lease,
    renew_task_lease,
    task_lease_owner_constraint,
    write_scopes_overlap,
)


@pytest.mark.parametrize("raw", [None, b'{"status":"active"}', b"{broken", b"\xff"],
                         ids=["missing", "valid", "invalid-json", "invalid-utf8"])
def test_local_lease_reader_preserves_bytes_and_types_corruption(tmp_path: Path, raw: bytes | None) -> None:
    path = tmp_path / "lease.json"
    if raw is not None:
        path.write_bytes(raw)
        before_mtime = path.stat().st_mtime_ns

    if raw in (b"{broken", b"\xff"):
        with pytest.raises(TaskLeaseError) as error:
            read_lease(path)
        assert error.value.code == "corrupt_lease"
    else:
        assert read_lease(path) == ({"status": "active"} if raw is not None else None)

    if raw is None:
        assert not path.exists()
    else:
        assert path.read_bytes() == raw
        assert path.stat().st_mtime_ns == before_mtime


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (["docs/**"], ["docs/a.md"], True),
        (["docs/sub/**"], ["docs/sub/a.md"], True),
        (["docs/a*.md"], ["docs/ab.md"], True),
        (["docs/**"], ["docs/sub/**"], True),
        (["**"], ["loopx/cli.py"], True),
        (["docs/a.md"], ["docs/b.md"], False),
        ([], ["docs/a.md"], False),
    ],
)
def test_write_scopes_overlap(
    left: list[str],
    right: list[str],
    expected: bool,
) -> None:
    assert write_scopes_overlap(left, right) is expected


@pytest.mark.parametrize(
    ("todo", "owner", "registered_agents", "reason"),
    [
        (None, "agent-a", ["agent-a"], "todo_not_found"),
        ({"status": "done"}, "agent-a", ["agent-a"], "todo_not_open"),
        ({"status": "open"}, "", ["agent-a"], "invalid_owner"),
        ({"status": "open"}, "agent-b", ["agent-a"], "owner_not_registered"),
        (
            {"status": "open", "excluded_agents": ["agent-a"]},
            "agent-a",
            ["agent-a"],
            "owner_excluded_from_todo",
        ),
        (
            {"status": "open", "claimed_by": "agent-b"},
            "agent-a",
            ["agent-a", "agent-b"],
            "owner_conflicts_with_claim",
        ),
    ],
)
def test_task_lease_owner_constraint_rejects_ineligible_owner(
    todo: dict[str, Any] | None,
    owner: str,
    registered_agents: list[str],
    reason: str,
) -> None:
    constraint = task_lease_owner_constraint(
        todo,
        owner=owner,
        registered_agents=registered_agents,
    )

    assert constraint["effective"] is False
    assert constraint["reason"] == reason


def test_task_lease_owner_constraint_accepts_matching_claim() -> None:
    constraint = task_lease_owner_constraint(
        {
            "status": "open",
            "claimed_by": "agent-a",
            "excluded_agents": ["agent-b"],
        },
        owner="agent-a",
        registered_agents=["agent-a", "agent-b"],
    )

    assert constraint == {"effective": True}


@pytest.mark.parametrize("ttl", [0, -1, MAX_TASK_LEASE_TTL_SECONDS + 1])
def test_normalize_ttl_seconds_rejects_out_of_range(ttl: int) -> None:
    with pytest.raises(TaskLeaseError, match="ttl seconds") as error:
        normalize_ttl_seconds(ttl)

    assert error.value.code == "invalid_ttl"


@pytest.mark.parametrize("key", ["", "contains space", "bad$key"])
def test_normalize_idempotency_key_rejects_non_token(key: str) -> None:
    with pytest.raises(TaskLeaseError) as error:
        normalize_idempotency_key(key)

    assert error.value.code == "invalid_idempotency_key"


def test_expected_version_is_compare_and_swap_guard() -> None:
    with pytest.raises(TaskLeaseError) as error:
        assert_expected_version({"version": 3}, 2)

    assert error.value.code == "version_mismatch"
    assert error.value.payload == {"expected_version": 2, "actual_version": 3}


def test_lease_epoch_migrates_legacy_records_and_rejects_corruption() -> None:
    assert task_lease.lease_epoch(None) == 0
    assert (
        task_lease.lease_epoch({"schema_version": "task_lease_v0", "version": 7}) == 1
    )

    for raw_epoch in (0, -1, "not-an-epoch"):
        with pytest.raises(TaskLeaseError) as error:
            task_lease.lease_epoch({"lease_epoch": raw_epoch})
        assert error.value.code == "corrupt_lease"


def test_bool_disguised_lease_integers_fail_closed() -> None:
    """bool is an int subclass: a JSON ``true`` must not become epoch/version/
    TTL ``1`` silently.  The shared Stage 2 head codec already rejects bool
    (head._is_count); the local record codec is the migration source for
    ``last_lease_epoch`` seeding, so the same typed corrupt_lease applies
    before any local-to-shared migration."""

    for raw in (True, False):
        with pytest.raises(TaskLeaseError) as error:
            task_lease.lease_epoch({"lease_epoch": raw})
        assert error.value.code == "corrupt_lease"
        with pytest.raises(TaskLeaseError) as error:
            task_lease.lease_version({"version": raw})
        assert error.value.code == "corrupt_lease"
        with pytest.raises(TaskLeaseError) as error:
            task_lease.lease_acquire_ttl_seconds({"acquire_ttl_seconds": raw})
        assert error.value.code == "corrupt_lease"


def test_lifecycle_mutation_fails_closed_without_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    monkeypatch.setattr(task_lease, "now_utc", lambda: now)
    runtime_root = tmp_path / "runtime"
    lease_path = task_lease.task_lease_path(
        runtime_root=runtime_root,
        goal_id="goal-a",
        todo_id="todo_missing_registry",
    )
    task_lease.write_lease(
        lease_path,
        task_lease.build_lease(
            goal_id="goal-a",
            todo_id="todo_missing_registry",
            owner="agent-a",
            idempotency_key="turn-1",
            write_scopes=[],
            acquire_ttl_seconds=120,
            version=1,
            lease_epoch=1,
            acquired_at=now.isoformat().replace("+00:00", "Z"),
            updated_at=now.isoformat().replace("+00:00", "Z"),
            expires_at=(now + timedelta(seconds=120))
            .isoformat()
            .replace("+00:00", "Z"),
        ),
    )
    before = task_lease.read_lease(lease_path)

    with pytest.raises(TaskLeaseError) as error:
        renew_task_lease(
            registry_path=tmp_path / "missing-registry.json",
            runtime_root=runtime_root,
            goal_id="goal-a",
            todo_id="todo_missing_registry",
            owner="agent-a",
            idempotency_key="turn-1",
            expected_version=1,
        )

    assert error.value.code == "goal_not_found"
    assert task_lease.read_lease(lease_path) == before


def test_legacy_generation_and_unrelated_ttl_do_not_block_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    monkeypatch.setattr(task_lease, "now_utc", lambda: now)
    runtime_root = tmp_path / "runtime"
    lease_path = task_lease.task_lease_path(
        runtime_root=runtime_root,
        goal_id="goal-a",
        todo_id="todo_legacy",
    )
    task_lease.write_lease(
        lease_path,
        {
            "schema_version": "task_lease_v0",
            "goal_id": "goal-a",
            "todo_id": "todo_legacy",
            "owner": "agent-a",
            "idempotency_key": "legacy-key",
            "write_scopes": [],
            "acquire_ttl_seconds": "unrelated-corrupt-legacy-field",
            "version": 7,
            "status": "active",
            "acquired_at": now.isoformat().replace("+00:00", "Z"),
            "updated_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": (now + timedelta(seconds=120))
            .isoformat()
            .replace("+00:00", "Z"),
        },
    )

    released = release_task_lease(
        runtime_root=runtime_root,
        goal_id="goal-a",
        todo_id="todo_legacy",
        owner="agent-a",
        idempotency_key="legacy-key",
        expected_version=7,
    )
    assert released["lease"]["lease_epoch"] == 1


@pytest.mark.parametrize(
    ("expected_version", "expected_forwarded"),
    [("2", 2), (2.5, 2)],
)
def test_native_lifecycle_expected_version_keeps_legacy_numeric_coercion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expected_version: Any,
    expected_forwarded: int,
) -> None:
    import loopx.control_plane.effect_runtime as effect_runtime
    forwarded: list[Any] = []

    def rejecting_effect_runtime_result(
        _method: str,
        params: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        assert timeout == 15.0
        forwarded.append(params["expected_version"])
        return {
            "ok": False,
            "schema_version": "task_lease_v0",
            "action": params["operation"],
            "error": "expected_version must be a safe integer or null",
            "error_code": "invalid_request",
        }

    monkeypatch.setattr(
        effect_runtime,
        "effect_runtime_result",
        rejecting_effect_runtime_result,
    )

    with pytest.raises(TaskLeaseError) as error:
        task_lease_acquire_adapter.execute_native_task_lease_lifecycle(
            runtime_root=tmp_path / "runtime",
            goal_id="goal-a",
            todo_id="todo_target",
            operation="release",
            owner="agent-a",
            idempotency_key="lease-a",
            expected_version=expected_version,
        )

    assert error.value.code == "invalid_request"
    assert "expected_version" in str(error.value)
    assert forwarded == [expected_forwarded]


def test_native_lifecycle_expected_version_bool_is_rejected_before_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import loopx.control_plane.effect_runtime as effect_runtime

    def unexpected_effect_runtime_result(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("bool expected_version must be rejected at the adapter edge")

    monkeypatch.setattr(
        effect_runtime,
        "effect_runtime_result",
        unexpected_effect_runtime_result,
    )

    with pytest.raises(TaskLeaseError) as error:
        task_lease_acquire_adapter.execute_native_task_lease_lifecycle(
            runtime_root=tmp_path / "runtime",
            goal_id="goal-a",
            todo_id="todo_target",
            operation="release",
            owner="agent-a",
            idempotency_key="lease-a",
            expected_version=True,
        )

    assert error.value.code == "version_required"
