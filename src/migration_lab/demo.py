"""Generate an asserted migration story from actual databases, reports and routing changes."""
import json
from pathlib import Path
from uuid import uuid4

from .audit import inspect, reconcile
from .contracts import Refused, canonical
from .coordinator import Coordinator
from .fixtures import command, person, seed
from .snapshot import export_snapshot, import_snapshot
from .system import catch_up, transaction


def refused(call):
    try:
        call()
    except Refused as exc:
        return str(exc)
    raise AssertionError("Expected refusal was bypassed")


def run(workspace, output, count=120):
    workspace, output = Path(workspace) / uuid4().hex, Path(output)
    output.mkdir(parents=True, exist_ok=True)
    coordinator = Coordinator(workspace)
    legacy, modern = coordinator.systems["legacy"], coordinator.systems["modern"]
    seed(coordinator, count)
    committed_during_snapshot = []
    def concurrent_change(entity, last):
        if not committed_during_snapshot:
            update = command("person", "person0000", person(0, score=97))
            update["changes"] += [{"entity": "person", "id": "person0010", "value": None},
                                  {"entity": "person", "id": "person0000a", "value": person(11)}]
            committed_during_snapshot.append(coordinator.write("during-snapshot", update))
    snapshot = export_snapshot(legacy, page_size=7, page_hook=concurrent_change)
    import_snapshot(modern, snapshot, mapping=1)
    snapshot_gap = reconcile(legacy, modern)
    assert not snapshot_gap["passed"] and snapshot_gap["source_cursor"] == snapshot_gap["destination_cursor"] + 1
    catch_up(legacy, modern, mapping=1, batch=3)
    assert reconcile(legacy, modern)["passed"]
    before_schema = int(modern.state()["cursor"])
    coordinator.write("schema-v2", command("person", "person0003", person(3, schema=2), schema=2))
    schema_refusal = refused(lambda: catch_up(legacy, modern, mapping=1))
    assert int(modern.state()["cursor"]) == before_schema
    catch_up(legacy, modern, mapping=2)
    with transaction(modern.path) as c:
        c.execute("UPDATE modern_contact SET subscription_state='no' WHERE contact_key='person0000'")
    coordinator.freeze("legacy")
    count_match_failure = coordinator.assess()
    assert count_match_failure["reconciliation"]["counts"]["source"] == count_match_failure["reconciliation"]["counts"]["destination"]
    blocked = refused(lambda: coordinator.activate(count_match_failure["hash"]))
    with transaction(modern.path) as c:
        c.execute("UPDATE modern_contact SET subscription_state='unknown' WHERE contact_key='person0000'")
    first_proof = coordinator.assess()
    with transaction(modern.path) as c:
        c.execute("UPDATE modern_contact SET engagement_score=96 WHERE contact_key='person0000'")
    stale_refusal = refused(lambda: coordinator.activate(first_proof["hash"]))
    with transaction(modern.path) as c:
        c.execute("UPDATE modern_contact SET engagement_score=97 WHERE contact_key='person0000'")
    ready = coordinator.assess()
    cutover = coordinator.activate(ready["hash"])
    new_command = command("person", "person0003", person(3, schema=2, score=99, subscribed=False), schema=2)
    modern_write = coordinator.write("after-cutover", new_command)
    coordinator.freeze("modern")
    rollback_before_catchup = coordinator.assess()
    assert not rollback_before_catchup["reconciliation"]["passed"]
    catch_up(modern, legacy)
    rollback_ready = coordinator.assess()
    rollback = coordinator.activate(rollback_ready["hash"])
    duplicate_after_rollback = coordinator.write("after-cutover", new_command)
    assert duplicate_after_rollback["receipt"] == modern_write["receipt"]
    final = reconcile(legacy, modern)
    assert final["passed"] and inspect(legacy)["rows"]["person"]["person0003"]["score"] == 99
    # A separate transition shows the boundary of reversibility, without discarding an unsupported value.
    coordinator.freeze("legacy")
    coordinator.activate(coordinator.assess()["hash"])
    coordinator.write("modern-only-feature", command("person", "person0003", person(3, status="archived")))
    frozen = coordinator.freeze("modern")
    irreversible = refused(lambda: catch_up(modern, legacy))
    assert coordinator.route()["phase"] == "frozen" and coordinator.route()["active"] == "modern"
    blocked_rollback = coordinator.assess()
    coordinator.resume_original(frozen["epoch"])
    with transaction(coordinator.path, False) as c:
        history = [{**dict(r), "detail": json.loads(r["detail"])} for r in c.execute("SELECT * FROM history ORDER BY seq")]
    with transaction(modern.path, False) as c:
        attempts = [{**dict(r), "detail": json.loads(r["detail"])} for r in c.execute("SELECT * FROM transfer_attempts ORDER BY id")]
    proof = {"scenario": "synthetic local customer-platform replacement", "initial_person_count": count,
             "snapshot_watermark": snapshot["watermark"], "snapshot_seal": snapshot["seal"],
             "concurrent_write": committed_during_snapshot, "initial_gap": snapshot_gap,
             "schema_upgrade": {"blocked_reason": schema_refusal, "checkpoint_held_at": before_schema, "recovered_with": 2},
             "matching_counts_failure": count_match_failure, "cutover_block_reason": blocked,
             "stale_proof_block_reason": stale_refusal, "accepted_cutover": ready, "cutover_route": cutover,
             "post_cutover_write": modern_write, "rollback_before_catchup": rollback_before_catchup,
             "accepted_rollback": rollback_ready, "rollback_route": rollback, "retry_after_rollback": duplicate_after_rollback,
             "post_rollback_parity": final, "nonrepresentable_rollback": {"reason": irreversible, "proof": blocked_rollback,
                 "resolution": "abort rollback and resume modern; no unsupported data discarded", "route": coordinator.route()},
             "history": history, "transfer_attempts": attempts}
    (output / "migration.json").write_text(json.dumps(proof, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "snapshot.json").write_text(canonical(snapshot) + "\n", encoding="utf-8")
    (output / "report.md").write_text(f"""# Executed migration evidence

All systems and records are synthetic. Native SQLite transactions and real routed writes were executed locally.

| Experiment | Result |
|---|---|
| Snapshot while a source transaction commits | {count} initial people; snapshot at sequence {snapshot['watermark']}; update/delete/insert captured in the suffix |
| Snapshot plus incremental replay | Semantic parity restored, including deletion and replacement |
| Schema v2 reaches mapping v1 | Blocked at cursor {before_schema}; upgrade to mapping v2 resumes without skipping |
| Destination changes unknown subscription to false | Counts match; semantic gate **BLOCKS** cutover |
| Destination changes after passing assessment | Activation **BLOCKS** stale proof |
| Valid cutover | Actual writes route to modern |
| New writes followed by rollback | Rollback initially fails; reverse catch-up preserves score 99 and subscription false |
| Retry of a modern-system command after rollback | Original receipt returned by legacy; no new change |
| Modern-only archived lifecycle | Reverse mapping **BLOCKS**; rollback aborted and modern resumed |

The detailed differences, immutable report identities, transfer attempts and route history are in [migration.json](migration.json).
The actual sealed baseline is in [snapshot.json](snapshot.json). Process-crash evidence is in [crashes.json](crashes.json).
Matching counts, a routing label and a zero-lag cursor are each insufficient on their own.
""", encoding="utf-8")
    return proof
