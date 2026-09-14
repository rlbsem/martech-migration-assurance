import argparse
from pathlib import Path

from migration_lab.demo import run

p = argparse.ArgumentParser()
p.add_argument("--workspace", type=Path, default=Path("work/demo"))
p.add_argument("--output", type=Path, default=Path("docs/evidence"))
args = p.parse_args()
proof = run(args.workspace, args.output)
print("Migration and rollback executed; lossy cutover and nonrepresentable rollback blocked.")
print("Evidence:", args.output / "report.md")
