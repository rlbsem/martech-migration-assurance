"""Explicit source fence, native barrier, and fresh semantic cutover gate."""
from uuid import uuid4

from psycopg2.extras import Json

from migration_lab.contracts import Refused, digest
from .audit import compare
from .consumer import checkpoint
from .db import connection, execute, health, implementation, lsn, query


def freeze(config):
    barrier = str(uuid4())
    with connection(config['source_admin']) as c:
        gate = query(c, 'SELECT * FROM source_control.gate WHERE singleton FOR UPDATE', one=True)
        if gate['fenced']:
            raise Refused('source_already_fenced')
        execute(c, 'UPDATE source_control.gate SET fenced=true,epoch=epoch+1 WHERE singleton')
        execute(c, 'INSERT INTO legacy.barriers VALUES(%s,%s,pg_current_xact_id()::text)', (barrier, gate['epoch'] + 1))
    return {'barrier': barrier, 'epoch': gate['epoch'] + 1}


def stop_native(lab, barrier):
    def received():
        with connection(lab.config['target_admin']) as c:
            return query(c, 'SELECT 1 FROM legacy.barriers WHERE id=%s', (barrier,), one=True)
    lab.wait(received)
    with connection(lab.config['target_admin'], autocommit=True) as c:
        execute(c, 'ALTER SUBSCRIPTION migration_sub DISABLE')
        lab.wait(lambda: not query(c, 'SELECT pid FROM pg_stat_subscription WHERE subname=%s AND pid IS NOT NULL',
                                   ('migration_sub',)))


def proof(source, target, config):
    gate = query(source, 'SELECT * FROM source_control.gate WHERE singleton FOR UPDATE', one=True)
    state, positions = health(config, target)
    checkpoint(target, state)
    maximum = query(target, 'SELECT coalesce(max(seq),0) AS seq FROM inbox.changes', one=True)['seq']
    barrier_source = query(source, 'SELECT * FROM legacy.barriers WHERE epoch=%s', (gate['epoch'],), one=True)
    barrier_target = query(target, 'SELECT * FROM legacy.barriers WHERE id=%s', (state['barrier'],), one=True)
    checks = {
        'source_fenced': gate['fenced'],
        'native_apply_stopped': not positions['native']['enabled'],
        'barrier_matched': bool(barrier_source and barrier_target and dict(barrier_source) == dict(barrier_target)
                                and str(barrier_source['id']) == str(state['barrier'])),
        'native_position_matched': state['native_floor'] == positions['native']['remote_lsn'],
        'all_native_groups_mapped': maximum == state['cursor'],
        'native_position_after_snapshot': lsn(positions['native']['remote_lsn']) >= lsn(state['binding']['consistent_lsn']),
    }
    reconciliation = compare(source, target)
    checks['semantic_parity'] = reconciliation['passed']
    return {'passed': all(checks.values()), 'checks': checks, 'epoch': gate['epoch'],
            'barrier': str(state['barrier']),
            'barrier_source_xid': barrier_source['source_xid'] if barrier_source else None, 'semantic_cursor': state['cursor'],
            'native_origin_lsn': positions['native']['remote_lsn'],
            'confirmed_flush_lsn': positions['slot']['confirmed_flush_lsn'],
            'snapshot_consistent_lsn': state['binding']['consistent_lsn'], 'mapping': state['mapping'],
            'chain': state['chain'], 'implementation': implementation(), 'reconciliation': reconciliation}


def assess(config):
    with connection(config['source_admin']) as source, connection(config['target_admin']) as target:
        query(target, 'SELECT * FROM target_control.state WHERE singleton FOR UPDATE', one=True)
        execute(target, 'LOCK TABLE modern.companies,modern.contacts IN SHARE MODE')
        body = proof(source, target, config)
        seal = digest(body)
        execute(target, 'INSERT INTO target_control.reports VALUES(%s,%s) ON CONFLICT DO NOTHING', (seal, Json(body)))
    return {'seal': seal, **body}


def activate(config, seal):
    with connection(config['source_admin']) as source, connection(config['target_admin']) as target:
        state = query(target, 'SELECT * FROM target_control.state WHERE singleton FOR UPDATE', one=True)
        if state['phase'] != 'catchup':
            raise Refused('native_cutover_phase')
        execute(target, 'LOCK TABLE modern.companies,modern.contacts IN SHARE MODE')
        saved = query(target, 'SELECT body FROM target_control.reports WHERE seal=%s', (seal,), one=True)
        body = proof(source, target, config)
        if not saved or digest(saved['body']) != seal or digest(body) != seal:
            raise Refused('stale_or_unregistered_native_proof')
        if not body['passed']:
            raise Refused('native_barrier_position_and_semantics_required')
        execute(target, "UPDATE target_control.state SET phase='modern_active' WHERE singleton")
    return {'phase': 'modern_active', 'source_remains_fenced': True, 'seal': seal}


def resume_native(config):
    with connection(config['target_admin'], autocommit=True) as target:
        state, _ = health(config, target, allow_stopped=True)
        if state['phase'] != 'catchup':
            raise Refused('native_mapping_sealed_or_snapshot_missing')
        checkpoint(target, state)
        execute(target, 'ALTER SUBSCRIPTION migration_sub ENABLE')
