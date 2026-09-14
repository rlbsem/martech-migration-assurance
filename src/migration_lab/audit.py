"""Independent semantic oracle. Deliberately does not call snapshot decoding or the migration mapper."""
import json

from .contracts import digest
from .system import meta, transaction


def inspect(system):
    with transaction(system.path, False) as c:
        state = meta(c)
        if system.kind == "legacy":
            accounts = c.execute("SELECT org_id AS id,display_name AS name FROM legacy_org ORDER BY org_id")
            account_rows = [dict(r) for r in accounts]
            people = c.execute("""SELECT person_id AS id,org_id AS account_id,full_name AS name,
              CASE stage WHEN 'Lead' THEN 'prospect' WHEN 'Customer' THEN 'customer' WHEN 'Hold' THEN 'paused'
                ELSE 'INVALID' END AS status,lead_score AS score,channels,subscribed,locale,schema_version
              FROM legacy_person ORDER BY person_id""").fetchall()
        else:
            account_rows = [dict(r) for r in c.execute(
                "SELECT company_key AS id,legal_name AS name FROM modern_company ORDER BY company_key")]
            people = c.execute("""SELECT contact_key AS id,company_key AS account_id,display_name AS name,
              CASE lifecycle WHEN 'P' THEN 'prospect' WHEN 'C' THEN 'customer' WHEN 'H' THEN 'paused'
                WHEN 'A' THEN 'archived' ELSE 'INVALID' END AS status,engagement_score AS score,
              channels_json AS channels,
              CASE subscription_state WHEN 'yes' THEN 1 WHEN 'no' THEN 0 WHEN 'unknown' THEN NULL ELSE -1 END AS subscribed,
              preferred_locale AS locale,schema_version FROM modern_contact ORDER BY contact_key""").fetchall()
        persons = []
        for raw in people:
            value = dict(raw)
            if system.kind == "legacy":
                value["channels"] = sorted(value["channels"].split(";")) if value["channels"] else []
            else:
                try:
                    value["channels"] = sorted(json.loads(value["channels"]))
                except (ValueError, TypeError):
                    value["channels"] = ["INVALID"]
            persons.append(value)
        rows = {"account": {r["id"]: r for r in account_rows}, "person": {r["id"]: r for r in persons}}
        receipts = [dict(r) for r in c.execute("SELECT * FROM requests ORDER BY seq")]
        orphans = [r["id"] for r in persons if r["account_id"] not in rows["account"]]
        # A representative read contract, not a new audience/activation product.
        eligible = [r["id"] for r in persons if r["status"] == "customer" and r["subscribed"] == 1
                    and "email" in r["channels"]]
    return {"cursor": int(state["cursor"]), "chain": state["chain"], "origin": state["origin"], "rows": rows,
            "receipts": receipts, "orphans": orphans, "email_customer_read": eligible}


def reconcile(left, right):
    a, b = inspect(left), inspect(right)
    differences = []
    for entity in ("account", "person"):
        for key in sorted(a["rows"][entity].keys() | b["rows"][entity].keys()):
            before, after = a["rows"][entity].get(key), b["rows"][entity].get(key)
            if before is None or after is None:
                differences.append({"entity": entity, "id": key, "field": "_record",
                                    "source": before, "destination": after})
            else:
                for field in sorted(before):
                    if before[field] != after[field]:
                        differences.append({"entity": entity, "id": key, "field": field,
                                            "source": before[field], "destination": after[field]})
    checks = {"same_lineage": bool(a["origin"]) and a["origin"] == b["origin"],
              "same_watermark": a["cursor"] == b["cursor"], "same_log_chain": a["chain"] == b["chain"],
              "same_receipts": a["receipts"] == b["receipts"], "no_semantic_differences": not differences,
              "no_orphans": not a["orphans"] and not b["orphans"],
              "same_read_contract": a["email_customer_read"] == b["email_customer_read"]}
    return {"passed": all(checks.values()), "checks": checks, "differences": differences,
            "source_cursor": a["cursor"], "destination_cursor": b["cursor"],
            "source_hash": digest(a), "destination_hash": digest(b),
            "counts": {"source": {k: len(v) for k, v in a["rows"].items()},
                       "destination": {k: len(v) for k, v in b["rows"].items()}},
            "read_contract": {"source": a["email_customer_read"], "destination": b["email_customer_read"]}}
