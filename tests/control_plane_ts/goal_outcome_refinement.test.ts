import assert from "node:assert/strict";
import test from "node:test";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import {refineGoalOutcome, projectGoalOutcomes} from "../../loopx/control_plane/goals/outcome_requirements.ts";
import {refineGoalAcceptanceOutcome, inspectGoalAcceptance} from "../../loopx/control_plane/goals/acceptance_authority.ts";
import {observeAcceptanceOracles} from "../../loopx/control_plane/goals/acceptance_oracle.ts";
import {projectGoalAcceptance} from "../../loopx/control_plane/goals/acceptance_contract.ts";

const goal = "tour-goal";
const goalRef = {goal_id: goal, goal_instance_id: "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"};
function fixture() {return authorityProjectionFixture(goal, [{todo_id: "todo_tour", role: "agent", status: "done", done: true,
  archive_state: "active", text: "Produce the presentation", task_class: "advancement_task", claimed_by: "author"}], [], "native",
  {root_commitments: {objective: "Explain the product", acceptance: ["Clear and accurate"]}});}
function patch(extra: JsonObject = {}): JsonObject {return {outcome_id: "tour", owner_agent_id: "author", objective: "Deliver the tour",
  requirements: [{id: "opening", description: "Keep the opening"}], todo_ids: ["todo_tour"],
  source: {ref: "message:initial", summary: "The owner requested a tour with an opening"}, ...extra};}

test("refinement retains original intent, accumulates requirements and cannot invent success", () => {
  const original = fixture();
  assert.deepEqual(projectGoalAcceptance(original, goal), {enabled: false});
  const state = refineGoalOutcome(original, goal, null, "author", patch());
  const head = {...original, goal_outcomes: state};
  const next = refineGoalOutcome(head, goal, null, "author", patch({requirements: [{id: "order", description: "Show delegation before the task board"}],
    todo_ids: [], source: {ref: "message:correction", summary: "Reorder the functional sections"}}));
  assert.deepEqual(next.outcomes[0].requirements.map(r => r.id), ["opening", "order"]);
  assert.deepEqual(next.outcomes[0].todo_ids, ["todo_tour"]);
  assert.deepEqual(original.root_commitments, head.root_commitments);
  assert.equal(next.outcomes[0].objective, state.outcomes[0].objective);
  const projection = projectGoalAcceptance({...original, goal_outcomes: next}, goal);
  const outcomes = (projection.outcome_requirements as JsonObject).outcomes as JsonObject[];
  assert.equal(((projection.outcome_requirements as JsonObject).acceptance_gaps as JsonObject[])[0].agent_id, "author");
  for (const status of ["open", "blocked", "deferred"]) {
    const waiting = authorityProjectionFixture(goal, [{todo_id: "todo_tour", role: "agent", text: "Produce the presentation", task_class: "advancement_task", claimed_by: "author", archive_state: "active", status, done: status === "deferred"}], [], "native", {goal_outcomes: next});
    assert.deepEqual(projectGoalOutcomes(waiting, goal, {enabled: false})!.acceptance_gaps, [], "real waits remain with their Todo owner");
  }
  assert.ok((outcomes[0].requirements as JsonObject[]).every(r => r.status === "unverified"), "a done Todo is not accepted requirements");
  for (const bad of [patch({objective: "Replace the original intent"}), patch({owner_agent_id: "other"}),
    patch({requirements: [{id: "opening", description: "Remove the opening"}]}), patch({todo_ids: ["todo_missing"]}),
    {...patch(), passed: true}]) assert.throws(() => refineGoalOutcome(head, goal, null, "author", bad));
  assert.throws(() => refineGoalOutcome(head, goal, null, "other", patch()));
  assert.throws(() => refineGoalOutcome(head, goal, goalRef, "author", patch()), /lifetime/);
});

test("only matching current acceptance and work coverage can qualify requirements", () => {
  const head = fixture(); head.goal_outcomes = refineGoalOutcome(head, goal, null, "author", patch());
  const accepted = {enabled: true, status: "accepted", criteria: [{id: "opening", description: "Keep the opening"}],
    tasks: [{todo_id: "todo_tour", state: "ready", criterion_ids: ["opening"]}]};
  const states = (checks: JsonObject) => ((projectGoalOutcomes(head, goal, checks)!.outcomes as JsonObject[])[0].requirements as JsonObject[]).map(r => r.status);
  assert.deepEqual(states(accepted), ["accepted"]);
  for (const status of ["stale", "failed", "partial", "held", "unverified"]) assert.deepEqual(states({...accepted, status}), [status]);
  assert.deepEqual(states({...accepted, tasks: []}), ["unverified"]);
  assert.deepEqual(states({...accepted, criteria: [{id: "opening", description: "Unrelated check"}]}), ["unverified"]);
  assert.deepEqual(states({...accepted, criteria: [{...accepted.criteria[0], oracle: {state: "recorded"}}]}), ["unverified"]);
  head.goal_acceptance_lifecycle = {schema_version: "loopx_goal_acceptance_lifecycle_v0", state: "active", goal_ref: goalRef};
  assert.deepEqual(states(accepted), ["unverified"], "legacy declarations never acquire a successor lifetime");
});

for (const provider of ["file", "sqlite"] as const) test(`${provider}: native CAS, replay and restart retain one declaration`, async t => {
  const root = await mkdtemp(join(tmpdir(), "outcome-refinement-")); t.after(() => rm(root, {recursive: true, force: true}));
  const open = () => provider === "file" ? new FileAuthorityStore(root, goal) : new SqliteAuthorityStore(root, goal);
  const store = open();
  const seeded = await store.commitAuthority({operation_id: "seed", expected_provider_revision: null, next_projection: fixture(), events: [], receipts: []});
  assert.equal(seeded.status, "applied");
  const loaded = await store.loadAuthority(); assert.equal(loaded.status, "loaded"); if (loaded.status !== "loaded") return;
  const request = {goal_id: goal, actor_agent_id: "author", operation_id: "first", expected_provider_revision: loaded.provider_revision, refinement: patch()};
  const preview = await refineGoalAcceptanceOutcome(store, {...request, dry_run: true});
  assert.equal(preview.status, "planned"); assert.deepEqual(await store.loadAuthority(), loaded);
  assert.equal((await refineGoalAcceptanceOutcome(store, request)).status, "applied");
  const committed = await store.loadAuthority();
  assert.equal((await refineGoalAcceptanceOutcome(open(), request)).status, "replayed");
  assert.deepEqual(await store.loadAuthority(), committed);
  assert.equal((await refineGoalAcceptanceOutcome(store, {...request, operation_id: "stale"})).status, "conflict");
  assert.equal((await refineGoalAcceptanceOutcome(store, {...request, refinement: patch({objective: "Different"})})).reason_code, "coordination_operation_identity_mismatch");
  const read = await inspectGoalAcceptance(open(), goal);
  assert.equal(((read.goal_acceptance_contract as JsonObject).outcome_requirements as JsonObject).revision, 1);
  if (committed.status === "loaded") assert.deepEqual(committed.head.root_commitments, loaded.head.root_commitments);
});


test("host oracle freshness recomputes outcome evidence and unmet coverage", () => {
  const head = fixture(); head.goal_outcomes = refineGoalOutcome(head, goal, null, "author", patch());
  const evidence = {input_versions: [{ref: "input.txt", sha256: "a".repeat(64)}], output_versions: [{ref: "tour.txt", sha256: "b".repeat(64)}]};
  const checks = {enabled: true, status: "accepted", criteria: [{id: "opening", description: "Keep the opening", oracle: {state: "recorded"}}],
    tasks: [{todo_id: "todo_tour", state: "ready", criterion_ids: ["opening"]}], verification: {results: [{criterion_id: "opening", oracle_evidence: evidence}]}};
  const contract = {...checks, outcome_requirements: projectGoalOutcomes(head, goal, checks)!};
  for (const current of [true, false]) {
    const result = observeAcceptanceOracles({contract, observations: [{criterion_id: "opening", available: true, source_current: current, ...evidence}]});
    const outcomes = result.outcome_requirements as JsonObject;
    const requirement = ((outcomes.outcomes as JsonObject[])[0].requirements as JsonObject[])[0];
    assert.equal(requirement.status, current ? "accepted" : "unverified");
    assert.equal((outcomes.acceptance_gaps as unknown[]).length, current ? 0 : 1);
  }
});


test("default Agent review closes planning gaps without claiming independent acceptance", () => {
  const original = fixture();
  const state = refineGoalOutcome(original, goal, null, "author", patch());
  const first = {...original, goal_outcomes: state};
  const item = {id: "opening", conclusion: "met", evidence_refs: ["tour.txt#introduction"], note: "Inspected the opening against the retained requirement"};
  for (const review of [[], [{...item, evidence_refs: []}], [{...item, id: "unknown"}], [{...item, passed: true}]]) {
    assert.throws(() => refineGoalOutcome(first, goal, null, "author", patch({requirements: [], review})));
  }
  const reviewed = {...original, goal_outcomes: refineGoalOutcome(first, goal, null, "author", patch({requirements: [], review: [item], source: {ref: "artifact:review", summary: "Reviewed the produced artifact"}}))};
  const read = projectGoalOutcomes(reviewed, goal, {enabled: false})!;
  assert.deepEqual(read.acceptance_gaps, []);
  const row = ((read.outcomes as JsonObject[])[0].requirements as JsonObject[])[0];
  assert.equal(row.status, "unverified");
  assert.equal((row.agent_review as JsonObject).independently_verified, false);
  assert.equal(row.planning_satisfied, true);
  const failed = projectGoalOutcomes(reviewed, goal, {enabled: true, status: "failed", criteria: [{id: "opening", description: "Keep the opening"}], tasks: []})!;
  assert.equal((failed.acceptance_gaps as unknown[]).length, 1, "self-assessment never bypasses a configured check");
  const changed = {...reviewed, goal_outcomes: refineGoalOutcome(reviewed, goal, null, "author", patch({requirements: [{id: "order", description: "Delegation before board"}], source: {ref: "message:order", summary: "Add ordering requirement"}}))};
  const stale = projectGoalOutcomes(changed, goal, {enabled: false})!;
  assert.equal((stale.acceptance_gaps as unknown[]).length, 1);
  assert.equal(((((stale.outcomes as JsonObject[])[0].requirements as JsonObject[])[0].agent_review) as JsonObject).basis_current, false);
});
