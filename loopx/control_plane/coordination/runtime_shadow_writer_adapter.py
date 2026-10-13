"""Retained lease-release capture settlement and prose write compatibility."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import local_authority_shadow_outbox as outbox
from .local_authority_shadow_projection import LEASE_PARTITION
from .legacy_writer_fence import (
    ActiveStateAuthorityMutationError as ActiveStateAuthorityMutationError,
    require_prose_state_write_allowed as require_prose_state_write_allowed,
)


def settle_lease_runtime_shadow_capture(
    payload: dict[str, Any],
    *,
    registry_path: Path | None,
    runtime_root: Path,
    goal_id: str,
) -> dict[str, Any]:
    """Turn the TS writer capture receipt into bounded runtime-shadow evidence."""

    raw = payload.pop("coordination_runtime_shadow_capture", None)
    if not isinstance(raw, Mapping) or registry_path is None:
        return payload
    capture = outbox.CaptureOutcome(
        entry_id=str(raw["entry_id"]) if isinstance(raw.get("entry_id"), str) else None,
        partition=LEASE_PARTITION,
        seq=int(raw["seq"]) if isinstance(raw.get("seq"), int) else None,
        source_bytes_digest=(
            str(raw["source_bytes_digest"])
            if isinstance(raw.get("source_bytes_digest"), str)
            else None
        ),
        failure=dict(raw["failure"]) if isinstance(raw.get("failure"), Mapping) else None,
        skipped_reason=str(raw["skipped_reason"]) if raw.get("skipped_reason") else None,
    )
    from .local_authority_shadow_adapter import (
        capture_evidence,
        drain_local_authority_shadow_outbox,
    )

    drain = (
        drain_local_authority_shadow_outbox(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
        )
        if capture.entry_id is not None
        else None
    )
    payload["coordination_runtime_shadow"] = capture_evidence(
        goal_id=goal_id,
        capture=capture,
        drain=drain,
    )
    return payload


__all__ = [
    "settle_lease_runtime_shadow_capture",
]
