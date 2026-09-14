"""Consistent keyset-paged export with one pinned WAL read transaction and a sealed receipt index."""
import json

from .contracts import Refused, digest, exact, identifier
from .system import meta, set_meta, transaction


def objects(c, kind, page_size=100, page_hook=None):
    result = []
    tables = (("account", "legacy_org", "org_id"), ("person", "legacy_person", "person_id")) if kind == "legacy" else (
        ("account", "modern_company", "company_key"), ("person", "modern_contact", "contact_key"))
    for entity, table, key in tables:
        last = ""
        while True:
            rows = c.execute(f"SELECT * FROM {table} WHERE {key}>? ORDER BY {key} LIMIT ?", (last, page_size)).fetchall()
            if not rows:
                break
            for r in rows:
                if entity == "account":
                    value = {"name": r["display_name" if kind == "legacy" else "legal_name"]}
                    schema = 1
                elif kind == "legacy":
                    value = {"account_id": r["org_id"], "name": r["full_name"],
                             "status": {"Lead": "prospect", "Customer": "customer", "Hold": "paused"}[r["stage"]],
                             "score": r["lead_score"], "channels": r["channels"].split(";") if r["channels"] else [],
                             "subscribed": None if r["subscribed"] is None else bool(r["subscribed"])}
                    schema = r["schema_version"]
                    if schema == 2:
                        value["locale"] = r["locale"]
                else:
                    value = {"account_id": r["company_key"], "name": r["display_name"],
                             "status": {"P": "prospect", "C": "customer", "H": "paused", "A": "archived"}[r["lifecycle"]],
                             "score": r["engagement_score"], "channels": json.loads(r["channels_json"]),
                             "subscribed": {"yes": True, "no": False, "unknown": None}[r["subscription_state"]]}
                    schema = r["schema_version"]
                    if schema == 2:
                        value["locale"] = r["preferred_locale"]
                result.append({"entity": entity, "id": r[key], "schema": schema, "value": value})
            last = rows[-1][key]
            if page_hook:
                page_hook(entity, last)
    return result


def export_snapshot(system, page_size=100, page_hook=None):
    if type(page_size) is not int or page_size < 1:
        raise Refused("invalid_page_size")
    with transaction(system.path, False) as c:
        state = meta(c)  # First read fixes the database snapshot before any page is exported.
        if not state["origin"]:
            raise Refused("source_has_no_lineage")
        body = {"format": 1, "origin": state["origin"], "watermark": int(state["cursor"]), "chain": state["chain"],
                "objects": objects(c, system.kind, page_size, page_hook),
                "requests": [dict(r) for r in c.execute("SELECT * FROM requests ORDER BY seq")]}
    return {**body, "seal": digest(body)}


def import_snapshot(system, snapshot, mapping=2):
    exact(snapshot, {"format", "origin", "watermark", "chain", "objects", "requests", "seal"})
    identifier(snapshot["origin"])
    if (type(mapping) is not int or mapping not in (1, 2) or type(snapshot["watermark"]) is not int
            or snapshot["watermark"] < 0 or not isinstance(snapshot["objects"], list)
            or not isinstance(snapshot["requests"], list)):
        raise Refused("invalid_snapshot_contract")
    body = {k: v for k, v in snapshot.items() if k != "seal"}
    if snapshot.get("format") != 1 or digest(body) != snapshot["seal"]:
        raise Refused("snapshot_seal_mismatch")
    if [r["seq"] for r in snapshot["requests"]] != list(range(1, snapshot["watermark"] + 1)):
        raise Refused("incomplete_request_index")
    identities = []
    for item in snapshot["objects"]:
        exact(item, {"schema", "entity", "id", "value"})
        identifier(item["id"])
        identities.append((item["entity"], item["id"]))
    if len(set(identities)) != len(identities):
        raise Refused("duplicate_snapshot_object")
    with transaction(system.path) as c:
        state = meta(c)
        if state["snapshot"] == snapshot["seal"] and int(state["cursor"]) == snapshot["watermark"]:
            if objects(c, system.kind) == snapshot["objects"]:
                return "duplicate"
            raise Refused("snapshot_target_drift")
        if state["origin"] or int(state["cursor"]) != 0:
            raise Refused("snapshot_requires_empty_destination")
        for item in snapshot["objects"]:
            system._mutate(c, {"schema": item["schema"], "changes": [{k: v for k, v in item.items() if k != "schema"}]}, mapping)
        c.executemany("INSERT INTO requests VALUES (:command_id,:command_hash,:seq,:event_hash)", snapshot["requests"])
        set_meta(c, origin=snapshot["origin"], cursor=snapshot["watermark"], chain=snapshot["chain"], snapshot=snapshot["seal"])
    return "loaded"
