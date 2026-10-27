# Bounded Turn interruption recovery

This implementation addresses an interrupted `turn run-once` on the same
original Turn. The export failure used in tests is synthetic. Its accepted
requirement is to preserve input row order, including equal keys. It is not a
historical incident. No controller/executor split or scheduler is introduced.

## Source and scope

Baseline: `47a525dcc8ca943c7e4ab39834c92e905892839c`, read 2026-10-27 UTC.
Design: [RFC #7416](https://github.com/loopx-project/loopx/pull/7416),
head `9d2d022d14732b3b2dac309013fe9538b1d9ba5d`,
base `99921829f2c5fa2ad35a024c72abb88717003ef3`,
merge `b3ce2cfe77dc99136411279c7bdb2f4db87f37f4`.
Its English and Chinese recovery, continuity, stop and settlement clauses are
requirements. They do not certify an implementation.

#7420 and #7426 are ancestors of this baseline through their respective merges
`d5d72cad4b4b8b6ecbb89682fd9ab161efcfb7b3` and
`0bd508b9445cb60544f5c9f69a07e0a1b72a0d23`.
#7430 (`411707c067e70493c08ce23079af6e2eedb03e96` at inspection) is
unmerged; none of its complete-contract claims are inherited here.
The existing #3412 proposal concerns prepared settlement effects. This change
concerns an earlier Host dispatch intent, before a typed result exists.

| Requirement | Responsible code | Durable record | Check | Gap / boundary |
| --- | --- | --- | --- | --- |
| Same original work and settlement | `turn_driver/driver.py`, `turn_journal.ts` | Saved plan, Turn key, settlement identity | Identity drift and replay checks | A new task/revision is not an original-Turn retry |
| Complete original context | `decision_capture.py`, `quota/context_delivery.ts` | Immutable capture and session-bound delivery | Hash/readback and full/delta tests | A reference alone is not delivery |
| One dispatch after restart | `executor.py`, `turn_journal.ts` | Attempt count before Host call, result checkpoint | Kill/restart and independent downstream log | Added refusal when the outcome is unknown |
| Current authority | `first_party_host_admission.py`, `first_party_host_runtime.ts` | Registry lifetime/witness and current admission | Recreated/revoked Goal checks | Restoring capture cannot grant authority |
| No duplicate settlement | `settlement.py`, `effect_program.ts` | Prepared effect reference, provider receipt, journal | Provider readback and original-identity replay | Existing mechanism; external tools are separate |
| Physical stop | `host_process.ts`, `host_process_transport.py` | Optional process record and OS group observation | Child termination and stop readback | Not a sandbox or external service fence |

## State machine and commit boundaries

State names remain the existing journal vocabulary. Unknown is a recovery
decision (`blocked`, reason `host_outcome_unknown`), not a successful result.

| Trigger / owner | Durable state after atomic checkpoint | Crash observation / next action |
| --- | --- | --- |
| Executor creates journal | `in_progress`, attempt 0, no phases | No dispatch was authorized; continue the original attempt |
| Executor saves dispatch intent | `in_progress`, attempt > 0, no typed result | Delivery/execution is unknown; refuse automatic redispatch |
| Host returns; executor saves result | `in_progress`, `host_execute, typed_result`, saved result | Validate saved result; do not call Host |
| Validator accepts | Append `validation` | Continue original settlement |
| Settlement prepares a provider effect | Existing `effect_attempts` with original effect reference | Query provider; committed means reuse; absent means retry same reference; unknown means stop |
| Provider checkpoint commits | Append `durable_writeback` / `quota_spend` | Resume only remaining phases |
| Scheduler closeout commits | `committed` or `stopped`, original receipt | Return retained receipt without another debit |

The journal owner checks transitions and writes a temporary file then replaces
the journal under mutation locks. Provider commits and the journal checkpoint
are separate transactions. Their gap is resolved by provider readback, not by
pretending the transactions are atomic together. Process-kill tests establish
restart behavior; they do not establish power-loss durability or directory-sync
ordering.

## Identities, invariants and continuation

Work is the Goal and selected Todo/obligation plus its accepted source revision.
The original settlement identity is Goal, Agent, binding and Turn instance;
its `effect_id` is independent of the host attempt counter. A native session
identifies conversation continuity, not permission. Context epoch/profile binds
retention. Goal instance and delegated lease epochs fence their existing owned
boundaries; this patch does not invent a universal execution generation.

Executable assertions must establish:

1. An unresolved dispatch intent never starts a replacement Host, even with
   `--retry-failed-turn`. Unknown/invalid attempt evidence cannot prove absence.
2. A saved typed result resumes validation/settlement without another Host.
   Replaying a terminal receipt does not produce another settlement.
3. Recovery preserves the original requirement and immutable capture; current
   authority is checked separately. Missing/corrupt context uses existing full
   recovery or refuses dependent work.

The lane and journal locks serialize cooperative writers on one local runtime.
They are not an external fencing token. This slice has no replacement executor
while an original dispatch outcome is unknown. It does not claim that an
arbitrary detached process, remote tool, or another newly admitted Turn cannot
write. A generic Host with unqueryable, non-idempotent tools cannot be safely
replayed just because its process has exited.

Under available storage and valid current authority, proven non-dispatch
continues; a durable result finishes its remaining settlement; a terminal Turn
returns its receipt. An unknown Host outcome returns a finite refusal requiring
operator/provider reconciliation. There is no polling loop or retry budget
that eventually guesses absence. This patch adds no generic reconciliation
API for arbitrary Host tools. Do not clear the journal or choose another Turn
identity to bypass that refusal.

## Compatibility and rollback

No new schema or ID is introduced. New initial journals explicitly persist the
existing attempt counter as zero. Historical incomplete journals without that
counter fail closed because non-dispatch cannot be proven. Terminal journals
and journals with a saved typed result retain their recovery route. Explicitly
recorded failed-Host retry policy is unchanged and is not qualified as safe
replay of arbitrary external effects by this change.

Old versions can parse these records but lack the new refusal. Therefore a
downgrade must first reconcile or quarantine unresolved Host intents; do not
silently resume them with the old binary. Captures/receipts are never rewritten
to simulate continuity. At-most-once settlement depends on provider atomic
idempotency/readback and retained history. Exactly-once arbitrary external
effects is not promised.
