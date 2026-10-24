"""Render a concise report from checked native observations, never from hand-entered results."""
import argparse
import gzip
import json
from pathlib import Path

from migration_lab.pgcdc.checker import check_directory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('evidence', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    summaries = check_directory(args.evidence)
    protocol = json.loads((args.evidence / 'protocol.json').read_text(encoding='utf-8'))
    records = {name: json.loads(gzip.decompress((args.evidence / 'cases' / (name + '.json.gz')).read_bytes()))
               for name in (summary['scenario'] for summary in summaries)}
    handoff = records['snapshot_handoff']['observations']
    cutover = records['semantic_cutover']['observations']['repaired']
    rows = ['# Executed native PostgreSQL migration acceptance', '',
            f"Source implementation: `{protocol['source_commit']}`. Actual local PostgreSQL WAL capture, "
            f"native apply, semantic consumer processes and synthetic data. "
            f"Each scenario starts with {protocol['people_per_scenario']} people and six organizations.", '',
            '| Scenario | Independently checked outcome | SQL observations |', '|---|---|---:|']
    for result in summaries:
        name = result['scenario']
        rows.append(f"| [{name}](native/cases/{name}.json.gz) | {result['outcome']} | {result['independent_sql_observations']} |")
    rows += ['', '## Source and checkpoint boundary', '',
             f"The exported snapshot was `{handoff['snapshot']['exported_snapshot']}` at consistent LSN "
             f"`{handoff['snapshot']['consistent_lsn']}` using `pgoutput`. The snapshot retained the old row "
             'while concurrent update/delete/insert transactions were subsequently caught up. ', '',
             f"The valid cutover barrier was `{cutover['barrier']}` in source transaction "
             f"`{cutover['barrier_source_xid']}`. Native receiver origin LSN was `{cutover['native_origin_lsn']}`; "
             f"the semantic inbox cursor was `{cutover['semantic_cursor']}`. "
             'The barrier, durable native floor, complete mapped prefix and independently read business state matched.', '',
             '## Process interruption and replay', '']
    for name in ('crash_before_commit', 'crash_after_commit_before_ack'):
        item = records[name]['observations']
        crash = item['interruption']
        rows.append(f"- {name}: actual killed PID `{crash['pid']}`, exit `{crash['actual_exit_code']}`; "
                    f"checkpoint before `{crash['before']['state']['cursor']}`, "
                    f"after kill `{crash['after_kill']['state']['cursor']}`. "
                    f"Restart result `{crash['recovered']['result']['status']}`; exact redelivery "
                    f"`{item['replay']['result']['status']}` with unchanged state/receipts. "
                    f"Measured through restart: {crash['seconds_through_restart']:.3f} seconds (one local observation).")
    rows += ['', '## Independent semantic comparison', '',
             'Equal-count corruption preserved row counts and checkpoint position while turning an unknown subscription '
             'into false. Independent source/target SQL identified the subscription difference and both stale proof and a freshly registered non-parity proof were refused. '
             'A fresh passing proof was required after repairing only that deliberate synthetic corruption.', '',
             'Every scenario independently confirmed removal of its exact two databases, slot, subscription, replication '
             'origin and four temporary roles. The source publication disappears with its verified-absent source database. '
             'Cleanup records are embedded in each case; no operator/business database was reset.', '',
             '[Protocol and source fingerprints](native/protocol.json) · [Derived results](native/results.json) · '
             '[Artifact hashes](native/manifest.json) · [Software verification](software/verification.json) · '
             '[Baseline](baseline/verification.json) · [Contract and reproduction](../../postgres-cdc.md)', '',
             'Expected blocked cases are not successful recovery claims. Missing slots, corrupt checkpoints and unsupported '
             'schema/publication changes require explicit repair or a fresh snapshot. Physical restore, HA/failover, '
             'long outages, automatic DDL migration, native reverse replication and unrestricted exactly-once delivery '
             'were not established. The original SQLite rollback lane remains separate and preserved.']
    args.output.write_text('\n'.join(rows) + '\n', encoding='utf-8', newline='\n')


if __name__ == '__main__':
    main()
