"""Offline oracle over independent SQL reads, native inbox groups and committed receipts.

Does not import the semantic mapper, execution harness, or database driver.
"""
import gzip
import hashlib
import json
from pathlib import Path

from migration_lab.contracts import canonical, digest


EXPECTED = {
    'snapshot_handoff', 'crash_before_commit', 'crash_after_commit_before_ack', 'native_disconnect',
    'mapping_upgrade', 'target_rejection', 'semantic_cutover', 'missing_slot', 'unusable_checkpoint',
    'schema_incompatibility', 'publication_changed', 'restricted_identities', 'fence_drains_writer',
}
BLOCKED = {
    'missing_slot': 'replication_slot_missing_or_mismatched',
    'unusable_checkpoint': 'unusable_checkpoint',
    'schema_incompatibility': 'source_schema_changed',
    'publication_changed': 'publication_changed',
}


def _lsn(value):
    a, b = value.split('/')
    return (int(a, 16) << 32) + int(b, 16)


def observation(body, implementation):
    reconciliation = body['reconciliation']
    for name in ('source', 'target'):
        value = reconciliation[name]
        for table in ('organizations', 'people'):
            ids = [row['id'] for row in value[table]]
            assert ids == sorted(set(ids)), 'duplicate/unsorted semantic identity'
        parents = {row['id'] for row in value['organizations']}
        orphans = [row['id'] for row in value['people'] if row['account_id'] not in parents]
        assert value['orphans'] == orphans, 'orphan observation mismatch'
        assert digest(value) == reconciliation[name + '_hash'], 'semantic observation digest mismatch'
    equal = canonical(reconciliation['source']) == canonical(reconciliation['target'])
    assert reconciliation['passed'] == (equal and not reconciliation['source']['orphans']), 'false parity claim'
    counts = {name: {table: len(reconciliation[name][table]) for table in ('organizations', 'people')}
              for name in ('source', 'target')}
    assert reconciliation['counts'] == counts
    assert reconciliation['same_counts'] == (counts['source'] == counts['target'])
    changes = body['changes']
    assert all(row['native_origin'] is True for row in changes), 'non-native inbox change'
    assert [row['seq'] for row in changes] == sorted({row['seq'] for row in changes}), 'inbox identity conflict'
    state = body['state']
    cursor, chain, covered, native_floor = 0, state['binding']['snapshot_seal'], set(), '0/0'
    for receipt in body['receipts']:
        rows = [row for row in changes if row['apply_xid'] == receipt['apply_xid']]
        assert rows and rows[0]['seq'] > cursor, 'overlapping receipt'
        assert not {row['seq'] for row in rows}.intersection(covered)
        covered.update(row['seq'] for row in rows)
        assert receipt['input_hash'] == digest(rows), 'receipt content mismatch'
        assert receipt['row_count'] == len(rows)
        assert receipt['first_seq'] == rows[0]['seq'] and receipt['last_seq'] == rows[-1]['seq']
        assert receipt['previous_chain'] == chain
        chain = digest({'previous': chain, 'input': digest(rows), 'end': rows[-1]['seq']})
        assert receipt['chain'] == chain
        assert receipt['result']['implementation'] == implementation
        assert _lsn(receipt['result']['native_origin_lsn']) >= _lsn(native_floor)
        native_floor = receipt['result']['native_origin_lsn']
        cursor = rows[-1]['seq']
    assert state['cursor'] == cursor and state['chain'] == chain and state['native_floor'] == native_floor, 'durable checkpoint mismatch'
    assert covered == {row['seq'] for row in changes if row['seq'] <= cursor}, 'skipped native change'
    assert body['positions']['slot']['plugin'] == 'pgoutput'
    assert body['positions']['slot']['wal_status'] in ('reserved', 'extended')
    return equal


def check_case(name, record, implementation):
    if not __debug__:
        raise RuntimeError("optimized_python_cannot_validate_native_evidence")
    assert name in EXPECTED
    assert record['scenario'] == name
    settings = record['postgresql']
    assert settings['wal_level'] == 'logical' and settings['fsync'] == 'on'
    assert settings['synchronous_commit'] == 'on' and 170000 <= int(settings['server_version_num']) < 180000
    clean = record['cleanup']
    assert not clean['errors'] and all(clean[key] == 0 for key in
                                       ('databases_remaining', 'slots_remaining', 'roles_remaining',
                                        'subscriptions_remaining', 'origins_remaining'))
    body = record['observations']
    snapshot = body['snapshot']
    assert snapshot['plugin'] == 'pgoutput'
    assert digest({'tables': snapshot['tables'], 'consistent_lsn': snapshot['consistent_lsn'],
                   'snapshot_name': snapshot['exported_snapshot']}) == snapshot['snapshot_seal']
    assert snapshot['table_counts'] == {table: len(rows) for table, rows in snapshot['tables'].items()}
    observed = []
    def visit(value):
        if isinstance(value, dict):
            if {'state', 'changes', 'receipts', 'reconciliation', 'positions'} <= value.keys():
                observation(value, implementation)
                observed.append(value)
            else:
                for child in value.values():
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(body)
    assert observed, 'no independent SQL observations'
    if name in BLOCKED:
        assert body['blocked']['exit_code'] == 2 and body['blocked']['result']['reason'] == BLOCKED[name]
        before, after = body['before']['state'], body['after']
        if name == 'unusable_checkpoint':
            assert after['cursor'] == before['cursor'] + 100 and after['chain'] == before['chain']
        else:
            assert after == before, 'blocked checkpoint advanced'
        if name == 'missing_slot':
            assert body['source_slot_absent']
        if name == 'schema_incompatibility':
            assert body['native_apply_disabled_on_error'] and 'unsupported' in body['source_columns_after']
        if name == 'publication_changed':
            assert body['publication_tables_after'] == ['barriers', 'organizations']
        outcome = 'blocked_as_expected_no_automatic_rebootstrap'
    else:
        assert body['final']['reconciliation']['passed']
        assert body['final']['state']['cursor'] == max(row['seq'] for row in body['final']['changes'])
        outcome = 'reconciled'
    if name == 'snapshot_handoff':
        baseline = {row['person_id']: row for row in snapshot['tables']['people']}
        final = {row['id']: row for row in body['final']['reconciliation']['source']['people']}
        assert baseline['person0000']['lead_score'] == 0 and final['person0000']['score'] == 91
        assert 'person0005' in baseline and 'person0005' not in final
        assert 'person_new' not in baseline and 'person_new' in final
        mixed = [row for row in body['final']['changes'] if row['new_row'] and
                 (row['new_row'].get('lead_score') == 91 or
                  row['new_row'].get('display_name') == 'Updated fictional org')]
        assert len(mixed) == 2 and len({row['apply_xid'] for row in mixed}) == 1
    if name.startswith('crash_'):
        crash = body['interruption']
        assert crash['actual_exit_code'] != 0
        if name == 'crash_before_commit':
            assert crash['before']['state'] == crash['paused']['state'] == crash['after_kill']['state']
            assert crash['before']['reconciliation']['target'] == crash['after_kill']['reconciliation']['target']
        else:
            assert crash['paused']['state']['cursor'] > crash['before']['state']['cursor']
            assert crash['paused']['state'] == crash['after_kill']['state']
        assert body['before_replay']['state'] == body['after_replay']['state']
        assert body['before_replay']['receipts'] == body['after_replay']['receipts']
        assert body['replay']['result']['status'] == 'duplicate'
    if name in ('mapping_upgrade', 'target_rejection'):
        assert body['before']['state'] == body['after_rejection']['state']
        assert body['before']['reconciliation']['target'] == body['after_rejection']['reconciliation']['target']
    if name == 'native_disconnect':
        assert body['before']['state'] == body['during']
        assert body['blocked']['result']['reason'] == 'native_stream_disconnected'
        assert body['retained_wal']['retained_bytes'] > 0
    if name == 'semantic_cutover':
        assert not body['unfenced']['passed']
        assert body['equal_count_corruption']['reconciliation']['same_counts']
        assert not body['equal_count_corruption']['reconciliation']['passed']
        assert any(row['field'] == 'subscribed' for row in body['equal_count_corruption']['reconciliation']['differences'])
        assert body['stale_proof_rejected'] == 'stale_or_unregistered_native_proof'
        assert body['fresh_semantic_proof_rejected'] == 'native_barrier_position_and_semantics_required'
        assert body['phase_after_semantic_block'] == 'catchup'
        proof = body['repaired']
        assert proof['passed'] and all(proof['checks'].values())
        assert _lsn(proof['native_origin_lsn']) >= _lsn(proof['snapshot_consistent_lsn'])
        assert body['final']['state']['phase'] == 'modern_active'
        assert body['source_write_denied_sqlstate'] == 'P0001'
    if name == 'restricted_identities':
        assert len(body['denials']) == 5 and all(row['sqlstate'] == '42501' for row in body['denials'])
    if name == 'fence_drains_writer':
        assert body['database_lock_wait_observed'] and body['assessment']['passed']
    return {'scenario': name, 'outcome': outcome, 'independent_sql_observations': len(observed)}


def check_directory(path, *, root=None):
    if not __debug__:
        raise RuntimeError("optimized_python_cannot_validate_native_evidence")
    path = Path(path)
    manifest = json.loads((path / 'manifest.json').read_text(encoding='utf-8'))
    for name, expected in manifest['files'].items():
        item = (path / name).resolve()
        assert item.is_relative_to(path.resolve()), 'unsafe evidence path'
        assert hashlib.sha256(item.read_bytes()).hexdigest() == expected, 'evidence digest mismatch'
    actual = {item.relative_to(path).as_posix() for item in path.rglob('*')
              if item.is_file() and item.relative_to(path).as_posix() != 'manifest.json'}
    assert set(manifest['files']) == actual, 'unmanifested evidence file'
    protocol = json.loads((path / 'protocol.json').read_text(encoding='utf-8'))
    assert protocol['qualified_source'] and protocol['capture'] == 'pgoutput_native_subscription'
    if root:
        for name, expected in protocol['source_sha256'].items():
            item = (Path(root) / name).resolve()
            assert item.is_relative_to(Path(root).resolve())
            assert hashlib.sha256(item.read_bytes().replace(b'\r\n', b'\n')).hexdigest() == expected, name
    cases = {item.stem.removesuffix('.json'): item for item in (path / 'cases').glob('*.json.gz')}
    assert cases.keys() == EXPECTED, 'incomplete scenario set'
    results = [check_case(name, json.loads(gzip.decompress(cases[name].read_bytes())), protocol['implementation'])
               for name in sorted(EXPECTED)]
    if (path / 'results.json').exists():
        saved = json.loads((path / 'results.json').read_text(encoding='utf-8'))
        assert saved['qualification'] == 'passed' and saved['scenarios'] == results
    return results
