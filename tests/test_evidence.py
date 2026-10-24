"""Preserved SQLite records remain byte-identical; offline native checks fail closed under -O."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def test_existing_sqlite_runtime_and_evidence_bytes_preserved():
    root = Path(__file__).resolve().parents[1]
    record = json.loads((root / 'docs/evidence/postgres-cdc/preserved-files.json').read_text(encoding='utf-8'))
    assert record['baseline_commit'] == '1d6bb75bf6fb802cc08c27ffaefe71b48b66e528'
    assert record['files']
    for name, expected in record['files'].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name


def test_optimized_python_cannot_qualify_native_evidence():
    result = subprocess.run([sys.executable, '-O', '-c',
                             "from migration_lab.pgcdc.checker import check_case; check_case('snapshot_handoff',{},'')"],
                            capture_output=True, text=True)
    assert result.returncode != 0 and 'optimized_python_cannot_validate_native_evidence' in result.stderr
