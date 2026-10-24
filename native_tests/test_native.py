"""Operational tests use real WAL replication, SQL roles and killed consumer processes."""
import pytest

from migration_lab.pgcdc.scenarios import SCENARIOS


@pytest.mark.parametrize('scenario', list(SCENARIOS))
def test_native_postgres_scenario(native_lab, scenario):
    record = SCENARIOS[scenario](native_lab)
    assert record
