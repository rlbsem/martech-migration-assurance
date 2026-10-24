"""Disposable local native-replication setup and exported-snapshot handoff."""
import secrets
import time
from pathlib import Path
from uuid import uuid4

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import make_dsn, parse_dsn
from psycopg2.extras import Json, LogicalReplicationConnection

from migration_lab.contracts import Refused, canonical, digest, encode_person
from migration_lab.fixtures import person
from .consumer import map_row
from .db import connection, execute, publication, query, schema, script


class Lab:
    def __init__(self, admin, workspace, count=120, publisher=None):
        if parse_dsn(admin).get('host') not in ('127.0.0.1', 'localhost', '::1', 'postgres'):
            raise Refused('isolated_local_postgres_only')
        self.admin, self.workspace = admin, Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=False)
        self.prefix = 'mcdc_' + uuid4().hex[:12]
        self.source_name, self.target_name = self.prefix + '_source', self.prefix + '_target'
        self.roles = {name: self.prefix + '_' + name for name in ('writer', 'mapper', 'repl', 'observer')}
        passwords = {name: secrets.token_urlsafe(24) for name in self.roles}
        self.config = {
            'source_admin': make_dsn(admin, dbname=self.source_name),
            'target_admin': make_dsn(admin, dbname=self.target_name),
            'source': make_dsn(admin, dbname=self.source_name, user=self.roles['observer'], password=passwords['observer']),
            'replication': make_dsn(admin, dbname=self.source_name, user=self.roles['repl'], password=passwords['repl']),
            'target': make_dsn(admin, dbname=self.target_name, user=self.roles['mapper'], password=passwords['mapper']),
            'target_read': make_dsn(admin, dbname=self.target_name, user=self.roles['observer'], password=passwords['observer']),
            'writer': make_dsn(admin, dbname=self.source_name, user=self.roles['writer'], password=passwords['writer']),
            'publisher': make_dsn(publisher or admin, dbname=self.source_name,
                                  user=self.roles['repl'], password=passwords['repl']),
        }
        self.count, self.passwords, self.created_databases, self.created_roles = count, passwords, [], []
        self.cleanup_record = None
        self.subscription_oid = None
        self.config_path = self.workspace / 'private-config.json'
        self.config_path.write_text(canonical({key: self.config[key] for key in ('source', 'target')}), encoding='utf-8')

    def __enter__(self):
        try:
            with connection(self.admin, autocommit=True) as c:
                settings = {name: query(c, 'SHOW ' + name, one=True)[name] for name in
                            ('wal_level', 'fsync', 'synchronous_commit', 'max_replication_slots', 'max_wal_senders', 'max_logical_replication_workers',
                             'max_worker_processes', 'max_slot_wal_keep_size', 'server_version', 'server_version_num')}
                if not 170000 <= int(settings['server_version_num']) < 180000:
                    raise Refused('postgresql_17_required')
                if settings['wal_level'] != 'logical' or settings['fsync'] != 'on' or settings['synchronous_commit'] != 'on':
                    raise Refused('logical_replication_and_durable_commit_required')
                self.settings = settings
                for name, role in self.roles.items():
                    execute(c, sql.SQL('CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE {} PASSWORD {}')
                            .format(sql.Identifier(role), sql.SQL('REPLICATION' if name == 'repl' else 'NOREPLICATION'),
                                    sql.Literal(self.passwords[name])))
                    self.created_roles.append(role)
                for name in (self.source_name, self.target_name):
                    execute(c, sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
                    self.created_databases.append(name)
                    execute(c, sql.SQL('REVOKE CONNECT ON DATABASE {} FROM PUBLIC').format(sql.Identifier(name)))
                for name, roles in ((self.source_name, ('writer', 'repl', 'observer')), (self.target_name, ('mapper', 'observer'))):
                    for role in roles:
                        execute(c, sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(
                            sql.Identifier(name), sql.Identifier(self.roles[role])))
            with connection(self.config['source_admin']) as c:
                script(c, 'legacy.sql')
                script(c, 'source.sql')
                execute(c, 'INSERT INTO source_control.gate(singleton,run_id) VALUES(true,%s)', (self.prefix,))
                with c.cursor() as q:
                    q.executemany('INSERT INTO legacy.organizations VALUES(%s,%s)',
                                  [(f'org{i}', f'Fictional Organization {i}') for i in range(6)])
                    q.executemany('INSERT INTO legacy.people VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                                  [(f'person{i:04d}', *encode_person('legacy', person(i), 1, 2)) for i in range(self.count)])
                execute(c, 'CREATE PUBLICATION migration_pub FOR TABLE legacy.organizations,legacy.people,legacy.barriers '
                           "WITH(publish='insert,update,delete')")
                for role in (self.roles['writer'], self.roles['repl'], self.roles['observer']):
                    execute(c, sql.SQL('GRANT USAGE ON SCHEMA legacy,source_control TO {}').format(sql.Identifier(role)))
                    execute(c, sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA legacy TO {}').format(sql.Identifier(role)))
                execute(c, sql.SQL('GRANT INSERT,UPDATE,DELETE ON legacy.organizations,legacy.people TO {}')
                        .format(sql.Identifier(self.roles['writer'])))
            with connection(self.config['target_admin']) as c:
                script(c, 'legacy.sql')
                script(c, 'target.sql')
                role = sql.Identifier(self.roles['mapper'])
                execute(c, sql.SQL('GRANT USAGE ON SCHEMA legacy,inbox,modern,target_control TO {}').format(role))
                execute(c, sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA legacy,inbox,target_control TO {}').format(role))
                execute(c, sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA modern TO {}').format(role))
                execute(c, sql.SQL('GRANT UPDATE(cursor,chain,barrier,native_floor) ON target_control.state TO {}').format(role))
                execute(c, sql.SQL('GRANT INSERT ON target_control.receipts TO {}').format(role))
                execute(c, sql.SQL('GRANT EXECUTE ON FUNCTION target_control.native_status() TO {}').format(role))
                observer = sql.Identifier(self.roles['observer'])
                execute(c, sql.SQL('GRANT USAGE ON SCHEMA legacy,inbox,modern,target_control TO {}').format(observer))
                execute(c, sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA legacy,inbox,modern,target_control TO {}').format(observer))
                execute(c, sql.SQL('GRANT EXECUTE ON FUNCTION target_control.native_status() TO {}').format(observer))
            return self
        except BaseException:
            self.close()
            raise

    def source_write(self, statements):
        with connection(self.config['writer']) as c:
            xid = query(c, 'SELECT pg_current_xact_id()::text AS id', one=True)['id']
            for statement, parameters in statements:
                execute(c, statement, parameters)
        with connection(self.config['source_admin']) as c:
            position = query(c, 'SELECT pg_current_wal_flush_lsn()::text AS lsn', one=True)['lsn']
        return {'source_xid': xid, 'source_flush_after_commit': position}

    def snapshot(self, hook=None, mapping=2):
        reader = None
        replication = psycopg2.connect(self.config['replication'], connection_factory=LogicalReplicationConnection)
        try:
            if hook:
                hook('before_slot')
            with replication.cursor() as q:
                q.execute(sql.SQL('CREATE_REPLICATION_SLOT {} LOGICAL pgoutput EXPORT_SNAPSHOT')
                          .format(sql.Identifier(self.prefix)))
                slot, consistent_lsn, snapshot_name, plugin = q.fetchone()
            if hook:
                hook('slot_exported')
            reader = psycopg2.connect(self.config['source'])
            reader.set_session(isolation_level='REPEATABLE READ', readonly=True)
            with reader.cursor() as q:
                q.execute(sql.SQL('SET TRANSACTION SNAPSHOT {}').format(sql.Literal(snapshot_name)))
            tables = {}
            for table, key in (('organizations', 'org_id'), ('people', 'person_id'), ('barriers', 'id')):
                last = None
                tables[table] = []
                while True:
                    statement = sql.SQL('SELECT * FROM legacy.{} {} ORDER BY {} LIMIT 20').format(
                        sql.Identifier(table), sql.SQL('') if last is None else sql.SQL('WHERE {} > %s').format(sql.Identifier(key)),
                        sql.Identifier(key))
                    with reader.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as q:
                        q.execute(statement, None if last is None else (last,))
                        rows = q.fetchall()
                    if not rows:
                        break
                    tables[table].extend(dict(row) for row in rows)
                    last = rows[-1][key]
                    if hook:
                        hook('page_read')
            with connection(self.config['source']) as s, connection(self.config['target_admin']) as t:
                legacy_schema = schema(s, 'legacy')
                if legacy_schema != schema(t, 'legacy'):
                    raise Refused('landing_schema_must_match')
                identity = query(s, 'SELECT source_control.identity() AS value', one=True)['value']
                seal = digest({'tables': tables, 'consistent_lsn': consistent_lsn, 'snapshot_name': snapshot_name})
                binding = {'source_identity': digest(identity), 'publication': publication(s), 'legacy_schema': legacy_schema,
                           'modern_schema': schema(t, 'modern'), 'slot': slot, 'consistent_lsn': consistent_lsn,
                           'snapshot_seal': seal, 'subscription_oid': None}
                execute(t, 'INSERT INTO target_control.state(singleton,binding,chain,mapping) VALUES(true,%s,%s,%s)',
                        (Json(binding), seal, mapping))
                for table, rows in tables.items():
                    for row in rows:
                        statement = sql.SQL('INSERT INTO legacy.{}({}) VALUES({})').format(
                            sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, row)),
                            sql.SQL(',').join(sql.Placeholder() for _ in row))
                        execute(t, statement, tuple(row.values()))
                        if table != 'barriers':
                            map_row(t, table, 'INSERT', None, row, mapping)
            reader.commit()
        finally:
            if reader:
                reader.close()
            replication.close()
        with connection(self.config['target_admin'], autocommit=True) as c:
            execute(c, sql.SQL("CREATE SUBSCRIPTION migration_sub CONNECTION {} PUBLICATION migration_pub WITH "
                              "(create_slot=false,copy_data=false,slot_name={},streaming=off,synchronous_commit=on,"
                              "disable_on_error=true)").format(sql.Literal(self.config['publisher']), sql.Literal(self.prefix)))
            native = query(c, 'SELECT target_control.native_status() AS value', one=True)['value']
            binding['subscription_oid'] = native['subscription_oid']
            self.subscription_oid = native['subscription_oid']
            execute(c, 'UPDATE target_control.state SET binding=%s WHERE singleton', (Json(binding),))
        return {'slot': slot, 'consistent_lsn': consistent_lsn, 'exported_snapshot': snapshot_name,
                'plugin': plugin, 'snapshot_seal': seal, 'table_counts': {k: len(v) for k, v in tables.items()},
                'tables': tables}

    def wait(self, predicate, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        raise Refused('native_wait_timeout')

    def wait_rows(self, minimum):
        def probe():
            with connection(self.config['target_admin']) as c:
                return query(c, 'SELECT count(*) AS n FROM inbox.changes', one=True)['n'] >= minimum
        return self.wait(probe)

    def close(self):
        errors = []
        if self.target_name in self.created_databases:
            try:
                with connection(self.config['target_admin'], autocommit=True) as c:
                    exists = query(c, 'SELECT 1 FROM pg_subscription WHERE subname=%s AND subdbid=(SELECT oid '
                                   'FROM pg_database WHERE datname=current_database())', ('migration_sub',), one=True)
                    if exists:
                        execute(c, 'ALTER SUBSCRIPTION migration_sub DISABLE')
                        def stopped():
                            return not query(c, 'SELECT pid FROM pg_stat_subscription WHERE subname=%s AND pid IS NOT NULL',
                                             ('migration_sub',))
                        self.wait(stopped)
                        execute(c, 'ALTER SUBSCRIPTION migration_sub SET(slot_name=NONE)')
                        execute(c, 'DROP SUBSCRIPTION migration_sub')
            except psycopg2.Error:
                errors.append('subscription_cleanup_failed')
        try:
            with connection(self.admin, autocommit=True) as c:
                slot = query(c, 'SELECT active_pid FROM pg_replication_slots WHERE slot_name=%s', (self.prefix,), one=True)
                if slot:
                    if slot['active_pid']:
                        query(c, 'SELECT pg_terminate_backend(%s)', (slot['active_pid'],))
                    self.wait(lambda: not query(c, 'SELECT 1 FROM pg_replication_slots WHERE slot_name=%s AND active',
                                                (self.prefix,)))
                    query(c, 'SELECT pg_drop_replication_slot(%s)', (self.prefix,))
                for name in reversed(self.created_databases):
                    execute(c, sql.SQL('DROP DATABASE {} WITH(FORCE)').format(sql.Identifier(name)))
                for role in self.created_roles:
                    execute(c, sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
            with connection(self.admin) as c:
                databases = query(c, 'SELECT datname FROM pg_database WHERE datname=ANY(%s)', (self.created_databases,))
                slots = query(c, 'SELECT slot_name FROM pg_replication_slots WHERE slot_name=%s', (self.prefix,))
                roles = query(c, 'SELECT rolname FROM pg_roles WHERE rolname=ANY(%s)', (self.created_roles,))
                subscriptions = query(c, 'SELECT oid FROM pg_subscription WHERE oid=%s', (self.subscription_oid,))
                origins = query(c, 'SELECT external_id FROM pg_replication_origin_status WHERE external_id=%s',
                                ('pg_' + str(self.subscription_oid),))
            self.cleanup_record = {'databases_remaining': len(databases), 'slots_remaining': len(slots),
                                   'roles_remaining': len(roles), 'subscriptions_remaining': len(subscriptions),
                                   'origins_remaining': len(origins), 'errors': errors,
                                   'method': 'separate catalog connection after exact owned-resource teardown'}
            if databases or slots or roles or subscriptions or origins or errors:
                raise Refused('native_cleanup_incomplete')
        finally:
            if self.cleanup_record is not None:
                (self.workspace / 'cleanup.json').write_text(canonical(self.cleanup_record), encoding='utf-8')

    def __exit__(self, *args):
        self.close()
