import assert from "node:assert/strict";
import test from "node:test";
import {normalizeStrideDecision, strideControlContract} from "../../loopx/control_plane/work_items/stride_policy.ts";
import {buildVisionCheckpoint, VISION_REFRESH_REQUEST_SCHEMA} from "../../loopx/control_plane/goals/vision_checkpoint.ts";
import {projectReplanSemantics} from "../../loopx/control_plane/work_items/replan_semantics.ts";

const contextId = "replan-context-" + "a".repeat(24);
const proposal = {schema_version: "stride_decision_v1", strategy_version: "evidence_conditioned_v1",
  reference_context_id: contextId, reference_obligation_id: "replan-" + "a".repeat(16), basis: "unexplained_gap", axes: {work_slice: "grow", route_distance: "grow", validation_scope: "hold"},
  axis_reasons: {work_slice: "Build a complete alternative.", route_distance: "Test a different mechanism.", validation_scope: "Existing coverage distinguishes it."}, next_probe: {
    scope: "Compare a bounded alternative route.", prediction: "The alternative explains the remaining gap.",
    adoption_criterion: "Retain an attributable validated gain.", cost_bound: "One complete experiment before expanding.", minimum_evidence: "An executed alternative and a controlled attributable comparison."}};
const packet = {schema_version: "goal_vision_replan_contract_v0", agent_id: "agent-a",
  vision_patch: {acceptance_summary: "Applicable evidence leaves a goal gap."}, path_delta: {
    schema_version: "goal_path_delta_v0", outcome: "replan", prior_assumption: "Local tuning could close the gap.",
    observed_reality: "Evidence leaves that explanation unresolved.", changed: ["Test an alternative mechanism."],
    evidence_refs: ["evidence:review"]}, stride_decision: proposal};
const request = {schema_version: VISION_REFRESH_REQUEST_SCHEMA, phase: "prepare", goal_id: "goal-a",
  agent_id: "agent-a", merge_patch: false, existing_agent_vision: null,
  require_path_delta_for_durable_change: false, agent_vision_packet: packet};

test("independent axes permit coherent larger route experiments and broader uncertainty validation", () => {
  assert.deepEqual(normalizeStrideDecision(proposal)[0]?.axes,
    {work_slice: "grow", route_distance: "grow", validation_scope: "hold"});
  assert.deepEqual(normalizeStrideDecision({...proposal, basis: "uncertain_evidence"})[0]?.axes,
    {work_slice: "grow", route_distance: "grow", validation_scope: "hold"});
  assert.deepEqual(normalizeStrideDecision({...proposal, basis: "supported_progress"})[0]?.axes,
    {work_slice: "grow", route_distance: "grow", validation_scope: "hold"});
  assert.equal(strideControlContract({stride_strategy: "off"}), null);
  const prepared = buildVisionCheckpoint(request);
  const decision = (prepared.agent_vision as Record<string, unknown>).stride_decision as Record<string, unknown>;
  assert.deepEqual(decision.axes, {work_slice: "grow", route_distance: "grow", validation_scope: "hold"});
  const budget = (prepared.agent_vision as Record<string, unknown>).vision_budget as Record<string, unknown>;
  assert.ok(Number(budget.total_usage) < 1800);
});

test("invalid, ambiguous, oversized and misplaced control proposals fail authoring", () => {
  for (const patch of [{basis: "plateau_from_elapsed_time"}, {strategy_version: "future_v2"},
    {axes: {work_slice: "tiny", route_distance: "grow", validation_scope: "grow"}}, {axis_reasons: undefined}, {next_probe: {...proposal.next_probe, minimum_evidence: ""}},
    {next_probe: {...proposal.next_probe, scope: "x".repeat(121)}}, {reference_context_id: "missing"},
    {permission: "execute_anything"}, {next_probe: {...proposal.next_probe, cost_bound: ""}}]) {
    assert.throws(() => buildVisionCheckpoint({...request, agent_vision_packet: {
      ...packet, stride_decision: {...proposal, ...patch}}}));
  }
  assert.throws(() => buildVisionCheckpoint({...request, agent_vision_packet: {
    ...packet, vision_patch: {...packet.vision_patch, stride_decision: proposal}, stride_decision: undefined}}), /agent_vision.stride_decision/);
  assert.throws(() => buildVisionCheckpoint({...request, agent_vision_packet: {
    ...packet, stride_decision: {...proposal, next_probe: {...proposal.next_probe, scope: "/Users/private-owner/file"}}}}), /private/);
});

test("an accepted schema and vector do not authorize work or certify gain; stale references cannot discharge a path review", () => {
  const vision = buildVisionCheckpoint(request).agent_vision;
  const obligation = {obligation_id: "replan-" + "a".repeat(16), triggers: [{kind: "vision_acceptance_gap"}],
    replan_context: {stride_strategy: "evidence_conditioned_v1", context_id: contextId}};
  assert.equal(projectReplanSemantics({operation: "qualify", obligation, agent_vision: vision}).accepted, true);
  const stale = projectReplanSemantics({operation: "qualify", obligation: {...obligation, obligation_id: "replan-" + "b".repeat(16)}, agent_vision: vision});
  // A changed live context from shared-pool churn cannot steal the admitted obligation.
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: {...obligation,
    replan_context: {...obligation.replan_context, context_id: "replan-context-" + "b".repeat(24)}}, agent_vision: vision}).accepted, true);
  const legacyVision = {...packet, stride_decision: undefined};
  const missing = projectReplanSemantics({operation: "qualify", obligation, agent_vision: legacyVision});
  assert.equal(missing.accepted, false);
  assert.equal(missing.reason_code, "stride_decision_required");
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: {...obligation,
    replan_context: {stride_strategy: "off"}}, agent_vision: legacyVision}).accepted, true);
  assert.equal(stale.accepted, false);
  assert.equal(stale.reason_code, "stride_reference_obligation_mismatch");
  assert.equal(projectReplanSemantics({operation: "qualify", obligation, stride_decision: proposal}).accepted, false);
});

test("unexplained gap and uncertain evidence do not force smaller experiments", () => {
  for (const basis of ["unexplained_gap", "uncertain_evidence"]) {
    const decision = normalizeStrideDecision({...proposal, basis,
      axes: {work_slice: "grow", route_distance: "grow", validation_scope: "grow"}})[0]!;
    assert.deepEqual(decision.axes, {work_slice: "grow", route_distance: "grow", validation_scope: "grow"});
  }
  assert.equal(normalizeStrideDecision({...proposal, axes: {work_slice: "hold", route_distance: "hold", validation_scope: "grow"}})[0]?.axes.work_slice, "hold");
});

test("Explore evidence and previous adoption are bound without inferring gain or granting authority", async () => {
  const {projectStrideExploreBasis, strideReviewMismatch} = await import("../../loopx/control_plane/work_items/stride_policy.ts");
  const basis = projectStrideExploreBasis({ok: true, goal_id: "goal-a", agent_id: "agent-a",
    graph_enabled: true, harness_enabled: true,
    graph: {writeback_results: [{finding_id: "finding-negative", node_id: "question-route", status: "refuted"}],
      result_page: {remaining: 4, revision: "b".repeat(64)}},
    harness: {selected_branches: [{todo_id: "todo-route", selection_status: "selected", actionable_open: true}]}}, "goal-a", "agent-a")!;
  const previous = "replan-decision-" + "b".repeat(24);
  const context = {stride_strategy: "evidence_conditioned_v1", explore_basis: basis,
    decision_followthrough: {previous_decision: {evidence_ref: previous}, observations: [{evidence_ref: "evidence:executed"}]}};
  const reviewed = {...proposal, previous_review: {reference_decision_ref: previous,
    adoption: "executed", information_gain: "new_evidence", goal_gain: "unknown", evidence_refs: ["evidence:executed"],
    lesson: "Execution gave new scoped evidence; target benefit remains unmeasured."},
    explore_review: {revision: basis.revision, disposition: "deferred", considered_refs: ["finding-negative"],
      reason: "Refutation covers different inputs; test the changed mechanism."}};
  const decision = normalizeStrideDecision(reviewed)[0]!;
  assert.equal(strideReviewMismatch(decision, context), null);
  assert.equal((decision.previous_review as Record<string, unknown>).goal_gain, "unknown");
  assert.match(String(strideReviewMismatch({...decision, previous_review: undefined}, context)), /previous/);
  assert.match(String(strideReviewMismatch({...decision, previous_review: {...reviewed.previous_review,
    reference_decision_ref: "replan-decision-" + "c".repeat(24)}}, context)), /previous/);
  assert.match(String(strideReviewMismatch({...decision, previous_review: {...reviewed.previous_review,
    evidence_refs: ["evidence:invented"]}}, context)), /undelivered/);
  assert.match(String(strideReviewMismatch({...decision, explore_review: {...reviewed.explore_review,
    revision: "c".repeat(64)}}, context)), /revision/);
  assert.match(String(strideReviewMismatch({...decision, explore_review: {...reviewed.explore_review,
    considered_refs: ["finding-foreign"]}}, context)), /foreign/);
  assert.equal(basis.result_page.remaining, 4); // Omitted evidence is not an empty graph.
  assert.throws(() => projectStrideExploreBasis({ok: true, goal_id: "foreign", agent_id: "agent-a"}, "goal-a", "agent-a"), /scope/);
  const unavailable = projectStrideExploreBasis({ok: false, goal_id: "goal-a", agent_id: "agent-a"}, "goal-a", "agent-a")!;
  const unknown = normalizeStrideDecision({...proposal, explore_review: {revision: null, disposition: "unavailable",
    considered_refs: [], reason: "Source unavailable; no applicability or utility inference."}})[0]!;
  assert.equal(strideReviewMismatch(unknown, {explore_basis: unavailable}), null);
  assert.match(String(strideReviewMismatch(decision, {explore_basis: unavailable})), /undelivered|revision/);
});

test("recorded fixed-vector decisions remain readable but cannot be newly authored", async () => {
  const {normalizeRecordedStrideDecision} = await import("../../loopx/control_plane/work_items/stride_policy.ts");
  const {axis_reasons, axes, ...legacy} = proposal;
  const {minimum_evidence, ...oldProbe} = legacy.next_probe;
  const recorded = {...legacy, next_probe: oldProbe};
  assert.equal(normalizeRecordedStrideDecision(recorded)?.axes.work_slice, "shrink");
  assert.equal(normalizeStrideDecision(recorded)[0]?.axes.work_slice, "shrink");
  const oldVision = {...packet, stride_decision: recorded};
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: {obligation_id: proposal.reference_obligation_id,
    triggers: [{kind: "vision_acceptance_gap"}], replan_context: {stride_strategy: "evidence_conditioned_v1"}}, agent_vision: oldVision}).accepted, false);
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: {obligation_id: proposal.reference_obligation_id,
    triggers: [{kind: "vision_acceptance_gap"}], replan_context: {stride_strategy: "off"}}, agent_vision: oldVision}).accepted, true);
  assert.throws(() => normalizeRecordedStrideDecision({...recorded, axes: {work_slice: "grow"}}), /historical/);
});

test("unrelated shared-pool churn keeps the evidence basis; changed scoped evidence does not", async () => {
  const {projectStrideExploreBasis} = await import("../../loopx/control_plane/work_items/stride_policy.ts");
  const source = {ok: true, goal_id: "goal-a", agent_id: "agent-a", graph_enabled: true, harness_enabled: true,
    graph: {writeback_results: [{finding_id: "finding-route", node_id: "route", status: "tentative", summary: "Inputs A only."}]},
    harness: {candidate_count: 1, selected_branches: []}};
  const first = projectStrideExploreBasis(source, "goal-a", "agent-a")!;
  assert.equal(projectStrideExploreBasis({...source, harness: {...source.harness, candidate_count: 100}}, "goal-a", "agent-a")!.revision, first.revision);
  assert.notEqual(projectStrideExploreBasis({...source, graph: {writeback_results: [
    {...source.graph.writeback_results[0], summary: "Different inputs and evidence."}]}}, "goal-a", "agent-a")!.revision, first.revision);
});


test("historical off-mode probes preserve normalized text and its reported budget", () => {
  const expected = {scope: "One complete experiment.", prediction: "Distinguish the remaining explanations.",
    adoption_criterion: "Retain a validated gain.", cost_bound: "One bounded comparison."};
  const historical = {schema_version: proposal.schema_version, strategy_version: proposal.strategy_version,
    reference_context_id: proposal.reference_context_id, reference_obligation_id: proposal.reference_obligation_id,
    basis: "unexplained_gap", next_probe: Object.fromEntries(Object.entries(expected)
      .map(([key, value]) => [key, " ".repeat(4000) + value + " ".repeat(4000)]))};
  const [normalized, usage] = normalizeStrideDecision(historical);
  assert.deepEqual(normalized!.next_probe, expected);
  for (const [key, value] of Object.entries(expected)) assert.equal(usage[`stride_decision.next_probe.${key}`], value.length);
  const authored = buildVisionCheckpoint({...request, agent_vision_packet: {...packet, stride_decision: historical}});
  assert.deepEqual((authored.agent_vision as Record<string, unknown>).stride_decision, normalized);
});
