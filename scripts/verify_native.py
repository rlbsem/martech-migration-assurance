"""Complete native lane verification with real software tests and source-frozen acceptance."""
import argparse
import json
import os
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import migration_lab

from postgres_acceptance import fingerprints, write

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.workspace.exists() or args.output.exists():
        parser.error('Fresh private workspace and proof directory required')
    if not os.environ.get('MIGRATION_PG_ADMIN_DSN'):
        parser.error('MIGRATION_PG_ADMIN_DSN is required; no native tests are skipped')
    args.workspace.mkdir(parents=True)
    args.output.mkdir(parents=True)
    software = args.output / 'software'
    software.mkdir()
    installed = Path(migration_lab.__file__).parent
    for source in (ROOT / 'src/migration_lab').rglob('*'):
        if source.suffix in ('.py', '.sql') and '__pycache__' not in source.parts:
            actual = installed / source.relative_to(ROOT / 'src/migration_lab')
            if not actual.exists() or source.read_bytes().replace(b'\r\n', b'\n') != actual.read_bytes().replace(b'\r\n', b'\n'):
                raise RuntimeError('Installed package differs from repository source')
    before = fingerprints()
    env = {**os.environ, 'MIGRATION_CRASH_EVIDENCE': str(args.workspace.resolve() / 'sqlite-crash-records')}
    commands = [
        [sys.executable, '-m', 'ruff', 'check', 'src', 'tests', 'native_tests', 'scripts'],
        [sys.executable, '-m', 'pip', 'check'],
        [sys.executable, '-m', 'pytest', '-q', 'tests', 'native_tests', '--basetemp', str(args.workspace / 'tests'),
         '-o', 'cache_dir=' + str(args.workspace / 'pytest-cache'), '--junitxml', str(software / 'tests.xml')],
    ]
    for command in commands:
        subprocess.run(command, cwd=ROOT, env=env, check=True)
    tree = ET.parse(software / 'tests.xml')
    suite = tree.getroot().find('testsuite')
    suite.attrib.pop('hostname', None)
    if any(int(suite.attrib[field]) for field in ('failures', 'errors', 'skipped')):
        raise RuntimeError('Complete native verification requires zero failures/errors/skips')
    tree.write(software / 'tests.xml', encoding='utf-8', xml_declaration=True)
    record = {'observed_at': datetime.now(UTC).isoformat(), 'python': platform.python_version(),
              'platform': platform.platform(), **{field: int(suite.attrib[field]) for field in
                                                ('tests', 'failures', 'errors', 'skipped')},
              'seconds': float(suite.attrib['time']), 'source_sha256': before,
              'hash_format': 'SHA-256 with LF text newlines',
              'dependencies': {name: version(name) for name in ('psycopg2-binary', 'pytest', 'ruff')},
              'scope': 'Existing SQLite regression plus real PostgreSQL logical-replication integration tests',
              'github_ci': 'workflow_in_progress' if os.environ.get('GITHUB_ACTIONS') else 'not_claimed_by_local_record'}
    write(software / 'verification.json', record)
    subprocess.run([sys.executable, 'scripts/postgres_acceptance.py', '--workspace', str(args.workspace / 'acceptance'),
                    '--output', str(args.output / 'acceptance')], cwd=ROOT, env=env, check=True)
    subprocess.run([sys.executable, 'scripts/check_postgres.py', str(args.output / 'acceptance'),
                    '--source-root', str(ROOT)], cwd=ROOT, check=True)
    if before != fingerprints():
        raise RuntimeError('Source changed during verification')
    print(json.dumps({'software_tests': record['tests'], 'native_acceptance': 'qualified', 'proof': str(args.output)}))


if __name__ == '__main__':
    main()
