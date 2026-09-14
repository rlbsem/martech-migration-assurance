"""Closed commands and explicit versioned, lossless mappings; no inferred customer identity."""
import hashlib
import json
import re


class Refused(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise Refused("invalid_identifier")


def exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise Refused("unexpected_or_missing_fields")


def validate_command(command):
    exact(command, {"schema", "changes"})
    if type(command["schema"]) is not int or command["schema"] not in (1, 2):
        raise Refused("unsupported_command_schema")
    changes = command["changes"]
    if not isinstance(changes, list) or not 1 <= len(changes) <= 100:
        raise Refused("invalid_change_count")
    seen = set()
    for change in changes:
        exact(change, {"entity", "id", "value"})
        identifier(change["id"])
        if change["entity"] not in ("account", "person"):
            raise Refused("unknown_entity")
        key = (change["entity"], change["id"])
        if key in seen:
            raise Refused("duplicate_entity_in_transaction")
        seen.add(key)
        value = change["value"]
        if value is None:
            continue
        fields = {"name"} if change["entity"] == "account" else {
            "account_id", "name", "status", "score", "channels", "subscribed"}
        if change["entity"] == "person" and command["schema"] == 2:
            fields.add("locale")
        exact(value, fields)
        if not isinstance(value["name"], str) or not 1 <= len(value["name"]) <= 150:
            raise Refused("invalid_name")
        if change["entity"] == "account":
            continue
        identifier(value["account_id"])
        if value["status"] not in ("prospect", "customer", "paused", "archived"):
            raise Refused("unknown_lifecycle")
        if type(value["score"]) is not int or not 0 <= value["score"] <= 100:
            raise Refused("invalid_score")
        if value["subscribed"] is not None and type(value["subscribed"]) is not bool:
            raise Refused("invalid_subscription")
        channels = value["channels"]
        if (not isinstance(channels, list) or any(c not in ("email", "sms", "web") for c in channels)
                or len(channels) != len(set(channels))):
            raise Refused("invalid_channels")
        if command["schema"] == 2 and value["locale"] not in (None, "en-CA", "fr-CA"):
            raise Refused("unsupported_locale")
    return command


def encode_person(kind, value, schema, mapping):
    if mapping not in (1, 2) or schema > mapping:
        raise Refused("mapping_upgrade_required")
    if kind == "legacy":
        if value["status"] == "archived":
            raise Refused("unrepresentable_legacy_lifecycle")
        stage = {"prospect": "Lead", "customer": "Customer", "paused": "Hold"}[value["status"]]
        subscription = None if value["subscribed"] is None else int(value["subscribed"])
        channels = ";".join(sorted(value["channels"]))
    else:
        stage = {"prospect": "P", "customer": "C", "paused": "H", "archived": "A"}[value["status"]]
        subscription = {None: "unknown", True: "yes", False: "no"}[value["subscribed"]]
        channels = canonical(sorted(value["channels"]))
    return (value["account_id"], value["name"], stage, value["score"], channels, subscription,
            value.get("locale"), schema)
