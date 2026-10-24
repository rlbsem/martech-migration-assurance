"""Transactional semantic replay of complete native-apply groups, never a source event-table poller."""
import json
import time
from pathlib import Path

from psycopg2.extras import Json

from migration_lab.contracts import Refused, digest, encode_person, validate_command
from .db import connection, execute, health, implementation, lsn, query


def map_row(c, table, operation, before, after, mapping):
    row = before if operation == 'DELETE' else after
    if table == 'barriers':
        if operation != 'INSERT' or set(row) != {'id', 'epoch', 'source_xid'}:
            raise Refused('unsupported_barrier_change')
        execute(c, 'UPDATE target_control.state SET barrier=%s WHERE singleton', (row['id'],))
        return
    if table == 'organizations':
        if set(row) != {'org_id', 'display_name'}:
            raise Refused('organization_schema_changed')
        key, value = row['org_id'], {'name': row['display_name']}
        validate_command({'schema': 1, 'changes': [{'entity': 'account', 'id': key,
                                                 'value': None if operation == 'DELETE' else value}]})
        if operation == 'DELETE':
            count = execute(c, 'DELETE FROM modern.companies WHERE company_key=%s', (key,))
        elif operation == 'INSERT':
            count = execute(c, 'INSERT INTO modern.companies VALUES(%s,%s)', (key, value['name']))
        elif operation == 'UPDATE':
            count = execute(c, 'UPDATE modern.companies SET legal_name=%s WHERE company_key=%s', (value['name'], key))
        else:
            raise Refused('unknown_row_operation')
    elif table == 'people':
        fields = {'person_id', 'org_id', 'full_name', 'stage', 'lead_score', 'channels', 'subscribed', 'locale',
                  'schema_version'}
        if set(row) != fields:
            raise Refused('person_schema_changed')
        key, version = row['person_id'], row['schema_version']
        if version not in (1, 2) or version > mapping:
            raise Refused('mapping_upgrade_required')
        if version == 1 and row['locale'] is not None:
            raise Refused('unversioned_locale')
        if row['subscribed'] not in (None, 0, 1):
            raise Refused('invalid_subscription')
        value = {'account_id': row['org_id'], 'name': row['full_name'],
                 'status': {'Lead': 'prospect', 'Customer': 'customer', 'Hold': 'paused'}.get(row['stage'], 'INVALID'),
                 'score': row['lead_score'], 'channels': row['channels'].split(';') if row['channels'] else [],
                 'subscribed': None if row['subscribed'] is None else bool(row['subscribed'])}
        if version == 2:
            value['locale'] = row['locale']
        validate_command({'schema': version, 'changes': [{'entity': 'person', 'id': key,
                                                        'value': None if operation == 'DELETE' else value}]})
        if operation == 'DELETE':
            count = execute(c, 'DELETE FROM modern.contacts WHERE contact_key=%s', (key,))
        else:
            encoded = list(encode_person('modern', value, version, mapping))
            encoded[4] = Json(json.loads(encoded[4]))
            if operation == 'INSERT':
                count = execute(c, 'INSERT INTO modern.contacts VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)', (key, *encoded))
            elif operation == 'UPDATE':
                count = execute(c, '''UPDATE modern.contacts SET company_key=%s,display_name=%s,lifecycle=%s,
                  engagement_score=%s,channels_json=%s,subscription_state=%s,preferred_locale=%s,schema_version=%s
                  WHERE contact_key=%s''', (*encoded, key))
            else:
                raise Refused('unknown_row_operation')
    else:
        raise Refused('unknown_source_table')
    if count != 1:
        raise Refused('target_row_missing_or_duplicated')


def checkpoint(c, state):
    receipts = query(c, 'SELECT * FROM target_control.receipts ORDER BY last_seq')
    chain, cursor, native_floor = state['binding']['snapshot_seal'], 0, '0/0'
    for receipt in receipts:
        rows = query(c, 'SELECT * FROM inbox.changes WHERE apply_xid=%s ORDER BY seq', (receipt['apply_xid'],))
        if not rows or rows[0]['seq'] <= cursor or digest(rows) != receipt['input_hash']:
            raise Refused('checkpoint_receipt_mismatch')
        expected = digest({'previous': chain, 'input': receipt['input_hash'], 'end': rows[-1]['seq']})
        if (receipt['previous_chain'] != chain or receipt['chain'] != expected
                or receipt['first_seq'] != rows[0]['seq'] or receipt['last_seq'] != rows[-1]['seq']
                or receipt['row_count'] != len(rows)):
            raise Refused('checkpoint_chain_mismatch')
        if lsn(receipt['result']['native_origin_lsn']) < lsn(native_floor):
            raise Refused('native_checkpoint_regressed')
        native_floor = receipt['result']['native_origin_lsn']
        cursor, chain = receipt['last_seq'], receipt['chain']
    covered = query(c, 'SELECT count(*) AS n FROM inbox.changes WHERE seq<=%s', (cursor,), one=True)['n']
    if (cursor != state['cursor'] or chain != state['chain'] or native_floor != state['native_floor']
            or covered != sum(receipt['row_count'] for receipt in receipts)):
        raise Refused('unusable_checkpoint')
    return receipts


def pause(point, selected, marker):
    if point == selected:
        Path(marker).write_text(point, encoding='utf-8')
        while True:
            time.sleep(0.05)


def apply_next(config, *, replay=None, pause_at=None, marker=None):
    with connection(config['target']) as c:
        state = query(c, 'SELECT * FROM target_control.state WHERE singleton FOR UPDATE', one=True)
        if not state or state['phase'] != 'catchup':
            raise Refused('native_mapping_sealed_or_snapshot_missing')
        _, positions = health(config, c)
        checkpoint(c, state)
        if replay is None:
            first = query(c, 'SELECT apply_xid FROM inbox.changes WHERE seq>%s ORDER BY seq LIMIT 1',
                          (state['cursor'],), one=True)
            if not first:
                return {'status': 'idle', 'cursor': state['cursor']}
            group = first['apply_xid']
        else:
            group = replay
        rows = query(c, 'SELECT * FROM inbox.changes WHERE apply_xid=%s ORDER BY seq', (group,))
        if not rows or not all(row['native_origin'] for row in rows):
            raise Refused('native_transaction_missing')
        sealed = digest(rows)
        receipt = query(c, 'SELECT * FROM target_control.receipts WHERE apply_xid=%s', (group,), one=True)
        if receipt:
            if receipt['input_hash'] != sealed:
                raise Refused('replay_content_conflict')
            return {'status': 'duplicate', 'cursor': state['cursor'], 'receipt': dict(receipt)}
        if rows[0]['seq'] <= state['cursor']:
            raise Refused('unreceipted_history')
        between = query(c, 'SELECT DISTINCT apply_xid FROM inbox.changes WHERE seq>%s AND seq<=%s',
                        (state['cursor'], rows[-1]['seq']))
        if {row['apply_xid'] for row in between} != {group}:
            raise Refused('transaction_order_or_parallel_apply_violation')
        for row in rows:
            map_row(c, row['table_name'], row['operation'], row['old_row'], row['new_row'], state['mapping'])
        chain = digest({'previous': state['chain'], 'input': sealed, 'end': rows[-1]['seq']})
        result = {'apply_xid': group, 'first_seq': rows[0]['seq'], 'last_seq': rows[-1]['seq'],
                  'row_count': len(rows), 'mapping_version': state['mapping'],
                  'implementation': implementation(), 'native_origin_lsn': positions['native']['remote_lsn'], 'input_hash': sealed, 'previous_chain': state['chain'], 'chain': chain}
        execute(c, 'INSERT INTO target_control.receipts VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                (group, rows[0]['seq'], rows[-1]['seq'], sealed, state['chain'], chain, len(rows), Json(result)))
        execute(c, 'UPDATE target_control.state SET cursor=%s,chain=%s,native_floor=%s WHERE singleton',
                (rows[-1]['seq'], chain, positions['native']['remote_lsn']))
        pause('before_commit', pause_at, marker)
    pause('after_commit', pause_at, marker)
    return {'status': 'applied', 'cursor': rows[-1]['seq'], 'receipt': result}
