import os
from pathlib import Path

import pytest
from scenario_inputs import preset_bytes
import scenario_inputs

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("BORETHM_CACHE_DIR", str(ROOT / ".workbench-data/cache"))


@pytest.fixture
def scenario():
    from borethm_workbench.contracts import Scenario

    return Scenario.model_validate_json(
        preset_bytes(ROOT / "src/borethm_workbench/presets/synthetic-water.json")
    )


@pytest.fixture(scope="session")
def physical_result():
    from borethm_workbench.contracts import Scenario
    from borethm_workbench.adapter import run_circulation

    scenario = Scenario.model_validate_json(
        preset_bytes(ROOT / "src/borethm_workbench/presets/synthetic-water.json")
    )
    return run_circulation(scenario)


def pytest_addoption(parser):
    parser.addoption("--physics", choices=["v1.8.0", "v1.7.0"], default="v1.8.0")


def pytest_configure(config):
    scenario_inputs.PHYSICS = config.getoption("--physics")
