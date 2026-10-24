"""Offline native-evidence verification; PostgreSQL and credentials are not required."""
import argparse
import json
from pathlib import Path

from migration_lab.pgcdc.checker import check_directory

parser = argparse.ArgumentParser()
parser.add_argument('evidence', type=Path)
parser.add_argument('--source-root', type=Path)
args = parser.parse_args()
print(json.dumps(check_directory(args.evidence, root=args.source_root), indent=2))
