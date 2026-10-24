import pytest

from migration_lab.pgcdc.db import connection, execute, query
from migration_lab.pgcdc.processes import capture, drain, observe, worker


@pytest.mark.parametrize('fault', ['native_position', 'source_identity'])
def test_native_identity_and_durable_position_cannot_be_rebased(native_lab, fault):
    native_lab.snapshot()
    capture(native_lab)
    drain(native_lab)
    before = observe(native_lab)
    if fault == 'native_position':
        with connection(native_lab.config['target_admin']) as c:
            execute(c, "UPDATE target_control.state SET native_floor='FFFF/FFFFFFFF' WHERE singleton")
        expected = 'native_origin_regressed'
    else:
        with connection(native_lab.config['source_admin']) as c:
            execute(c, "UPDATE source_control.gate SET run_id='different_source_generation' WHERE singleton")
        expected = 'source_identity_changed'
    result = worker(native_lab)
    assert result['exit_code'] == 2 and result['result']['reason'] == expected
    with connection(native_lab.config['target_read']) as c:
        after = query(c, 'SELECT cursor,chain FROM target_control.state WHERE singleton', one=True)
    assert after['cursor'] == before['state']['cursor'] and after['chain'] == before['state']['chain']
