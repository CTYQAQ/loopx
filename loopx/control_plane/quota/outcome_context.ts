/** Captured outcome presentation extracted from the C1 proposal. No state writes,
 * admission, authority lookup or new requirement owner lives here. */
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {jsonObject, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {canonicalAuthorityJson, canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {BARE_SHA256_PATTERN, ENVELOPED_SHA256_PATTERN} from "../content_digest.ts";
import {OUTCOME_CONTEXT_READ_PLAN_SCHEMA} from "../goals/outcome_requirements.ts";
import {selectedWorkTodoId} from "../work_items/interaction_contract.ts";
import {scopeInteractionOutcomeReadGuidance} from "../work_items/outcome_read_guidance.ts";
import {prepareContextDelivery, OUTCOME_CONTEXT_PROJECTION_SCHEMA} from "./context_delivery.ts";

const object = (value: unknown): JsonObject => ({...(jsonObject(value) ?? {})});
const canonicalValue = canonicalAuthorityJson;
const canonicalHash = (value: unknown): string => `sha256:${canonicalAuthoritySha256(value)}`;

/** Existing acceptance carriers only: this is not a general JSON-path codec.
 * The loaded work context is preferred, then the complete planning/replan facts.
 */
function acceptanceCarriers(payload: JsonObject) {
  // Keep carrier parents attached to the cloned document. The general object()
  // helper intentionally returns copies for projection callers.
  const carrierObject = (value: unknown): JsonObject =>
    typeof value === "object" && value !== null && !Array.isArray(value)
      ? value as JsonObject : {};
  const carriers: {parent: JsonObject; field: string; path: string}[] = [];
  const sources = carrierObject(carrierObject(carrierObject(payload.interaction_contract).agent_channel).work_context).sources;
  if (Array.isArray(sources)) {
    const index = sources.findIndex(value => {
      const source = carrierObject(value), content = carrierObject(source.content);
      return source.source === "goal_acceptance" &&
        (content.status === undefined || content.status === "loaded") &&
        Object.keys(carrierObject(content.goal_acceptance_contract)).length > 0;
    });
    if (index >= 0) carriers.push({parent: carrierObject(carrierObject(sources[index]).content),
      field: "goal_acceptance_contract",
      path: `$.interaction_contract.agent_channel.work_context.sources[${index}].content.goal_acceptance_contract`});
  }
  carriers.push(
    {parent: carrierObject(carrierObject(payload.planning_horizon).core_goal), field: "acceptance_contract",
      path: "$.planning_horizon.core_goal.acceptance_contract"},
    {parent: carrierObject(carrierObject(carrierObject(payload.autonomous_replan_obligation).replan_context).core_goal),
      field: "acceptance_contract", path: "$.autonomous_replan_obligation.replan_context.core_goal.acceptance_contract"},
    {parent: carrierObject(payload.agent_todo_summary), field: "goal_acceptance_contract",
      path: "$.agent_todo_summary.goal_acceptance_contract"},
  );
  return carriers;
}

function inlineAcceptance(payload: JsonObject, reference: unknown): JsonObject {
  // Older captures may name any loaded work-context carrier, not only the
  // preferred first source chosen for new sharing.
  const match = typeof reference === "string"
    ? /^\$\.interaction_contract\.agent_channel\.work_context\.sources\[(0|[1-9]\d*)\]\.content\.goal_acceptance_contract$/.exec(reference) : null;
  if (match) {
    const sources = object(object(object(payload.interaction_contract).agent_channel).work_context).sources;
    const source = object(Array.isArray(sources) ? sources[Number(match[1])] : null);
    const content = object(source.content);
    if (source.source === "goal_acceptance" &&
        (content.status == null || content.status === "loaded") &&
        Object.keys(object(content.goal_acceptance_contract)).length) {
      return object(content.goal_acceptance_contract);
    }
  }
  const carrier = acceptanceCarriers(payload).find(item => item.path === reference);
  const value = object(carrier?.parent[carrier.field]);
  if (!carrier || !Object.keys(value).length) {
    throw new EffectRuntimeRequestError("Goal acceptance reference must resolve to complete inline work context or planning facts");
  }
  return value;
}

/** One registry for ordinary captured reads and staged replan. Only registered
 * acceptance carriers participate; unrelated objects named `outcomes` are not
 * sources. Equal content shares a reference, while differing revisions of the
 * same outcome retain their complete bodies, including unknown fields.
 */
function capturedAcceptanceOutcomes(payload: JsonObject): Map<string, JsonObject> | null {
  const outcomes = new Map<string, JsonObject>();
  const carriers = acceptanceCarriers(payload);
  // Sharing prefers the first source, but a diagnostic index must cover every
  // loaded registered carrier, including different same-id revisions.
  const sources = object(object(object(payload.interaction_contract).agent_channel).work_context).sources;
  if (Array.isArray(sources)) for (const value of sources) {
    const source = object(value), content = object(source.content);
    if (source.source === "goal_acceptance" && (content.status == null || content.status === "loaded")) {
      carriers.push({parent: content, field: "goal_acceptance_contract", path: ""});
    }
  }
  let hasSource = false;
  for (const {parent, field} of carriers) {
    const acceptance = Object.hasOwn(parent, `${field}_ref`)
      ? inlineAcceptance(payload, parent[`${field}_ref`]) : object(parent[field]);
    if (!Object.keys(acceptance).length) continue;
    hasSource = true;
    const requirements = object(acceptance.outcome_requirements);
    if (!Object.hasOwn(acceptance, "outcome_requirements")) continue;
    if (!Array.isArray(requirements.outcomes)) throw new EffectRuntimeRequestError("captured outcome source is unsupported");
    for (const value of requirements.outcomes) {
      const outcome = requireJsonObject(value, "captured acceptance outcome");
      outcomes.set(canonicalHash(outcome), outcome);
    }
  }
  return hasSource ? outcomes : null;
}

/** Expose existing facts, not another acceptance verdict. Missing values stay
 * absent; an Agent's `met` review is not a current independent check.
 */
function capturedOutcomeIndex(outcomes: ReadonlyMap<string, JsonObject>): JsonObject[] {
  return [...outcomes].map(([ref, outcome]) => {
    const states = new Map<string, JsonObject>();
    if (Array.isArray(outcome.requirements)) for (const value of outcome.requirements) {
      const requirement = requireJsonObject(value, "captured outcome requirement");
      const review = jsonObject(requirement.agent_review);
      const state = {
        ...Object.fromEntries(["status", "planning_satisfied"].filter(key => Object.hasOwn(requirement, key))
          .map(key => [key, requirement[key]])),
        ...(Object.hasOwn(requirement, "agent_review") ? {agent_review: review === null
          ? requirement.agent_review : Object.fromEntries(
          ["conclusion", "basis_current", "independently_verified"].filter(key => Object.hasOwn(review, key))
            .map(key => [key, review[key]]))} : {}),
      };
      const key = JSON.stringify(canonicalValue(state));
      const group = states.get(key);
      if (group) (group.requirement_ids as unknown[]).push(requirement.id);
      else states.set(key, {requirement_ids: [requirement.id], ...state});
    }
    return {outcome_ref: ref, id: outcome.id, objective: outcome.objective, revision: outcome.revision,
      ...Object.fromEntries(["owner_agent_id", "work", ...(Object.hasOwn(outcome, "work") ? [] : ["todo_ids"])].filter(key => Object.hasOwn(outcome, key))
        .map(key => [key, outcome[key]])),
      requirement_count: Array.isArray(outcome.requirements) ? outcome.requirements.length : null,
      requirement_states: Array.isArray(outcome.requirements) ? [...states.values()] : null};
  });
}

/** A captured, source-owned reading plan can prioritize exact detail reads.
 * This does not relax the existing complete-capture obligation. The eventual
 * scope cutover must also migrate delivery and Host recovery together.
 */
function capturedOutcomeReadPlan(payload: JsonObject, outcomes: ReadonlyMap<string, JsonObject>): {
  required: ReadonlySet<string>; facts: JsonObject;
} {
  const agent = payload.agent_id ?? object(payload.agent_identity).agent_id;
  const todo = selectedWorkTodoId(payload);
  const all = new Set(outcomes.keys());
  const fallback = (reason: string) => ({required: all,
    facts: {complete: false, fallback_reasons: [reason], scope_applied: false}});
  if (typeof agent !== "string" || typeof todo !== "string") return fallback("selected_work_unbound");
  if (payload.agent_id !== undefined && object(payload.agent_identity).agent_id !== undefined &&
      payload.agent_id !== object(payload.agent_identity).agent_id) return fallback("captured_scope_unavailable");
  if (Object.keys(object(payload.autonomous_replan_obligation)).length ||
      Object.keys(object(payload.replan_action_packet)).length) return fallback("broad_replan_read");
  const sources = object(object(object(payload.interaction_contract).agent_channel).work_context).sources;
  const plans = (Array.isArray(sources) ? sources : []).map(object)
    .filter(source => source.source === "goal_acceptance").map(source => object(source.content))
    .filter(content => content.status === "loaded" && object(content.outcome_context).complete === true);
  const covered = new Set<string>(), required = new Set<string>();
  for (const content of plans) {
    const plan = object(content.outcome_context), acceptance = object(content.goal_acceptance_contract);
    const declaration = object(acceptance.outcome_requirements);
    if (plan.schema_version !== OUTCOME_CONTEXT_READ_PLAN_SCHEMA ||
        plan.goal_id !== payload.goal_id || plan.agent_id !== agent || plan.todo_id !== todo ||
        typeof content.provider_revision !== "string" || plan.provider_revision !== content.provider_revision ||
        plan.outcome_revision !== declaration.revision || !Array.isArray(declaration.outcomes) ||
        !Array.isArray(plan.required_outcome_ids) || plan.required_outcome_ids.some(id => typeof id !== "string")) continue;
    try {
      // Bind the whole source, including unknown evidence and negative facts.
      // Later oracle observations or an upgraded projection widen to full;
      // the reader does not recompute scope from a different observation.
      if (plan.acceptance_digest !== canonicalAuthoritySha256(acceptance)) continue;
    } catch { continue; }
    const ids = new Set(plan.required_outcome_ids);
    const rows = declaration.outcomes.map(object);
    if (rows.some(outcome => outcome.owner_agent_id === agent && !ids.has(outcome.id)) ||
        [...ids].some(id => !rows.some(outcome => outcome.id === id))) continue;
    for (const outcome of rows) {
      const ref = canonicalHash(outcome);
      covered.add(ref);
      if (ids.has(outcome.id)) required.add(ref);
    }
  }
  if ([...all].some(ref => !covered.has(ref))) return fallback("captured_scope_unavailable");
  return {required, facts: {complete: true, fallback_reasons: [], scope_applied: false,
    goal_id: payload.goal_id, agent_id: agent, todo_id: todo}};
}

/** A model presentation, never a partial canonical decision. The full source
 * remains immutable; native baseline/recovery stores this exact presentation,
 * so an unread optional body cannot become a retained requirement later. */
function capturedOutcomeContext(request: JsonObject): JsonObject {
  const payload = requireJsonObject(request.payload, "captured outcome source");
  const outcomes = capturedAcceptanceOutcomes(payload);
  if (payload.ok !== true || Object.hasOwn(payload, "schema_version")
      || Object.hasOwn(payload, "contract_binding") || outcomes === null || !outcomes.size) return payload;
  const plan = capturedOutcomeReadPlan(payload, outcomes);
  if (plan.facts.complete !== true || plan.required.size === outcomes.size) return payload;
  // Unknown outcome-local fields may carry new obligations. Preserve full
  // delivery until the existing projection owner explicitly supports them.
  const known = (value: JsonObject, keys: string[]) => Object.keys(value).every(key => keys.includes(key));
  for (const [ref, row] of outcomes) {
    if (plan.required.has(ref)) continue;
    if (!known(row, ["id", "owner_agent_id", "objective", "revision", "requirements", "todo_ids", "sources", "basis_digest", "work"]) ||
        !Array.isArray(row.requirements) || row.requirements.some(value => {
          const requirement = object(value), review = jsonObject(requirement.agent_review);
          return !known(requirement, ["id", "description", "status", "planning_satisfied", "agent_review"]) ||
            (Object.hasOwn(requirement, "agent_review") && (!review || !known(review,
              ["id", "conclusion", "evidence_refs", "note", "basis_current", "independently_verified"])));
        }) || !Array.isArray(row.sources) || row.sources.some(value => !known(object(value), ["ref", "summary"])) ||
        !Array.isArray(row.work) || row.work.some(value => !known(object(value), ["todo_id", "status"]))) return payload;
  }
  const source = requireJsonObject(request.source_capture, "original capture reference");
  requireNonEmptyString(source.path, "original capture path");
  if (typeof source.sha256 !== "string" || !BARE_SHA256_PATTERN.test(source.sha256))
    throw new EffectRuntimeRequestError("outcome context needs the original file digest");
  const context = structuredClone(payload), carriers = acceptanceCarriers(context);
  const channel = jsonObject(jsonObject(context.interaction_contract)?.agent_channel);
  if (!channel || !scopeInteractionOutcomeReadGuidance(channel)) return payload;
  const sources = jsonObject(jsonObject(jsonObject(context.interaction_contract)?.agent_channel)?.work_context)?.sources;
  if (Array.isArray(sources)) for (const value of sources) {
    const entry = jsonObject(value), content = jsonObject(entry?.content);
    if (entry?.source === "goal_acceptance" && content?.status === "loaded")
      carriers.push({parent: content, field: "goal_acceptance_contract", path: ""});
  }
  for (const {parent, field} of carriers) {
    const acceptance = jsonObject(parent[field]), declaration = jsonObject(acceptance?.outcome_requirements);
    if (Array.isArray(declaration?.outcomes)) declaration.outcomes = declaration.outcomes
      .filter(row => plan.required.has(canonicalHash(row)));
  }
  const presentation: JsonObject = {schema_version: OUTCOME_CONTEXT_PROJECTION_SCHEMA,
    source_capture: {path: source.path, sha256: source.sha256},
    read_scope: {...plan.facts, scope_applied: true, required_outcome_refs: [...plan.required]},
    outcome_index: capturedOutcomeIndex(outcomes).map(row => ({...row,
      required_in_scope: plan.required.has(String(row.outcome_ref))})),
    instruction: "This is the complete required model context, not a canonical decision or execution authority. " +
      "Read this entire presentation before dependent work. The nested context expands every required outcome; " +
      "other independent outcomes retain current index facts and exact references in source_capture. " +
      "Use quota context-read on that original file/hash with --outcome-ref for complete detail before relying on it. " +
      "Goal-wide acceptance or unproven relevance requires the complete original source. " +
      "After context loss recover this exact presentation, not a fresh admission. Original requirements and evidence remain binding.",
    context};
  // A scope/index frame can cost more than small independent bodies. Keep the
  // complete source unless this actual presentation is smaller, including all
  // references and read guidance. This is a byte guard, not a billing estimate.
  return Buffer.byteLength(JSON.stringify(presentation), "utf8") < Buffer.byteLength(JSON.stringify(payload), "utf8")
    ? presentation : payload;
}

export function capturedOutcomeRead(request: JsonObject): JsonObject {
  let payload = requireJsonObject(request.payload, "captured outcome source");
  let scoped = false;
  if (payload.schema_version === OUTCOME_CONTEXT_PROJECTION_SCHEMA) {
    const original = requireJsonObject(request.source_payload, "original outcome source");
    const expected = capturedOutcomeContext({payload: original, source_capture: payload.source_capture});
    if (canonicalHash(expected) !== canonicalHash(payload))
      throw new EffectRuntimeRequestError("scoped context differs from its original source; recover the original complete capture");
    payload = original; scoped = true;
  }
  if (payload.ok !== true) throw new EffectRuntimeRequestError("outcome read requires an original successful capture");
  if (Object.hasOwn(payload, "schema_version") || Object.hasOwn(payload, "contract_binding"))
    throw new EffectRuntimeRequestError("unsupported captured decision version; recover the original full capture");
  const outcomes = capturedAcceptanceOutcomes(payload);
  if (outcomes === null) throw new EffectRuntimeRequestError("capture has no registered acceptance source; recover the original complete capture");
  const common = {ok: true, goal_id: payload.goal_id,
    agent_id: payload.agent_id ?? object(payload.agent_identity).agent_id};
  if (request.outcome_ref === "") {
    const plan = capturedOutcomeReadPlan(payload, outcomes);
    return {...common, read_plan: {...plan.facts, scope_applied: scoped}, outcomes: capturedOutcomeIndex(outcomes)
      .map(row => ({...row, required_in_scope: plan.required.has(String(row.outcome_ref))})),
    instruction: (scoped ? "Read the complete required presentation; other detail must be read before reliance. " :
      "This index and reading plan do not replace full-read obligations; scope_applied is false. ") +
      "Read required complete outcome detail before a dependent decision. Unknown scope retains all outcomes. " +
      "Planning satisfaction, Agent review and independent checks remain distinct; no execution authority or fresh admission is granted."};
  }
  const ref = request.outcome_ref;
  const outcome = typeof ref === "string" && ENVELOPED_SHA256_PATTERN.test(ref) ? outcomes.get(ref) : null;
  if (!outcome) throw new EffectRuntimeRequestError("acceptance read requires an exact captured outcome reference");
  return {...common, outcome_ref: ref, outcome};
}

/** One native projection call, after IO has published the current full recovery.
 * Reuse both owners without another model wire or retention claim. Full codec
 * results already carry the projection; delta/fallback results return it only
 * for the caller's immutable required-context capture, never as hidden input.
 */
export function nativeContextDelivery(request: JsonObject, legacyQuotaFields: readonly string[] = []): JsonObject {
  let projection = requireJsonObject(request.payload, "native context source");
  try { projection = capturedOutcomeContext(request); }
  catch { /* Optional scope failure retains the original complete source. */ }
  try {
    const delivery = prepareContextDelivery({...request, context: projection}, legacyQuotaFields);
    return delivery.mode === "full" ? {delivery} : {projection, delivery};
  } catch {
    // Preserve a successful scope projection when the optional codec fails.
    return {projection};
  }
}

