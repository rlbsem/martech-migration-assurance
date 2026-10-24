import os

import pytest

from migration_lab.pgcdc.lab import Lab


@pytest.fixture
def native_lab(tmp_path):
    admin = os.environ.get('MIGRATION_PG_ADMIN_DSN')
    if not admin:
        pytest.fail('Native suite requires an explicitly configured isolated MIGRATION_PG_ADMIN_DSN')
    with Lab(admin, tmp_path / 'native', count=12) as lab:
        yield lab
