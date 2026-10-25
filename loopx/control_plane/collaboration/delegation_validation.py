"""Host IO for delegation's single typed acceptance/validation plan.

The canonical reader resolves Goal scope; TypeScript selects validation effects.
Python resolves private declarations and executes only those authorized effects.
Original failure and validation-to-settlement recovery use the same Turn journal
and typed decisions; the service retains operation admission, locking and effects.
No validator output or successful declaration read completes a canonical Todo.
"""

from pathlib import Path

from ...agent_registry import load_goal_from_registry
from ...materials import goal_state_path
from ..agents.workspace_guard import capture_delivery_workspace
from ..effect_runtime import effect_runtime_result
from . import delegation_results
from ..goals.acceptance import (
    inspect_goal_acceptance,
    run_goal_acceptance_validation_effect,
    validation_effect_files_current,
)
from ..todos.completion_validation import resolve_private_completion_validation_declaration


def capture(service, binding: dict) -> dict:
    basis = inspect_goal_acceptance(
        registry_path=service.registry, runtime_root=str(service.root),
        goal_id=service.goal_id, agent_id=binding["agent_id"], todo_id=binding["todo_id"],
        validation_workspace_path=Path(binding["workspace"]),
    )
    todo = basis.get("todo")
    if not isinstance(todo, dict):
        raise ValueError("delegation canonical Todo unavailable")
    goal = load_goal_from_registry(service.registry, service.goal_id)
    state_file = goal_state_path(goal) if goal is not None else None
    if state_file is None:
        raise ValueError("delegation validation workspace unavailable")
    declaration = resolve_private_completion_validation_declaration(
        canonical_todo=todo, state_file=state_file, runtime_root=service.root,
        registry_path=service.registry, goal_id=service.goal_id,
        todo_id=binding["todo_id"], role=todo.get("role"), persist_if_resolved=False,
    )
    plan = effect_runtime_result("collaboration.delegation.validation_plan", {
        "binding": {key: binding[key] for key in ("id", "agent_id", "todo_id")},
        "basis": basis, "declaration": declaration,
    })
    # The configured worker worktree is the host-owned execution context. The
    # typed plan still owns which validators run; a path-free snapshot lets the
    # ordinary completion runner verify cross-repository Todo validators.
    workspace = Path(binding["workspace"])
    delivery_workspace = (
        capture_delivery_workspace(workspace, peer_independent_worktree_required=True)
        if any(effect.get("task_repository") for effect in plan["effects"])
        else None
    )
    files_current = plan["state"] == "ready" and validation_effect_files_current(
        effects=plan["effects"], registry_path=service.registry, goal_id=service.goal_id,
        delivery_workspace=delivery_workspace, validation_workspace_path=workspace,
    )
    return {"basis": basis, "plan": plan, "files_current": files_current,
            "delivery_workspace": delivery_workspace}


def validate(service, binding: dict) -> dict:
    before = capture(service, binding)
    if before["plan"]["state"] != "ready" or not before["files_current"]:
        raise ValueError("delegation task acceptance rejected")
    results = [run_goal_acceptance_validation_effect(
        effect=effect, registry_path=service.registry, goal_id=service.goal_id,
        delivery_workspace=before["delivery_workspace"],
        validation_workspace_path=Path(binding["workspace"]),
    ) for effect in before["plan"]["effects"]]
    after = capture(service, binding)
    # The TS plan binds this task's work, claim/lifecycle and current rules.
    # A whole-Goal provider revision may advance for unrelated peer progress.
    if (after["plan"] != before["plan"] or not after["files_current"]
            or after["delivery_workspace"] != before["delivery_workspace"]):
        raise ValueError("delegation task acceptance changed during validation")
    if not all(result["passed"] for result in results):
        raise ValueError("delegation task acceptance rejected")
    return after


def record_revalidation_result(service, path, row, binding, result):
    from ..turn_driver.loop_controller import ValidatedTurnReceipt
    receipt = ValidatedTurnReceipt.from_execution(result)
    decision = turn_recovery(service, row, binding, revalidated_result={
        "turn_key": receipt.turn_key, "result_kind": receipt.result_kind.value,
        "lineage": dict(receipt.lineage),
        "host_reinvoked": result.get("effects", {}).get("host_invoked"),
    })
    if decision["revalidated_status"] != "turn_returned":
        raise ValueError("revalidation recovery requires the original committed Turn")
    service._record_turn_result(path, row, result, publish=False)
    row["status"] = decision["revalidated_status"]
    row.pop("error", None)
    service._fenced_write(path, row)


def turn_recovery(service, row, binding, *, revalidated_result=None):
    return effect_runtime_result("collaboration.delegation.turn_recovery", {
        "runtime_root": str(service.root.resolve()), "goal_id": service.goal_id,
        "agent_id": binding["agent_id"], "todo_id": binding["todo_id"],
        "turn_instance_id": service._turn_instance_id(row), "status": row.get("status"),
        "validation_failure": row.get("validation_failure"),
        "revalidated_result": revalidated_result,
    })


def matching_turn_key(service, row: dict, binding: dict) -> str | None:
    return turn_recovery(service, row, binding)["turn_key"]


def validated_turn_journal(service, row: dict, binding: dict) -> dict | None:
    inspection = turn_recovery(service, row, binding)
    if not inspection["validated_settlement"]:
        return None
    row["turn_key"] = inspection["turn_key"]
    return {"status": inspection["status"], "result_kind": inspection["result_kind"],
            "task_validation": inspection["task_validation"], "host_result": inspection["host_result"]}


def recover_validated_settlement(
    service, path: Path, row: dict, binding: dict, *, inspection: dict | None = None
) -> bool:
    """Reopen only an exact, independently validated settlement boundary."""

    inspection = inspection or turn_recovery(service, row, binding)
    committed_key = inspection["turn_key"] if inspection["committed_revalidation"] else None
    if committed_key is not None:
        service._bound(row, require_active=True)
        delegation_results.require_dependencies(service, binding, delegation_results.operation_brief(service, row))
        # Read the original committed result through the Turn replay owner;
        # do not fabricate durable effect flags from a saved status string.
        result = service._cli(binding, "turn", "run-once", "--goal-id", service.goal_id,
            "--agent-id", binding["agent_id"], "--resume-turn-key", committed_key,
            *service._execution_arguments(binding, row["identity"]["operation_id"]), "--execute",
            timeout=binding["timeout_seconds"] + 60,
            host_record=service._host_process_record(path))
        record_revalidation_result(service, path, row, binding, result)
        return True
    if not inspection["validated_settlement"]:
        return False
    decision = effect_runtime_result("collaboration.delegation.recover_validated_settlement", {
        "from": row["status"], "identity_matched": True,
        "journal_status": inspection["status"], "result_kind": inspection["result_kind"],
        "completed_phases": inspection["completed_phases"],
        "task_validation_passed": inspection["task_validation_ok"],
    })
    row["turn_key"] = inspection["turn_key"]
    row["status"] = decision["status"]
    row["turn_result"] = {
        "status": inspection["status"],
        "result_kind": inspection["result_kind"],
        "resume_turn_key": row["turn_key"],
        "reason": "validated Turn settlement requires same-operation recovery",
        "host_failure": None,
        "error": None,
    }
    row.pop("error", None)
    service._fenced_write(path, row)
    return True
