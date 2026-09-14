import pytest

from migration_lab.coordinator import Coordinator
from migration_lab.fixtures import seed
from migration_lab.snapshot import export_snapshot, import_snapshot


@pytest.fixture
def lab(tmp_path):
    coordinator = Coordinator(tmp_path / "lab")
    seed(coordinator, 12)
    return coordinator


@pytest.fixture
def paired(lab):
    import_snapshot(lab.systems["modern"], export_snapshot(lab.systems["legacy"]))
    return lab
