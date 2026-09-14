import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from migration_lab.audit import inspect, reconcile
from migration_lab.fixtures import command, person


@pytest.mark.parametrize("point,code", [("before_commit", 71), ("after_commit", 72)])
def test_real_process_exit_at_commit_boundary_recovers_without_skipping(paired, tmp_path, point, code):
    left, right = paired.systems.values()
    before = inspect(right)
    paired.write("crash-source-1", command("person", "person0000", person(0, score=91)))
    paired.write("crash-source-2", command("person", "person0001", None))
    batch = left.events(before["cursor"])
    path = tmp_path / "batch.json"
    path.write_text(json.dumps(batch), encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "migration_lab.worker", str(right.path), "modern", str(path),
                             "--crash", point], capture_output=True, text=True, timeout=15)
    assert result.returncode == code, result.stderr
    recovered = inspect(right)
    if point == "before_commit":
        assert recovered == before
    else:
        assert reconcile(left, right)["passed"]
    right.apply(batch)
    assert reconcile(left, right)["passed"]
    final = inspect(right)
    assert final["cursor"] == before["cursor"] + 2 and len(final["receipts"]) == len(before["receipts"]) + 2
    evidence = os.getenv("MIGRATION_CRASH_EVIDENCE")
    if evidence:
        output = Path(evidence)
        output.mkdir(parents=True, exist_ok=True)
        (output / f"{point}.json").write_text(json.dumps({"fault": point, "actual_exit_code": result.returncode,
            "before_cursor": before["cursor"], "reopened_cursor": recovered["cursor"], "final_cursor": final["cursor"],
            "final_reconciliation": reconcile(left, right), "command": "python -m migration_lab.worker --crash " + point}, indent=2),
            encoding="utf-8")
