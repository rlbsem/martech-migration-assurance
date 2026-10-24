"""Corrupt real observations to verify the standalone oracle; no fabricated CDC execution."""
import copy
import hashlib
import json
import os

import pytest

from migration_lab.pgcdc.checker import check_case, check_directory
from migration_lab.pgcdc.db import implementation
from migration_lab.pgcdc.lab import Lab
from migration_lab.pgcdc.scenarios import handoff


@pytest.fixture(scope='module')
def executed_case(tmp_path_factory):
    with Lab(os.environ['MIGRATION_PG_ADMIN_DSN'], tmp_path_factory.mktemp('oracle') / 'native', count=12) as lab:
        body = handoff(lab)
    return {'scenario': 'snapshot_handoff', 'postgresql': lab.settings,
            'observations': body, 'cleanup': lab.cleanup_record}


def test_independent_checker_accepts_real_case(executed_case):
    assert check_case('snapshot_handoff', executed_case, implementation())['outcome'] == 'reconciled'


@pytest.mark.parametrize('fault', ['semantic_value', 'checkpoint', 'receipt', 'native_origin',
                                  'cleanup', 'snapshot_seal', 'missing_delete', 'parity_claim'])
def test_independent_checker_rejects_corruption(executed_case, fault):
    record = copy.deepcopy(executed_case)
    final = record['observations']['final']
    if fault == 'semantic_value':
        final['reconciliation']['target']['people'][0]['subscribed'] = False
    elif fault == 'checkpoint':
        final['state']['cursor'] += 1
    elif fault == 'receipt':
        final['receipts'][0]['input_hash'] = '0' * 64
    elif fault == 'native_origin':
        final['changes'][0]['native_origin'] = False
    elif fault == 'cleanup':
        record['cleanup']['slots_remaining'] = 1
    elif fault == 'snapshot_seal':
        record['observations']['snapshot']['snapshot_seal'] = '0' * 64
    elif fault == 'missing_delete':
        final['changes'] = [row for row in final['changes'] if row['operation'] != 'DELETE']
    else:
        final['reconciliation']['passed'] = False
    with pytest.raises(AssertionError):
        check_case('snapshot_handoff', record, implementation())


def test_checker_rejects_manifest_escape(tmp_path):
    outside = tmp_path / 'outside.txt'
    outside.write_bytes(b'no execution evidence')
    folder = tmp_path / 'evidence'
    folder.mkdir()
    (folder / 'manifest.json').write_text(json.dumps({'files': {'../outside.txt': hashlib.sha256(outside.read_bytes()).hexdigest()}}))
    with pytest.raises(AssertionError, match='unsafe evidence path'):
        check_directory(folder)


def test_checker_rejects_unmanifested_files(tmp_path):
    (tmp_path / 'manifest.json').write_text(json.dumps({'files': {}}))
    (tmp_path / 'unlisted.bin').write_bytes(b'not authenticated by the manifest')
    with pytest.raises(AssertionError, match='unmanifested evidence file'):
        check_directory(tmp_path)
