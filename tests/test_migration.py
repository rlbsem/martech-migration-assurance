import copy
import json
import random
import sqlite3

import pytest

from migration_lab.audit import inspect, reconcile
from migration_lab.contracts import Refused, digest
from migration_lab.fixtures import command, person
from migration_lab.snapshot import export_snapshot, import_snapshot
from migration_lab.system import System, catch_up, transaction


def test_snapshot_is_pinned_across_keyset_pages_while_source_changes(lab):
    left, right = lab.systems.values()
    before = inspect(left)
    changed = []
    def page_hook(entity, last):
        if not changed:
            update = command("person", "person0000", None)
            update["changes"].append({"entity": "person", "id": "person0000a", "value": person(0, score=100)})
            changed.append(lab.write("during-read", update))
    snapshot = export_snapshot(left, 2, page_hook)
    import_snapshot(right, snapshot)
    assert inspect(right) == before
    assert not reconcile(left, right)["passed"]
    catch_up(left, right, batch=1)
    assert reconcile(left, right)["passed"]
    assert "person0000" not in inspect(right)["rows"]["person"]


def test_tampered_snapshot_and_incomplete_receipts_reject_without_partial_data(lab):
    left, right = lab.systems.values()
    snapshot = export_snapshot(left)
    snapshot["objects"][0]["value"]["name"] = "Changed after sealing"
    with pytest.raises(Refused, match="seal"):
        import_snapshot(right, snapshot)
    assert inspect(right)["rows"] == {"account": {}, "person": {}}
    snapshot = export_snapshot(left)
    snapshot["requests"].pop(2)
    snapshot["seal"] = digest({k: v for k, v in snapshot.items() if k != "seal"})
    with pytest.raises(Refused, match="incomplete_request"):
        import_snapshot(right, snapshot)


def test_snapshot_retry_is_idempotent_but_cannot_reset_a_live_target(lab):
    left, right = lab.systems.values()
    snapshot = export_snapshot(left)
    assert import_snapshot(right, snapshot) == "loaded"
    assert import_snapshot(right, snapshot) == "duplicate"
    right.write("new-target-write", command("person", "person0000", person(0, score=88)))
    with pytest.raises(Refused, match="empty_destination"):
        import_snapshot(right, snapshot)


def test_receipt_retry_survives_snapshot_and_conflicting_key_refuses(paired):
    left, right = paired.systems.values()
    original = command("person", "person0000", person(0))
    assert left.write("seed-person-0", original) == right.write("seed-person-0", original)
    with pytest.raises(Refused, match="identity_conflict"):
        right.write("seed-person-0", command("person", "person0000", person(0, score=44)))


def test_schema_upgrade_blocks_entire_batch_and_resumes_same_suffix(paired):
    left, right = paired.systems.values()
    before = inspect(right)
    paired.write("v1-change", command("person", "person0000", person(0, score=90)))
    paired.write("v2-change", command("person", "person0001", person(1, schema=2), schema=2))
    with pytest.raises(Refused, match="mapping_upgrade"):
        catch_up(left, right, mapping=1)
    assert inspect(right) == before
    catch_up(left, right, mapping=2)
    assert reconcile(left, right)["passed"]
    assert inspect(right)["rows"]["person"]["person0001"]["locale"] == "fr-CA"


def test_relationship_changes_are_atomic_and_orphans_rollback(paired):
    left, right = paired.systems.values()
    before = inspect(left)
    invalid = command("account", "org0", None)
    with pytest.raises(sqlite3.IntegrityError):
        paired.write("orphan", invalid)
    assert inspect(left) == before
    update = command("account", "org0", None)
    update["changes"] += [{"entity": "person", "id": f"person{i:04d}", "value": None} for i in (0, 6)]
    paired.write("family-delete", update)
    catch_up(left, right)
    assert reconcile(left, right)["passed"] and "org0" not in inspect(right)["rows"]["account"]


@pytest.mark.parametrize("fault", ["gap", "fork", "seal", "lineage"])
def test_change_log_integrity_refuses_gap_fork_tamper_or_unrelated_source(paired, fault):
    left, right = paired.systems.values()
    before = inspect(right)
    for i in range(2):
        paired.write(f"new-{i}", command("person", f"person{i:04d}", person(i, score=80)))
    batch = left.events(before["cursor"])
    if fault == "gap":
        batch["events"].pop(0)
    elif fault == "fork":
        batch["events"][0]["previous"] = "wrong"
        batch["events"][0]["hash"] = digest({k: v for k, v in batch["events"][0].items() if k != "hash"})
    elif fault == "seal":
        batch["events"][0]["payload"]["changes"][0]["value"]["score"] = 77
    else:
        batch["origin"] = "unrelated"
    with pytest.raises(Refused):
        right.apply(batch)
    assert inspect(right) == before


def test_missing_retained_suffix_is_explicit_not_zero_lag(paired):
    left, right = paired.systems.values()
    paired.write("new", command("person", "person0000", person(0, score=99)))
    with transaction(left.path) as c:
        c.execute("DROP TRIGGER changes_no_delete")  # Fault injection by a privileged filesystem owner.
        c.execute("DELETE FROM changes WHERE command_id='new'")
    with pytest.raises(Refused, match="retention_missing"):
        catch_up(left, right)


@pytest.mark.parametrize("seed", [7, 29, 101])
def test_incremental_partition_and_replay_equal_fresh_snapshot(paired, tmp_path, seed):
    left, right = paired.systems.values()
    rng = random.Random(seed)
    initial = int(right.state()["cursor"])
    for i in range(24):
        index = rng.randrange(12)
        value = None if i % 7 == 0 else person(index, schema=2, score=rng.randrange(101),
                    status=rng.choice(["prospect", "customer", "paused"]), subscribed=rng.choice([None, False, True]))
        paired.write(f"random-{i}", command("person", f"person{index:04d}", value, schema=2))
    catch_up(left, right, batch=rng.randrange(1, 6))
    assert reconcile(left, right)["passed"]
    # The same committed suffix is replayed after success; data and checkpoint cannot change.
    before = inspect(right)
    right.apply(left.events(initial))
    assert inspect(right) == before
    fresh = System(tmp_path / "fresh.sqlite", "modern")
    import_snapshot(fresh, export_snapshot(left, page_size=3))
    assert inspect(fresh) == inspect(right)


@pytest.mark.parametrize("field,bad", [("score", True), ("score", 101), ("subscribed", "false"),
                                      ("channels", ["email", "email"]), ("status", "surprise")])
def test_contract_rejections_do_not_mutate_or_advance(lab, field, bad):
    left = lab.systems["legacy"]
    before = inspect(left)
    value = person(0)
    value[field] = bad
    with pytest.raises(Refused):
        lab.write("invalid", command("person", "person0000", value))
    assert inspect(left) == before


def test_duplicate_change_and_unexpected_fields_fail_closed(lab):
    update = command("person", "person0000", person(0))
    update["changes"].append(copy.deepcopy(update["changes"][0]))
    with pytest.raises(Refused, match="duplicate_entity"):
        lab.write("duplicate-change", update)
    update = command("person", "person0000", person(0))
    update["changes"][0]["value"]["guessed_identity"] = "not allowed"
    with pytest.raises(Refused, match="unexpected"):
        lab.write("extra", update)


def test_change_and_receipt_history_are_immutable(paired):
    with transaction(paired.systems["legacy"].path) as c:
        for sql in ("UPDATE changes SET hash='x'", "DELETE FROM requests"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                c.execute(sql)


def test_replay_same_id_changed_payload_cannot_replace_history(paired):
    left, right = paired.systems.values()
    paired.write("new", command("person", "person0000", person(0, score=99)))
    batch = left.events(int(right.state()["cursor"]))
    right.apply(batch)
    batch["events"][0]["payload"]["changes"][0]["value"]["score"] = 3
    event = batch["events"][0]
    event["command_hash"] = digest(event["payload"])
    event["hash"] = digest({k: v for k, v in event.items() if k != "hash"})
    with pytest.raises(Refused, match="replayed_event_conflict"):
        right.apply(batch)
    assert reconcile(left, right)["passed"]


def test_snapshot_json_roundtrip_preserves_unicode_and_unknown(paired):
    left, right = paired.systems.values()
    assert "Montréal" in json.dumps(export_snapshot(left), ensure_ascii=False)
    assert inspect(right)["rows"]["person"]["person0000"]["subscribed"] is None
    assert reconcile(left, right)["passed"]


def test_replayed_fork_after_snapshot_is_not_hidden_by_equal_command_hash(paired):
    left, right = paired.systems.values()
    batch = left.events(0, limit=1)
    batch["events"][0]["previous"] = "different-history"
    event = batch["events"][0]
    event["hash"] = digest({k: v for k, v in event.items() if k != "hash"})
    with pytest.raises(Refused, match="replayed_event_conflict"):
        right.apply(batch)


def test_boolean_sequence_and_duplicate_snapshot_objects_are_rejected(lab):
    left, right = lab.systems.values()
    snapshot = export_snapshot(left)
    snapshot["objects"].append(copy.deepcopy(snapshot["objects"][0]))
    snapshot["seal"] = digest({k: v for k, v in snapshot.items() if k != "seal"})
    with pytest.raises(Refused, match="duplicate_snapshot"):
        import_snapshot(right, snapshot)
    import_snapshot(right, export_snapshot(left))
    batch = left.events(0, limit=1)
    batch["events"][0]["seq"] = True
    with pytest.raises(Refused, match="invalid_event_sequence"):
        right.apply(batch)


def test_reverse_direction_must_not_silently_accept_destination_ahead(paired):
    left, right = paired.systems.values()
    right.write("target-native", command("person", "person0000", person(0, score=100)))
    with pytest.raises(Refused, match="destination_ahead"):
        catch_up(left, right)
