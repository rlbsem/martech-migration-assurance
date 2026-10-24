# Executed native PostgreSQL migration acceptance

Source implementation: `99104575c683f2b1e3287937141b182b7c89b094`. Actual local PostgreSQL WAL capture, native apply, semantic consumer processes and synthetic data. Each scenario starts with 120 people and six organizations.

| Scenario | Independently checked outcome | SQL observations |
|---|---|---:|
| [crash_after_commit_before_ack](native/cases/crash_after_commit_before_ack.json.gz) | reconciled | 6 |
| [crash_before_commit](native/cases/crash_before_commit.json.gz) | reconciled | 6 |
| [fence_drains_writer](native/cases/fence_drains_writer.json.gz) | reconciled | 1 |
| [mapping_upgrade](native/cases/mapping_upgrade.json.gz) | reconciled | 3 |
| [missing_slot](native/cases/missing_slot.json.gz) | blocked_as_expected_no_automatic_rebootstrap | 1 |
| [native_disconnect](native/cases/native_disconnect.json.gz) | reconciled | 2 |
| [publication_changed](native/cases/publication_changed.json.gz) | blocked_as_expected_no_automatic_rebootstrap | 1 |
| [restricted_identities](native/cases/restricted_identities.json.gz) | reconciled | 1 |
| [schema_incompatibility](native/cases/schema_incompatibility.json.gz) | blocked_as_expected_no_automatic_rebootstrap | 1 |
| [semantic_cutover](native/cases/semantic_cutover.json.gz) | reconciled | 1 |
| [snapshot_handoff](native/cases/snapshot_handoff.json.gz) | reconciled | 1 |
| [target_rejection](native/cases/target_rejection.json.gz) | reconciled | 3 |
| [unusable_checkpoint](native/cases/unusable_checkpoint.json.gz) | blocked_as_expected_no_automatic_rebootstrap | 1 |

## Source and checkpoint boundary

In the snapshot-handoff scenario, the exported snapshot was `00000075-0000005F-1` at consistent LSN `0/303D0D28` using `pgoutput`. The snapshot retained the old row while concurrent update/delete/insert transactions were subsequently caught up.

In the separate cutover scenario, the valid barrier was `4ead8403-9d8c-44d6-8c8a-74b21e573d47` in source transaction `3884`. Native receiver origin LSN was `0/33CC0C80`; the semantic inbox cursor was `2`. The barrier, durable native floor, complete mapped prefix and independently read business state matched.

## Process interruption and replay

- crash_before_commit: actual killed PID `11192`, exit `1`; checkpoint before `0`, after kill `0`. Restart result `applied`; exact redelivery `duplicate` with unchanged state/receipts. Measured through restart: 0.703 seconds (one local observation).
- crash_after_commit_before_ack: actual killed PID `65696`, exit `1`; checkpoint before `0`, after kill `2`. Restart result `applied`; exact redelivery `duplicate` with unchanged state/receipts. Measured through restart: 0.594 seconds (one local observation).

## Independent semantic comparison

Equal-count corruption preserved row counts and checkpoint position while turning an unknown subscription into false. Independent source/target SQL identified the subscription difference and both stale proof and a freshly registered non-parity proof were refused. A fresh passing proof was required after repairing only that deliberate synthetic corruption.

Every scenario independently confirmed removal of its exact two databases, slot, subscription, replication origin and four temporary roles. The source publication disappears with its verified-absent source database. Cleanup records are embedded in each case; no operator/business database was reset.

[Protocol and source fingerprints](native/protocol.json) · [Derived results](native/results.json) · [Artifact hashes](native/manifest.json) · [Software verification](software/verification.json) · [Baseline](baseline/verification.json) · [Contract and reproduction](../../postgres-cdc.md)

Expected blocked cases are not successful recovery claims. Missing slots, corrupt checkpoints and unsupported schema/publication changes require explicit repair or a fresh snapshot. Physical restore, HA/failover, long outages, automatic DDL migration, native reverse replication and unrestricted exactly-once delivery were not established. The original SQLite rollback lane remains separate and preserved.
