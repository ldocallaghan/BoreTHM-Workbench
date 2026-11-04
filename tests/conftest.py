import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("BORETHM_CACHE_DIR", str(ROOT / ".workbench-data/cache"))


@pytest.fixture
def scenario():
    from borethm_workbench.contracts import Scenario

    return Scenario.model_validate_json(
        (ROOT / "src/borethm_workbench/presets/synthetic-water.json").read_bytes()
    )


@pytest.fixture(scope="session")
def physical_result():
    from borethm_workbench.contracts import Scenario
    from borethm_workbench.adapter import run_circulation

    scenario = Scenario.model_validate_json(
        (ROOT / "src/borethm_workbench/presets/synthetic-water.json").read_bytes()
    )
    return run_circulation(scenario)


def pytest_collection_modifyitems(items):
    from borethm_workbench.adapter import history_capabilities, HISTORY_MESSAGE

    if all(history_capabilities().values()):
        return
    modules = {
        "test_histories.py",
        "test_interventions.py",
        "test_static_state.py",
        "test_radial_grid.py",
        "test_run_browsing.py",
    }
    history_tests = {
        "test_failed_physical_balance_remains_a_replay_failure",
        "test_prescribed_wall_archive_replays_without_property_table",
    }
    for item in items:
        if item.path.name in modules or item.originalname in history_tests:
            item.add_marker(pytest.mark.skip(reason=HISTORY_MESSAGE))
