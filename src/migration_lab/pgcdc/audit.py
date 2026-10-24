"""Independent source/target SQL projections; no mapper or snapshot decoder imports."""
import json

from migration_lab.contracts import digest
from .db import query


def inspect(c, kind):
    if kind == 'source':
        organizations = query(c, 'SELECT org_id AS id,display_name AS name FROM legacy.organizations ORDER BY org_id')
        people = query(c, '''SELECT person_id AS id,org_id AS account_id,full_name AS name,
          CASE stage WHEN 'Lead' THEN 'prospect' WHEN 'Customer' THEN 'customer' WHEN 'Hold' THEN 'paused'
          ELSE 'INVALID' END AS lifecycle,lead_score AS score,channels,
          CASE subscribed WHEN 1 THEN true WHEN 0 THEN false ELSE NULL END AS subscribed,
          locale,schema_version FROM legacy.people ORDER BY person_id''')
        for person in people:
            person['channels'] = sorted(person['channels'].split(';')) if person['channels'] else []
    else:
        organizations = query(c, 'SELECT company_key AS id,legal_name AS name FROM modern.companies ORDER BY company_key')
        people = query(c, '''SELECT contact_key AS id,company_key AS account_id,display_name AS name,
          CASE lifecycle WHEN 'P' THEN 'prospect' WHEN 'C' THEN 'customer' WHEN 'H' THEN 'paused'
          WHEN 'A' THEN 'archived' ELSE 'INVALID' END AS lifecycle,engagement_score AS score,channels_json AS channels,
          CASE subscription_state WHEN 'yes' THEN true WHEN 'no' THEN false ELSE NULL END AS subscribed,
          preferred_locale AS locale,schema_version FROM modern.contacts ORDER BY contact_key''')
        for person in people:
            person['channels'] = sorted(person['channels'])
    ids = {row['id'] for row in organizations}
    return {'organizations': [dict(row) for row in organizations], 'people': [dict(row) for row in people],
            'orphans': [row['id'] for row in people if row['account_id'] not in ids]}


def compare(source, target):
    left, right = inspect(source, 'source'), inspect(target, 'target')
    differences = []
    for table in ('organizations', 'people'):
        a, b = {row['id']: row for row in left[table]}, {row['id']: row for row in right[table]}
        for key in sorted(a.keys() | b.keys()):
            if key not in a or key not in b:
                differences.append({'table': table, 'id': key, 'field': '_record'})
            else:
                for field in sorted(a[key]):
                    if json.dumps(a[key][field], sort_keys=True) != json.dumps(b[key][field], sort_keys=True):
                        differences.append({'table': table, 'id': key, 'field': field,
                                            'source': a[key][field], 'target': b[key][field]})
    counts = {kind: {name: len(body[name]) for name in ('organizations', 'people')}
              for kind, body in (('source', left), ('target', right))}
    return {'passed': not differences and not left['orphans'] and not right['orphans'],
            'same_counts': counts['source'] == counts['target'], 'counts': counts, 'differences': differences,
            'source_hash': digest(left), 'target_hash': digest(right),
            'source': left, 'target': right}
