"""Real-process helpers and independently read records for bounded native acceptance."""
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from uuid import uuid4

from migration_lab.contracts import Refused
from .audit import compare
from .consumer import apply_next
from .db import connection, execute, health, query


def stamp():
    return datetime.now(UTC).isoformat()


def capture(lab):
    marker = str(uuid4())
    with connection(lab.config['source_admin']) as c:
        execute(c, 'INSERT INTO legacy.barriers VALUES(%s,0,pg_current_xact_id()::text)', (marker,))
    def arrived():
        with connection(lab.config['target_read']) as c:
            return query(c, 'SELECT 1 FROM legacy.barriers WHERE id=%s', (marker,), one=True)
    lab.wait(arrived)
    return marker


def drain(lab):
    results = []
    for _ in range(100):
        result = apply_next(lab.config)
        results.append(result)
        if result['status'] == 'idle':
            return results
    raise Refused('bounded_drain_exhausted')


def observe(lab):
    with connection(lab.config['source']) as source, connection(lab.config['target_read']) as target:
        reconciliation = compare(source, target)
        state = query(target, 'SELECT * FROM target_control.state WHERE singleton', one=True)
        changes = query(target, 'SELECT * FROM inbox.changes ORDER BY seq')
        receipts = query(target, 'SELECT * FROM target_control.receipts ORDER BY last_seq')
        positions = health(lab.config, target)[1]
    return {'observed_at': stamp(), 'reconciliation': reconciliation, 'state': dict(state),
            'changes': [dict(row) for row in changes], 'receipts': [dict(row) for row in receipts],
            'positions': positions}


def worker(lab, **options):
    command = [sys.executable, '-m', 'migration_lab.pgcdc.worker', str(lab.config_path)]
    for key, value in options.items():
        command.extend(['--' + key, str(value)])
    output = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=20,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if not output.stdout.strip():
        raise Refused('worker_result_missing')
    return {'exit_code': output.returncode, 'result': json.loads(output.stdout)}


def kill_at(lab, point):
    marker = lab.workspace / f'{point}.marker'
    log_path = lab.workspace / f'{point}.private.log'
    command = [sys.executable, '-m', 'migration_lab.pgcdc.worker', str(lab.config_path),
               '--pause', point, '--marker', str(marker)]
    before = observe(lab)
    start = time.monotonic()
    with log_path.open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            lab.wait(lambda: marker.exists() or process.poll() is not None)
            if not marker.exists():
                raise Refused('worker_did_not_reach_fault_point')
            paused = observe(lab)
            process.kill()
            process.wait(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
    after = observe(lab)
    recovered = worker(lab)
    return {'fault': point, 'before': before, 'paused': paused, 'after_kill': after,
            'actual_exit_code': process.returncode, 'pid': process.pid,
            'recovered': recovered, 'seconds_through_restart': time.monotonic() - start}
