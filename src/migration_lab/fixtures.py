"""Entirely fictional records; compact edge cases are intentional, not a scale claim."""


def person(index, schema=1, **changes):
    value = {"account_id": f"org{index % 6}", "name": f"Synthetic Person {index} — Montréal",
             "status": ("prospect", "customer", "paused")[index % 3], "score": index % 101,
             "channels": ["email", "web"] if index % 2 else [], "subscribed": (None, True, False)[index % 3]}
    if schema == 2:
        value["locale"] = "fr-CA"
    return {**value, **changes}


def command(entity, key, value, schema=1):
    return {"schema": schema, "changes": [{"entity": entity, "id": key, "value": value}]}


def seed(coordinator, count=120):
    for i in range(6):
        coordinator.write(f"seed-org-{i}", command("account", f"org{i}", {"name": f"Fictional Organization {i}"}))
    for i in range(count):
        coordinator.write(f"seed-person-{i}", command("person", f"person{i:04d}", person(i)))
