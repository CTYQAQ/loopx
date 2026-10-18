# RFC: Persistent Goal outcome refinement (v0)

- Status: Proposed; implementation and installed adoption are separate checkpoints.
- Owner: Core Goal acceptance and work planning (S3/R4), with S1/S5 callers.
- Language mirror: [中文](goal-outcome-refinement-v0.zh-CN.md).
- Existing contract: [shared Goal alignment](shared-goal-alignment-and-governed-amendment-v0.md#37-current-work-and-goal-requirements).

## Problem and current evidence

An important deliverable can remain a sentence inside a broad recurring Todo.
Later user corrections guide individual turns without preserving the complete
set of requirements. Reordering a presentation can then accidentally remove an
accepted introduction. Completing a task, publishing a candidate or switching
work does not establish that the deliverable meets the accumulated intent.
This is a generalized reproduction; private conversations and live state are
not part of this document or its fixtures.

Current owners solve adjacent problems:

- Todo owns executable work, claims, leases and completion. One broad recurring
  Todo does not identify every independent outcome it supports.
- Agent Vision is a bounded current route and checkpoint; it is not a durable
  catalogue of all promised deliverables and their cumulative requirements.
- Governed Goal amendment preserves root commitments but its executable class
  currently instantiates owner-authorized work templates. Ordinary refinements
  are not arbitrary new templates or permission grants.
- Goal acceptance owns configured validators, work bindings and current evidence.
  Preparing a check is not adopting it, and passing local checks is not whole-Goal
  completion. Direction materials supply source revisions, not work ownership.

## Decision

Add structured supporting outcomes inside the existing canonical Goal authority,
owned beside acceptance. Each outcome has a stable ID, responsible Agent, original
objective, cumulative requirement IDs/descriptions, linked existing Todos and
bounded source references. This is a planning declaration, not a new independent
Goal, scheduler, capability, acceptance verdict or permission envelope.

The original Goal and its root commitments remain untouched. An outcome's original
objective and existing requirement descriptions cannot be overwritten by the
refinement operation. Refinement adds requirements and work links with source
provenance and an operation receipt. A conflicting correction must be reconciled
explicitly; v0 does not pretend append-only refinement implements general
supersession or governed root-intent amendment.

Use a separate child Goal only when independent ownership, budget, lifetime or
execution boundary warrants it. Importance alone does not require another runtime.
An outcome is useful when it has a separately recognizable result and criteria,
especially across multiple turns, tasks or repeated user corrections. Do not
promote every message, housekeeping action or intermediate step.

## Ordinary user messages

The bound Agent distinguishes a direct human request from heartbeat instructions,
quoted documents, tool results and peer messages using the actual host context.
No substring classifier infers user authority or importance. Before continuing a
material new deliverable or correction, the Agent reads existing outcomes,
refines the matching outcome and links work through normal Todo operations.
It retains previously stated requirements and records the latest source reference.
If a reliable host message ID is unavailable, use a stable local opaque evidence
reference; do not manufacture verified sender provenance.

The source label is attribution, never authentication or authorization. The host
continues to own identity and scope; a registered Agent can refine its own
outcomes. Root changes, additional permissions, publishing and executable checks
retain their existing authority. Existing authorization should not cause a new
confirmation merely to record an in-scope refinement. Unclear intent can remain
in conversation until a material consequential choice is resolved.

A CLI/skill bridge makes this available in external hosts after upgrade. It is
not a universal message interception hook: host context delivery and Agent
adherence require separate qualification. App and Lark callers reuse the same
native owner when using the bridge, rather than a second parser or state file.

## State, reads and recovery

Use the promoted canonical authority, its writer fence, provider CAS, append-only
operation journal and existing Goal lifetime checks. No fallback to Markdown on
provider failure. A stable operation ID recovers a lost reply; conflicting retries,
stale revisions, another Goal instance and another Agent's mutation are rejected.
No existing Goal acquires outcomes or changed scheduling merely by upgrading.

The acceptance readback includes declared outcomes even when executable checks
are disabled. It retains every requirement and work link across focus switches
and restarts. Matched current acceptance criteria can provide check evidence;
unconfigured, mismatched, stale and failed checks stay distinguishable. No caller
can submit a passing result through refinement, and all Todos being done cannot
mark an outcome accepted. The existing acceptance owner remains the only verifier.
The outcome objective is subordinate to the original Goal, never its replacement.

The normal admitted-work source list reads this same complete declaration before
work. Missing coverage remains visible for planning; an empty Todo frontier must
not erase an unresolved outcome. Replan and work selection continue through the
existing owners, with unresolved requirement identities carried as evidence.
An outcome record alone does not prove that a model read it, changed its action
or produced a better deliverable. The decisive replay must demonstrate retained
requirements influencing subsequent work and independently check the artifact.

## Product journey and delivery slice

The Agent records the outcome from the user's request without asking the user to
retype JSON. Goal Overview shows it beside the current direction and acceptance,
with original objective, accumulated requirements, responsible Agent and work.
The local owner can add a requirement in place and read the native result. Stale
writes and unavailable authority expose recovery without blind retry. CLI and
frontend share the same typed owner. No new configuration editor or capability ID
is needed: provider is the existing promoted local authority, builtin Core origin.

Submit this design before implementation. The implementation slice includes native
refinement, required reads, preserved unresolved coverage, CLI, existing Goal UI,
external-host skill guidance, negative cases and packaged backend readback.
It does not upgrade the current installation, modify another chat's live work,
merge the PR or launch a paid model experiment.

## Decisive acceptance

Use disposable public-safe File and SQLite Goals, never active operational state.

1. Record an outcome and an initial requirement; link one real existing Todo.
2. Process a second synthetic user refinement under the same outcome; preserve
   the original Goal, outcome objective and earlier requirement byte-for-byte.
3. Restart the caller and switch work. The normal admitted-work reads still show
   both requirements and the original source lineage.
4. Finish the linked Todo with one requirement unmet. Readback must not accept the
   outcome; continuation still exposes the gap. Existing acceptance verification,
   not a model's claim, supplies any later passing evidence.
5. Reject lost requirements, changed text under an old ID, foreign-Agent changes,
   nonexistent work, stale CAS and conflicting retries. Exact replay writes once.
6. Exercise the packaged Goal UI against the real backend: read, refine, reload,
   stale-write recovery, desktop/narrow view. Verify absent-outcome behavior parity.

These checks establish product semantics and recovery. Live-model recognition,
installed host adoption and comparative quality/attention gains remain unproven
until separately measured; test counts and a merged PR are not those outcomes.
