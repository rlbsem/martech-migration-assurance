"""Capture real pgoutput acceptance evidence in fresh workspaces; no external services."""
import argparse
import gzip
import hashlib
import json
import os
import platform
import re
import subprocess
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from migration_lab.pgcdc.checker import check_case, check_directory
from migration_lab.pgcdc.db import implementation
from migration_lab.pgcdc.lab import Lab
from migration_lab.pgcdc.scenarios import SCENARIOS

ROOT = Path(__file__).resolve().parents[1]


def fingerprints():
    paths = [p for folder in ('src', 'tests', 'native_tests', 'scripts', '.github')
             for p in (ROOT / folder).rglob('*') if p.is_file() and p.suffix in ('.py', '.sql', '.yml')
             and '__pycache__' not in p.parts]
    paths += [ROOT / name for name in ('requirements.lock', 'requirements-postgres.lock', 'pyproject.toml',
                                      'Dockerfile', 'compose.postgres.yml', '.dockerignore') if (ROOT / name).exists()]
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            for p in sorted(paths)}


def source_revision(paths):
    try:
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        dirty = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=all', '--', *paths],
                                        cwd=ROOT, text=True).strip()
        return head, not dirty, 'git_checkout'
    except (FileNotFoundError, subprocess.CalledProcessError):
        head = os.environ.get('MIGRATION_SOURCE_COMMIT', '')
        return head, bool(re.fullmatch('[0-9a-f]{40}', head)), 'container_build_argument_plus_source_fingerprints'


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8', newline='\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, required=True, help='Fresh ignored/private workspace for credentials/logs')
    parser.add_argument('--output', type=Path, required=True, help='Fresh sanitized evidence directory')
    parser.add_argument('--people', type=int, default=120)
    parser.add_argument('--development', action='store_true', help='Never labels uncommitted source as qualifying')
    args = parser.parse_args()
    if args.output.exists() or args.workspace.exists() or not 12 <= args.people <= 500:
        parser.error('Fresh directories and 12..500 synthetic people required')
    admin = os.environ.get('MIGRATION_PG_ADMIN_DSN')
    if not admin:
        parser.error('Set MIGRATION_PG_ADMIN_DSN to an isolated local PostgreSQL 17 server')
    hashes = fingerprints()
    head, clean, source_kind = source_revision(list(hashes))
    if not clean and not args.development:
        parser.error('Commit implementation source before qualifying acceptance; use --development only for private diagnostics')
    args.output.mkdir(parents=True)
    args.workspace.mkdir(parents=True)
    (args.output / 'cases').mkdir()
    protocol = {'version': 1, 'started_at': datetime.now(UTC).isoformat(), 'source_commit': head,
                'source_kind': source_kind, 'qualified_source': clean and not args.development,
                'source_sha256': hashes, 'hash_format': 'SHA-256 with LF text newlines',
                'implementation': implementation(), 'capture': 'pgoutput_native_subscription',
                'people_per_scenario': args.people, 'scenarios': list(SCENARIOS),
                'bounds': {'wait_timeout_seconds': 20, 'worker_timeout_seconds': 20, 'maximum_drain_groups': 100},
                'python': platform.python_version(), 'platform': platform.platform(), 'logical_cpus': os.cpu_count(),
                'dependencies': {name: version(name) for name in ('psycopg2-binary', 'pytest', 'ruff')},
                'scope': 'Local source/target databases, PostgreSQL native apply and synthetic data; no vendor or cloud execution',
                'command': 'python scripts/postgres_acceptance.py --workspace <private-fresh-workspace> --output <fresh-evidence>',
                'worker_command': 'python -m migration_lab.pgcdc.worker <private-config-file>'}
    write(args.output / 'protocol.json', protocol)
    results = []
    for name, scenario in SCENARIOS.items():
        lab = Lab(admin, args.workspace / name, count=args.people,
                  publisher=os.environ.get('MIGRATION_PG_PUBLISHER_DSN'))
        try:
            with lab:
                settings = lab.settings
                observations = scenario(lab)
            record = {'scenario': name, 'observed_at': datetime.now(UTC).isoformat(),
                      'postgresql': settings, 'observations': observations, 'cleanup': lab.cleanup_record}
            raw = (json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
            (args.output / 'cases' / (name + '.json.gz')).write_bytes(gzip.compress(raw, mtime=0))
            results.append(check_case(name, record, protocol['implementation']))
            print(json.dumps(results[-1]), flush=True)
        except BaseException as exc:
            write(args.output / 'failure.json', {'scenario': name, 'exception_type': type(exc).__name__,
                                                 'qualification': 'withheld', 'cleanup': lab.cleanup_record,
                                                 'details': 'Private logs retained; no raw connection exceptions published'})
            raise
    results.sort(key=lambda item: item['scenario'])
    if hashes != fingerprints():
        raise RuntimeError('Source changed during acceptance; records retained but qualification withheld')
    write(args.output / 'manifest.json', {'format': 'SHA-256 of observed bytes', 'files': {
        p.relative_to(args.output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(args.output.rglob('*')) if p.is_file()}})
    if protocol['qualified_source']:
        assert results == check_directory(args.output, root=ROOT)
    write(args.output / 'results.json', {'qualification': 'passed' if protocol['qualified_source'] else 'development_only',
                                        'checker': 'independent raw SQL/inbox/receipt oracle', 'scenarios': results})
    # Include the derived result in the delivery integrity record without changing any observed case bytes.
    manifest = json.loads((args.output / 'manifest.json').read_text(encoding='utf-8'))
    manifest['files']['results.json'] = hashlib.sha256((args.output / 'results.json').read_bytes()).hexdigest()
    write(args.output / 'manifest.json', manifest)
    print('Native acceptance complete: ' + ('QUALIFIED' if protocol['qualified_source'] else 'DEVELOPMENT ONLY'))


if __name__ == '__main__':
    main()
