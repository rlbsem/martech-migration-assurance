"""Bounded SQL connections, catalog contracts and health checks for the native lane."""
from contextlib import contextmanager
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

from migration_lab.contracts import Refused, digest


@contextmanager
def connection(dsn, *, autocommit=False):
    c = psycopg2.connect(dsn, connect_timeout=3, cursor_factory=RealDictCursor,
                         options='-c statement_timeout=10000 -c lock_timeout=5000')
    c.autocommit = autocommit
    try:
        yield c
        if not autocommit:
            c.commit()
    except BaseException:
        if not autocommit:
            c.rollback()
        raise
    finally:
        c.close()


def query(c, statement, parameters=None, *, one=False):
    with c.cursor() as q:
        q.execute(statement, parameters)
        return q.fetchone() if one else q.fetchall()


def execute(c, statement, parameters=None):
    with c.cursor() as q:
        q.execute(statement, parameters)
        return q.rowcount


def script(c, name):
    execute(c, Path(__file__).with_name(name).read_text(encoding='utf-8'))


def schema(c, name):
    columns = query(c, """SELECT t.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod) AS type,
      a.attnotnull,t.relreplident FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
      JOIN pg_attribute a ON a.attrelid=t.oid WHERE n.nspname=%s AND t.relkind='r'
      AND a.attnum>0 AND NOT a.attisdropped ORDER BY t.relname,a.attnum""", (name,))
    constraints = query(c, """SELECT t.relname,p.conname,pg_get_constraintdef(p.oid) AS definition
      FROM pg_constraint p JOIN pg_class t ON t.oid=p.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace
      WHERE n.nspname=%s ORDER BY t.relname,p.conname""", (name,))
    return digest({'columns': columns, 'constraints': constraints})


def publication(c):
    properties = query(c, """SELECT oid,puballtables,pubinsert,pubupdate,pubdelete,pubtruncate,pubviaroot
      FROM pg_publication WHERE pubname='migration_pub'""")
    tables = query(c, """SELECT schemaname,tablename,attnames,rowfilter FROM pg_publication_tables
      WHERE pubname='migration_pub' ORDER BY schemaname,tablename""")
    return digest({'properties': properties, 'tables': tables})


def implementation():
    paths = list(Path(__file__).parent.glob('*.py')) + list(Path(__file__).parent.glob('*.sql'))
    paths.append(Path(__file__).parents[1] / 'contracts.py')
    return digest({p.name: p.read_text(encoding='utf-8') for p in sorted(paths)})


def lsn(value):
    upper, lower = value.split('/')
    return (int(upper, 16) << 32) + int(lower, 16)


def health(config, target, *, allow_stopped=False):
    state = query(target, 'SELECT * FROM target_control.state WHERE singleton', one=True)
    if not state:
        raise Refused('snapshot_required')
    binding = state['binding']
    if schema(target, 'legacy') != binding['legacy_schema'] or schema(target, 'modern') != binding['modern_schema']:
        raise Refused('target_schema_changed')
    try:
        with connection(config['source']) as source:
            identity = query(source, 'SELECT source_control.identity() AS value', one=True)['value']
            gate = query(source, 'SELECT source_control.gate_status() AS value', one=True)['value']
            if digest(identity) != binding['source_identity']:
                raise Refused('source_identity_changed')
            if schema(source, 'legacy') != binding['legacy_schema']:
                raise Refused('source_schema_changed')
            if publication(source) != binding['publication']:
                raise Refused('publication_changed')
            slot = query(source, """SELECT slot_name,plugin,database,wal_status,invalidation_reason,
              restart_lsn::text,confirmed_flush_lsn::text,active,
              pg_wal_lsn_diff(pg_current_wal_lsn(),restart_lsn)::bigint AS retained_bytes,
              pg_wal_lsn_diff(pg_current_wal_lsn(),confirmed_flush_lsn)::bigint AS lag_bytes
              FROM pg_replication_slots WHERE slot_name=%s""", (binding['slot'],), one=True)
            if not slot or slot['plugin'] != 'pgoutput' or slot['database'] != identity['database']:
                raise Refused('replication_slot_missing_or_mismatched')
            if slot['wal_status'] not in ('reserved', 'extended') or slot['invalidation_reason']:
                raise Refused('wal_continuity_lost_rebootstrap_required')
            if lsn(slot['confirmed_flush_lsn']) < lsn(binding['consistent_lsn']):
                raise Refused('slot_position_regressed')
    except psycopg2.Error as exc:
        raise Refused('source_unavailable') from exc
    native = query(target, 'SELECT target_control.native_status() AS value', one=True)['value']
    if not native or native['slot'] != binding['slot'] or native['publications'] != ['migration_pub']:
        raise Refused('subscription_identity_changed')
    if str(native['subscription_oid']) != str(binding['subscription_oid']):
        raise Refused('subscription_replaced')
    if not allow_stopped and ((native['enabled'] and not slot['active']) or (not native['enabled'] and not gate['fenced'])):
        raise Refused('native_stream_disconnected')
    if lsn(native['remote_lsn']) < lsn(state['native_floor']):
        raise Refused('native_origin_regressed')
    if state['cursor'] and native['remote_lsn'] == '0/0':
        raise Refused('native_origin_lost')
    if native['synchronous_commit'] != 'on' or native['two_phase'] != 'd' or not native['relations_ready']:
        raise Refused('native_apply_contract_changed')
    if native['streaming'] != 'f':
        raise Refused('parallel_or_streaming_apply_unsupported')
    return state, {'slot': dict(slot), 'native': native}
