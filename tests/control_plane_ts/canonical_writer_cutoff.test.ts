import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {executeCanonicalTaskLeaseAcquire} from "../../loopx/control_plane/coordination/task_lease_acquire.ts";
import {executeCanonicalTaskLeaseLifecycle} from "../../loopx/control_plane/coordination/task_lease_lifecycle.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {mkdir, mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {executeTaskLeaseAcquire, TASK_LEASE_ACQUIRE_REQUEST_SCHEMA_VERSION} from "../../loopx/control_plane/work_items/task_lease_acquire.ts";
import {executeTaskLeaseLifecycle, TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION} from "../../loopx/control_plane/work_items/task_lease_lifecycle.ts";
import {manageNewGoalStorage} from "../../loopx/control_plane/coordination/local_authority_defaults.ts";

for (const provider of ["file", "sqlite"]) {
  for (const legacy of [true, false]) test(`2.0 rejects ${provider} target-only creation (${legacy ? "v0" : "disabled"})`, async () => {
    const configuration = legacy ? {schema_version:"loopx_goal_storage_defaults_v0", new_goal_provider:provider}
      : {schema_version:"loopx_goal_storage_defaults_v1", new_goal_provider:provider, canonical_creation:false, new_goal_handoff_mode:"hard_lease"};
    await assert.rejects(manageNewGoalStorage({action:"resolve", configuration}), /requires canonical creation/);
  });
}

test("cold source refuses new grants but releases an original lease with CAS", async t => {
  const root = await mkdtemp(join(tmpdir(), "cutoff-lease-"));
  t.after(() => rm(root, {recursive:true, force:true}));
  const source = join(root, "state.md");
  await writeFile(source, "original source");
  const authority = {handoff_mode:"hard_lease", registered_agent_candidates:[["agent-a", "agent-b"]],
    todos:[{todo_id:"todo_target",status:"open",claimed_by:null,excluded_agents:[]}],todo_projection_error:null,
    source_receipts:[{source_id:"authority",path:source,state:"file",sha256:createHash("sha256").update("original source").digest("hex")}]};
  const acquire = {schema_version:TASK_LEASE_ACQUIRE_REQUEST_SCHEMA_VERSION, runtime_root:join(root,"runtime"),
    goal_id:"goal-a",todo_id:"todo_target",owner:"agent-a",idempotency_key:"lease-a",ttl_seconds:600,
    write_scopes:[],expected_version:null,authority};
  const now = () => new Date("2026-09-01T03:01:00Z");
  const refused = await executeTaskLeaseAcquire(acquire, {now});
  assert.equal(refused.ok,false); assert.equal(refused.error_code,"canonical_authority_required");
  const directory = join(root,"runtime/goals/goal-a/task-leases");
  await mkdir(directory,{recursive:true});
  const path = join(directory,"todo_target.json");
  // Retained 1.x lease, including original identity, generation and expiry.
  await writeFile(path, JSON.stringify({schema_version:"task_lease_v0",goal_id:"goal-a",todo_id:"todo_target",
    owner:"agent-a",idempotency_key:"lease-a",write_scopes:[],acquire_ttl_seconds:600,version:1,lease_epoch:1,
    acquired_at:"2026-09-01T03:00:00Z",updated_at:"2026-09-01T03:00:00Z",expires_at:"2026-09-01T03:10:00Z",status:"active"}));
  const before = await readFile(path,"utf8");
  const replay = await executeTaskLeaseAcquire(acquire,{now});
  assert.equal(replay.ok,true); assert.equal(replay.idempotent,true);
  const base = {schema_version:TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA_VERSION,runtime_root:acquire.runtime_root,
    goal_id:"goal-a",todo_id:"todo_target",owner:"agent-a",idempotency_key:"lease-a",expected_version:1,
    ttl_seconds:600,new_owner:null,new_idempotency_key:null,lock_token:null,committed:false,release_lease:false,
    fence_owner:null,fence_idempotency_key:null,fence_expected_version:null,fence_expected_lease_epoch:1,authority};
  for (const operation of ["renew","transfer"]) {
    const result = await executeTaskLeaseLifecycle({...base,operation,
      ...(operation === "transfer" ? {new_owner:"agent-b",new_idempotency_key:"lease-b"} : {})},{now});
    assert.equal(result.ok,false); assert.equal(result.error_code,"canonical_authority_required");
    assert.equal(await readFile(path,"utf8"),before);
  }
  const invalid = await executeTaskLeaseLifecycle({...base,operation:"release",expected_version:0},{now});
  assert.equal(invalid.ok,false); assert.equal(await readFile(path,"utf8"),before);
  const released = await executeTaskLeaseLifecycle({...base,operation:"release"},{now});
  assert.equal(released.ok,true); assert.equal(released.released,true);
  assert.equal(JSON.parse(await readFile(path,"utf8")).status,"released");
  assert.equal((await executeTaskLeaseLifecycle({...base,operation:"release"},{now})).ok,true);
  assert.equal(await readFile(source,"utf8"),"original source");
});


for (const provider of ["file", "sqlite"] as const) test(`${provider} imported legacy policy requires upgrade before new execution`, async t => {
  const root = await mkdtemp(join(tmpdir(), "cutoff-policy-"));
  t.after(() => rm(root, {recursive:true, force:true}));
  const store = provider === "file" ? new FileAuthorityStore(root, "goal-a") : new SqliteAuthorityStore(root, "goal-a");
  const todo = {todo_id:"todo_work",role:"agent",status:"open",done:false,text:"Imported work",
    archive_state:"active",claimed_by:null,task_class:"advancement_task"};
  const lease = {schema_version:"task_lease_v0",goal_id:"goal-a",todo_id:"todo_work",owner:"agent-a",
    idempotency_key:"old",version:1,lease_epoch:1,status:"active",write_scopes:[],acquire_ttl_seconds:600,
    acquired_at:"2026-09-01T03:00:00Z",updated_at:"2026-09-01T03:00:00Z",expires_at:"2026-09-01T03:10:00Z"};
  assert.equal((await store.commitAuthority({expected_provider_revision:null, operation_id:"seed",
    next_projection:authorityProjectionFixture("goal-a",[todo],[lease],"native",{handoff_mode:"legacy"}),
    events:[],receipts:[]})).status,"applied");
  const command = {goal_id:"goal-a",todo_id:"todo_work",owner:"agent-a",idempotency_key:"old",
    expected_version:1,ttl_seconds:600,write_scopes:[],registered_agents:["agent-a","agent-b"],now:new Date("2026-09-01T03:01:00Z")};
  const before = await store.loadAuthority();
  for (const operation of ["renew","transfer"] as const) {
    const refused = await executeCanonicalTaskLeaseLifecycle(store,{...command,operation,
      ...(operation === "transfer" ? {new_owner:"agent-b",new_idempotency_key:"new"} : {})});
    assert.equal(refused.reason_code,"handoff_mode_retired");
    assert.match(String(refused.reason),/handoff-mode plan-migration/);
    assert.deepEqual(await store.loadAuthority(),before);
  }
  assert.equal((await executeCanonicalTaskLeaseLifecycle(store,{...command,operation:"release",ttl_seconds:null})).status,"applied");
  const released = await store.loadAuthority();
  const denied = await executeCanonicalTaskLeaseAcquire(store,{...command,idempotency_key:"new",expected_version:1});
  assert.equal(denied.reason_code,"handoff_mode_retired");
  assert.deepEqual(await store.loadAuthority(),released);
});
