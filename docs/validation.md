# Validation and skeptical review

## What was executed

The implementation ran locally on Windows with Python 3.12 and native SQLite. Exact versions, test totals and source hashes are in [verification.json](evidence/verification.json); [tests.xml](evidence/tests.xml) records individual tests. The final verification also runs the migration story against newly created system files. No test substitutes an unavailable database with a mock.

Two worker subprocesses actually exit at controlled commit boundaries using `os._exit`: before commit and after commit. The parent reopens the database and retries the same suffix. [Crash evidence](evidence/crashes.json) shows the first case retains the old cursor and the second retains the complete new cursor, with parity after recovery. This is process-failure evidence, not a simulated exception or a claimed power-loss experiment.

The source commits an update/delete/insert from a separate connection during paginated export. WAL preserves the original snapshot; suffix transfer restores parity. Concurrency tests hold a native write in flight while a second thread requests freeze, then verify that freeze drains that write and refuses later writes. These are real local locks and transactions, not a distributed coordination benchmark.

## Core negative and metamorphic checks

| Claim | Evidence that could falsify it |
|---|---|
| Snapshot has a consistent boundary | Native source change between export pages; baseline remains unchanged |
| No silent cursor advance | Mapping v1 encounters v2 after an earlier valid event in the same batch; all batch changes roll back |
| No silent historical fork | Replayed event with identical command but changed predecessor is refused using the snapshot receipt's event hash |
| Batch boundaries do not change meaning | Three seeded randomized histories with deletes/recreations, varied batch sizes and replay equal an independently imported final snapshot |
| Relationship changes are atomic | Parent deletion with remaining children fails; complete parent/children deletion migrates together |
| A seal is not sufficient validation | Tampered seal, incomplete receipt index, duplicate snapshot IDs and boolean event sequences are refused |
| Counts/cursors do not prove correctness | Independent native-SQL comparison detects deliberately wrong subscription, channel, lifecycle and score fields |
| Cutover proof is current | Data, implementation and barrier-epoch changes invalidate previously passing proof |
| Rollback preserves post-cutover data | New modern writes transfer back and their original receipts survive retry on legacy |
| Rollback cannot erase incompatible semantics | Modern-only lifecycle blocks reverse mapping without advancing legacy |

## Corrections made during review

The first complete run passed its initial suite. Review then looked for gaps in the claims rather than treating green tests as sufficient.

1. **Replay checked command identity but did not retain historical event identity through snapshot import.** An old event could claim a changed predecessor while preserving the same command. Receipts now retain the sealed event hash, and a targeted test replays a fork from before the imported baseline.
2. **A self-sealed snapshot could list the same object twice.** Import now rejects duplicate object identities before mutation rather than silently accepting the last value.
3. **Python boolean/integer equivalence could admit a malformed sequence.** Event sequence and envelope bounds now use explicit integer checks; a negative test exercises a boolean sequence.
4. **Catch-up in the wrong direction could return without transferring when its destination was ahead.** It now refuses that condition explicitly.
5. **A migration's success could be reduced to counts or a routing switch.** The implementation uses an independent native-SQL oracle and proves reverse transfer of new-system writes. Counterexamples remain in the generated report.

These corrections are implementation changes with executable checks. They do not imply a third-party audit or certification.

## Known limits and execution boundaries

- The systems are synthetic relational platforms, not Salesforce, HubSpot, a CDP, billing or a cloud service. Stable IDs and field meanings are given; identity matching and consent governance remain outside scope.
- All supported writers must use the local coordinator. Native administrator access bypasses it. Migration transfer is a trusted operator activity; concurrently running custom writers against an inactive replica is unsupported.
- The coordinator serializes writes and intentionally pauses them for final reconciliation. No zero-downtime, distributed fencing, production throughput or availability guarantee is claimed.
- The baseline, receipt index and reconciliation are held in memory. Long WAL snapshots can retain storage; complete receipt history is unbounded. Large migrations require partitioned manifests, retained-log planning and independently checked aggregate/detail comparisons.
- The synthetic source provides stronger snapshot, complete change-log and receipt guarantees than many SaaS APIs. Eventual consistency, vendor pagination races, rate limits, undocumented deletes and limited idempotency retention require adapter-specific solutions; no network API integration is exercised here.
- Schema evolution is an explicit v1-to-v2 mapping example. Physical database-schema upgrades of existing deployments, arbitrary transformation languages and automatic compatibility inference are not implemented.
- Subprocess exits prove recovery at tested transaction boundaries, not sudden power loss, disk corruption, host loss, network partition or backup/restore behavior.
- Hashes and append-only triggers are local integrity checks. A filesystem owner can replace databases or code. Independent authorization, signed provenance and external attestation are not implemented.
- The auditor is independently coded from the mapper, but its domain contract is still synthetic and known. It cannot prove that a real enterprise's agreed field definitions are correct.
- GitHub Actions is supplied for Windows and Linux. Hosted CI, Linux execution, cloud deployment and customer use were not observed during this build. No badges or claims imply otherwise.

The [decision record](decision.md) identifies inspected portfolio commits and the user-supplied hiring context. No employment history, revenue result or professional credential is invented or necessary to operate this repository.
