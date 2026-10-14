# Architecture decision — migration assurance

## Problem and chosen responsibility

A system replacement can preserve record counts while silently changing customer and subscription meaning. Correctness needs a consistent source snapshot, ordered catch-up, explicit mapping versions, an independent semantic audit, a write barrier, and a reversal path that accounts for post-cutover writes.

This implementation concentrates on **semantic parity, evidence-bound cutover, crash recovery and reverse catch-up**. It does not duplicate the separate concerns of customer identity governance, agent evaluation, or analytics marts.

## Alternatives and controls

| Alternative | Decision |
|---|---|
| Raw copy and matching record counts | Insufficient to prove relationship and subscription semantics |
| Generic event bus | Not the key invariant; the important controls are frontier completeness, mapping validity, and write admission |
| Unconditional rollback | Unsafe after unsupported new-system writes |
| Independent semantic comparison | Required so acceptance does not reuse the mapper's own assertions |

## Selected thesis

**Can we replace a customer platform without losing the meaning of its data, missing concurrent changes, or pretending rollback is just a routing switch?**

The independently derived direction is a platform-migration assurance lab. It replaces one synthetic relational schema with another, pins a consistent snapshot boundary, applies a complete change-log suffix, detects semantic drift despite matching counts, establishes a write barrier, binds cutover to current reconciliation, and brings post-cutover writes back before rollback. Source IDs are supplied; no identity resolution, authority engine, agent, activation campaign or financial ledger is built.

The essential new proof is cross-schema preservation through a system replacement. The gate is a small supporting mechanism, not a second agent-release platform. Failed mappings and irreversible target features must be visible rather than silently coerced.

## Technology decision

Python and SQLite provide real transactions, WAL snapshots, relational constraints and independent durable system files without infrastructure prerequisites. A small local coordinator serializes writes against a durable cutover barrier. A subprocess crash test will exercise commit/checkpoint recovery. Explicit SQL lets an independent auditor compare native representations without calling the migration mapper.

No Kafka, cloud warehouse, dbt, Terraform, SaaS connector, vector store, MCP or LLM is justified. This is not an analytics transformation framework or a model-reasoning problem. No distributed fencing or zero-downtime guarantee is claimed: the bounded local protocol deliberately pauses writes for final reconciliation, and all supported writers must use the coordinator. Real SaaS migration would require vendor-specific snapshot, log retention, identity, rate-limit and write-fencing adapters.
