from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ...agent_registry import (
    registered_agent_ids_from_registry,
    require_registered_agent_id,
)
from ...history import load_registry
from ...paths import resolve_runtime_root
from ..runtime.time import now_utc as runtime_now_utc
from ..runtime.time import parse_timestamp, utc_isoformat
from ..coordination.authority_core import (
    write_scopes_overlap as core_write_scopes_overlap,
)
from ..todos.contract import (
    normalize_required_write_scopes,
    normalize_todo_claimed_by,
    normalize_todo_excluded_agents,
    normalize_todo_id,
)
from .local_lease_record import (
    TASK_LEASE_SCHEMA_VERSION,
    TaskLeaseError,
    assert_expected_version as assert_expected_version,
    build_lease as build_lease,
    lease_acquire_ttl_seconds as lease_acquire_ttl_seconds,
    lease_epoch as lease_epoch,
    lease_version as lease_version,
    read_lease as read_lease,
    require_expected_version as require_expected_version,
    write_lease as write_lease,
)

DEFAULT_TASK_LEASE_TTL_SECONDS = 45 * 60
MAX_TASK_LEASE_TTL_SECONDS = 24 * 60 * 60
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.:@/-]{1,160}$")


def now_utc() -> datetime:
    return runtime_now_utc()


_DEFAULT_NOW_UTC = now_utc


def isoformat(value: datetime) -> str:
    """Retain the historical task-lease timestamp helper import surface."""

    return utc_isoformat(value)


def normalize_idempotency_key(value: Any) -> str:
    candidate = str(value or "").strip()
    if not candidate or not IDEMPOTENCY_KEY_PATTERN.match(candidate):
        raise TaskLeaseError(
            "idempotency key must be a public-safe token",
            code="invalid_idempotency_key",
        )
    return candidate


def normalize_owner(value: Any) -> str:
    owner = normalize_todo_claimed_by(value)
    if not owner:
        raise TaskLeaseError("owner must be a public-safe agent id", code="invalid_owner")
    return owner


def normalize_ttl_seconds(value: int | None) -> int:
    ttl = DEFAULT_TASK_LEASE_TTL_SECONDS if value is None else int(value)
    if ttl <= 0 or ttl > MAX_TASK_LEASE_TTL_SECONDS:
        raise TaskLeaseError(
            f"ttl seconds must be between 1 and {MAX_TASK_LEASE_TTL_SECONDS}",
            code="invalid_ttl",
        )
    return ttl


def normalize_goal_id(value: Any) -> str:
    goal_id = str(value or "").strip()
    if not goal_id or goal_id in {".", ".."} or "/" in goal_id or "\\" in goal_id:
        raise TaskLeaseError("goal id must be a single path segment", code="invalid_goal_id")
    if Path(goal_id).name != goal_id:
        raise TaskLeaseError("goal id must not include path traversal", code="invalid_goal_id")
    return goal_id


def normalize_lease_todo_id(value: Any) -> str:
    todo_id = normalize_todo_id(value)
    if not todo_id:
        raise TaskLeaseError("todo id must use the todo_<token> shape", code="invalid_todo_id")
    return todo_id


def task_lease_dir(*, runtime_root: Path, goal_id: str) -> Path:
    return runtime_root / "goals" / normalize_goal_id(goal_id) / "task-leases"


def task_lease_path(*, runtime_root: Path, goal_id: str, todo_id: str) -> Path:
    return task_lease_dir(runtime_root=runtime_root, goal_id=goal_id) / f"{normalize_lease_todo_id(todo_id)}.json"


def task_lease_lock_path(*, runtime_root: Path, goal_id: str) -> Path:
    return task_lease_dir(runtime_root=runtime_root, goal_id=goal_id) / ".task-leases"


def _execute_native_task_lease_lifecycle(**kwargs: Any) -> dict[str, Any]:
    from .task_lease_acquire_adapter import execute_native_task_lease_lifecycle

    # Existing Python parity tests monkeypatch this module's clock. Production
    # calls leave time ownership in TypeScript; only the replaced test clock is
    # forwarded through the internal adapter seam.
    if now_utc is not _DEFAULT_NOW_UTC:
        kwargs.setdefault("_now", now_utc())
    return execute_native_task_lease_lifecycle(**kwargs)


def runtime_root_from_registry(
    registry_path: Path,
    runtime_root_override: str | None,
) -> Path:
    """Effective runtime root for lease state: override, else registry root.

    A relative ``common_runtime_root`` resolves against the registry's project
    root, matching the observation hooks, so the lease files and every
    candidate observation of one goal share a single root.
    """

    registry = load_registry(registry_path)
    return resolve_runtime_root(
        registry,
        runtime_root_override,
        registry_path=registry_path,
    )


def task_lease_todo_projection(
    *,
    registry_path: Path,
    goal_id: str,
    todo_id: str,
) -> dict[str, Any] | None:
    """Resolve the canonical Markdown/event todo projection for lease guards."""

    from ...control_plane.todos.list_readback import list_goal_todos

    try:
        payload = list_goal_todos(
            registry_path=registry_path,
            goal_id=goal_id,
            todo_id=todo_id,
        )
    except (OSError, ValueError) as exc:
        raise TaskLeaseError(
            "cannot resolve todo projection for task lease",
            code="todo_projection_unavailable",
            payload={"goal_id": goal_id, "todo_id": todo_id, "error": str(exc)},
        ) from exc
    todo = payload.get("todo")
    return dict(todo) if isinstance(todo, dict) else None


def task_lease_owner_constraint(
    todo: dict[str, Any] | None,
    *,
    owner: Any,
    registered_agents: list[str] | None = None,
) -> dict[str, Any]:
    from ..effect_runtime import effect_runtime_result

    normalized_owner = normalize_todo_claimed_by(owner)
    registered = registered_agents if registered_agents is not None else ([normalized_owner] if normalized_owner else [])
    result = effect_runtime_result("task_lease.owner_eligibility", {
        "todo": None if todo is None else {
            "status": str(todo.get("status") or "").strip().lower(),
            "claimed_by": normalize_todo_claimed_by(todo.get("claimed_by")),
            "excluded_agents": normalize_todo_excluded_agents(todo.get("excluded_agents")),
        },
        "owner": normalized_owner, "registered_agents": registered,
    })
    if not isinstance(result, dict) or result.get("schema_version") != "task_lease_owner_eligibility_v0":
        raise RuntimeError("typed lease owner constraint is missing; update the runtime")
    constraint = result.get("constraint")
    if not isinstance(constraint, dict) or not isinstance(constraint.get("effective"), bool):
        raise RuntimeError("typed lease owner constraint shape mismatch")
    if constraint["effective"] is False and not isinstance(constraint.get("reason"), str):
        raise RuntimeError("typed lease owner rejection omitted its reason")
    return constraint


def require_registered_task_lease_owner(
    *,
    registry_path: Path,
    goal_id: str,
    todo_id: str,
    owner: str,
) -> str:
    try:
        return require_registered_agent_id(
            registry_path=registry_path,
            goal_id=goal_id,
            agent_id=owner,
            field="task lease owner",
        )
    except ValueError as exc:
        raise TaskLeaseError(
            str(exc),
            code="owner_not_registered",
            payload={"goal_id": goal_id, "todo_id": todo_id, "owner": owner},
        ) from exc


def require_task_lease_owner_allowed(
    *,
    registry_path: Path,
    goal_id: str,
    todo_id: str,
    owner: str,
) -> dict[str, Any]:
    owner = require_registered_task_lease_owner(
        registry_path=registry_path,
        goal_id=goal_id,
        todo_id=todo_id,
        owner=owner,
    )
    todo = task_lease_todo_projection(
        registry_path=registry_path,
        goal_id=goal_id,
        todo_id=todo_id,
    )
    constraint = task_lease_owner_constraint(
        todo,
        owner=owner,
        registered_agents=registered_agent_ids_from_registry(registry_path, goal_id),
    )
    if constraint.get("effective") is not True:
        reason = str(constraint.get("reason") or "owner_not_allowed")
        if reason == "todo_not_found":
            message = "todo is missing from the canonical projection"
        elif reason == "todo_not_open":
            message = (
                f"task lease requires an open todo; "
                f"{todo_id!r} is {constraint.get('todo_status')!r}"
            )
        elif reason == "owner_excluded_from_todo":
            message = f"task lease owner {owner!r} is excluded from todo {todo_id!r}"
        elif reason == "owner_conflicts_with_claim":
            message = (
                f"task lease owner {owner!r} conflicts with todo claim "
                f"{constraint.get('claimed_by')!r}"
            )
        elif reason == "owner_not_registered":
            message = f"task lease owner {owner!r} is not registered for goal {goal_id!r}"
        else:
            message = f"task lease owner {owner!r} is not eligible for todo {todo_id!r}"
        raise TaskLeaseError(
            message,
            code=reason,
            payload={
                "goal_id": goal_id,
                "todo_id": todo_id,
                "owner": owner,
                **constraint,
            },
        )
    return todo


def lease_expires_at(lease: dict[str, Any] | None) -> datetime | None:
    return parse_timestamp((lease or {}).get("expires_at"))


def lease_is_active(lease: dict[str, Any] | None, *, at: datetime | None = None) -> bool:
    if not lease or lease.get("schema_version") != TASK_LEASE_SCHEMA_VERSION:
        return False
    if lease.get("status") != "active":
        return False
    expires_at = lease_expires_at(lease)
    return bool(expires_at and expires_at > (at or now_utc()))


def scope_root(scope: str) -> str:
    value = scope.strip()
    if value in {"*", "**", "./"}:
        return ""
    wildcard_indexes = [index for index in (value.find("*"), value.find("?"), value.find("[")) if index >= 0]
    if wildcard_indexes:
        value = value[: min(wildcard_indexes)]
    if "/" in value:
        value = value[: value.rfind("/") + 1]
    return value.rstrip("/")


def write_scopes_overlap(left: list[str], right: list[str]) -> bool:
    left_scopes = normalize_required_write_scopes(left)
    right_scopes = normalize_required_write_scopes(right)
    return core_write_scopes_overlap(left_scopes, right_scopes)


def acquire_task_lease(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    todo_id: str,
    owner: str,
    idempotency_key: str,
    ttl_seconds: int | None = None,
    write_scopes: list[str] | None = None,
    expected_version: int | None = None,
) -> dict[str, Any]:
    from .task_lease_acquire_adapter import execute_native_task_lease_acquire

    payload = execute_native_task_lease_acquire(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        todo_id=todo_id,
        owner=owner,
        idempotency_key=idempotency_key,
        ttl_seconds=ttl_seconds,
        write_scopes=write_scopes,
        expected_version=expected_version,
    )
    if payload.get("ok") is not True:
        error_payload = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "ok",
                "schema_version",
                "action",
                "error",
                "error_code",
                "settlement",
            }
        }
        raise TaskLeaseError(
            str(payload.get("error") or "native task lease acquire rejected"),
            code=str(payload.get("error_code") or "task_lease_acquire_rejected"),
            payload=error_payload,
        )
    return payload


def renew_task_lease(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    todo_id: str,
    owner: str,
    idempotency_key: str,
    ttl_seconds: int | None = None,
    expected_version: int | None = None,
) -> dict[str, Any]:
    """Renew a local lease through the native TypeScript lifecycle owner.

    This function is intentionally a compatibility facade: argument
    normalization and the historical Python import/return surface remain, but
    lifecycle authority, locking, CAS, and persistence live in the native
    handler.
    """

    normalized_goal_id = normalize_goal_id(goal_id)
    normalized_todo_id = normalize_lease_todo_id(todo_id)
    normalized_owner = normalize_owner(owner)
    normalized_key = normalize_idempotency_key(idempotency_key)
    normalized_ttl = normalize_ttl_seconds(ttl_seconds)
    # Keep version validation and handoff/error ordering in the native owner;
    # the adapter performs only the legacy numeric coercion at the transport
    # edge.  In particular, soft_claim must report its mode gate before a
    # missing expected version, matching the historical writer.
    normalized_expected_version = expected_version

    return _execute_native_task_lease_lifecycle(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=normalized_goal_id,
        todo_id=normalized_todo_id,
        operation="renew",
        owner=normalized_owner,
        idempotency_key=normalized_key,
        ttl_seconds=normalized_ttl,
        expected_version=normalized_expected_version,
    )


def transfer_task_lease(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    todo_id: str,
    owner: str,
    idempotency_key: str,
    new_owner: str,
    new_idempotency_key: str,
    ttl_seconds: int | None = None,
    expected_version: int | None = None,
    transfer_claim: bool = False,
) -> dict[str, Any]:
    """Transfer a local lease, optionally with its canonical Todo claim."""

    if not isinstance(transfer_claim, bool):
        raise TaskLeaseError("transfer_claim must be a boolean", code="invalid_claim_transfer_request")

    normalized_goal_id = normalize_goal_id(goal_id)
    normalized_todo_id = normalize_lease_todo_id(todo_id)
    normalized_owner = normalize_owner(owner)
    normalized_key = normalize_idempotency_key(idempotency_key)
    normalized_new_owner = normalize_owner(new_owner)
    normalized_new_key = normalize_idempotency_key(new_idempotency_key)
    normalized_ttl = normalize_ttl_seconds(ttl_seconds)
    normalized_expected_version = expected_version

    return _execute_native_task_lease_lifecycle(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=normalized_goal_id,
        todo_id=normalized_todo_id,
        operation="transfer",
        owner=normalized_owner,
        idempotency_key=normalized_key,
        new_owner=normalized_new_owner,
        new_idempotency_key=normalized_new_key,
        **({"transfer_claim": True} if transfer_claim else {}),
        ttl_seconds=normalized_ttl,
        expected_version=normalized_expected_version,
    )


def release_task_lease(
    *,
    runtime_root: Path,
    goal_id: str,
    todo_id: str,
    owner: str,
    idempotency_key: str,
    expected_version: int | None = None,
    registry_path: Path | None = None,
) -> dict[str, Any]:
    """Release a local lease through the native TypeScript lifecycle owner."""

    normalized_goal_id = normalize_goal_id(goal_id)
    normalized_todo_id = normalize_lease_todo_id(todo_id)
    normalized_owner = normalize_owner(owner)
    normalized_key = normalize_idempotency_key(idempotency_key)
    normalized_expected_version = expected_version

    return _execute_native_task_lease_lifecycle(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=normalized_goal_id,
        todo_id=normalized_todo_id,
        operation="release",
        owner=normalized_owner,
        idempotency_key=normalized_key,
        expected_version=normalized_expected_version,
    )


def inspect_task_lease(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    todo_id: str,
) -> dict[str, Any]:
    from .task_lease_acquire_adapter import inspect_native_task_lease

    return inspect_native_task_lease(
        registry_path=registry_path, runtime_root=runtime_root,
        goal_id=normalize_goal_id(goal_id), todo_id=normalize_lease_todo_id(todo_id),
    )
