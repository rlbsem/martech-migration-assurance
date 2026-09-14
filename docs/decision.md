# Why a fourth repository

## Inspection before selection

The public repositories were fetched and inspected on 2026-09-13. The review included READMEs, architecture, implementation, tests, generated evidence and CI definitions at these commits:

- [Telemetry](https://github.com/rlbsem/game-telemetry-analytics-engineering/tree/205491319fd25c0ad107e25e0e25d2003cb2a5d6): incremental analytical truth, late-event repair, contracts and replay; it already covers much of a new warehouse pipeline's hiring signal.
- [Control plane](https://github.com/rlbsem/enterprise-martech-ai-control-plane/tree/3b7cef9cf87db0a681d4e2fb284b5736f84e6bc5): authority, identity, consent, durable effects, uncertain outcomes and process recovery. Reimplementing these beneath an activation label would overlap substantially.
- [Agent runtime](https://github.com/rlbsem/enterprise-agent-runtime-evaluation/tree/bcd3b9379b48b110f886fd65f87df79adab00375): live model behavior, adversarial evaluation and configuration promotion. Another agent or release gate is not the missing capability. A CI file is not treated as evidence that its remote jobs passed.

The role priorities and career context are user-supplied; this decision does not claim independently verified vacancies, hiring outcomes or internal company architecture.

## Candidate ranking

Scores are architectural judgment on a 1–5 scale, not empirical hiring probabilities. For overlap, 5 means least duplication. Current-role usefulness receives weight four (twelve total weight units); the other criteria have weight one. This deliberately makes the current search more influential than abstract novelty.

| Criterion | C: migration assurance | A: temporal customer activation | B: revenue integrity |
|---|---:|---:|---:|
| Portfolio complementarity | 5 | 3 | 5 |
| Current-role usefulness (weight 4) | 5 | 5 | 3 |
| Senior technical hiring signal | 5 | 4 | 5 |
| Engineering depth | 4 | 5 | 5 |
| Executable evidence potential | 5 | 5 | 4 |
| Low overlap risk | 4 | 2 | 5 |
| Interview defensibility | 4 | 5 | 4 |
| Long-term portfolio value | 5 | 5 | 5 |
| New capability not already proved | 5 | 3 | 5 |
| Weighted total / 60 | **57** | **52** | **50** |

Final ranking: **C first, A second, B third**. Without the extra current-role weight, revenue integrity would outrank temporal activation. With it, activation ranks second. Extreme Networks' integration/migration theme and Neon One's synchronization/data-integrity theme matter more here than optimizing another repository for BMO's already advanced process. Migration's local cooperative-write boundary limits its depth and interview claims, reflected in its scores; it still provides the most useful distinct proof. ShyftLabs benefits from clear incident and dependency evidence, but another code artifact is unlikely to be its deciding factor.

## Selected thesis

**Can we replace a customer platform without losing the meaning of its data, missing concurrent changes, or pretending rollback is just a routing switch?**

The independently derived direction is a platform-migration assurance lab. It replaces one synthetic relational schema with another, pins a consistent snapshot boundary, applies a complete change-log suffix, detects semantic drift despite matching counts, establishes a write barrier, binds cutover to current reconciliation, and brings post-cutover writes back before rollback. Source IDs are supplied; no identity resolution, authority engine, agent, activation campaign or financial ledger is built.

The essential new proof is cross-schema preservation through a system replacement. The gate is a small supporting mechanism, not a second agent-release platform. Failed mappings and irreversible target features must be visible rather than silently coerced.

## Technology decision

Python and SQLite provide real transactions, WAL snapshots, relational constraints and independent durable system files without infrastructure prerequisites. A small local coordinator serializes writes against a durable cutover barrier. A subprocess crash test will exercise commit/checkpoint recovery. Explicit SQL lets an independent auditor compare native representations without calling the migration mapper.

No Kafka, cloud warehouse, dbt, Terraform, SaaS connector, vector store, MCP or LLM is justified. This is not an analytics transformation framework or a model-reasoning problem. No distributed fencing or zero-downtime guarantee is claimed: the bounded local protocol deliberately pauses writes for final reconciliation, and all supported writers must use the coordinator. Real SaaS migration would require vendor-specific snapshot, log retention, identity, rate-limit and write-fencing adapters.
