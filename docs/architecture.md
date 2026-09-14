# Architecture and preservation contract

## The invariant

At activation, the current and proposed systems must describe the same business records and request history at the same source-change boundary. The router must have drained supported writers before that boundary is assessed. A reverse migration has the same obligations; merely changing a route is insufficient.

The source and destination are intentionally different native relational schemas. Stable IDs are supplied by the synthetic fixtures. The migration does not infer identity, establish authority, compute audiences or govern campaign permission.

| Meaning | Legacy representation | Modern representation | Preservation rule |
|---|---|---|---|
| Organization identity and name | `legacy_org.org_id`, `display_name` | `modern_company.company_key`, `legal_name` | Exact supplied identity/name |
| Person-to-organization relationship | `org_id` foreign key | `company_key` foreign key | No orphan or guessed relationship |
| Lifecycle | `Lead`, `Customer`, `Hold` | `P`, `C`, `H` | Explicit bijection; no fallback default |
| Modern-only lifecycle | Unrepresentable | `A` (archived) | Reverse transfer refuses |
| Score | Integer 0–100 | Integer 0–100 | Exact integer; booleans and clamping rejected |
| Channels | Semicolon-delimited set | JSON array | Same permitted set; canonical ordering; duplicate entries rejected at admission |
| Subscription observation | SQL NULL / 0 / 1 | `unknown` / `no` / `yes` | Unknown remains distinct from false; no legal interpretation |
| Locale | Nullable `locale`, schema v2 | Nullable `preferred_locale`, schema v2 | v1 mapper refuses v2 payload; v2 explicitly preserves it |
| Name | Unicode text | Unicode text | Preserve entire string; no speculative name splitting |

## Snapshot plus change suffix

Each native write transaction updates records, inserts an immutable command receipt, appends one sealed change event and advances the local cursor/hash chain. A command may contain several related record changes; foreign keys are deferred until the transaction commits. Failed writes cannot leave an advanced cursor or half a relationship update.

`export_snapshot` begins one WAL read transaction and reads metadata first, pinning boundary **W**. Keyset pagination then exports native rows from that same snapshot. The request index and chain anchor also come from the pinned transaction. A concurrent source write commits through another connection, but cannot appear in only some export pages. The complete artifact is sealed before import. This implementation gathers the bounded export in memory; it does not claim streamed large-scale export.

Import validates the seal, request-index coverage and unique object IDs, then loads all native destination rows, receipts and W atomically. It requires an empty destination. An identical retry is allowed only at that same baseline without drift. Reimport cannot overwrite a destination that has progressed.

Incremental transfer reads the source suffix after the destination cursor, up to a fixed catch-up bound. Every new event must have the next sequence and the preceding chain hash. The event seal binds its command, identity and predecessor. Applied data, receipts and cursor commit in **one destination transaction per batch**. Unsupported schemas roll back the entire batch and record a blocked transfer attempt; an operator upgrades the mapping and retries without skipping a sequence.

The snapshot's request index retains event hashes as well as command hashes. A duplicate whose command body matches but whose claimed predecessor differs is therefore refused even when its original event predates the destination's snapshot. At-least-once transfer under this synthetic log contract produces idempotent materialization; this is not universal exactly-once delivery.

Deletes are first-class changes. A deleted person can later be recreated under its supplied ID, and partitioned replay must equal a fresh snapshot. Missing retained events are an explicit failure, never interpreted as “caught up.”

## Independent reconciliation

`audit.py` queries each native schema directly with independently written SQL projections. It never calls the transfer encoder or snapshot decoder. This matters: comparing a mapper's output with the same mapper's expected output can reproduce the same mistake twice.

The auditor compares identity sets, every mapped field, relationships, request receipts, source lineage, log position and chain anchor. A representative email-customer read is also compared; it is a query contract, not an activation engine. Reports retain exact per-field differences, counts and complete-state hashes. The demo deliberately coerces unknown subscription to false: counts and log anchors still match, but the semantic check blocks activation.

The oracle is explicit synthetic domain logic, not an independent third-party authority. Shared misunderstandings of the domain contract remain possible and require real business-owner review in a real migration.

## Cutover barrier and failure behavior

`Coordinator.write` holds a write transaction on the routing database while the active native system commits. `freeze` takes that same lock, so it waits for supported in-flight writes to finish. It then durably records a frozen phase, destination and new epoch. Later writers are refused; stale caller epochs are also refused. This is local serialization across cooperative writers, not distributed fencing enforced by arbitrary vendors.

While frozen, the operator finishes incremental transfer and runs assessment. A proof binds the active/destination identities, barrier epoch, implementation hash and reconciliation. Activation reads the registered proof and recomputes current evidence under the routing lock. Failed parity, changed data, changed code, an aborted/refrozen epoch or an invented proof prevents activation.

The route update and transition history commit together. A process stopping while frozen leaves writes frozen after restart. The explicit escape is to abort the attempted switch and resume the existing authoritative system under a fresh epoch. The protocol does not silently fail over on ambiguity.

Native system commits and routing metadata are not one distributed transaction. The supported write operation does not change routing metadata: if a caller loses its result after the native commit, the native receipt handles its retry. That receipt is copied during migration, so retries after cutover or rollback cannot become a new command merely because a different system now receives them. Changed payloads under an existing ID conflict.

## Rollback is a data operation

After cutover, modern-system commands continue the same logical sequence and receipt history. Before rollback, freeze modern, transfer its suffix into legacy, reconcile the current states, then activate legacy. The demo proves that a new score and subscription value survive, and that retrying the modern command on legacy returns its original receipt.

If modern has accepted an `archived` lifecycle, the legacy mapper cannot represent it. Reverse transfer refuses without advancing its checkpoint. The demonstration aborts rollback and resumes modern. Resolving this in a real migration would require a reviewed compatibility change or a new migration plan; silently mapping archived to another state would violate the preservation contract.

## Deployment assumptions

There is one local coordinator and two independent SQLite system files on a filesystem supporting SQLite locking. Supported migration operations are performed by a trusted operator; only the coordinator handles business writes. Direct native writes, administrative SQL or concurrent custom migration writers bypass this boundary and are not permitted operating modes. Hashes and append-only triggers detect ordinary misuse; a filesystem owner can replace files or disable triggers.

No broker, agent framework, warehouse, cloud control plane or vector database is needed. A real SaaS adapter must establish a stable baseline cursor, complete retained transaction suffix, deletion semantics, durable receipt identity and a credible write barrier before this protocol's guarantees can be transferred to it.
