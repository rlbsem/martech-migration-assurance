"""One durable change batch; crash hooks are explicit fault injection for recovery tests."""
import argparse
import json
from pathlib import Path

from .system import System


def main():
    p = argparse.ArgumentParser()
    p.add_argument("destination", type=Path)
    p.add_argument("kind", choices=["legacy", "modern"])
    p.add_argument("batch", type=Path)
    p.add_argument("--mapping", type=int, default=2)
    p.add_argument("--crash", choices=["before_commit", "after_commit"])
    args = p.parse_args()
    System(args.destination, args.kind).apply(json.loads(args.batch.read_text(encoding="utf-8")), args.mapping, args.crash)


if __name__ == "__main__":
    main()
