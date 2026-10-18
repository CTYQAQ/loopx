/** Supporting outcomes retain planning requirements; existing acceptance owns
 * verification. Refinement cannot rewrite root intent, grant work or attest success. */
import type {JsonObject} from "../effect_program.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityObject, canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {indexCoordinationProjectionTodos} from "../coordination/coordination_projection.ts";
import {readGoalAcceptanceLifecycle, sameExactGoalRef, parseWireExactGoalRef, type WireExactGoalRef} from "./acceptance_lifecycle.ts";

import {goalAcceptanceTodoDigest} from "./acceptance_work_basis.ts";

export const GOAL_OUTCOMES_SCHEMA = "loopx_goal_outcomes_v0";
interface Requirement extends JsonObject {id: string; description: string}
interface ReviewItem extends JsonObject {id: string; conclusion: "met" | "unmet" | "uncertain"; evidence_refs: string[]; note: string}
interface OutcomeReview extends JsonObject {basis_digest: string; items: ReviewItem[]}
interface Outcome extends JsonObject {
  id: string; owner_agent_id: string; objective: string; revision: number;
  requirements: Requirement[]; todo_ids: string[];
  sources: {ref: string; summary: string}[];
  review?: OutcomeReview;
}
interface OutcomeState extends JsonObject {
  schema_version: typeof GOAL_OUTCOMES_SCHEMA; goal_id: string;
  goal_ref: WireExactGoalRef | null; revision: number; outcomes: Outcome[];
}
function requireValue(condition: unknown, reason: string): asserts condition {
  if (!condition) throw new AuthorityStoreProtocolError(reason);
}
function text(value: unknown, label: string, max = 512): string {
  requireValue(typeof value === "string" && value === value.trim() && !!value && value.length <= max && !value.includes("\0"), `${label} must be bounded nonempty text`);
  return value;
}
function id(value: unknown, label: string): string {
  const result = text(value, label, 128);
  requireValue(/^[A-Za-z0-9][A-Za-z0-9_.:@-]*$/.test(result), `${label} must be an opaque identifier`);
  return result;
}
function keys(row: JsonObject, required: string[], optional: string[] = []): void {
  requireValue(required.every(key => Object.hasOwn(row, key)) && Object.keys(row).every(key => [...required, ...optional].includes(key)), "outcome fields are missing or unsupported");
}
function list(value: unknown, label: string, max: number): unknown[] {
  requireValue(Array.isArray(value) && value.length <= max, `${label} exceeds its supported capacity`);
  return value;
}
function unique(values: string[], label: string): void {requireValue(new Set(values).size === values.length, `${label} contains duplicate ids`);}
function requirements(value: unknown): Requirement[] {
  const result = list(value, "requirements", 32).map(value => {
    const row = canonicalAuthorityObject(value, "requirement"); keys(row, ["id", "description"]);
    return {id: id(row.id, "requirement id"), description: text(row.description, "requirement description")};
  });
  unique(result.map(row => row.id), "requirements"); return result;
}
function revision(value: unknown): number {
  requireValue(Number.isSafeInteger(value) && Number(value) > 0, "outcome revision is invalid"); return Number(value);
}
function source(value: unknown): {ref: string; summary: string} {
  const row = canonicalAuthorityObject(value, "outcome source"); keys(row, ["ref", "summary"]);
  return {ref: id(row.ref, "source ref"), summary: text(row.summary, "source summary")};
}
function reviewItems(value: unknown): ReviewItem[] {
  const items = list(value, "review items", 32).map(value => {
    const row = canonicalAuthorityObject(value, "Agent review"); keys(row, ["id", "conclusion", "evidence_refs", "note"]);
    requireValue(["met", "unmet", "uncertain"].includes(String(row.conclusion)), "review conclusion must be met, unmet or uncertain");
    const evidence = list(row.evidence_refs, "review evidence", 8).map(ref => text(ref, "evidence reference", 1024));
    requireValue(row.conclusion !== "met" || evidence.length > 0, "a met self-assessment requires evidence references");
    return {id: id(row.id, "review requirement"), conclusion: row.conclusion as ReviewItem["conclusion"], evidence_refs: evidence, note: text(row.note, "review reasoning")};
  });
  unique(items.map(row => row.id), "review items"); return items;
}
function reviewBasis(outcome: Outcome, work: ReadonlyMap<string, JsonObject>): string {
  return canonicalAuthoritySha256({objective: outcome.objective, requirements: outcome.requirements,
    work: outcome.todo_ids.map(key => ({todo_id: key, digest: work.has(key) ? goalAcceptanceTodoDigest(work.get(key)!) : null}))});
}
function readState(head: JsonObject, goalId: string): OutcomeState | null {
  if (head.goal_outcomes === undefined) return null;
  const row = canonicalAuthorityObject(head.goal_outcomes, "Goal outcomes");
  keys(row, ["schema_version", "goal_id", "goal_ref", "revision", "outcomes"]);
  requireValue(row.schema_version === GOAL_OUTCOMES_SCHEMA && row.goal_id === goalId, "outcome source Goal mismatch");
  const goalRef = row.goal_ref === null ? null : parseWireExactGoalRef(row.goal_ref, "outcome Goal ref");
  requireValue(goalRef === null || goalRef.goal_id === goalId, "outcome lifetime Goal mismatch");
  const outcomes = list(row.outcomes, "outcomes", 24).map(value => {
    const o = canonicalAuthorityObject(value, "outcome");
    keys(o, ["id", "owner_agent_id", "objective", "revision", "requirements", "todo_ids", "sources"], ["review"]);
    let review: OutcomeReview | undefined;
    if (o.review !== undefined) {
      const row = canonicalAuthorityObject(o.review, "outcome review"); keys(row, ["basis_digest", "items"]);
      requireValue(typeof row.basis_digest === "string" && /^[a-f0-9]{64}$/.test(row.basis_digest), "review basis digest is invalid");
      review = {basis_digest: row.basis_digest, items: reviewItems(row.items)};
    }
    const todoIds = list(o.todo_ids, "outcome work", 64).map(v => id(v, "Todo id")); unique(todoIds, "outcome work");
    const sources = list(o.sources, "outcome sources", 64).map(source); unique(sources.map(s => s.ref), "sources");
    const reqs = requirements(o.requirements); requireValue(reqs.length && sources.length, "outcome requirements and source are required");
    return {id: id(o.id, "outcome id"), owner_agent_id: id(o.owner_agent_id, "outcome Agent"), objective: text(o.objective, "outcome objective", 1024),
      revision: revision(o.revision), requirements: reqs, todo_ids: todoIds, sources, ...(review ? {review} : {})};
  });
  unique(outcomes.map(o => o.id), "outcomes");
  return {schema_version: GOAL_OUTCOMES_SCHEMA, goal_id: goalId, goal_ref: goalRef, revision: revision(row.revision), outcomes};
}

export function refineGoalOutcome(head: JsonObject, goalId: string, goalRef: WireExactGoalRef | null,
  actor: string | null, value: unknown): OutcomeState {
  const previous = readState(head, goalId);
  const lifecycle = readGoalAcceptanceLifecycle(head, goalId);
  requireValue(!lifecycle || lifecycle.state === "active" && goalRef && sameExactGoalRef(lifecycle.goal_ref, goalRef), "outcome Goal lifetime is unavailable or changed");
  requireValue(!previous || canonicalAuthoritySha256(previous.goal_ref) === canonicalAuthoritySha256(goalRef), "outcome Goal lifetime changed; reconcile retained declarations");
  const patch = canonicalAuthorityObject(value, "outcome refinement");
  keys(patch, ["outcome_id", "owner_agent_id", "objective", "requirements", "todo_ids", "source"], ["review"]);
  const outcomeId = id(patch.outcome_id, "outcome id"), owner = id(patch.owner_agent_id, "outcome Agent");
  requireValue(actor === null || actor === owner, "Agents can refine only their own outcomes");
  const old = previous?.outcomes.find(o => o.id === outcomeId);
  const objective = text(patch.objective, "outcome objective", 1024);
  requireValue(!old || old.owner_agent_id === owner && old.objective === objective, "refinement cannot replace the original outcome objective or owner");
  const todos = indexCoordinationProjectionTodos(head, goalId).todos;
  const links = list(patch.todo_ids, "outcome work", 64).map(v => id(v, "Todo id")); unique(links, "outcome work");
  for (const key of links) {
    const todo = todos.get(key);
    requireValue(todo && todo.role === "agent" && (todo.task_class == null || todo.task_class === "advancement_task"), "outcomes link existing Agent advancement Todos only");
    requireValue(actor === null || !todo.claimed_by || todo.claimed_by === actor, "an Agent cannot attach another Agent's claimed work");
  }
  const reqs = new Map((old?.requirements ?? []).map(row => [row.id, row]));
  for (const row of requirements(patch.requirements)) {
    requireValue(!reqs.has(row.id) || reqs.get(row.id)!.description === row.description, "an existing requirement cannot be overwritten; reconcile conflicting intent explicitly");
    reqs.set(row.id, row);
  }
  const origin = source(patch.source), sources = new Map((old?.sources ?? []).map(row => [row.ref, row]));
  requireValue(!sources.has(origin.ref) || sources.get(origin.ref)!.summary === origin.summary, "source reference already identifies another refinement");
  sources.set(origin.ref, origin);
  const outcome: Outcome = {id: outcomeId, owner_agent_id: owner, objective, revision: (old?.revision ?? 0) + 1,
    requirements: [...reqs.values()], todo_ids: [...new Set([...(old?.todo_ids ?? []), ...links])], sources: [...sources.values()],
    ...(old?.review ? {review: old.review} : {})};
  if (patch.review !== undefined) {
    const items = reviewItems(patch.review);
    requireValue(items.length === outcome.requirements.length && items.every(row => reqs.has(row.id)), "review must address every retained requirement exactly once");
    outcome.review = {basis_digest: reviewBasis(outcome, todos), items};
  }
  const state: OutcomeState = {schema_version: GOAL_OUTCOMES_SCHEMA, goal_id: goalId, goal_ref: goalRef, revision: (previous?.revision ?? 0) + 1,
    outcomes: [...(previous?.outcomes.filter(o => o.id !== outcomeId) ?? []), outcome]};
  requireValue(Buffer.byteLength(JSON.stringify(state), "utf8") <= 131072, "outcome declaration capacity exceeded; retain source detail in its owning material");
  return readState({...head, goal_outcomes: state}, goalId)!;
}

/** Only the existing acceptance projection supplies check results. A matched
 * description alone is not evidence; work coverage and freshness also matter. */
export function projectGoalOutcomes(head: JsonObject, goalId: string, acceptance: JsonObject): JsonObject | null {
  const state = readState(head, goalId); if (!state) return null;
  const lifecycle = readGoalAcceptanceLifecycle(head, goalId);
  const current = lifecycle === null ? state.goal_ref === null
    : lifecycle.state === "active" && state.goal_ref !== null && sameExactGoalRef(lifecycle.goal_ref, state.goal_ref);
  const work = indexCoordinationProjectionTodos(head, goalId).todos;
  return observeOutcomeRequirements({schema_version: GOAL_OUTCOMES_SCHEMA, revision: state.revision, current_lifetime: current,
    outcomes: state.outcomes.map(o => ({...o, review_basis_current: !!o.review && o.review.basis_digest === reviewBasis(o, work), work: o.todo_ids.map(key => ({todo_id: key, status: work.get(key)?.status ?? "missing"}))})),
    execution_authority_granted: false, goal_completion_assessed: false}, acceptance);
}

/** Re-use after the host observes oracle freshness; raw stored evidence cannot
 * become a current passing result without those observations. No state writes. */
export function observeOutcomeRequirements(declaration: JsonObject, acceptance: JsonObject): JsonObject {
  const current = declaration.current_lifetime === true;
  const criteria = (acceptance.criteria ?? []) as JsonObject[], tasks = (acceptance.tasks ?? []) as JsonObject[];
  const outcomes = (declaration.outcomes as (Outcome & {review_basis_current: boolean; work: {todo_id: string; status: string}[]})[]).map(o => ({...o,
    requirements: o.requirements.map(r => {
      const criterion = criteria.find(c => c.id === r.id && c.description === r.description);
      const covered = o.todo_ids.length > 0 && o.todo_ids.every(key => tasks.some(t => t.todo_id === key && t.state === "ready" && (t.criterion_ids as string[]).includes(r.id)));
      const oracle = criterion?.oracle as JsonObject | undefined;
      const checked = current && criterion && covered && acceptance.status === "accepted" && (!oracle || oracle.state === "current");
      const review = o.review?.items.find(item => item.id === r.id) ?? r.agent_review as (ReviewItem & {basis_current?: boolean}) | undefined;
      const reviewCurrent = current && (o.review_basis_current ?? review?.basis_current === true);
      const configured = criteria.some(c => c.id === r.id);
      return {...r, ...(review ? {agent_review: {...review, basis_current: reviewCurrent, independently_verified: false}} : {}),
        planning_satisfied: !!checked || (!configured && reviewCurrent && review?.conclusion === "met"),
        status: checked ? "accepted" : current && criterion && covered && acceptance.status !== "accepted" ? acceptance.status : "unverified"};
    }),
  }));
  const gaps = current ? outcomes.flatMap(outcome => {
    const unmet = outcome.requirements.filter(r => !r.planning_satisfied).map(r => r.id);
    if (!unmet.length || outcome.work.some(w => !["done", "missing"].includes(String(w.status)))) return [];
    return [{kind: "goal_outcome_uncovered", source: "goal_acceptance_contract", agent_id: outcome.owner_agent_id,
      outcome_id: outcome.id, requirement_ids: unmet, frontier_revision: canonicalAuthoritySha256(outcome),
      replan_trigger_summary: `Outcome ${outcome.id} still has unmet requirements without active linked work.`,
      acceptance_summary: outcome.objective, vision_todo_ids: [],
      resolution_hint: "Read goal-acceptance inspect; preserve its original objective and cumulative requirements. " +
        "Link concrete existing or successor work with goal-acceptance refine, or record the actual acceptance/owner blocker. " +
        "Default review records evidence and unmet requirements; configured checks remain authoritative. Todo completion alone cannot settle requirements."}];
  }) : [];
  return {...declaration, outcomes: outcomes.map(({review: _review, review_basis_current: _basis, ...outcome}) => outcome), acceptance_gaps: gaps};
}
