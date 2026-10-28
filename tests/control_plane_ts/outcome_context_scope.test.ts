import assert from "node:assert/strict";
import test from "node:test";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {projectGoalAcceptance} from "../../loopx/control_plane/goals/acceptance_contract.ts";
import {inspectGoalAcceptance} from "../../loopx/control_plane/goals/acceptance_authority.ts";
import {refineGoalOutcome, outcomeContextReadPlan} from "../../loopx/control_plane/goals/outcome_requirements.ts";
import {outcomeReadGuidance} from "../../loopx/control_plane/work_items/outcome_read_guidance.ts";
import {nativeContextDelivery, capturedOutcomeRead} from "../../loopx/control_plane/quota/outcome_context.ts";
import {restoreContextDelivery} from "../../loopx/control_plane/quota/context_delivery.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";

const goal = "scope-goal", agent = "author", todo = "todo_selected";
const source = {path: "/synthetic/capture.json", sha256: "a".repeat(64)};
const scope = {goal_id: goal, agent_id: agent, session_id: "session", context_epoch: "epoch"};

function fixture(extra: JsonObject = {}, sharedSource = false) {
  let head = authorityProjectionFixture(goal, [
    {todo_id: todo, role: "agent", status: "open", done: false, text: "Selected task tail: preserve cancellation.",
      task_class: "advancement_task", claimed_by: agent, archive_state: "active", ...extra},
    {todo_id: "todo_peer", role: "agent", status: "open", done: false, text: "Independent peer task.",
      task_class: "advancement_task", claimed_by: "peer", archive_state: "active"},
  ]);
  for (const [owner, work] of [[agent, todo], ["peer", "todo_peer"]]) {
    head = {...head, goal_outcomes: refineGoalOutcome(head, goal, null, owner, {
      outcome_id: owner, owner_agent_id: owner, objective: `Preserve ${owner} requirements.`, todo_ids: [work],
      requirements: Array.from({length: 12}, (_, i) => ({id: `r${i}`,
        description: `${owner} requirement ${i}: ` + "Preserve exact behavior and evidence. ".repeat(8).trim()})),
      source: {ref: sharedSource ? "message:shared" : `message:${owner}`, summary: "Original explicit requirement."},
    })};
  }
  return head;
}

function packet(head = fixture(), change?: (acceptance: JsonObject) => void): JsonObject {
  const acceptance = projectGoalAcceptance(head, goal, [agent, "peer"]);
  change?.(acceptance);
  const plan = outcomeContextReadPlan(head, goal, agent, todo, acceptance)!;
  return {ok: true, goal_id: goal, agent_identity: {agent_id: agent}, selected_todo: {todo_id: todo},
    interaction_contract: {agent_channel: {must_attempt: true, delivery_allowed: true, required_reads: [],
      work_context: {complete: true, instruction: "Read all required context.", sources: [{source: "goal_acceptance",
        reason: outcomeReadGuidance(), content: {status: "loaded", provider_revision: "source-revision",
          goal_acceptance_contract: acceptance, outcome_context: {...plan, provider_revision: "source-revision"}}}]}},
      cli_channel: {settlement_plan: {identity: {turn_instance_id: "original-turn"}, ordered_steps: []}}},
    goal_boundary: {stop: "Do not publish."}};
}
function content(p: JsonObject): JsonObject {
  return ((p.interaction_contract as JsonObject).agent_channel as JsonObject).work_context.sources[0].content;
}
function prepare(payload: JsonObject, extra: JsonObject = {}): JsonObject {
  return nativeContextDelivery({payload, source_capture: source, scope, capture_id: "capture", surface: "quota_packet", ...extra});
}
function projected(result: JsonObject): JsonObject {
  return (result.projection ?? (result.delivery as JsonObject)?.context) as JsonObject;
}
function projectedRows(view: JsonObject): JsonObject[] {
  return content(view.context as JsonObject).goal_acceptance_contract.outcome_requirements.outcomes;
}

test("cold scope retains all selected requirements, current controls, original source and exact optional detail", () => {
  const p = packet(), before = structuredClone(p), result = prepare(p), view = projected(result);
  assert.equal(view.schema_version, "outcome_context_projection_v0");
  assert.deepEqual(projectedRows(view).map(row => row.id), [agent]);
  assert.deepEqual(projectedRows(view)[0], content(p).goal_acceptance_contract.outcome_requirements.outcomes[0]);
  assert.deepEqual((view.context as JsonObject).goal_boundary, p.goal_boundary);
  assert.deepEqual((view.context as JsonObject).interaction_contract.cli_channel, p.interaction_contract.cli_channel);
  assert.deepEqual(p, before, "presentation cannot mutate the canonical capture");
  const peer = (view.outcome_index as JsonObject[]).find(row => row.id === "peer")!;
  assert.equal(peer.required_in_scope, false);
  const detail = capturedOutcomeRead({payload: view, source_payload: p, outcome_ref: peer.outcome_ref});
  assert.deepEqual(detail.outcome, content(p).goal_acceptance_contract.outcome_requirements.outcomes[1]);
  assert.ok(JSON.stringify(view).length < JSON.stringify(p).length);
});

for (const [name, extra, shared] of [
  ["successor dependency", {successor_todo_ids: ["todo_peer"]}, false],
  ["unblocks dependency", {unblocks_todo_id: "todo_peer"}, false],
  ["shared source evidence", {}, true],
] as const) test(`${name} widens to the complete source`, () => {
  const p = packet(fixture(extra, shared));
  assert.deepEqual(projected(prepare(p)), p);
});

for (const field of ["failed", "stale"]) test(`${field} peer evidence cannot be hidden by ownership`, () => {
  const p = packet(fixture(), acceptance => {
    acceptance.outcome_requirements.outcomes[1].requirements[0].status = field;
  });
  assert.deepEqual(projected(prepare(p)), p);
});

for (const [name, mutate] of [
  ["unknown outcome field", (p: JsonObject) => {content(p).goal_acceptance_contract.outcome_requirements.outcomes[1].unknown_rule = "Preserve me";}],
  ["unknown instruction", (p: JsonObject) => {p.interaction_contract.agent_channel.work_context.sources[0].reason += " Extra mandatory read.";}],
  ["pending other source read", (p: JsonObject) => {p.interaction_contract.agent_channel.required_reads.push({source: "goal_state"});}],
  ["incomplete work context", (p: JsonObject) => {p.interaction_contract.agent_channel.work_context.complete = false;}],
  ["pending acceptance read", (p: JsonObject) => {p.interaction_contract.agent_channel.required_reads.push({source: "goal_acceptance"});}],
  ["broad replan", (p: JsonObject) => {p.replan_action_packet = {obligation_id: "replan"};}],
  ["source revision mismatch", (p: JsonObject) => {content(p).provider_revision = "changed";}],
  ["source body mismatch", (p: JsonObject) => {content(p).goal_acceptance_contract.outcome_requirements.outcomes[0].objective += " amended";}],
  ["missing plan", (p: JsonObject) => {delete content(p).outcome_context;}],
  ["scope mismatch", (p: JsonObject) => {content(p).outcome_context.agent_id = "peer";}],
  ["incomplete plan", (p: JsonObject) => {content(p).outcome_context.complete = false;}],
] as const) test(`${name} retains full delivery`, () => {
  const p = packet(); mutate(p);
  assert.deepEqual(projected(prepare(p)), p);
});

test("Goal-wide acceptance remains full", () => {
  const p = packet(fixture(), acceptance => {acceptance.criteria = [{id: "global"}]; acceptance.scope = {kind: "all_advancement"};});
  assert.deepEqual(projected(prepare(p)), p);
});

test("warm delta reconstructs precisely the required presentation, not hidden optional bodies", () => {
  const p = packet(), first = prepare(p), full = first.delivery as JsonObject;
  const next = structuredClone(p); next.goal_boundary = {stop: "Stop immediately."};
  const second = prepare(next, {capture_id: "next", baseline: full, retained: {complete: true, target: full.target}});
  const delivery = second.delivery as JsonObject;
  assert.equal(delivery.mode, "delta");
  const expected = projected(prepare(next));
  assert.deepEqual(restoreContextDelivery({current: {...full, context: expected, target: delivery.target},
    delivery, baseline: full}).context, expected);
  assert.deepEqual(projectedRows(expected).map(row => row.id), [agent]);
  assert.equal(JSON.stringify(delivery).includes("Stop immediately."), true);
  const fresh = prepare(next, {scope: {...scope, context_epoch: "new-epoch"}, baseline: full,
    retained: {complete: true, target: full.target}});
  assert.equal(fresh.delivery.mode, "full");
  assert.deepEqual(projected(fresh), expected);
});

test("edited scoped wrapper and foreign detail references are rejected", () => {
  const p = packet(), view = projected(prepare(p));
  const edited = structuredClone(view); edited.context.goal_boundary = {stop: "Never stop."};
  assert.throws(() => capturedOutcomeRead({payload: edited, source_payload: p, outcome_ref: ""}), /differs/);
  assert.throws(() => capturedOutcomeRead({payload: p, outcome_ref: "sha256:" + "0".repeat(64)}), /exact captured/);
});

for (const provider of ["file", "sqlite"]) test(`${provider}: inspect derives scope from one actual canonical snapshot without mutations`, async t => {
  const root = await mkdtemp(join(tmpdir(), "scope-provider-")); t.after(() => rm(root, {recursive: true, force: true}));
  const store = provider === "file" ? new FileAuthorityStore(root, goal) : new SqliteAuthorityStore(root, goal);
  const seeded = await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
    next_projection: fixture(), events: [], receipts: []});
  assert.equal(seeded.status, "applied", JSON.stringify(seeded));
  const before = await store.loadAuthority(); let reads = 0; const load = store.loadAuthority.bind(store);
  store.loadAuthority = async () => {reads++; return load();};
  const scoped = await inspectGoalAcceptance(store, goal, todo, undefined, false, undefined, [agent, "peer"], agent);
  assert.equal(reads, 1);
  assert.equal(scoped.outcome_context.complete, true);
  assert.deepEqual(scoped.outcome_context.required_outcome_ids, [agent]);
  assert.equal(scoped.outcome_context.acceptance_digest, canonicalAuthoritySha256(scoped.goal_acceptance_contract));
  const off = await inspectGoalAcceptance(store, goal, todo, undefined, false, undefined, [agent, "peer"]);
  assert.equal(off.outcome_context, undefined);
  assert.deepEqual(off.goal_acceptance_contract, scoped.goal_acceptance_contract);
  assert.deepEqual(await store.loadAuthority(), before);
});


test("a short optional body is kept inline when scope framing would cost more", () => {
  const p = packet(fixture(), acceptance => {
    for (const row of acceptance.outcome_requirements.outcomes) {
      row.requirements = [{id: "small", description: "Keep it.", status: "unverified", planning_satisfied: false}];
    }
  });
  assert.deepEqual(projected(prepare(p)), p);
});


test("newly related work is delivered in full before relying on previously unread detail", () => {
  const first = prepare(packet()).delivery as JsonObject;
  const next = packet(fixture({successor_todo_ids: ["todo_peer"]}));
  const nextFull = prepare(next).delivery as JsonObject;
  const result = prepare(next, {baseline: first, retained: {complete: true, target: first.target}});
  const delivery = result.delivery as JsonObject;
  const restored = delivery.mode === "full" ? delivery.context : restoreContextDelivery({
    current: nextFull, delivery, baseline: first,
  }).context;
  assert.deepEqual(restored, next);
  assert.equal(content(restored).goal_acceptance_contract.outcome_requirements.outcomes.length, 2);
});
