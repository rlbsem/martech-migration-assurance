# Finished repository handoff

**Selected project:** MarTech Migration Assurance (`martech-migration-assurance`).

**Core question:** Can a customer-platform replacement preserve data meaning and remain reversible after the new system accepts writes?

## Why this direction

All three public portfolio repositories were inspected before implementation. The ranking was migration assurance first, temporal customer activation second, revenue integrity third. Migration adds system-replacement evidence to the existing analytics, authority and agent-quality projects and fits the user-supplied integration/migration themes in the Extreme Networks and Neon One opportunities. The [decision record](decision.md) contains the nine-criterion comparison, weights, inspected commits and rejected alternatives.

## Built and demonstrated

Python and three local SQLite files provide two different native customer schemas plus a small write-routing/barrier ledger. No third-party runtime dependencies, external account or paid infrastructure are required. The implementation includes consistent paginated snapshots, complete incremental change transfer, strict versioned mappings, an independent SQL auditor, data-bound cutover and reverse catch-up before rollback.

The generated story proves:

1. Writes committed during export are absent from the pinned baseline but arrive through the retained change suffix.
2. An unsupported v2 field blocks the entire batch; upgrading the mapping resumes without skipping the checkpoint.
3. Matching counts and log positions cannot hide a changed subscription meaning.
4. Data changed after assessment invalidates the passing cutover proof.
5. New modern-system writes survive rollback, including their original retry receipts.
6. A modern-only lifecycle blocks reverse migration; aborting rollback preserves modern as the active system.

**Final validation: 42 tests passed, zero failed or skipped**, followed by the complete asserted migration story. Validation ran from an installed wheel with package/source parity checks. The suite includes two actual process exits at transaction boundaries, concurrent writer/barrier behavior, malformed input, relationship failures, historical forks and seeded randomized replay-versus-snapshot equivalence.

Read the [generated report](evidence/report.md), [detailed migration proof](evidence/migration.json), [crash results](evidence/crashes.json), and [verification record](evidence/verification.json). The source, native SQL schema, tests, synthetic fixture generator, CI configuration and operating commands are included.

## Corrections and limits

Review strengthened historical replay identity, duplicate-snapshot admission, strict sequence typing and wrong-direction catch-up. These fixes have targeted tests and are documented in [validation](validation.md).

This is an executed local migration protocol against synthetic systems, not a deployed SaaS integration. All supported writers cooperate with the local router. Final reconciliation pauses writes. Snapshot size, complete receipt retention and full in-memory auditing constrain scale. No distributed fencing, zero-downtime SLA, cloud deployment, live Salesforce/Data Cloud connection, security certification or production customer use is claimed. Hosted CI and Linux execution remain unobserved for this build.

## Before publication

Extract the archive, create a Python 3.12 environment, follow the [README setup](../README.md), and run `python scripts/verify.py`. Inspect the failed-safety scenarios as well as the successful migration, review the MIT license, then publish to a new repository yourself. Observe both configured CI jobs before adding a passing-CI claim. No remote repository or fabricated commit history was created.

No implementation work is intentionally deferred for the stated local scope. Vendor adapters, distributed deployment and large-volume experiments are separate future projects, not unfinished features presented as complete.
