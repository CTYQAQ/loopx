#!/usr/bin/env python3
"""Qualify one built wheel or sdist through an isolated installed-package E2E.

The current receiver never imports the checkout. Historical writes use only
the immutable 1.x Git object pinned by the installed recovery fixture owner.
It installs the supplied artifact into an empty temporary venv, executes the installed console command from a separate
working directory, and retains all process results in the requested JSON report.
Run it once per distribution format; every stage is required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any


GOAL = "installed-authority-e2e"
REQUIRED_STAGES = {
    "installed_python_ts_json_resources", "real_baseline_bootstrap",
    "three_mutations_one_receipt_each", "default_qualification_and_exact_candidate_read",
    "rollback_inactive_write_new_lineage", "installed_fallback_dispatch",
    "cutoff_import_recovery_preserves_later_writes",
    "canonical_file_creation_recovery", "canonical_sqlite_creation_recovery",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class InstalledQualification:
    def __init__(self, artifact: Path, workspace: Path, report: dict[str, Any]) -> None:
        self.artifact = artifact
        workspace = workspace.resolve()
        self.workspace = workspace
        self.report = report
        self.venv = workspace / "venv"
        self.cwd = workspace / "outside-checkout"
        self.cwd.mkdir()
        self.runtime = self.cwd / "runtime"
        self.registry = self.cwd / "project/.loopx/registry.json"
        self.project = self.cwd / "project"
        self.project.mkdir()
        self.python = self.venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.console = self.python.parent / ("loopx.exe" if os.name == "nt" else "loopx")
        node = shutil.which("node")
        require(node is not None, "Node.js is required; this qualification cannot skip native execution")
        self.node = Path(str(node)).absolute()
        removed = [key for key in os.environ if key.startswith(("PYTHON", "LOOPX", "NODE", "PIP_", "GIT_")) or key in {"VIRTUAL_ENV", "CONDA_PREFIX"}]
        self.environment = {key: value for key, value in os.environ.items() if key not in removed}
        self.environment["PATH"] = os.pathsep.join([str(self.python.parent), str(self.node.parent), os.defpath])
        self.environment["PYTHONNOUSERSITE"] = "1"
        self.report["isolation"] = {"cwd": str(self.cwd), "venv": str(self.venv),
            "removed_environment_keys": sorted(removed), "node": str(self.node),
            "checkout_added_to_import_path": False}

    def process(self, stage: str, argv: list[str], *, timeout: int = 60, parse_json: bool = True, succeeds: bool = True) -> Any:
        started = time.monotonic()
        result = subprocess.run(argv, cwd=self.cwd, env=self.environment,
            text=True, capture_output=True, timeout=timeout, check=False)
        self.report["processes"].append({"stage": stage, "argv": argv, "returncode": result.returncode,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "stdout": result.stdout, "stderr": result.stderr})
        require((result.returncode == 0) is succeeds, f"{stage} exited {result.returncode}: {result.stdout}\n{result.stderr}")
        return json.loads(result.stdout) if parse_json else result.stdout

    def checked(self, stage: str, **evidence: Any) -> None:
        self.report["checks"].append({"stage": stage, "status": "passed", **evidence})

    def cli(self, stage: str, *args: str, historical: bool = False, succeeds: bool = True) -> dict[str, Any]:
        command = [str(self.console)]
        if historical:
            command = [str(self.python), "-I", "-c",
                "import sys;sys.path.insert(0,sys.argv.pop(1));"
                "from loopx.entrypoint import main;raise SystemExit(main())", str(self.historical)]
        result = self.process(stage, [*command, "--registry", str(self.registry),
            "--runtime-root", str(self.runtime), "--format", "json", *args], succeeds=succeeds)
        require(isinstance(result, dict) and result.get("ok") is succeeds, f"{stage} did not return ok: {result}")
        return result

    def sdk_add(self, stage: str, text: str, *, historical: bool = False) -> dict[str, Any]:
        code = """
import json, sys
from pathlib import Path
from loopx.todos import add_goal_todo
value = add_goal_todo(registry_path=Path(sys.argv[1]), runtime_root_arg=sys.argv[2],
    goal_id=sys.argv[3], role='agent', text=sys.argv[4], task_class='advancement_task', action_kind='analyze')
print(json.dumps(value))
"""
        if historical:
            code = "import sys;sys.path.insert(0,sys.argv.pop(1));" + code
        value = self.process(stage, [str(self.python), "-I", "-c", code,
            *([str(self.historical)] if historical else []),
            str(self.registry), str(self.runtime), GOAL, text])
        require(value.get("ok") is True and value.get("added") is True, f"{stage} did not add one Todo")
        return value

    def read_store(self, stage: str, package: Path, *, canonical: bool = False) -> dict[str, Any]:
        module = package / "control_plane/coordination" / (
            "local_authority_provider.ts" if canonical else "file_authority_store.ts")
        code = """
import {pathToFileURL} from 'node:url';
import {join} from 'node:path';
const [modulePath, root, goal, canonical] = process.argv.slice(1);
// Imported modules must not mistake a probe argument for their CLI entrypoint.
process.argv[1] = "installed-authority-readback";
const api = await import(pathToFileURL(modulePath).href);
const store = canonical === 'true'
  ? await api.openLocalAuthorityStore(root, goal, {}, {existingOnly: true})
  : new api.FileAuthorityStore(join(root, 'authority-shadow', 'file-v0'), goal, {existingOnly: true});
const head = await store.loadAuthority();
const history = await store.scanCommitted(null, 100);
console.log(JSON.stringify({module_path: modulePath, head, history}));
"""
        return self.process(stage, [str(self.node), "--no-warnings", "--experimental-strip-types", "--experimental-sqlite",
            "--input-type=module", "-e", code, str(module), str(self.runtime), GOAL, str(canonical).lower()])

    def run(self) -> None:
        self.process("create_empty_venv", [sys.executable, "-I", "-m", "venv", str(self.venv)],
            timeout=120, parse_json=False)
        self.process("install_artifact", [str(self.python), "-I", "-m", "pip", "--disable-pip-version-check",
            "install", "--no-input", "--no-cache-dir", "--no-deps", str(self.artifact)],
            timeout=240, parse_json=False)
        provenance_code = """
import hashlib, importlib.metadata, importlib.resources, json, pathlib, sys
import loopx, loopx.todos, loopx.control_plane.coordination.shadow_management
from loopx.control_plane.testing.authority_e2e_fixtures import ORIGINAL_1X_REVISION
package = pathlib.Path(loopx.__file__).resolve().parent
resources = {}
for relative in ['control_plane/coordination/runtime_shadow.ts', 'control_plane/coordination/shadow_management.ts',
    'control_plane/coordination/file_authority_store.ts', 'control_plane/coordination/local_authority_shadow_identity.ts',
    'control_plane/coordination/legacy_writer_lock_paths.ts',
    'control_plane/runtime/receipt_log_snapshot.ts',
    'control_plane/goals/goal_frontier/fallback_disposition.ts',
    'control_plane/work_items/task_lease_acquire.ts',
    'control_plane/coordination/coordination_state_contract_v0.json',
    'control_plane/coordination/coordination_state_contract.generated.ts']:
    resource = importlib.resources.files('loopx').joinpath(relative)
    raw = resource.read_bytes()
    if relative.endswith('.json'): json.loads(raw)
    resources[relative] = {'path': str(resource), 'sha256': hashlib.sha256(raw).hexdigest()}
print(json.dumps({'executable': sys.executable, 'package': str(package),
    'version': importlib.metadata.version('loopx'), 'resources': resources,
    'original_1x_revision': ORIGINAL_1X_REVISION,
    'python_modules': [loopx.__file__, loopx.todos.__file__, loopx.control_plane.coordination.shadow_management.__file__]}))
"""
        provenance = self.process("installed_resource_provenance", [str(self.python), "-I", "-c", provenance_code])
        package = Path(provenance["package"])
        require(package.is_relative_to(self.venv), "Python imported outside the isolated venv")
        for path in provenance["python_modules"] + [item["path"] for item in provenance["resources"].values()]:
            require(Path(path).resolve().is_relative_to(package), f"resource escaped installed package: {path}")
        self.report["provenance"] = provenance
        self.checked("installed_python_ts_json_resources", resource_count=len(provenance["resources"]))

        fallback_code = """
import json
from loopx.control_plane.effect_runtime import effect_runtime_result
print(json.dumps(effect_runtime_result('goal.fallback_disposition.project', {
    'schema_version': 'goal_fallback_disposition_request_v0',
    'terminal': False, 'agent_id': 'agent-a', 'blocker_present': False,
    'resume_planning': {}, 'source_state': 'complete', 'items': [],
    'resume_evaluation': {}, 'legacy_selectable_ids': [], 'declarations': [],
    'created_or_reopened_ids': [],
})))
"""
        fallback = self.process("installed_fallback_dispatch", [str(self.python), "-I", "-c", fallback_code])
        require(fallback.get("kind") == "resolved", "empty fallback declarations must resolve")
        self.checked("installed_fallback_dispatch")

        # Archive immutable producer history; the receiver remains the installed artifact.
        self.historical = self.workspace / "historical-source"
        self.historical.mkdir()
        archive_path = self.workspace / "historical.tar"
        self.process("archive_original_producer", ["git", "-C", str(Path(__file__).resolve().parents[2]),
            "archive", "--format=tar", "--output", str(archive_path), provenance["original_1x_revision"], "loopx"], parse_json=False)
        with tarfile.open(archive_path) as archive:
            for member in archive:
                target = self.historical / member.name
                require(target.resolve().is_relative_to(self.historical), "historical archive escaped isolation")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    require(member.isfile(), "historical archive contains a non-file member")
                    content = archive.extractfile(member)
                    require(content is not None, "historical archive member is empty")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with content:
                        target.write_bytes(content.read())
        self.report["historical_producer"] = {"revision": provenance["original_1x_revision"],
            "archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest()}

        # This lifecycle qualifies historical pre-promotion capture. Explicitly retain the
        # File target-only source for the original 1.x producer. The installed
        # receiver must reject new legacy writes and require explicit import.
        source_storage = {"schema_version": "loopx_goal_storage_defaults_v1",
            "new_goal_provider": "file", "canonical_creation": False,
            "new_goal_handoff_mode": "hard_lease"}
        source_storage_path = self.cwd / "shadow-source-storage.json"
        source_storage_path.write_text(json.dumps(source_storage), encoding="utf-8")
        preview = self.cli("console_source_storage_preview", "machine-config", "preview",
            "--namespace", "goal_storage", "--config-json", str(source_storage_path))
        revision = preview.get("plan_revision")
        require(isinstance(revision, str) and bool(revision), "source storage preview has no revision")
        applied = self.cli("console_source_storage_apply", "machine-config", "apply",
            "--namespace", "goal_storage", "--config-json", str(source_storage_path),
            "--expected-plan-revision", revision, "--execute")
        require(applied.get("readback_verified") is True
            and applied["machine_configuration"]["namespaces"]["goal_storage"] == source_storage,
            "target-only source configuration did not read back exactly")
        created = self.cli("console_project_bootstrap", "bootstrap", "--project", str(self.project),
            "--goal-id", GOAL, "--objective", "Qualify installed authority transactions.",
            "--no-global-sync", historical=True)
        require(created.get("storage_target") == {
            "schema_version": "loopx_new_goal_storage_target_v0", "provider": "file"},
            "shadow source was not created with the target-only File contract")
        self.source_state = Path(created["state_file"])
        selection = created["storage_selection"]
        require(selection.get("provider") == "file" and selection.get("promotion_performed") is False
            and "authority_initialized" not in selection,
            "shadow source unexpectedly initialized canonical authority")
        # Set configuration only, before shadow bootstrap creates the real binding.
        registry = json.loads(self.registry.read_text())
        goal = next(item for item in registry["goals"] if item["id"] == GOAL)
        goal["coordination"].update({"agent_model": "peer_v1", "registered_agents": ["agent-a", "agent-b"],
            "runtime_shadow": {"schema_version": "loopx_coordination_runtime_shadow_config_v0",
                "enabled": True, "provider": "file_v0"}})
        self.registry.write_text(json.dumps(registry))
        boot = self.cli("console_shadow_bootstrap", "coordination-shadow", "bootstrap", "--goal-id", GOAL, "--execute")["bootstrap"]
        require(boot.get("status") == "applied" and bool(boot.get("capture_lineage_id")), f"bootstrap not applied: {boot}")
        self.checked("real_baseline_bootstrap", capture_lineage_id=boot["capture_lineage_id"])

        first = self.sdk_add("original_python_todo_add", "First original producer mutation.", historical=True)
        require(first["coordination_runtime_shadow"]["outcome"] == "delivered", f"first Todo capture failed: {first}")
        lease = self.cli("console_native_lease_acquire", "task-lease", "acquire", "--goal-id", GOAL,
            "--todo-id", first["todo_id"], "--owner", "agent-a", "--idempotency-key", "installed-lease-a", "--ttl-seconds", "600", historical=True)
        require(lease["coordination_runtime_shadow"]["outcome"] == "delivered", f"native lease capture failed: {lease}")
        second = self.sdk_add("original_python_second_todo_add", "Third original producer mutation.", historical=True)
        require(second["coordination_runtime_shadow"]["outcome"] == "delivered", f"second Todo capture failed: {second}")
        drained = self.cli("console_drain", "authority-shadow", "drain", "--goal-id", GOAL)
        require(drained.get("pending_after") == 0 and drained.get("prepared_only_after") == 0,
            f"drain left unverified work: {drained}")
        qualified = self.cli("console_qualify_default_policy", "coordination-shadow", "qualify", "--goal-id", GOAL)["qualification"]
        require(qualified.get("status") == "qualified" and qualified.get("qualified") is True,
            f"default policy was not qualified: {qualified}")
        require(qualified["policy"]["minimum_operations"] == 3 and qualified["evidence"]["operation_count"] == 3,
            "qualification did not prove exactly three real primary mutations under the default threshold")
        candidate = self.cli("console_read_candidate", "coordination-shadow", "read-candidate", "--goal-id", GOAL,
            "--todo-id", first["todo_id"])["read_candidate"]
        require(candidate.get("status") == "matched" and candidate.get("read_candidate_qualified") is True,
            f"candidate read was not qualified: {candidate}")
        independent = self.read_store("independent_installed_native_readback", package)
        require(independent["head"]["status"] == "loaded" and independent["history"]["status"] == "page", "independent provider read failed")
        transactions = independent["history"]["transactions"]
        require(len(transactions) == 4 and independent["head"]["cursor"] == "4", "primary writes did not map one-to-one to receipts")
        require(all(len(tx["receipts"]) == 1 for tx in transactions[1:]), "a primary mutation has missing or duplicate receipts")
        old_head = independent["head"]["head"]
        require({first["todo_id"], second["todo_id"]}.issubset({item["todo_id"] for item in old_head["todos"]}), "native readback lost a Todo")
        require(any(item["todo_id"] == first["todo_id"] and item["owner"] == "agent-a" for item in old_head["leases"]), "native readback lost the lease")
        self.checked("three_mutations_one_receipt_each", mutation_count=3, cursor="4")
        self.checked("default_qualification_and_exact_candidate_read", minimum_operations=3, todo_id=first["todo_id"])

        rolled = self.cli("console_rollback", "coordination-shadow", "rollback", "--goal-id", GOAL,
            "--provider-revision", independent["head"]["provider_revision"], "--execute")["rollback"]
        require(rolled.get("status") == "applied", f"rollback not applied: {rolled}")
        absent = self.read_store("independent_read_after_rollback", package)
        require(absent["head"]["status"] == "missing", "rollback left an active candidate")
        inactive = self.sdk_add("original_python_inactive_primary_write", "Original 1.x primary remains writable after rollback.", historical=True)
        require(inactive["coordination_runtime_shadow"]["reason_code"] == "bootstrap_required", "inactive write fabricated capture qualification")
        require(not (self.runtime / "authority-shadow" / "outbox" / GOAL).exists(), "inactive write created outbox without a lineage")
        again = self.cli("console_new_bootstrap", "coordination-shadow", "bootstrap", "--goal-id", GOAL, "--execute")["bootstrap"]
        require(again.get("status") == "applied" and again.get("capture_lineage_id") != boot["capture_lineage_id"], "new baseline reused the retired lineage")
        final = self.read_store("independent_new_baseline_readback", package)
        require(final["head"]["status"] == "loaded" and final["head"]["cursor"] == "1", "new baseline has an invalid initial cursor")
        require(inactive["todo_id"] in {item["todo_id"] for item in final["head"]["head"]["todos"]}, "new baseline omitted the inactive primary write")
        require(len(final["history"]["transactions"]) == 1, "new baseline pretended to retain old mutation coverage")
        self.checked("rollback_inactive_write_new_lineage", previous_lineage=boot["capture_lineage_id"], new_lineage=again["capture_lineage_id"])
        self.import_and_recover(package, first["todo_id"], lease["lease"]["version"], final)
        for provider in ("file", "sqlite"):
            self.canonical_creation(package, provider)

    def import_and_recover(self, package: Path, todo_id: str, lease_version: int, shadow: dict[str, Any]) -> None:
        # Stop capture, settle the original lease, then import with a real backup.
        self.cli("retire_capture_for_import", "coordination-shadow", "rollback", "--goal-id", GOAL,
            "--provider-revision", shadow["head"]["provider_revision"], "--execute")
        state = self.source_state
        original = state.read_bytes()
        rejected = self.cli("installed_legacy_write_rejected", "todo", "add", "--goal-id", GOAL,
            "--role", "agent", "--text", "Must not resume source writes", succeeds=False)
        require(rejected.get("error_code") == "canonical_authority_required" and state.read_bytes() == original,
            "installed receiver did not reject source mutation without modifying it")
        released = self.cli("installed_original_lease_release", "task-lease", "release", "--goal-id", GOAL,
            "--todo-id", todo_id, "--owner", "agent-a", "--idempotency-key", "installed-lease-a",
            "--expected-version", str(lease_version))
        require(released["released"] is True and released["lease"]["status"] == "released", "original lease was not settled")
        backup = self.cli("installed_source_backup", "backup-state", "--project", str(self.project),
            "--output-dir", str(self.cwd / "backups"), "--backup-id", "installed-original",
            "--current-project-only", "--no-skills", "--no-automations", "--execute")
        prepared = self.cli("installed_prepare_import", "coordination-shadow", "prepare-import", "--goal-id", GOAL,
            "--operation-id", "installed-import", "--backup-manifest", backup["manifest_path"],
            "--provider", "sqlite", "--target-handoff-mode", "hard_lease")["cold_import"]
        require(prepared["status"] == "prepared", "cold import was not prepared")
        digest = prepared["plan_sha256"]
        applied = self.cli("installed_apply_import", "coordination-shadow", "apply-import", "--goal-id", GOAL,
            "--operation-id", "installed-import", "--plan-sha256", digest, "--writers-stopped", "--execute")["cold_import"]
        require(applied["status"] == "applied" and applied["execution_authority_granted"] is False,
            "import did not preserve the execution authority boundary")
        require(state.read_bytes() == original, "import rewrote original source")
        imported = self.read_store("installed_import_native_readback", package, canonical=True)
        expected = {item["todo_id"]: item for item in shadow["head"]["head"]["todos"]}
        require(expected == {item["todo_id"]: item for item in imported["head"]["head"]["todos"]}, "import changed original Todo records")
        retained_lease = self.cli("installed_imported_lease_readback", "task-lease", "inspect", "--goal-id", GOAL, "--todo-id", todo_id)
        require(retained_lease["lease"] == released["lease"], "import changed the released original lease")
        later = self.sdk_add("installed_canonical_later_add", "Retain canonical writes after original import.")
        before = self.read_store("installed_later_native_readback", package, canonical=True)
        require(later["todo_id"] in {item["todo_id"] for item in before["head"]["head"]["todos"]}, "later write missing")
        state.unlink()
        recovered = self.cli("installed_source_free_import_recovery", "coordination-shadow", "recover-import", "--goal-id", GOAL,
            "--operation-id", "installed-import", "--plan-sha256", digest, "--execute")["cold_import"]
        require(recovered["status"] == "replayed", "original import was not replayed")
        after = self.read_store("installed_recovery_native_readback", package, canonical=True)
        require(after == before, "original recovery replaced later canonical data or receipts")
        self.checked("cutoff_import_recovery_preserves_later_writes", provider="sqlite", todo_id=later["todo_id"])

    def canonical_creation(self, package: Path, provider: str) -> None:
        self.project = self.cwd / f"new-{provider}"
        self.project.mkdir()
        self.registry = self.project / ".loopx/registry.json"
        self.runtime = self.cwd / f"runtime-{provider}"
        config = self.runtime / "machine/configuration.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"schema_version": "loopx_machine_configuration_v0", "namespaces": {
            "goal_storage": {"schema_version": "loopx_goal_storage_defaults_v1", "new_goal_provider": provider,
                "canonical_creation": True, "new_goal_handoff_mode": "soft_claim"}}}))
        args = ("bootstrap", "--project", str(self.project), "--goal-id", GOAL,
            "--objective", "Qualify fresh canonical creation and retained writes.", "--no-global-sync")
        created = self.cli(f"{provider}_canonical_bootstrap", *args)
        require(created["storage_selection"]["authority_initialized"] is True
            and created["storage_selection"]["provider"] == provider, "fresh Goal was not canonical")
        later = self.sdk_add(f"{provider}_canonical_sdk_add", "Preserve work after original creation.")
        before = self.read_store(f"{provider}_canonical_native_readback", package, canonical=True)
        require(later["todo_id"] in {item["todo_id"] for item in before["head"]["head"]["todos"]}, "new canonical Todo missing")
        Path(created["state_file"]).unlink()
        self.cli(f"{provider}_creation_recovery", *args)
        after = self.read_store(f"{provider}_creation_recovery_readback", package, canonical=True)
        require(before == after, "creation recovery replaced later canonical data or receipts")
        self.checked(f"canonical_{provider}_creation_recovery", todo_id=later["todo_id"])



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True, type=Path, help="Exact built wheel or sdist to install")
    parser.add_argument("--report-json", required=True, type=Path, help="Complete qualification report and raw process results")
    args = parser.parse_args(argv)
    artifact = args.artifact.expanduser().resolve()
    report: dict[str, Any] = {"schema_version": "loopx_installed_authority_e2e_v1", "status": "failed",
        "artifact": str(artifact), "artifact_sha256": None, "checks": [], "processes": [],
        "fail": 0, "pending": 0, "unverified": 0}
    try:
        require(artifact.is_file() and (artifact.name.endswith(".whl") or artifact.name.endswith(".tar.gz")), "artifact must be an existing wheel or sdist")
        report["artifact_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="loopx-installed-authority-") as directory:
            InstalledQualification(artifact, Path(directory), report).run()
        require({row["stage"] for row in report["checks"]} == REQUIRED_STAGES, "a required qualification stage did not execute")
        report["status"] = "passed"
    except Exception as error:
        report["fail"] = 1
        report["unverified"] = len(REQUIRED_STAGES - {row["stage"] for row in report["checks"]})
        report["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        destination = args.report_json.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report_json": str(destination),
        "checks_passed": len(report["checks"]), "fail": report["fail"], "pending": report["pending"], "unverified": report["unverified"]}))
    return 0 if report["status"] == "passed" and report["fail"] == report["pending"] == report["unverified"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
