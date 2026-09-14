import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from migration_lab.audit import inspect, reconcile
from migration_lab.contracts import Refused
from migration_lab.coordinator import Coordinator
from migration_lab.fixtures import command, person
from migration_lab.system import catch_up, transaction


@pytest.mark.parametrize("sql,field", [
    ("UPDATE modern_contact SET subscription_state='no' WHERE contact_key='person0000'", "subscribed"),
    ("UPDATE modern_contact SET channels_json='[]' WHERE contact_key='person0001'", "channels"),
    ("UPDATE modern_contact SET lifecycle='P' WHERE contact_key='person0001'", "status"),
    ("UPDATE modern_contact SET engagement_score=100 WHERE contact_key='person0002'", "score")])
def test_equal_counts_and_cursor_do_not_hide_semantic_loss(paired, sql, field):
    with transaction(paired.systems["modern"].path) as c:
        c.execute(sql)
    paired.freeze("legacy")
    report = paired.assess()
    audit = report["reconciliation"]
    assert audit["counts"]["source"] == audit["counts"]["destination"]
    assert audit["checks"]["same_watermark"] and audit["checks"]["same_log_chain"]
    assert any(d["field"] == field for d in audit["differences"])
    with pytest.raises(Refused, match="parity"):
        paired.activate(report["hash"])
    assert paired.route()["active"] == "legacy"


def test_forged_unregistered_proof_and_no_barrier_rejected(paired):
    with pytest.raises(Refused, match="barrier"):
        paired.assess()
    with pytest.raises(Refused, match="registered"):
        paired.activate("invented-pass")
    paired.freeze("legacy")
    with pytest.raises(Refused, match="registered"):
        paired.activate("invented-pass")


def test_target_changes_after_assessment_invalidate_proof(paired):
    paired.freeze("legacy")
    report = paired.assess()
    with transaction(paired.systems["modern"].path) as c:
        c.execute("UPDATE modern_contact SET engagement_score=88 WHERE contact_key='person0000'")
    with pytest.raises(Refused, match="stale_or_tampered"):
        paired.activate(report["hash"])


def test_mapping_implementation_change_invalidates_proof(paired, monkeypatch):
    paired.freeze("legacy")
    report = paired.assess()
    monkeypatch.setattr("migration_lab.coordinator.implementation_hash", lambda: "new-build")
    with pytest.raises(Refused, match="stale_or_tampered"):
        paired.activate(report["hash"])


def test_proof_cannot_be_reused_after_abort_and_refreeze(paired):
    old = paired.freeze("legacy")
    report = paired.assess()
    paired.resume_original(old["epoch"])
    paired.freeze("legacy")
    with pytest.raises(Refused, match="stale_or_tampered"):
        paired.activate(report["hash"])


def test_post_cutover_writes_must_be_reverse_copied_before_rollback(paired):
    paired.freeze("legacy")
    route = paired.activate(paired.assess()["hash"])
    request = command("person", "person0000", person(0, schema=2, score=99, subscribed=False), 2)
    receipt = paired.write("target-write", request)
    assert receipt["system"] == "modern"
    paired.freeze("modern")
    blocked = paired.assess()
    with pytest.raises(Refused):
        paired.activate(blocked["hash"])
    catch_up(paired.systems["modern"], paired.systems["legacy"])
    paired.activate(paired.assess()["hash"])
    retried = paired.write("target-write", request)
    assert retried["system"] == "legacy" and retried["receipt"] == receipt["receipt"]
    assert inspect(paired.systems["legacy"])["rows"]["person"]["person0000"]["score"] == 99
    assert reconcile(*paired.systems.values())["passed"]
    with pytest.raises(Refused, match="stale_client_epoch"):
        paired.write("stale-writer", request, expected_epoch=route["epoch"])


def test_modern_only_state_blocks_lossy_rollback_and_abort_preserves_active_data(paired):
    paired.freeze("legacy")
    paired.activate(paired.assess()["hash"])
    before = inspect(paired.systems["legacy"])
    paired.write("modern-only", command("person", "person0000", person(0, status="archived")))
    barrier = paired.freeze("modern")
    with pytest.raises(Refused, match="unrepresentable"):
        catch_up(paired.systems["modern"], paired.systems["legacy"])
    assert inspect(paired.systems["legacy"]) == before
    assert not paired.assess()["reconciliation"]["passed"]
    paired.resume_original(barrier["epoch"])
    assert paired.route()["active"] == "modern" and paired.route()["phase"] == "active"
    assert inspect(paired.systems["modern"])["rows"]["person"]["person0000"]["status"] == "archived"


def test_frozen_route_survives_reopening_and_refuses_writes(paired):
    paired.freeze("legacy")
    restarted = Coordinator(paired.folder)
    with pytest.raises(Refused, match="writes_paused"):
        restarted.write("during-freeze", command("person", "person0000", person(0)))
    assert restarted.route() == paired.route()


def test_freeze_waits_for_inflight_native_commit_then_fences_new_calls(paired, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    left = paired.systems["legacy"]
    real_write = left.write
    def delayed(*args):
        entered.set()
        assert release.wait(5)
        return real_write(*args)
    monkeypatch.setattr(left, "write", delayed)
    with ThreadPoolExecutor(2) as pool:
        writing = pool.submit(paired.write, "in-flight", command("person", "person0000", person(0, score=88)))
        assert entered.wait(5)
        freezing = pool.submit(paired.freeze, "legacy")
        assert not freezing.done()
        release.set()
        assert writing.result(timeout=5)["system"] == "legacy"
        assert freezing.result(timeout=5)["phase"] == "frozen"
    with pytest.raises(Refused, match="writes_paused"):
        paired.write("late", command("person", "person0001", person(1)))
    assert not paired.assess()["reconciliation"]["passed"]
    catch_up(left, paired.systems["modern"])
    assert paired.assess()["reconciliation"]["passed"]


def test_parallel_same_command_has_one_receipt_and_sequence(paired):
    before = int(paired.systems["legacy"].state()["cursor"])
    update = command("person", "person0000", person(0, score=77))
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: paired.write("same-command", update), range(8)))
    assert all(r == results[0] for r in results)
    assert int(paired.systems["legacy"].state()["cursor"]) == before + 1


def test_reports_and_history_cannot_be_rewritten(paired):
    paired.freeze("legacy")
    paired.assess()
    with transaction(paired.path) as c:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            c.execute("UPDATE reports SET body='{}'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            c.execute("DELETE FROM history")
