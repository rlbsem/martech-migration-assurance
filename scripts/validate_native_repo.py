"""Check public links, immutable baseline bytes, sanitized evidence and delivery hashes."""
import argparse
import gzip
import hashlib
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'docs/evidence/postgres-cdc/package-manifest.json'


def files():
    result = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard'], cwd=ROOT, text=True)
    return sorted({name for name in result.splitlines() if (ROOT / name).is_file()})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--write-package', action='store_true')
    args = parser.parse_args()
    names = files()
    if args.write_package:
        record = {'format': 'SHA-256 of exact delivered bytes; LF checkout', 'excluded_self': PACKAGE,
                  'files': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                            for name in names if name != PACKAGE}}
        (ROOT / PACKAGE).write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8', newline='\n')
        print(json.dumps({'package_hashes_written': len(record['files'])}))
        return
    baseline = json.loads((ROOT / 'docs/evidence/postgres-cdc/preserved-files.json').read_text(encoding='utf-8'))
    for name, expected in baseline['files'].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, 'historical bytes changed: ' + name
    links = 0
    private = {'.local', '.venv', '.git', '__pycache__'}
    forbidden = [rb'ghp_[A-Za-z0-9]{36}', rb'(?:AKIA|ASIA)[A-Z0-9]{16}',
                 rb'-----BEGIN(?: [A-Z]+)* PRIVATE KEY-----\s+[A-Za-z0-9/+\r\n=]{100,}']
    for name in names:
        path = ROOT / name
        assert not private.intersection(path.parts) and not name.startswith('work/'), 'private file in delivery'
        raw = path.read_bytes()
        if path.suffix == '.gz':
            raw = gzip.decompress(raw)
        for pattern in forbidden:
            assert not re.search(pattern, raw), 'credential pattern: ' + name
        if name.startswith('docs/evidence/postgres-cdc/') and path.suffix in ('.gz', '.json'):
            assert not re.search(rb'"(?:password|source_admin|target_admin|publisher)"\s*:', raw), 'private configuration: ' + name
        if path.suffix == '.md':
            body = re.sub(r'```.*?```', '', raw.decode('utf-8'), flags=re.S)
            for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', body):
                target = target.split(' "', 1)[0].strip('<>')
                url = urlsplit(target)
                if url.scheme or not url.path:
                    continue
                resolved = (path.parent / unquote(url.path)).resolve()
                assert resolved.is_relative_to(ROOT), 'link escapes repository: ' + name
                assert resolved.exists(), 'missing link: ' + name + ' -> ' + target
                links += 1
    package = json.loads((ROOT / PACKAGE).read_text(encoding='utf-8'))
    assert set(package['files']) == set(names) - {PACKAGE}, 'package file set mismatch'
    for name, expected in package['files'].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, 'package hash mismatch: ' + name
    print(json.dumps({'relative_links': links, 'preserved_baseline_files': len(baseline['files']),
                      'package_hashes': len(package['files']), 'public_content': 'clear'}))


if __name__ == '__main__':
    main()
