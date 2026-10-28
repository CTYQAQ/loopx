# Scoped outcome context in native quota delivery

This is an opt-in presentation of an immutable quota capture, not a partial
canonical decision, a new acceptance owner or the controller/executor runtime.
It extracts the outcome-reading slice of the [unified-contract proposal](../architecture/rfcs/controller-executor-unified-contract-v0.md)
without adopting its complete-Turn schema or changing ordinary TurnEnvelope.

## Activation and rollback

Use the existing [Codex differential-context setup](../heartbeat-automation-prompt.md#opt-in-native-context-delivery)
with `--context-delivery on`. The native lifecycle hooks must actually be loaded
and trusted; saving a preference is not evidence that a session is using it.
The new scope is available for ordinary captured quota delivery only. An explicit
`--turn-envelope` keeps its existing full-source behavior. Unsupported hosts,
missing native setup and the default `off` path do not acquire a scope plan.
There is no second switch or benchmark-specific requirement owner.

For a new run, select `--context-delivery off` to restore ordinary full delivery.
Do not change the treatment of an active or frozen trial. Before downgrading the
reader, finish pending work or recover its original complete capture. Preserve
both the original source and any required-context artifacts needed by an existing
session; historical captures are never rewritten into the new format.

## What is read

The existing acceptance owner computes the read plan from one complete canonical
work snapshot, with its provider revision and complete acceptance digest. It
keeps all outcomes owned by the current Agent and the closure of the selected
work's registered relations. Shared acceptance bindings, requests and evidence,
including failed or stale observations, expand the required set. Ownership alone
is not proof that another outcome is irrelevant.

Only unrelated peer outcomes outside that declared closure can be deferred.
Their current identity, revision, work links and requirement-state index remain
visible. Their exact complete bodies remain in the original immutable capture.
An Agent must read that detail before relying on it, switching into related work
or assessing broader acceptance. The index is not an acceptance verdict and does
not turn an Agent's review into independently verified success.

Original Goal intent, global acceptance facts, permissions, stops, selected work,
settlement identity and current controls remain in the presentation. The existing
interaction owner changes only its exact fulfilled outcome-read instruction.
Unknown guidance, any outstanding required read, incomplete work context, broad
replanning, unbound work, missing relations, mismatched source revisions or
unsupported outcome fields keep complete source delivery. A new relationship or
changed source is evaluated by a fresh guard, not inferred from an old index.
This conservative declared-relation closure does not establish semantic
independence of arbitrary natural-language requirements.

The scope is used only when the complete derived presentation, including its
index, references and instructions, is smaller in UTF-8 bytes than the original.
Small sources can therefore remain full even with a valid read plan. This is a
transport-byte check, not a token-price or complete-request cost estimate.

## Detail and recovery

The model sees `outcome_context_projection_v0`: the required context, current
outcome index and an exact `source_capture` path/SHA-256. With that original
reference, the existing reader can return the index or one complete outcome:

```sh
loopx --format json quota context-read \
  --capture-file "$CAPTURE" --capture-sha256 "$SHA256" --outcome-ref

loopx --format json quota context-read \
  --capture-file "$CAPTURE" --capture-sha256 "$SHA256" --outcome-ref "$OUTCOME_REF"
```

Follow the returned `next_command` for every page. An outcome reference identifies
its complete captured content, not a live lookup by outcome id. Selected pages
also pin a `selection_sha256`, because a changed rendering cannot safely reuse
byte cursors merely because the source file has the same hash. Reading an index
or detail does not count as reading the complete context and cannot establish a
full-context baseline. Invalid selections retain a complete-capture fallback.

The full original source is published first. The scoped presentation has its own
immutable `required-context.json`, recovery reference and full/delta baseline;
optional unread bodies are never silently marked as retained. If this second
publication fails, the caller returns the complete original source. Compaction,
lost continuity, damaged or truncated pages require recovery of the same
captured presentation before dependent work. Recovery never runs a replacement
guard, renews authority or spends quota. Current cancellation and admission rules
continue to be checked by their existing owners.

## Qualification boundary

Focused tests exercise the TypeScript owner, actual File authority, public quota
and reader CLI, byte-bound details, synthetic native lifecycle events, exact
delta reconstruction and publication failure. Synthetic native events are not
an installed-Codex or model-retention qualification. Supported SQLite,
PostgreSQL where applicable, full repository checks and native-host qualification
remain separate gates. The PR records which checks actually ran and any failures.

A smaller capture does not establish lower complete-request tokens, cached-token
costs, billable savings or better accepted outcomes. Measure cold delivery,
subsequent wakes, detail reads, compaction and recovery together on a fixed source
revision. This slice does not narrow independent outcomes owned by the same Agent
and therefore does not claim to solve all single-Agent benchmark overhead. It
also does not complete C1, dual controller/executor sessions or the full C2
cross-entrypoint migration.
