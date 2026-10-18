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
import {refineGoalAcceptanceOutcome, inspectGoalAcceptance, configureGoalAcceptance} from "../../loopx/control_plane/goals/acceptance_authority.ts";
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
  assert.deepEqual(projectGoalAcceptance(original, goal), {});
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
    assert.deepEqual(projectGoalOutcomes(waiting, goal, {})!.acceptance_gaps, [], "real waits remain with their Todo owner");
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

for (const provider of ["file", "sqlite"] as const) test(`${provider}: first exact-lifetime outcome preserves legacy acceptance atomically`, async t => {
  const root = await mkdtemp(join(tmpdir(), "outcome-lifetime-")); t.after(() => rm(root, {recursive: true, force: true}));
  const store = provider === "file" ? new FileAuthorityStore(root, goal) : new SqliteAuthorityStore(root, goal);
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null, next_projection: fixture(), events: [], receipts: []});
  const basis = await inspectGoalAcceptance(store, goal);
  const configured = await configureGoalAcceptance(store, {goal_id: goal, actor_agent_id: null, operation_id: "legacy-check",
    expected_provider_revision: basis.provider_revision, document: {scope: {kind: "selected_work", todo_ids: ["todo_tour"]},
      objective: "Deliver the tour", non_goals: [], criteria: [{id: "opening", description: "Keep the opening", validation_argv: ["true"]}],
      bindings: [{todo_id: "todo_tour", criterion_ids: ["opening"]}]}});
  assert.equal(configured.status, "applied");
  const before = await inspectGoalAcceptance(store, goal);
  const request = {goal_id: goal, goal_ref: goalRef, actor_agent_id: "author", operation_id: "first-exact",
    expected_provider_revision: before.provider_revision, refinement: patch()};
  assert.equal((await refineGoalAcceptanceOutcome(store, request)).status, "applied");
  const after = (await inspectGoalAcceptance(store, goal, undefined, goalRef)).goal_acceptance_contract as JsonObject;
  const {outcome_requirements, lifecycle_state, goal_ref, ...checks} = after;
  assert.deepEqual(checks, before.goal_acceptance_contract);
  assert.equal((outcome_requirements as JsonObject).current_lifetime, true);
  assert.deepEqual(goal_ref, goalRef); assert.equal(lifecycle_state, "active");
  assert.equal((await refineGoalAcceptanceOutcome(store, request)).status, "replayed");
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
  const read = projectGoalOutcomes(reviewed, goal, {})!;
  assert.deepEqual(read.acceptance_gaps, []);
  const row = ((read.outcomes as JsonObject[])[0].requirements as JsonObject[])[0];
  assert.equal(row.status, "unverified");
  assert.equal((row.agent_review as JsonObject).independently_verified, false);
  assert.equal(row.planning_satisfied, true);
  const failed = projectGoalOutcomes(reviewed, goal, {enabled: true, status: "failed", criteria: [{id: "opening", description: "Keep the opening"}], tasks: []})!;
  assert.equal((failed.acceptance_gaps as unknown[]).length, 1, "self-assessment never bypasses a configured check");
  const changed = {...reviewed, goal_outcomes: refineGoalOutcome(reviewed, goal, null, "author", patch({requirements: [{id: "order", description: "Delegation before board"}], source: {ref: "message:order", summary: "Add ordering requirement"}}))};
  const stale = projectGoalOutcomes(changed, goal, {})!;
  assert.equal((stale.acceptance_gaps as unknown[]).length, 1);
  assert.equal(((((stale.outcomes as JsonObject[])[0].requirements as JsonObject[])[0].agent_review) as JsonObject).basis_current, false);
});

test("delta-only refinement resolves immutable intent without permitting foreign writes", () => {
  const original = fixture();
  const head = {...original, goal_outcomes: refineGoalOutcome(original, goal, null, "author", patch())};
  const delta = {outcome_id: "tour", source: {ref: "message:delta", summary: "Add the ordering requirement"},
    requirements: [{id: "order", description: "Keep the correct order"}]};
  const result = refineGoalOutcome(head, goal, null, "author", delta);
  assert.equal(result.outcomes[0].objective, "Deliver the tour");
  assert.equal(result.outcomes[0].owner_agent_id, "author");
  assert.deepEqual(result.outcomes[0].todo_ids, ["todo_tour"]);
  assert.deepEqual(result.outcomes[0].requirements.map(r => r.id), ["opening", "order"]);
  assert.throws(() => refineGoalOutcome(original, goal, null, "author", delta), /outcome Agent/);
  assert.throws(() => refineGoalOutcome(head, goal, null, "other", delta), /own outcomes/);
});

for (const provider of ["file", "sqlite"] as const) test(`${provider}: referenced checks bind reviewed requirements, preserve disabled checks and replay`, async t => {
  const root = await mkdtemp(join(tmpdir(), "referenced-check-")); t.after(() => rm(root, {recursive: true, force: true}));
  const store = provider === "file" ? new FileAuthorityStore(root, goal) : new SqliteAuthorityStore(root, goal);
  const head = fixture(); head.goal_outcomes = refineGoalOutcome(head, goal, null, "author", patch());
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null, next_projection: head, events: [], receipts: []});
  const original = await inspectGoalAcceptance(store, goal);
  const document = {outcome_id: "tour", criteria: [{id: "opening", validation_argv: ["true"]}]};
  const request = {goal_id: goal, actor_agent_id: null, operation_id: "checks", expected_provider_revision: original.provider_revision, document};
  const preview = await configureGoalAcceptance(store, {...request, dry_run: true});
  const resolved = preview.resolved_document as JsonObject;
  assert.equal(resolved.objective, "Deliver the tour");
  assert.deepEqual(resolved.scope, {kind: "selected_work", todo_ids: ["todo_tour"]});
  assert.deepEqual(resolved.bindings, [{todo_id: "todo_tour", criterion_ids: ["opening"]}]);
  assert.equal((resolved.criteria as JsonObject[])[0].description, "Keep the opening");
  assert.deepEqual(await inspectGoalAcceptance(store, goal), original, "prepare never writes or executes");
  for (const document of [{outcome_id: "missing", criteria: request.document.criteria},
    {outcome_id: "tour", criteria: [{id: "missing", validation_argv: ["true"]}]},
    {...request.document, objective: "Replace the original"},
    {outcome_id: "tour", criteria: [{id: "opening", description: "Rewrite the requirement", validation_argv: ["true"]}]}]) {
    await assert.rejects(configureGoalAcceptance(store, {...request, document, dry_run: true}));
  }
  const reviewed = await refineGoalAcceptanceOutcome(store, {goal_id: goal, actor_agent_id: "author", operation_id: "review",
    expected_provider_revision: original.provider_revision, refinement: {outcome_id: "tour", source: {ref: "review:1", summary: "Inspected output"},
      review: [{id: "opening", conclusion: "met", evidence_refs: ["tour.txt"], note: "Opening remains"}]}});
  const admitted = {...request, review_context_digest: preview.review_context_digest};
  assert.equal((await configureGoalAcceptance(store, admitted)).status, "applied", "review bookkeeping alone does not invalidate a prepared check");
  const configured = await inspectGoalAcceptance(store, goal);
  assert.equal((configured.goal_acceptance_contract as JsonObject).outcome_id, "tour");
  const next = await configureGoalAcceptance(store, {...request, operation_id: "next-preview", expected_provider_revision: configured.provider_revision, dry_run: true});
  const changed = await refineGoalAcceptanceOutcome(store, {goal_id: goal, actor_agent_id: "author", operation_id: "requirement",
    expected_provider_revision: configured.provider_revision, refinement: {outcome_id: "tour", source: {ref: "message:2", summary: "Preserve ordering too"},
      requirements: [{id: "order", description: "Preserve ordering"}]}});
  const stale = await configureGoalAcceptance(store, {...request, operation_id: "stale-plan", expected_provider_revision: configured.provider_revision,
    review_context_digest: next.review_context_digest});
  assert.equal(stale.reason_code, "goal_acceptance_review_context_mismatch");
  assert.equal((await configureGoalAcceptance(store, admitted)).status, "replayed", "historical retry never re-resolves or reconfigures intent");
  const disabled = await configureGoalAcceptance(store, {...request, operation_id: "disable", expected_provider_revision: changed.provider_revision, document: null});
  const after = await refineGoalAcceptanceOutcome(store, {goal_id: goal, actor_agent_id: "author", operation_id: "more-evidence",
    expected_provider_revision: disabled.provider_revision, refinement: {outcome_id: "tour", source: {ref: "review:2", summary: "Continue polishing"}}});
  assert.equal((after.goal_acceptance_contract as JsonObject).criteria, undefined, "removing checks leaves default outcome review available");
  assert.equal(Object.hasOwn((await store.loadAuthority() as {head: JsonObject}).head.goal_acceptance as JsonObject, "enabled"), false);
  assert.equal(Object.hasOwn(reviewed.goal_acceptance_contract as JsonObject, "enabled"), false);
});

for (const checksConfigured of [false, true]) test(`upgrade: every Goal receives the same refinement/review loop with checks=${checksConfigured}`, async t => {
  const root = await mkdtemp(join(tmpdir(), "outcome-upgrade-")); t.after(() => rm(root, {recursive: true, force: true}));
  const store = new SqliteAuthorityStore(root, goal);
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null, next_projection: fixture(), events: [], receipts: []});
  const before = await inspectGoalAcceptance(store, goal);
  const document = {scope: {kind: "selected_work", todo_ids: ["todo_tour"]}, objective: "Deliver the tour", non_goals: [],
    criteria: [{id: "opening", description: "Keep the opening", validation_argv: ["true"]}], bindings: [{todo_id: "todo_tour", criterion_ids: ["opening"]}]};
  await configureGoalAcceptance(store, {goal_id: goal, actor_agent_id: null, operation_id: "initial-checks", document,
    expected_provider_revision: before.provider_revision});
  const current = await store.loadAuthority(); assert.equal(current.status, "loaded"); if (current.status !== "loaded") return;
  const {owner_goal_ref: _ref, ...state} = current.head.goal_acceptance as JsonObject;
  // An actual old persisted Goal; not a specially activated new fixture.
  await store.commitAuthority({operation_id: "previous-version", expected_provider_revision: current.provider_revision,
    next_projection: {...current.head, goal_acceptance: {...state, schema_version: "loopx_goal_acceptance_v0", enabled: checksConfigured}}, events: [], receipts: []});
  const old = await inspectGoalAcceptance(store, goal);
  const request = {goal_id: goal, goal_ref: goalRef, actor_agent_id: "author", operation_id: "upgrade-refinement",
    expected_provider_revision: old.provider_revision, refinement: patch()};
  const upgraded = await refineGoalAcceptanceOutcome(store, request);
  assert.equal(upgraded.status, "applied");
  const result = upgraded.goal_acceptance_contract as JsonObject;
  assert.equal(Object.hasOwn(result, "enabled"), false);
  assert.equal(Array.isArray(result.criteria), checksConfigured);
  assert.equal(((result.outcome_requirements as JsonObject).outcomes as JsonObject[]).length, 1);
  const canonical = await store.loadAuthority(); assert.equal(canonical.status, "loaded"); if (canonical.status !== "loaded") return;
  assert.equal((canonical.head.goal_acceptance as JsonObject).schema_version, "loopx_goal_acceptance_v2");
  assert.equal(Object.hasOwn(canonical.head.goal_acceptance as JsonObject, "enabled"), false);
  assert.deepEqual(canonical.head.root_commitments, current.head.root_commitments);
  const review = await refineGoalAcceptanceOutcome(store, {...request, operation_id: "upgrade-review", expected_provider_revision: upgraded.provider_revision,
    refinement: {outcome_id: "tour", source: {ref: "artifact:review", summary: "Inspected the introduction"},
      review: [{id: "opening", conclusion: "unmet", evidence_refs: ["tour.txt"], note: "Introduction still missing"}]}});
  assert.equal(((review.goal_acceptance_contract as JsonObject).outcome_requirements as JsonObject).goal_completion_assessed, false);
  assert.equal((((review.goal_acceptance_contract as JsonObject).outcome_requirements as JsonObject).acceptance_gaps as JsonObject[]).length, 1);
});
