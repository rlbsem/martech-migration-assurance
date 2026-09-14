"""Two native schemas in independent SQLite files, each with an atomic change/receipt ledger."""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .contracts import Refused, canonical, digest, encode_person, exact, identifier, validate_command

ZERO = "0" * 64


@contextmanager
def transaction(path, write=True):
    c = sqlite3.connect(path, timeout=10, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=FULL")
    try:
        c.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


def meta(c):
    return dict(c.execute("SELECT key,value FROM meta"))


def set_meta(c, **values):
    c.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [(k, str(v)) for k, v in values.items()])


class System:
    def __init__(self, path, kind):
        if kind not in ("legacy", "modern"):
            raise Refused("unknown_system")
        self.path, self.kind = Path(path), kind
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with transaction(self.path) as c:
            c.executescript(Path(__file__).with_name("schema.sql").read_text())
            old = meta(c)
            if old and old["kind"] != kind:
                raise Refused("system_kind_mismatch")
            if not old:
                set_meta(c, kind=kind, cursor=0, chain=ZERO, origin="", snapshot="")

    def state(self):
        with transaction(self.path, False) as c:
            return meta(c)

    def _mutate(self, c, command, mapping):
        validate_command(command)
        if command["schema"] > mapping:
            raise Refused("mapping_upgrade_required")
        account_table, person_table = (("legacy_org", "legacy_person") if self.kind == "legacy"
                                      else ("modern_company", "modern_contact"))
        account_key, person_key = (("org_id", "person_id") if self.kind == "legacy" else ("company_key", "contact_key"))
        for item in command["changes"]:
            table, key = (account_table, account_key) if item["entity"] == "account" else (person_table, person_key)
            value = item["value"]
            if value is None:
                c.execute(f"DELETE FROM {table} WHERE {key}=?", (item["id"],))
            elif item["entity"] == "account":
                name = "display_name" if self.kind == "legacy" else "legal_name"
                c.execute(f"INSERT INTO {table} VALUES (?,?) ON CONFLICT({key}) DO UPDATE SET {name}=excluded.{name}",
                          (item["id"], value["name"]))
            else:
                values = encode_person(self.kind, value, command["schema"], mapping)
                # Delete+insert is inside the transaction; deferred relationships are checked at commit.
                c.execute(f"DELETE FROM {table} WHERE {key}=?", (item["id"],))
                c.execute(f"INSERT INTO {table} VALUES (?,?,?,?,?,?,?,?,?)", (item["id"], *values))

    def write(self, command_id, command):
        identifier(command_id)
        validate_command(command)
        fingerprint = digest(command)
        with transaction(self.path) as c:
            receipt = c.execute("SELECT * FROM requests WHERE command_id=?", (command_id,)).fetchone()
            if receipt:
                if receipt["command_hash"] != fingerprint:
                    raise Refused("command_identity_conflict")
                return dict(receipt)
            current = meta(c)
            seq = int(current["cursor"]) + 1
            self._mutate(c, command, 2)
            event = {"seq": seq, "command_id": command_id, "command_hash": fingerprint,
                     "payload": command, "previous": current["chain"]}
            seal = self._record(c, event)
            if not current["origin"]:
                set_meta(c, origin=str(uuid4()))
            return {"command_id": command_id, "command_hash": fingerprint, "seq": seq, "event_hash": seal}

    @staticmethod
    def _record(c, event):
        seal = digest(event)
        c.execute("INSERT INTO changes VALUES (?,?,?,?,?,?)", (event["seq"], event["command_id"],
                  event["command_hash"], canonical(event["payload"]), event["previous"], seal))
        c.execute("INSERT INTO requests VALUES (?,?,?,?)", (event["command_id"], event["command_hash"], event["seq"], seal))
        set_meta(c, cursor=event["seq"], chain=seal)
        return seal

    def events(self, after, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise Refused("invalid_batch_limit")
        with transaction(self.path, False) as c:
            state = meta(c)
            rows = [{**dict(r), "payload": json.loads(r["payload"])} for r in c.execute(
                "SELECT * FROM changes WHERE seq>? ORDER BY seq LIMIT ?", (after, limit))]
            return {"origin": state["origin"], "through": int(state["cursor"]), "events": rows}

    def apply(self, envelope, mapping=2, crash=None):
        """Data, receipts and cursor commit together. A crash cannot persist only the cursor."""
        try:
            exact(envelope, {"origin", "through", "events"})
            identifier(envelope["origin"])
            if (type(mapping) is not int or mapping not in (1, 2) or type(envelope["through"]) is not int
                    or envelope["through"] < 0 or not isinstance(envelope["events"], list)
                    or len(envelope["events"]) > 1000):
                raise Refused("invalid_batch_contract")
            with transaction(self.path) as c:
                current = meta(c)
                if not current["origin"] or envelope["origin"] != current["origin"]:
                    raise Refused("migration_lineage_mismatch")
                for raw in envelope["events"]:
                    exact(raw, {"seq", "command_id", "command_hash", "payload", "previous", "hash"})
                    identifier(raw["command_id"])
                    if type(raw["seq"]) is not int or not 1 <= raw["seq"] <= envelope["through"]:
                        raise Refused("invalid_event_sequence")
                    event = {k: v for k, v in raw.items() if k != "hash"}
                    if digest(event) != raw["hash"] or digest(event["payload"]) != event["command_hash"]:
                        raise Refused("event_seal_mismatch")
                    current = meta(c)
                    if event["seq"] <= int(current["cursor"]):
                        old = c.execute("SELECT * FROM requests WHERE seq=?", (event["seq"],)).fetchone()
                        if (not old or old["command_id"] != event["command_id"]
                                or old["command_hash"] != event["command_hash"] or old["event_hash"] != raw["hash"]):
                            raise Refused("replayed_event_conflict")
                        continue
                    if event["seq"] != int(current["cursor"]) + 1 or event["previous"] != current["chain"]:
                        raise Refused("change_log_gap_or_fork")
                    self._mutate(c, event["payload"], mapping)
                    self._record(c, event)
                if crash == "before_commit":
                    import os
                    os._exit(71)
                c.execute("INSERT INTO transfer_attempts(at,outcome,detail) VALUES (?,?,?)",
                          (time.time(), "committed", canonical({"mapping": mapping, "cursor": int(meta(c)["cursor"])})))
            if crash == "after_commit":
                import os
                os._exit(72)
        except (Refused, sqlite3.IntegrityError) as exc:
            with transaction(self.path) as c:
                c.execute("INSERT INTO transfer_attempts(at,outcome,detail) VALUES (?,?,?)",
                          (time.time(), "blocked", canonical({"mapping": mapping, "reason": str(exc)})))
            raise
        return int(self.state()["cursor"])


def catch_up(source, destination, mapping=2, batch=100):
    bound = int(source.state()["cursor"])
    if int(destination.state()["cursor"]) > bound:
        raise Refused("destination_ahead_of_source")
    while int(destination.state()["cursor"]) < bound:
        before = int(destination.state()["cursor"])
        envelope = source.events(before, batch)
        envelope["events"] = [e for e in envelope["events"] if e["seq"] <= bound]
        if not envelope["events"]:
            raise Refused("required_log_retention_missing")
        destination.apply(envelope, mapping)
    return bound
