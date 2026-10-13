import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test, { type TestContext } from "node:test";
import { atomicWriteJson } from "../../loopx/control_plane/effect_runtime_io.ts";
import { executeTaskLeaseAcquire } from "../../loopx/control_plane/work_items/task_lease_acquire.ts";
import { executeTaskLeaseLifecycle } from "../../loopx/control_plane/work_items/task_lease_lifecycle.ts";
import { evaluateTaskLeaseLifecycleDecision } from "../../loopx/control_plane/work_items/task_lease_lifecycle_decision.ts";
import { shadowManagementStatePath } from "../../loopx/control_plane/coordination/shadow_management.ts";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";

function lifecycleDecision(
  operation: "renew" | "transfer" | "release",
  overrides: Record<string, unknown> = {},
) {
  return evaluateTaskLeaseLifecycleDecision({
    handoff_mode: "hard_lease",
    registered_agents: ["agent-a", "agent-b"],
    todo: {
      todo_id: "todo_target",
      status: "open",
      claimed_by: null,
      excluded_agents: [],
    },
    lease: {
      present: true,
      active: true,
      status: "active",
      owner: "agent-a",
      idempotency_key: "lease-a",
      version: 3,
      lease_epoch: 7,
      write_scopes: ["docs/**"],
      acquire_ttl_seconds: 300,
    },
    command: {
      operation,
      owner: "agent-a",
      idempotency_key: "lease-a",
      expected_version: 3,
      ttl_seconds: operation === "release" ? null : 600,
      new_owner: operation === "transfer" ? "agent-b" : null,
      new_idempotency_key: operation === "transfer" ? "lease-b" : null,
    },
    ...overrides,
  });
}

test("pure lifecycle decision owns renew transfer and release generations", () => {
  const renewed = lifecycleDecision("renew");
  assert.equal(renewed.outcome, "apply");
  assert.equal(renewed.next_lease?.version, 4);
  assert.equal(renewed.next_lease?.lease_epoch, 7);

  const transferred = lifecycleDecision("transfer");
  assert.equal(transferred.outcome, "apply");
  assert.equal(transferred.next_lease?.owner, "agent-b");
  assert.equal(transferred.next_lease?.version, 4);
  assert.equal(transferred.next_lease?.lease_epoch, 8);

  const released = lifecycleDecision("release");
  assert.equal(released.outcome, "apply");
  assert.equal(released.next_lease?.active, false);
  assert.equal(released.next_lease?.status, "released");
});

test("pure lifecycle decision keeps provider executions behind the same gates", () => {
  const stale = lifecycleDecision("renew", {
    command: {
      operation: "renew",
      owner: "agent-a",
      idempotency_key: "lease-a",
      expected_version: 2,
      ttl_seconds: 600,
      new_owner: null,
      new_idempotency_key: null,
    },
  });
  assert.equal(stale.outcome, "conflict");
  assert.equal(stale.code, "version_mismatch");

  const softClaim = lifecycleDecision("transfer", {
    handoff_mode: "soft_claim",
  });
  assert.equal(softClaim.outcome, "rejected");
  assert.equal(softClaim.code, "handoff_mode_forbids_lease");
});

for (const operation of ["renew", "transfer", "release"] as const) {
  test(`native ${operation} rejects contradictory lease facts before mode and CAS`, () => {
    for (const contradiction of [{present: false}, {status: "released"}]) {
      const result = lifecycleDecision(operation, {
        handoff_mode: "soft_claim",
        lease: {present: true, active: true, status: "active", owner: "agent-a",
          idempotency_key: "lease-a", version: 3, lease_epoch: 7,
          write_scopes: [], acquire_ttl_seconds: 300, ...contradiction},
        command: {operation, owner: "agent-a", idempotency_key: "lease-a",
          expected_version: null, ttl_seconds: null, new_owner: null,
          new_idempotency_key: null},
      });
      assert.equal(result.outcome, "rejected");
      assert.equal(result.code, "invalid_lease_snapshot");
      assert.equal(result.next_lease, null);
    }
  });
}

test("soft-claim rejects renew and transfer before missing version; release keeps its fence", () => {
  for (const operation of ["renew", "transfer", "release"] as const) {
    const result = lifecycleDecision(operation, {
      handoff_mode: "soft_claim",
      command: {operation, owner: "agent-a", idempotency_key: "lease-a",
        expected_version: null, ttl_seconds: null, new_owner: null,
        new_idempotency_key: null},
    });
    assert.equal(result.outcome, "rejected");
    assert.equal(result.code, operation === "release" ? "version_required" : "handoff_mode_forbids_lease");
  }
  const cleanup = lifecycleDecision("release", {handoff_mode: "soft_claim"});
  assert.equal(cleanup.outcome, "apply");
  assert.equal(cleanup.next_lease?.status, "released");
  assert.equal(cleanup.next_lease?.version, 3);
  assert.equal(cleanup.next_lease?.lease_epoch, 7);
});

// Independent 1.x artifacts from the last pre-cutoff base. Only $ROOT paths
// relocate; original operation IDs, digests, generation tuples and tokens do not.
// No old acquire/renew/fence producer is executed to prepare candidate tests.
interface HistoricalScenario {
  request: JsonObject;
  files: Record<string, JsonObject>;
}
const historical = JSON.parse(await readFile(new URL(
  "../control_plane/fixtures/task_lease_1x.json", import.meta.url), "utf8")) as {
    source_commit: string; scenarios: Record<string, HistoricalScenario>;
  };
const now = () => new Date("2026-09-01T03:03:00Z");
async function restore(t: TestContext, name = "original") {
  const root = await mkdtemp(join(tmpdir(), "loopx-lease-recovery-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const fixture = JSON.parse(JSON.stringify(historical.scenarios[name]).replaceAll("$ROOT", root)) as HistoricalScenario;
  await writeFile(join(root, "authority.json"), "authority-v1");
  for (const [relative, value] of Object.entries(fixture.files)) {
    const path = join(root, "runtime", relative);
    await mkdir(dirname(path), {recursive: true});
    await writeFile(path, JSON.stringify(value));
  }
  const path = join(root, "runtime/goals/goal-a/task-leases/todo_target.json");
  const record = async () => JSON.parse(await readFile(path, "utf8")) as JsonObject;
  const receiptPath = Object.keys(fixture.files).find(p => p.includes("/.lifecycle"));
  return {root, ...fixture, path, record,
    receiptPath: receiptPath ? join(root, "runtime", receiptPath) : null};
}
function lifecycle(f: {request: JsonObject}, operation: string, overrides: JsonObject = {}): JsonObject {
  return {...f.request, schema_version: "loopx_task_lease_lifecycle_native_v0", operation,
    expected_version: 1, new_owner: null, new_idempotency_key: null, lock_token: null,
    committed: false, release_lease: false, fence_owner: null, fence_idempotency_key: null,
    fence_expected_version: null, fence_expected_lease_epoch: 1, ...overrides};
}
function closeRequest(f: {request: JsonObject}, fence: JsonObject, overrides: JsonObject = {}): JsonObject {
  return lifecycle(f, "fence_close", {authority: null, owner: null, idempotency_key: null,
    expected_version: null, lock_token: fence.lock_token, fence_operation_id: fence.fence_operation_id,
    committed: true, release_lease: true, fence_owner: fence.owner ?? null,
    fence_idempotency_key: fence.idempotency_key ?? null, fence_expected_version: fence.version ?? null,
    fence_expected_lease_epoch: fence.lease_epoch ?? null, ...overrides});
}
async function receipt(f: {receiptPath: string | null}): Promise<JsonObject> {
  assert.ok(f.receiptPath);
  return JSON.parse(await readFile(f.receiptPath, "utf8"));
}

for (const operation of ["renew", "transfer", "terminal_verify", "holder_verify"]) {
  test(`2.0 refuses fresh source ${operation} without touching an original lease`, async t => {
    const f = await restore(t); const before = await readFile(f.path, "utf8");
    const result = await executeTaskLeaseLifecycle(lifecycle(f, operation,
      operation === "transfer" ? {new_owner: "agent-b", new_idempotency_key: "lease-b"} : {}), {now});
    assert.equal(result.error_code, "canonical_authority_required");
    assert.equal(await readFile(f.path, "utf8"), before);
  });
}

test("maintenance corruption blocks acquire and original release before mutation", async t => {
  const f = await restore(t); const before = await readFile(f.path, "utf8");
  await atomicWriteJson(shadowManagementStatePath(join(f.root, "runtime"), "goal-a"), {});
  for (const result of [await executeTaskLeaseAcquire(f.request),
    await executeTaskLeaseLifecycle(lifecycle(f, "release"))]) {
    assert.equal(result.error_code, "shadow_management_state_invalid");
    assert.equal(await readFile(f.path, "utf8"), before);
  }
});

for (const [overrides, error] of [
  [{expected_version: "1"}, "invalid_request"],
  [{expected_version: null}, "version_required"],
  [{delegated_authority: "false"}, "invalid_request"],
  [{operation: "terminal_verify", fence_operation_id: "../../escape"}, "invalid_fence_operation_id"],
  [{operation: "fence_close", lock_token: "   "}, "invalid_lock_token"],
] as const) test(`lifecycle input rejection: ${error} ${JSON.stringify(overrides)}`, async t => {
  const f = await restore(t); const before = await readFile(f.path, "utf8");
  const r = await executeTaskLeaseLifecycle(lifecycle(f, "release", overrides));
  assert.equal(r.error_code, error); assert.equal(await readFile(f.path, "utf8"), before);
});

for (const [overrides, error] of [
  [{owner: "agent-b"}, "lease_cas_mismatch"],
  [{idempotency_key: "other"}, "lease_cas_mismatch"],
  [{expected_version: 0}, "version_mismatch"],
] as const) test(`original release preserves the lease on ${JSON.stringify(overrides)}`, async t => {
  const f = await restore(t); const before = await readFile(f.path, "utf8");
  const r = await executeTaskLeaseLifecycle(lifecycle(f, "release", {authority: null, ...overrides}), {now});
  assert.equal(r.error_code, error); assert.equal(await readFile(f.path, "utf8"), before);
});

test("original release allows expiry, preserves generation and replays its tombstone", async t => {
  const f = await restore(t); const original = await f.record();
  const request = lifecycle(f, "release", {authority: null});
  const released = await executeTaskLeaseLifecycle(request, {now: () => new Date("2026-09-02T00:00:00Z")});
  assert.equal(released.released, true);
  const after = await f.record();
  assert.equal(after.version, original.version); assert.equal(after.lease_epoch, original.lease_epoch);
  assert.equal(after.status, "released");
  const replay = await executeTaskLeaseLifecycle(request, {now});
  assert.equal(replay.idempotent, true); assert.deepEqual(await f.record(), after);
});

test("original release survives pre-write loss and missing leases remain a no-op", async t => {
  const f = await restore(t); const request = lifecycle(f, "release", {authority: null});
  const failed = await executeTaskLeaseLifecycle(request, {now, beforeWrite: () => {throw new Error("write interrupted");}});
  assert.equal(failed.ok, false); assert.equal((await f.record()).status, "active");
  assert.equal((await executeTaskLeaseLifecycle(request, {now})).released, true);
  assert.equal((await executeTaskLeaseLifecycle(request, {now})).idempotent, true);
  const other = await restore(t); await rm(other.path);
  const missing = await executeTaskLeaseLifecycle(lifecycle(other, "release", {expected_version: 0, authority: null}), {now});
  assert.equal(missing.ok, true); assert.equal(missing.missing, true); assert.equal(missing.released, false);
});

for (const name of ["renew", "transfer"]) {
  for (const state of ["committed", "prepared"]) test(`1.x ${name} ${state} receipt recovers matching bytes once`, async t => {
    const f = await restore(t, name); const before = await readFile(f.path, "utf8");
    if (state === "prepared") await atomicWriteJson(f.receiptPath!, {...await receipt(f), state});
    const r = await executeTaskLeaseLifecycle(f.request, {now, beforeWrite: () => assert.fail("recovery must not repeat a lease write")});
    assert.equal(r.ok, true); assert.equal(r.idempotent, true);
    assert.equal((await receipt(f)).state, "committed");
    assert.equal(await readFile(f.path, "utf8"), before);
  });
  for (const state of ["committed", "prepared"]) test(`1.x ${name} ${state} cannot overwrite a later lease`, async t => {
    const f = await restore(t, name);
    await atomicWriteJson(f.receiptPath!, {...await receipt(f), state});
    await atomicWriteJson(f.path, {...await f.record(), version: 9, lease_epoch: 9, idempotency_key: "later"});
    const before = await readFile(f.path, "utf8");
    const r = await executeTaskLeaseLifecycle(f.request, {now});
    assert.equal(r.error_code, state === "committed" ? "lifecycle_receipt_state_mismatch" : "canonical_authority_required");
    assert.equal(await readFile(f.path, "utf8"), before);
  });
  test(`1.x ${name} rejects changed identity parameters and malformed receipts`, async t => {
    const f = await restore(t, name); const before = await readFile(f.path, "utf8");
    assert.equal((await executeTaskLeaseLifecycle({...f.request, ttl_seconds: 300}, {now})).error_code, "idempotency_key_reuse");
    const original = await receipt(f);
    await atomicWriteJson(f.receiptPath!, {...original, planned_lease: {...original.planned_lease as JsonObject, owner: 42}});
    assert.equal((await executeTaskLeaseLifecycle(f.request, {now})).error_code, "corrupt_lifecycle_receipt");
    assert.equal(await readFile(f.path, "utf8"), before);
  });
}

for (const name of ["terminal-held", "holder-held", "terminal-aborted", "auto-held", "auto-postwrite"]) {
  test(`1.x ${name} resumes after restart and closes only the original generation`, async t => {
    const f = await restore(t, name); const before = await f.record();
    const r = await executeTaskLeaseLifecycle(f.request, {now});
    assert.equal(r.ok, true, JSON.stringify(r));
    const fence = r.fence as JsonObject;
    assert.equal(fence.fence_operation_id, (await receipt(f)).operation_id);
    assert.deepEqual(await f.record(), before);
    const close = closeRequest(f, fence);
    const closed = await executeTaskLeaseLifecycle(close, {now});
    assert.equal(closed.released, true, JSON.stringify(closed));
    assert.equal((await f.record()).status, "released");
    assert.equal((await f.record()).version, before.version);
    assert.equal((await executeTaskLeaseLifecycle(close, {now})).idempotent, true);
  });
}

test("1.x pre-write auto-acquire intent cannot mint a new lease after cutoff", async t => {
  const f = await restore(t, "auto-prewrite");
  const r = await executeTaskLeaseLifecycle(f.request, {now, beforeWrite: () => assert.fail("fresh lease writer retired")});
  assert.equal(r.error_code, "canonical_authority_required", JSON.stringify(r));
  await assert.rejects(readFile(f.path), {code: "ENOENT"});
});

test("closed original fence replays without a lock and refuses a later generation", async t => {
  const f = await restore(t, "terminal-closed"); const before = await f.record();
  const r = await executeTaskLeaseLifecycle(f.request, {now});
  assert.equal(r.ok, true); assert.equal((r.fence as JsonObject).closed, true);
  assert.equal((r.fence as JsonObject).lock_token, undefined);
  assert.deepEqual(await f.record(), before);
  await atomicWriteJson(f.path, {...before, status: "active", version: 9, lease_epoch: 9, idempotency_key: "later"});
  const later = await readFile(f.path, "utf8");
  assert.equal((await executeTaskLeaseLifecycle(f.request, {now})).error_code, "fence_state_invalid");
  assert.equal(await readFile(f.path, "utf8"), later);
});

test("closed legacy no-op replays only while no lease is present", async t => {
  const f = await restore(t, "noop-closed");
  const r = await executeTaskLeaseLifecycle(f.request, {now});
  assert.equal(r.ok, true); assert.equal((r.fence as JsonObject).required, false);
  const original = await restore(t);
  await atomicWriteJson(f.path, await original.record());
  const before = await readFile(f.path, "utf8");
  assert.equal((await executeTaskLeaseLifecycle(f.request, {now})).error_code, "canonical_authority_required");
  assert.equal(await readFile(f.path, "utf8"), before);
});

for (const field of ["owner", "version", "lease_epoch", "idempotency_key"] as const) {
  test(`held fence refuses changed ${field} and preserves later state`, async t => {
    const f = await restore(t, "terminal-held");
    const original = await f.record();
    await atomicWriteJson(f.path, {...original, [field]: field === "owner" ? "agent-b" : field === "idempotency_key" ? "later" : 9});
    const before = await readFile(f.path, "utf8");
    const r = await executeTaskLeaseLifecycle(f.request, {now});
    assert.equal(r.ok, false); assert.equal(await readFile(f.path, "utf8"), before);
  });
}

for (const name of ["terminal-held", "auto-postwrite"]) for (const change of ["claim", "excluded", "unregistered", "source"]) {
  test(`${name} receipt does not bypass changed ${change} authority`, async t => {
    const f = await restore(t, name); const before = await readFile(f.path, "utf8");
    const authority = f.request.authority as JsonObject;
    const todo = (authority.todos as JsonObject[])[0];
    if (change === "claim") todo.claimed_by = "agent-b";
    if (change === "excluded") todo.excluded_agents = ["agent-a"];
    if (change === "unregistered") authority.registered_agent_candidates = [["agent-b"]];
    if (change === "source") await writeFile(join(f.root, "authority.json"), "later source");
    const r = await executeTaskLeaseLifecycle(f.request, {now});
    assert.equal(r.ok, false); assert.equal(r.error_code, change === "source" ? "authority_source_changed"
      : name === "terminal-held" ? "authority_todo_mismatch" : "handoff_mode_lease_claim_divergence");
    assert.equal(await readFile(f.path, "utf8"), before);
  });
}

for (const [field, value] of [["fence_operation_id", "a".repeat(64)], ["goal_id", "other-goal"], ["runtime_root", "$NEW_RUNTIME"]]) {
  test(`receipt identity cannot move across ${field}`, async t => {
    const f = await restore(t, "terminal-held"); const before = await readFile(f.path, "utf8");
    const r = await executeTaskLeaseLifecycle({...f.request, [field]: value === "$NEW_RUNTIME" ? join(f.root, "other-runtime") : value}, {now});
    assert.equal(r.ok, false); assert.equal(await readFile(f.path, "utf8"), before);
  });
}

for (const field of ["fence_expected_version", "fence_expected_lease_epoch"] as const) {
  test(`original fence close rejects ${field} drift`, async t => {
    const f = await restore(t, "terminal-held");
    const r = await executeTaskLeaseLifecycle(f.request, {now}); assert.equal(r.ok, true);
    const before = await readFile(f.path, "utf8");
    const close = closeRequest(f, r.fence as JsonObject, {[field]: 0});
    assert.equal((await executeTaskLeaseLifecycle(close, {now})).error_code, "fence_cas_mismatch");
    assert.equal(await readFile(f.path, "utf8"), before);
  });
}

// The recovered lock is still a real transferable capability. A concurrent
// verifier must not steal it while the original closer owns its token claim.
test("recovered fence close excludes a concurrent verifier until its write commits", {timeout: 10_000}, async t => {
  const f = await restore(t, "terminal-held");
  const verified = await executeTaskLeaseLifecycle(f.request, {now}); assert.equal(verified.ok, true);
  let entered!: () => void; let release!: () => void;
  const writing = new Promise<void>(resolve => {entered = resolve;});
  const proceed = new Promise<void>(resolve => {release = resolve;});
  const closing = executeTaskLeaseLifecycle(closeRequest(f, verified.fence as JsonObject),
    {now, beforeWrite: async () => {entered(); await proceed;}});
  await writing;
  try {
    const retry = await executeTaskLeaseLifecycle(f.request, {now});
    assert.equal(retry.ok, false); assert.equal(retry.error_code, "fence_token_invalid");
    assert.equal((await f.record()).status, "active");
  } finally {release();}
  assert.equal((await closing).released, true);
});

test("recovered fence close preserves its proof across source drift and pre-write failure", async t => {
  const f = await restore(t, "terminal-held");
  const verified = await executeTaskLeaseLifecycle(f.request, {now}); assert.equal(verified.ok, true);
  const close = closeRequest(f, verified.fence as JsonObject, {authority: f.request.authority,
    runtime_shadow: {schema_version: "loopx_coordination_runtime_shadow_binding_v0", provider: "file_v0"}});
  await writeFile(join(f.root, "authority.json"), "changed source");
  const stale = await executeTaskLeaseLifecycle(close, {now});
  assert.equal(stale.error_code, "authority_source_changed");
  assert.equal((await f.record()).status, "active");
  await writeFile(join(f.root, "authority.json"), "authority-v1");
  const failed = await executeTaskLeaseLifecycle(close, {now, beforeWrite: () => {throw new Error("close interrupted");}});
  assert.equal(failed.ok, false); assert.equal((await f.record()).status, "active");
  const replay = await executeTaskLeaseLifecycle(f.request, {now});
  assert.equal(replay.ok, true);
  assert.equal((await executeTaskLeaseLifecycle(closeRequest(f, replay.fence as JsonObject), {now})).released, true);
});
