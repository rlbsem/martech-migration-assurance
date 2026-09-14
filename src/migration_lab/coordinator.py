"""Single-host cutover barrier. Every supported business writer must use this coordinator."""
import json
import time
from pathlib import Path

from .audit import reconcile
from .contracts import Refused, canonical, digest
from .system import System, transaction


def implementation_hash():
    folder = Path(__file__).parent
    return digest({p.name: p.read_text(encoding="utf-8") for p in sorted(folder.iterdir())
                   if p.suffix in (".py", ".sql")})


class Coordinator:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / "routing.sqlite"
        self.systems = {kind: System(self.folder / f"{kind}.sqlite", kind) for kind in ("legacy", "modern")}
        with transaction(self.path) as c:
            c.executescript("""CREATE TABLE IF NOT EXISTS route(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
              active TEXT NOT NULL, phase TEXT NOT NULL, epoch INTEGER NOT NULL, destination TEXT);
              INSERT OR IGNORE INTO route VALUES (1,'legacy','active',0,NULL);
              CREATE TABLE IF NOT EXISTS reports(hash TEXT PRIMARY KEY,body TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS history(seq INTEGER PRIMARY KEY,at REAL,event TEXT,detail TEXT);
              CREATE TRIGGER IF NOT EXISTS history_no_update BEFORE UPDATE ON history BEGIN SELECT RAISE(ABORT,'immutable history'); END;
              CREATE TRIGGER IF NOT EXISTS history_no_delete BEFORE DELETE ON history BEGIN SELECT RAISE(ABORT,'immutable history'); END;
              CREATE TRIGGER IF NOT EXISTS reports_no_update BEFORE UPDATE ON reports BEGIN SELECT RAISE(ABORT,'immutable report'); END;
              CREATE TRIGGER IF NOT EXISTS reports_no_delete BEFORE DELETE ON reports BEGIN SELECT RAISE(ABORT,'immutable report'); END;""")

    @staticmethod
    def log(c, event, detail):
        c.execute("INSERT INTO history(at,event,detail) VALUES (?,?,?)", (time.time(), event, canonical(detail)))

    def route(self):
        with transaction(self.path, False) as c:
            return dict(c.execute("SELECT * FROM route").fetchone())

    def write(self, command_id, command, expected_epoch=None):
        # Hold the routing writer lock until the native commit finishes. Freeze drains in-flight calls.
        with transaction(self.path) as c:
            route = dict(c.execute("SELECT * FROM route").fetchone())
            if route["phase"] != "active":
                raise Refused("writes_paused_for_cutover")
            if expected_epoch is not None and expected_epoch != route["epoch"]:
                raise Refused("stale_client_epoch")
            receipt = self.systems[route["active"]].write(command_id, command)
            return {"system": route["active"], "epoch": route["epoch"], "receipt": receipt}

    def freeze(self, expected_active):
        with transaction(self.path) as c:
            route = dict(c.execute("SELECT * FROM route").fetchone())
            if route["phase"] != "active" or route["active"] != expected_active:
                raise Refused("freeze_precondition")
            destination = "modern" if route["active"] == "legacy" else "legacy"
            c.execute("UPDATE route SET phase='frozen',epoch=epoch+1,destination=?", (destination,))
            self.log(c, "writes_frozen", {"active": expected_active, "destination": destination, "epoch": route["epoch"] + 1})
        return self.route()

    def _proof(self, route):
        return {"epoch": route["epoch"], "active": route["active"], "destination": route["destination"],
                "implementation": implementation_hash(),
                "reconciliation": reconcile(self.systems[route["active"]], self.systems[route["destination"]])}

    def assess(self):
        with transaction(self.path) as c:
            route = dict(c.execute("SELECT * FROM route").fetchone())
            if route["phase"] != "frozen":
                raise Refused("write_barrier_required")
            body = self._proof(route)
            seal = digest(body)
            c.execute("INSERT OR IGNORE INTO reports VALUES (?,?)", (seal, canonical(body)))
            self.log(c, "cutover_assessed", {"hash": seal, "passed": body["reconciliation"]["passed"]})
            return {"hash": seal, **body}

    def activate(self, report_hash):
        with transaction(self.path) as c:
            route = dict(c.execute("SELECT * FROM route").fetchone())
            saved = c.execute("SELECT body FROM reports WHERE hash=?", (report_hash,)).fetchone()
            if route["phase"] != "frozen" or not saved:
                raise Refused("registered_frozen_proof_required")
            proof = json.loads(saved["body"])
            current = self._proof(route)
            if digest(current) != report_hash or digest(proof) != report_hash:
                raise Refused("stale_or_tampered_proof")
            if not current["reconciliation"]["passed"]:
                raise Refused("semantic_parity_required")
            c.execute("UPDATE route SET active=destination,destination=NULL,phase='active',epoch=epoch+1")
            self.log(c, "activated", {"from": route["active"], "to": route["destination"], "proof": report_hash,
                                       "epoch": route["epoch"] + 1})
        return self.route()

    def resume_original(self, expected_epoch):
        with transaction(self.path) as c:
            route = dict(c.execute("SELECT * FROM route").fetchone())
            if route["phase"] != "frozen" or route["epoch"] != expected_epoch:
                raise Refused("resume_precondition")
            c.execute("UPDATE route SET phase='active',destination=NULL,epoch=epoch+1")
            self.log(c, "cutover_aborted", {"active": route["active"], "epoch": route["epoch"] + 1})
