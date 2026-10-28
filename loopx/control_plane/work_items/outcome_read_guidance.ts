/** Reading instructions shared by the interaction owner and exact scoped presentation. */
import type {JsonObject} from "../effect_program.ts";
import {jsonObject} from "../runtime_decode.ts";

export const OBJECTIVE_READ_GUIDANCE = "Use the canonical goal_objective before the older source text. Only if your adoption is missing/stale, review your own work, reconcile Todo/Vision changes, then use adopt-objective with your Agent id, exact revision/digest, own objective_work_digest and review_summary. New execution is held until your adoption. Other peers adopt independently; existing Turn settlement/release remains available. Preserve root acceptance, permissions and stops. Adopt once per objective revision, never per Turn or work/peer update. Reuse complete inline context without extra reads/writes.";
export function outcomeReadGuidance(scoped = false): string {
  return (scoped
    ? "Read the full global acceptance basis and every outcome required by this capture's read_scope, including cumulative requirements, work links and sources. Other outcomes retain current index facts and exact original-capture references; read their complete bodies before reliance. Unknown relevance or Goal-wide acceptance requires the complete original source. "
    : "Read the full acceptance basis and any supporting outcomes with cumulative requirements, work links and sources. ") +
    "Preserve the original Goal and earlier requirements when applying direct user refinements. Before switching work or declaring completion, reconcile unmet outcome requirements with subsequent work; batch changed requirements/evidence reviews through refresh-state --goal-outcome-update-json or goal-acceptance refine. Reuse complete inline context; unchanged work needs no extra inspect/write. Agent review is distinct from independent checks. Scoped acceptance never replaces Goal intent. Changed requirements need a fresh guard.";
}

/** The caller has already verified the captured reading plan. Adapt only this
 * owner's exact fulfilled acceptance instruction in the derived presentation;
 * unknown guidance or an outstanding acceptance read keeps full delivery.
 * This changes no canonical source, adoption rule or execution authority. */
export function scopeInteractionOutcomeReadGuidance(channel: JsonObject): boolean {
  const pending = Array.isArray(channel.required_reads) ? channel.required_reads : [];
  if (pending.length) return false;
  const context = jsonObject(channel.work_context);
  const sources = Array.isArray(context?.sources) ? context.sources : [];
  const changes: {source: JsonObject; reason: string}[] = [];
  for (const value of sources) {
    const source = jsonObject(value), content = jsonObject(source?.content);
    if (source?.source !== "goal_acceptance") continue;
    const acceptance = jsonObject(content?.goal_acceptance_contract);
    if (content?.status !== "loaded" || content.read_complete === false || !acceptance?.outcome_requirements) return false;
    const prefix = acceptance.goal_objective ? `${OBJECTIVE_READ_GUIDANCE} ` : "";
    if (source.reason !== prefix + outcomeReadGuidance()) return false;
    changes.push({source, reason: prefix + outcomeReadGuidance(true)});
  }
  if (context?.complete !== true || !changes.length) return false;
  for (const {source, reason} of changes) source.reason = reason;
  return true;
}
