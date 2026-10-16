"""Owner-confirmed Goal acceptance for the planned benchmark entry.

The planning checkpoint asks the model to write a Goal acceptance document; the
benchmark host, acting as the trusted local owner inside the trial, configures
it through the existing ``goal-acceptance`` contract. The product owner then
holds the selected Todos open until their criteria pass, so "all Todos done"
can no longer derive terminal no-follow-up before the deliverable was exercised.
No scoring, hidden-verifier feedback or new state vocabulary is introduced.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ACCEPTANCE_DOCUMENT_ENV = "LOOPX_ACCEPTANCE_DOCUMENT"
ACCEPTANCE_CONTRACT_ENV = "LOOPX_ACCEPTANCE_CONTRACT"
# Host-held copy of the last configured document. The model may keep editing its
# planning draft; re-confirmation only ever re-applies what the host accepted.
ACCEPTED_RECORD_NAME = "benchmark-accepted-acceptance.json"


def planned_acceptance_document(env: dict[str, str]) -> str | None:
    """Return the document locator only when the explicit planned switch is on."""
    if env.get(ACCEPTANCE_CONTRACT_ENV, "off") != "planned":
        return None
    if not env.get(ACCEPTANCE_DOCUMENT_ENV):
        raise ValueError(f"{ACCEPTANCE_CONTRACT_ENV}=planned requires {ACCEPTANCE_DOCUMENT_ENV}")
    return env[ACCEPTANCE_DOCUMENT_ENV]


def acceptance_planning_guidance(document_path: str) -> str:
    """Host instructions appended to the planning prompt for the planned contract."""
    return (
        "Host acceptance checkpoint:\n"
        f"Before returning, write a Goal acceptance document to {document_path}. "
        "It is a JSON object with objective, non_goals (list), scope "
        '{"kind": "selected_work", "todo_ids": [...]} naming the Todo(s) whose '
        "completion delivers the task result, criteria (list of {id, description, "
        "validation_argv, validation_timeout_seconds}; do not add validation_files or "
        "other keys) and "
        "bindings (one {todo_id, criterion_ids} per selected Todo). Use the exact "
        "Todo ids you created. Each criterion is an argv command run from the task "
        "workspace without a shell, 1-25 seconds each and 25 seconds in total. "
        "Criteria must exercise the deliverable through the interface the task says "
        "its verifier uses (for example run the documented command on the public "
        "inputs into a fresh output directory, then require exit 0 and the required "
        "artifacts), must fail until the deliverable exists, and must not read "
        "held-out or hidden data or fixture answers. For a check longer than its "
        "timeout, run it during the work and let the criterion verify its receipt "
        "artifact. The host configures this document as the owner-confirmed "
        "acceptance contract: a selected Todo cannot complete until its criteria "
        "pass, and the Goal cannot close while it stays open. A missing or invalid "
        "document fails this planning phase.\n\n"
    )


def _run_cli(env: dict[str, str], cli: list[str], args: list[str]) -> dict[str, Any]:
    response = subprocess.run(
        cli + args, cwd=env["LOOPX_PROJECT"], env=env, text=True,
        capture_output=True, check=False, timeout=120,
    )
    try:
        payload = json.loads(response.stdout or "")
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"goal-acceptance {args[1]} returned invalid JSON (exit {response.returncode})"
        ) from exc
    if response.returncode != 0 or payload.get("ok") is False:
        raise ValueError(f"goal-acceptance {args[1]} failed: {payload.get('error')}")
    return payload


def _load_document(env: dict[str, str]) -> dict[str, Any]:
    path = Path(env[ACCEPTANCE_DOCUMENT_ENV])
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("planning did not write the Goal acceptance document") from exc
    except (OSError, ValueError) as exc:
        raise ValueError("Goal acceptance document is unreadable or not JSON") from exc
    if not isinstance(document, dict):
        raise ValueError("Goal acceptance document must be a JSON object")
    return document


def _accepted_record_path(env: dict[str, str]) -> Path:
    return Path(env.get("LOOPX_RUNTIME_ROOT") or env["LOOPX_CODEX_HOME"]) / ACCEPTED_RECORD_NAME


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _selected_and_bound(document: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    scope = document.get("scope")
    selected = scope.get("todo_ids") if isinstance(scope, dict) else None
    if (
        not isinstance(scope, dict) or scope.get("kind") != "selected_work"
        or not isinstance(selected, list) or not selected
        or any(not isinstance(item, str) or not item for item in selected)
        or len(set(selected)) != len(selected)
    ):
        raise ValueError("Goal acceptance scope must select the delivering Todo ids")
    bindings = document.get("bindings")
    if not isinstance(bindings, list) or not bindings or any(
        not isinstance(row, dict) or row.get("todo_id") not in selected for row in bindings
    ):
        raise ValueError("Goal acceptance bindings must cover only selected Todos")
    if set(selected) - {row["todo_id"] for row in bindings}:
        raise ValueError("every selected Todo needs an acceptance binding")
    criteria = document.get("criteria")
    if not isinstance(criteria, list) or not criteria or any(
        not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in criteria
    ):
        raise ValueError("Goal acceptance requires identified criteria")
    # The owner contract pins validator files as {path, sha256}; planning cannot
    # know digests of files that do not exist yet, so a bare path list is a
    # misunderstanding of the schema rather than a usable pin.
    for row in criteria:
        pins = row.get("validation_files", [])
        if pins and any(not isinstance(pin, dict) or set(pin) != {"path", "sha256"} for pin in pins):
            raise ValueError(
                f"criterion {row['id']}: validation_files must be [{{path, sha256}}] pins or omitted"
            )
    return list(selected), bindings


def _configure(env, cli, document: dict[str, Any], *, operation_id: str,
               expected_todo_ids: list[str]) -> dict[str, Any]:
    goal = ["--goal-id", env["LOOPX_GOAL_ID"]]
    basis = _run_cli(env, cli, ["goal-acceptance", "inspect", *goal])
    record_path = _accepted_record_path(env)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    # Configure exactly the document already read and validated by the host.
    # Neither side of this IO may re-read the model's mutable planning draft.
    with tempfile.TemporaryDirectory(prefix="acceptance-", dir=record_path.parent) as directory:
        snapshot_path = Path(directory) / "document.json"
        _write_json(snapshot_path, document)
        applied = _run_cli(env, cli, [
            "goal-acceptance", "configure", *goal, "--document", str(snapshot_path),
            "--expected-provider-revision", str(basis["provider_revision"]),
            "--operation-id", operation_id, "--execute",
        ])
    contract = applied.get("goal_acceptance_contract") or {}
    if applied.get("status") != "applied" or contract.get("enabled") is not True:
        raise ValueError("Goal acceptance configuration was not applied")
    states = {task.get("todo_id"): task.get("state") for task in contract.get("tasks", [])
              if isinstance(task, dict)}
    not_ready = [todo_id for todo_id in expected_todo_ids if states.get(todo_id) != "ready"]
    if not_ready:
        raise ValueError(f"acceptance association is not ready for {', '.join(not_ready)}")
    _write_json(record_path, {
        "revision": contract["revision"], "digest": contract["digest"],
        "document": document,
    })
    return {
        "status": "applied",
        "revision": contract["revision"],
        "digest": contract["digest"],
        "operation_id": operation_id,
        "selected_todo_ids": expected_todo_ids,
        "criterion_ids": [row["id"] for row in contract.get("criteria", [])],
    }


def apply_planned_acceptance(env: dict[str, str], planning: dict[str, Any],
                             cli: list[str]) -> dict[str, Any]:
    """Configure the model-written document for the Todos planning returned."""
    document = _load_document(env)
    selected, _ = _selected_and_bound(document)
    unknown = sorted(set(selected) - set(planning["todo_ids"]))
    if unknown:
        raise ValueError(f"acceptance selects Todos outside the planning result: {', '.join(unknown)}")
    digest = hashlib.sha256(
        json.dumps(document, sort_keys=True, ensure_ascii=False).encode("utf-8")
        + planning["input_digest"].encode("utf-8")
    ).hexdigest()
    return _configure(env, cli, document,
                      operation_id=f"planned-acceptance:{digest[:32]}",
                      expected_todo_ids=selected)


def reconfirm_planned_acceptance(env: dict[str, str], cli: list[str]) -> dict[str, Any]:
    """Re-confirm associations the model's own Todo edits made stale.

    The criteria never change here: owner confirmation of the current Todo text
    is what the product requires after a selected Todo was edited. The document
    re-applied is the host's accepted copy, never the model-writable draft, and
    it must still match the owner's current contract digest. Completed or
    cancelled Todos leave the selection; their checks already ran at completion.
    """
    record_path = _accepted_record_path(env)
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        document = record["document"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("no host-accepted Goal acceptance document to re-confirm") from exc
    selected, bindings = _selected_and_bound(document)
    goal = ["--goal-id", env["LOOPX_GOAL_ID"]]
    contract = _run_cli(env, cli, ["goal-acceptance", "inspect", *goal]).get(
        "goal_acceptance_contract") or {}
    if contract.get("enabled") is not True:
        raise ValueError("planned acceptance contract is no longer enabled")
    if contract.get("digest") != record.get("digest"):
        raise ValueError("Goal acceptance contract changed outside the planned host; "
                         "refusing to re-confirm it from the host copy")
    tasks = {task["todo_id"]: task for task in contract.get("tasks", [])
             if isinstance(task, dict) and isinstance(task.get("todo_id"), str)}
    applicable = [todo_id for todo_id in selected
                  if tasks.get(todo_id, {}).get("applicable") is True]
    stale = [todo_id for todo_id in applicable if tasks[todo_id].get("state") == "stale"]
    if not stale:
        return {"reconfirmed": [], "revision": contract["revision"]}
    revised = {**document,
               "scope": {"kind": "selected_work", "todo_ids": applicable},
               "bindings": [row for row in bindings if row["todo_id"] in applicable]}
    receipt = _configure(env, cli, revised,
                         operation_id=f"planned-acceptance-reconfirm:{contract['revision']}",
                         expected_todo_ids=applicable)
    return {"reconfirmed": stale, "revision": receipt["revision"]}
