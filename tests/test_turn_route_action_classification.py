"""Characterisation of how ``_typed_route`` classifies repair actions.

Repair routing is an explicit typed contract selected from ``EffectiveAction``.
Names do not acquire repair authority merely because they end in ``_repair`` or
``_repair_required``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.quota.effective_action import EffectiveAction
from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from loopx.control_plane.turn_driver.turn_contract_generated import LoopXTurnRoute
from loopx.control_plane.turn_driver.driver import (
    HOST_EXECUTION_ACTIONS,
    REPAIR_ACTIONS,
    REPLAN_ACTIONS,
    _typed_route,
    unregistered_route,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_REGISTERED_REPAIR_ACTIONS = frozenset(
    {
        EffectiveAction.AGENT_WORKSPACE_REPAIR,
        EffectiveAction.BOUNDARY_PROJECTION_REPAIR,
        EffectiveAction.CAPABILITY_BRIDGE_REPAIR,
        EffectiveAction.CONTROL_PLANE_HEALTH_REPAIR,
        EffectiveAction.CONTROL_PLANE_PROJECTION_REPAIR,
        EffectiveAction.CONTROL_PLANE_REPAIR,
        EffectiveAction.RUNTIME_USER_GATE_PROJECTION_REPAIR,
        EffectiveAction.STATE_PROJECTION_GAP_REPAIR,
        EffectiveAction.TODO_DECISION_SCOPE_PROJECTION_REPAIR,
    }
)
RETIRED_REPAIR_ACTIONS = (
    "capability_repair",
    "projection_repair",
    "self_repair",
    "state_projection_repair",
    "workspace_repair",
)


def _run_decision(**overrides: Any) -> dict[str, Any]:
    decision: dict[str, Any] = {
        "ok": True,
        "goal_id": "g",
        "agent_id": "a",
        "agent_identity": {"agent_id": "a"},
        "decision": "run",
        "should_run": True,
        "effective_action": "normal_run",
        "state": "eligible",
        "recommended_action": "Advance.",
        "selected_todo": {"todo_id": "t001", "text": "Advance."},
        "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0",
            "mode": "normal_run",
            "user_channel": {"action_required": False, "notify": "DONT_NOTIFY"},
            "agent_channel": {
                "must_attempt": True,
                "delivery_allowed": True,
                "quiet_noop_allowed": False,
            },
            "cli_channel": {"spend_after_validation": True},
        },
        "open_count": 0,
        "action_required": False,
    }
    decision.update(overrides)
    return decision


def _route_for(action: str) -> str:
    return _typed_route(
        build_turn_envelope(_run_decision(effective_action=action))
    ).value


def test_registered_repair_actions_are_explicit_and_typed() -> None:
    assert REPAIR_ACTIONS == EXPECTED_REGISTERED_REPAIR_ACTIONS
    for action in REPAIR_ACTIONS:
        assert isinstance(action, EffectiveAction)
        assert _route_for(action.value) == "repair_required", action


def test_repair_like_unknown_actions_do_not_gain_repair_authority() -> None:
    for action in RETIRED_REPAIR_ACTIONS + (
        "skip_repair",
        "restore_state_repair",
        "some_future_repair_required",
    ):
        assert _route_for(action) == "ready_for_host", action


@pytest.mark.parametrize("delivery_allowed", [True, False])
def test_replan_actions_reach_the_replan_route(delivery_allowed: bool) -> None:
    for action in sorted(REPLAN_ACTIONS):
        decision = _run_decision(effective_action=action)
        decision["interaction_contract"]["agent_channel"]["delivery_allowed"] = delivery_allowed
        envelope = build_turn_envelope(decision)
        assert envelope["action"]["delivery_allowed"] is delivery_allowed
        assert _typed_route(envelope) is LoopXTurnRoute.REPLAN_REQUIRED, action


@pytest.mark.parametrize("control", ["should_run", "must_attempt"])
@pytest.mark.parametrize("action", ["autonomous_replan_required", "successor_replan_required"])
def test_replan_still_requires_an_admitted_attempt(control: str, action: str) -> None:
    decision = _run_decision(effective_action=action)
    channel = decision["interaction_contract"]["agent_channel"]
    channel["delivery_allowed"] = False
    if control == "should_run":
        decision["should_run"] = False
    else:
        channel["must_attempt"] = False
    assert _typed_route(build_turn_envelope(decision)) is LoopXTurnRoute.BLOCKED


@pytest.mark.parametrize("action", ["normal_run", "control_plane_repair", "future_repair"])
def test_replan_does_not_admit_other_delivery_routes(action: str) -> None:
    decision = _run_decision(effective_action=action)
    decision["interaction_contract"]["agent_channel"]["delivery_allowed"] = False
    assert _typed_route(build_turn_envelope(decision)) is LoopXTurnRoute.BLOCKED


def test_replan_still_requires_the_action_signature() -> None:
    decision = _run_decision(effective_action="autonomous_replan_required")
    decision["interaction_contract"]["agent_channel"]["delivery_allowed"] = False
    envelope = build_turn_envelope(decision)
    envelope["action_signature"]["matches"] = False
    assert _typed_route(envelope) is LoopXTurnRoute.CONTRACT_ERROR


def test_remaining_registered_actions_reach_the_host_route() -> None:
    special = (
        {value for value in REPLAN_ACTIONS}
        | {EffectiveAction.GOVERNED_CAPABILITY_INTENT.value}
        | {action.value for action in REPAIR_ACTIONS}
    )
    for member in EffectiveAction:
        action = member.value
        if action in special:
            continue
        assert _route_for(action) == "ready_for_host", action


def test_every_registered_action_is_classified_deliberately() -> None:
    """The registered union is partitioned, not covered by a fallthrough.

    The root should-run/Envelope slot is a disjoint union of two owners. If an
    owner gains a value, this fails until the value is classified, which is the
    reason to state host execution rather than reach it by subtraction.
    """

    registry = json.loads(
        (REPO_ROOT / "loopx" / "semantics" / "vocabulary_v0.json").read_text(
            encoding="utf-8"
        )
    )["vocabularies"]
    registered = set(registry["effective_action"]["values"]) | set(
        registry["agent_scope_frontier_action"]["values"]
    )

    classified = (
        {action.value for action in REPAIR_ACTIONS}
        | set(HOST_EXECUTION_ACTIONS)
        | (REPLAN_ACTIONS & registered)
        | {EffectiveAction.GOVERNED_CAPABILITY_INTENT.value}
    )

    assert registered - classified == set(), "registered actions with no stated route"
    assert classified - registered - REPLAN_ACTIONS == set(), "classified value no owner declares"


def test_host_execution_classifications_hold_at_runtime() -> None:
    """Naming a value is only evidence if the driver actually routes it there."""

    for action in sorted(HOST_EXECUTION_ACTIONS):
        assert _route_for(action) == "ready_for_host", action


def test_unregistered_actions_keep_reaching_the_host_route() -> None:
    """Admission is unchanged: an unknown value still runs, it is not refused."""

    for action in ("totally_unknown_action", "future_channel_due", "skip_repair"):
        assert _route_for(action) == "ready_for_host", action
        assert unregistered_route(action) is LoopXTurnRoute.READY_FOR_HOST
