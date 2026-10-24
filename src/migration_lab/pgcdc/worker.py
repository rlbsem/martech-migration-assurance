"""Native lane worker. Connection material stays in an ignored private configuration file."""
import argparse
import json

import psycopg2

from migration_lab.contracts import Refused, canonical
from .consumer import apply_next


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('config')
    parser.add_argument('--replay')
    parser.add_argument('--pause', choices=('before_commit', 'after_commit'))
    parser.add_argument('--marker')
    args = parser.parse_args()
    if args.pause and not args.marker:
        parser.error('--pause requires --marker')
    with open(args.config, encoding='utf-8') as stream:
        config = json.load(stream)
    try:
        result = apply_next(config, replay=args.replay, pause_at=args.pause, marker=args.marker)
    except Refused as exc:
        print(canonical({'status': 'blocked', 'reason': str(exc)}))
        raise SystemExit(2) from None
    except psycopg2.Error as exc:
        print(canonical({'status': 'blocked', 'reason': 'database_rejected', 'sqlstate': exc.pgcode}))
        raise SystemExit(3) from None
    print(canonical(result))


if __name__ == '__main__':
    main()
