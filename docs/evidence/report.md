# Executed migration evidence

All systems and records are synthetic. Native SQLite transactions and real routed writes were executed locally.

| Experiment | Result |
|---|---|
| Snapshot while a source transaction commits | 120 initial people; snapshot at sequence 126; update/delete/insert captured in the suffix |
| Snapshot plus incremental replay | Semantic parity restored, including deletion and replacement |
| Schema v2 reaches mapping v1 | Blocked at cursor 127; upgrade to mapping v2 resumes without skipping |
| Destination changes unknown subscription to false | Counts match; semantic gate **BLOCKS** cutover |
| Destination changes after passing assessment | Activation **BLOCKS** stale proof |
| Valid cutover | Actual writes route to modern |
| New writes followed by rollback | Rollback initially fails; reverse catch-up preserves score 99 and subscription false |
| Retry of a modern-system command after rollback | Original receipt returned by legacy; no new change |
| Modern-only archived lifecycle | Reverse mapping **BLOCKS**; rollback aborted and modern resumed |

The detailed differences, immutable report identities, transfer attempts and route history are in [migration.json](migration.json).
The actual sealed baseline is in [snapshot.json](snapshot.json). Process-crash evidence is in [crashes.json](crashes.json).
Matching counts, a routing label and a zero-lag cursor are each insufficient on their own.
