"""Actual PostgreSQL failure scenarios shared by tests and recorded acceptance."""
import threading
import time

import psycopg2
from psycopg2 import sql

from migration_lab.contracts import Refused
from .cutover import activate, assess, freeze, resume_native, stop_native
from .db import connection, execute, query
from .processes import capture, drain, kill_at, observe, stamp, worker


def change(lab, score=87):
    return lab.source_write([
        ('UPDATE legacy.organizations SET display_name=%s WHERE org_id=%s', ('Updated fictional org', 'org0')),
        ('UPDATE legacy.people SET lead_score=%s WHERE person_id=%s', (score, 'person0000')),
    ])


def handoff(lab):
    events, flags, threads = [], set(), []
    def hook(stage):
        if stage in flags:
            return
        flags.add(stage)
        if stage == 'before_slot':
            c = psycopg2.connect(lab.config['writer'])
            with c.cursor() as q:
                q.execute('UPDATE legacy.people SET lead_score=69 WHERE person_id=%s', ('person0002',))
            events.append({'event': 'source_transaction_open_during_slot_acquisition', 'at': stamp()})
            def commit():
                try:
                    time.sleep(0.2)
                    c.commit()
                    events.append({'event': 'acquisition_writer_committed', 'at': stamp()})
                finally:
                    c.close()
            thread = threading.Thread(target=commit)
            thread.start()
            threads.append(thread)
        elif stage == 'slot_exported':
            events.append({'event': stage, 'at': stamp(), 'write': change(lab, 91)})
        elif stage == 'page_read':
            trace = lab.source_write([
                ('DELETE FROM legacy.people WHERE person_id=%s', ('person0005',)),
                ('INSERT INTO legacy.people VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                 ('person_new', 'org2', 'Synthetic new person', 'Customer', 77, 'email;sms', 0, 'fr-CA', 2)),
            ])
            events.append({'event': 'concurrent_transfer_delete_insert', 'at': stamp(), 'write': trace})
    snapshot = lab.snapshot(hook)
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert next(p for p in snapshot['tables']['people'] if p['person_id'] == 'person0000')['lead_score'] == 0
    marker = capture(lab)
    results = drain(lab)
    final = observe(lab)
    assert final['reconciliation']['passed']
    mixed = [row for row in final['changes'] if row['new_row'] and
             (row['new_row'].get('lead_score') == 91 or row['new_row'].get('display_name') == 'Updated fictional org')]
    assert len(mixed) == 2 and len({row['apply_xid'] for row in mixed}) == 1
    return {'snapshot': snapshot, 'events': events, 'marker': marker, 'applications': results, 'final': final}


def crash(lab, point):
    snapshot = lab.snapshot()
    trace = change(lab)
    capture(lab)
    interruption = kill_at(lab, point)
    if point == 'before_commit':
        assert interruption['before']['state'] == interruption['paused']['state'] == interruption['after_kill']['state']
        assert interruption['before']['reconciliation']['target'] == interruption['after_kill']['reconciliation']['target']
        assert interruption['recovered']['result']['status'] == 'applied'
        group = interruption['recovered']['result']['receipt']['apply_xid']
    else:
        assert interruption['paused']['state']['cursor'] > interruption['before']['state']['cursor']
        assert interruption['after_kill']['state'] == interruption['paused']['state']
        group = interruption['paused']['receipts'][-1]['apply_xid']
    before_replay = observe(lab)
    replay = worker(lab, replay=group)
    after_replay = observe(lab)
    assert replay['exit_code'] == 0 and replay['result']['status'] == 'duplicate'
    assert before_replay['state'] == after_replay['state'] and before_replay['receipts'] == after_replay['receipts']
    drain(lab)
    final = observe(lab)
    assert final['reconciliation']['passed']
    return {'snapshot': snapshot, 'write': trace, 'interruption': interruption,
            'replay': replay, 'before_replay': before_replay, 'after_replay': after_replay, 'final': final}


def disconnect(lab):
    snapshot = lab.snapshot()
    capture(lab)
    drain(lab)
    before = observe(lab)
    with connection(lab.admin, autocommit=True) as c:
        execute(c, sql.SQL('ALTER ROLE {} NOLOGIN').format(sql.Identifier(lab.roles['repl'])))
        pid = query(c, 'SELECT active_pid FROM pg_replication_slots WHERE slot_name=%s', (lab.prefix,), one=True)['active_pid']
        query(c, 'SELECT pg_terminate_backend(%s)', (pid,))
        lab.wait(lambda: not query(c, 'SELECT 1 FROM pg_replication_slots WHERE slot_name=%s AND active', (lab.prefix,)))
    try:
        trace = change(lab)
        blocked = worker(lab)
        assert blocked['exit_code'] == 2 and blocked['result']['reason'] == 'native_stream_disconnected'
        with connection(lab.config['target_read']) as c:
            during = dict(query(c, 'SELECT * FROM target_control.state WHERE singleton', one=True))
        with connection(lab.config['source']) as c:
            retained = dict(query(c, '''SELECT restart_lsn::text,confirmed_flush_lsn::text,wal_status,
              pg_wal_lsn_diff(pg_current_wal_lsn(),restart_lsn)::bigint AS retained_bytes
              FROM pg_replication_slots WHERE slot_name=%s''', (lab.prefix,), one=True))
        assert during == before['state']
    finally:
        with connection(lab.admin, autocommit=True) as c:
            execute(c, sql.SQL('ALTER ROLE {} LOGIN').format(sql.Identifier(lab.roles['repl'])))
    resume_native(lab.config)
    capture(lab)
    results = drain(lab)
    final = observe(lab)
    assert final['reconciliation']['passed']
    return {'snapshot': snapshot, 'before': before, 'disconnected_walsender_pid': pid,
            'write_during_disconnect': trace, 'blocked': blocked, 'during': during,
            'retained_wal': retained, 'applications': results, 'final': final}


def mapping_upgrade(lab):
    snapshot = lab.snapshot(mapping=1)
    lab.source_write([('UPDATE legacy.people SET schema_version=2,locale=%s WHERE person_id=%s',
                       ('fr-CA', 'person0001'))])
    capture(lab)
    before = observe(lab)
    blocked = worker(lab)
    after = observe(lab)
    assert blocked['result']['reason'] == 'mapping_upgrade_required' and before['state'] == after['state']
    with connection(lab.config['target_admin']) as c:
        execute(c, 'UPDATE target_control.state SET mapping=2 WHERE singleton')
    applications = drain(lab)
    final = observe(lab)
    assert final['reconciliation']['passed']
    return {'snapshot': snapshot, 'before': before, 'blocked': blocked, 'after_rejection': after,
            'explicit_supported_mapping_upgrade': 2, 'applications': applications, 'final': final}


def target_rejection(lab):
    snapshot = lab.snapshot()
    with connection(lab.config['target_admin']) as c:
        execute(c, 'INSERT INTO modern.companies VALUES(%s,%s)', ('collision', 'Deliberate target conflict'))
    lab.source_write([
        ('UPDATE legacy.people SET lead_score=88 WHERE person_id=%s', ('person0000',)),
        ('INSERT INTO legacy.organizations VALUES(%s,%s)', ('collision', 'Source authorized company')),
    ])
    capture(lab)
    before = observe(lab)
    blocked = worker(lab)
    after = observe(lab)
    assert blocked['exit_code'] == 3 and blocked['result']['sqlstate'] == '23505'
    assert before['state'] == after['state'] and before['reconciliation']['target'] == after['reconciliation']['target']
    with connection(lab.config['target_admin']) as c:
        execute(c, 'DELETE FROM modern.companies WHERE company_key=%s', ('collision',))
    applications = drain(lab)
    final = observe(lab)
    assert final['reconciliation']['passed']
    return {'snapshot': snapshot, 'before': before, 'blocked': blocked, 'after_rejection': after,
            'repair': 'Removed only the deliberately injected target conflict; replayed the same native group',
            'applications': applications, 'final': final}


def cutover(lab):
    snapshot = lab.snapshot()
    capture(lab)
    drain(lab)
    unfenced = assess(lab.config)
    assert not unfenced['passed']
    fence = freeze(lab.config)
    stop_native(lab, fence['barrier'])
    drain(lab)
    valid = assess(lab.config)
    assert valid['passed']
    with connection(lab.config['target_admin']) as c:
        execute(c, "UPDATE modern.contacts SET subscription_state='no' WHERE contact_key='person0000'")
    stale = None
    try:
        activate(lab.config, valid['seal'])
    except Refused as exc:
        stale = str(exc)
    corrupted = assess(lab.config)
    assert stale == 'stale_or_unregistered_native_proof'
    assert not corrupted['passed'] and corrupted['reconciliation']['same_counts']
    assert any(d['field'] == 'subscribed' for d in corrupted['reconciliation']['differences'])
    semantic_block = None
    try:
        activate(lab.config, corrupted['seal'])
    except Refused as exc:
        semantic_block = str(exc)
    assert semantic_block == 'native_barrier_position_and_semantics_required'
    with connection(lab.config['target_read']) as c:
        phase_after_block = query(c, 'SELECT phase FROM target_control.state WHERE singleton', one=True)['phase']
    assert phase_after_block == 'catchup'
    with connection(lab.config['target_admin']) as c:
        execute(c, "UPDATE modern.contacts SET subscription_state='unknown' WHERE contact_key='person0000'")
    repaired = assess(lab.config)
    activation = activate(lab.config, repaired['seal'])
    write_block = None
    try:
        change(lab)
    except psycopg2.Error as exc:
        write_block = exc.pgcode
    assert write_block == 'P0001'
    final = observe(lab)
    assert final['reconciliation']['passed']
    return {'snapshot': snapshot, 'unfenced': unfenced, 'fence': fence, 'valid': valid,
            'stale_proof_rejected': stale, 'equal_count_corruption': corrupted,
            'fresh_semantic_proof_rejected': semantic_block, 'phase_after_semantic_block': phase_after_block,
            'repair': 'Restored only the deliberately corrupted synthetic subscription before obtaining fresh proof',
            'repaired': repaired, 'activation': activation, 'source_write_denied_sqlstate': write_block, 'final': final}


def continuity(lab):
    snapshot = lab.snapshot()
    capture(lab)
    drain(lab)
    before = observe(lab)
    with connection(lab.config['target_admin'], autocommit=True) as c:
        execute(c, 'ALTER SUBSCRIPTION migration_sub DISABLE')
        lab.wait(lambda: not query(c, 'SELECT pid FROM pg_stat_subscription WHERE pid IS NOT NULL AND subname=%s',
                                   ('migration_sub',)))
    with connection(lab.config['source_admin'], autocommit=True) as c:
        query(c, 'SELECT pg_drop_replication_slot(%s)', (lab.prefix,))
    with connection(lab.config['source']) as source:
        absent = not query(source, 'SELECT 1 FROM pg_replication_slots WHERE slot_name=%s', (lab.prefix,))
    blocked = worker(lab)
    with connection(lab.config['target_read']) as c:
        after = dict(query(c, 'SELECT * FROM target_control.state WHERE singleton', one=True))
    assert absent and blocked['result']['reason'] == 'replication_slot_missing_or_mismatched' and after == before['state']
    return {'snapshot': snapshot, 'before': before, 'blocked': blocked, 'after': after,
            'source_slot_absent': absent,
            'recovery_required': 'Fresh exported snapshot and a new run/slot identity; no automatic slot recreation'}


def checkpoint_corruption(lab):
    snapshot = lab.snapshot()
    capture(lab)
    drain(lab)
    before = observe(lab)
    with connection(lab.config['target_admin']) as c:
        execute(c, 'UPDATE target_control.state SET cursor=cursor+100 WHERE singleton')
    blocked = worker(lab)
    with connection(lab.config['target_read']) as c:
        after = dict(query(c, 'SELECT * FROM target_control.state WHERE singleton', one=True))
    assert blocked['result']['reason'] == 'unusable_checkpoint' and after['cursor'] == before['state']['cursor'] + 100
    return {'snapshot': snapshot, 'before': before, 'blocked': blocked, 'after': after,
            'recovery_required': 'Restore a trusted checkpoint/receipt set or rebootstrap; never skip the inbox'}


def schema_change(lab):
    snapshot = lab.snapshot()
    capture(lab)
    drain(lab)
    before = observe(lab)
    with connection(lab.config['source_admin']) as c:
        execute(c, 'ALTER TABLE legacy.people ADD COLUMN unsupported text')
    lab.source_write([('UPDATE legacy.people SET unsupported=%s WHERE person_id=%s', ('new meaning', 'person0000'))])
    blocked = worker(lab)
    with connection(lab.config['target_read']) as c:
        after = dict(query(c, 'SELECT * FROM target_control.state WHERE singleton', one=True))
    assert blocked['result']['reason'] == 'source_schema_changed' and before['state'] == after
    def disabled():
        with connection(lab.config['target_read']) as c:
            return not query(c, 'SELECT target_control.native_status() AS value', one=True)['value']['enabled']
    lab.wait(disabled)
    with connection(lab.config['source']) as c:
        columns = [row['column_name'] for row in query(c, "SELECT column_name FROM information_schema.columns WHERE table_schema='legacy' AND table_name='people' ORDER BY ordinal_position")]
    return {'snapshot': snapshot, 'before': before, 'blocked': blocked, 'after': after,
            'native_apply_disabled_on_error': True, 'source_columns_after': columns,
            'recovery_required': 'Coordinated schema/mapping upgrade and verified restart, or fresh bootstrap'}


def publication_change(lab):
    snapshot = lab.snapshot()
    capture(lab)
    drain(lab)
    before = observe(lab)
    with connection(lab.config['source_admin']) as c:
        execute(c, 'ALTER PUBLICATION migration_pub DROP TABLE legacy.people')
    blocked = worker(lab)
    with connection(lab.config['target_read']) as c:
        after = dict(query(c, 'SELECT * FROM target_control.state WHERE singleton', one=True))
    assert blocked['result']['reason'] == 'publication_changed' and before['state'] == after
    with connection(lab.config['source']) as c:
        tables = [row['tablename'] for row in query(c, "SELECT tablename FROM pg_publication_tables WHERE pubname='migration_pub' ORDER BY tablename")]
    return {'snapshot': snapshot, 'before': before, 'blocked': blocked, 'after': after, 'publication_tables_after': tables}


def permissions(lab):
    snapshot = lab.snapshot()
    capture(lab)
    drain(lab)
    attempts = [
        ('observer_cannot_drop_slot', lab.config['source'], 'SELECT pg_drop_replication_slot(%s)', (lab.prefix,)),
        ('writer_cannot_unfence', lab.config['writer'], 'UPDATE source_control.gate SET fenced=false', None),
        ('mapper_cannot_write_landing', lab.config['target'], "UPDATE legacy.people SET lead_score=99", None),
        ('mapper_cannot_change_route', lab.config['target'], "UPDATE target_control.state SET phase='modern_active'", None),
        ('observer_cannot_change_target', lab.config['target_read'], "DELETE FROM modern.contacts", None),
    ]
    denials = []
    for name, dsn, statement, parameters in attempts:
        try:
            with connection(dsn) as c:
                execute(c, statement, parameters)
        except psycopg2.Error as exc:
            denials.append({'check': name, 'sqlstate': exc.pgcode})
        else:
            raise AssertionError('expected permission denial: ' + name)
    assert all(item['sqlstate'] == '42501' for item in denials)
    return {'snapshot': snapshot, 'denials': denials, 'final': observe(lab)}


def fence_drains_writer(lab):
    snapshot = lab.snapshot()
    capture(lab)
    drain(lab)
    pending = psycopg2.connect(lab.config['writer'])
    with pending.cursor() as q:
        q.execute("UPDATE legacy.organizations SET display_name='In-flight synthetic change' WHERE org_id='org0'")
    result, errors = [], []
    begin = time.monotonic()
    def controller():
        try:
            result.append(freeze(lab.config))
        except BaseException as exc:
            errors.append(type(exc).__name__)
    thread = threading.Thread(target=controller)
    thread.start()
    try:
        def waiting():
            with connection(lab.config['source_admin']) as c:
                return query(c, """SELECT count(*) AS n FROM pg_stat_activity WHERE datname=current_database()
                  AND wait_event_type='Lock' AND query LIKE 'SELECT %%source_control.gate%%FOR UPDATE'""",
                             one=True)['n'] > 0
        lab.wait(waiting)
        assert not result and thread.is_alive()
        pending.commit()
    finally:
        pending.close()
        thread.join(timeout=10)
    assert not errors and len(result) == 1
    elapsed = time.monotonic() - begin
    stop_native(lab, result[0]['barrier'])
    drain(lab)
    assessment = assess(lab.config)
    assert assessment['passed']
    return {'snapshot': snapshot, 'database_lock_wait_observed': True, 'freeze_elapsed_seconds': elapsed,
            'fence': result[0], 'assessment': assessment, 'final': observe(lab)}


SCENARIOS = {
    'snapshot_handoff': handoff,
    'crash_before_commit': lambda lab: crash(lab, 'before_commit'),
    'crash_after_commit_before_ack': lambda lab: crash(lab, 'after_commit'),
    'native_disconnect': disconnect,
    'mapping_upgrade': mapping_upgrade,
    'target_rejection': target_rejection,
    'semantic_cutover': cutover,
    'missing_slot': continuity,
    'unusable_checkpoint': checkpoint_corruption,
    'schema_incompatibility': schema_change,
    'publication_changed': publication_change,
    'restricted_identities': permissions,
    'fence_drains_writer': fence_drains_writer,
}
