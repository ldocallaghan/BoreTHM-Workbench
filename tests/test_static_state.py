from scenario_inputs import preset_bytes
import copy
import json
from pathlib import Path

import numpy as np
import pytest

from borethm_workbench.adapter import validate, CalculationError
from borethm_workbench.contracts import parse_scenario
from borethm_workbench.histories import wall_dataset

ROOT = Path(__file__).resolve().parents[1]
PRESETS = ROOT / "src/borethm_workbench/presets"


def preset(name):
    return json.loads(preset_bytes(PRESETS / (name + ".json")))


def split_hold():
    value = preset("open-hole-history")
    value["numerics"].update(radial_nodes=32, initial_step_s=0.1, step_growth=1.02)
    second = copy.deepcopy(value["definition"]["states"][1])
    second["id"] = "connection-2"
    value["definition"]["states"].insert(2, second)
    return value


def test_split_static_hold_continuity_convergence_and_two_balances():
    split = parse_scenario(split_hold())
    joined = split.model_dump()
    joined["definition"]["states"][1]["duration_s"] *= 2
    del joined["definition"]["states"][2]
    joined = parse_scenario(joined)
    for col, continuous_col in zip(split.definition.columns, joined.definition.columns):
        data = wall_dataset(split, col)
        control = wall_dataset(joined, continuous_col)
        right = data["events"][2]["right_index"]
        assert data["time_s"][right] == pytest.approx(2400.0)
        assert (
            data["rock_temperature_C"][right - 1] == data["rock_temperature_C"][right]
        )
        assert (
            data["fluid_node_temperature_C"][right - 1]
            == data["fluid_node_temperature_C"][right]
        )
        assert data["fluid_node_temperature_C"][right] != 60.0
        # Splitting resets the timestep controller, not physical state. Bound the
        # finite timestep difference; do not demand bit-identical integrations.
        assert (
            np.max(
                np.abs(
                    np.asarray(data["rock_temperature_C"][-1])
                    - control["rock_temperature_C"][-1]
                )
            )
            < 0.01
        )
        assert all(e["passed"] for e in data["statistics"]["event_energy"])


def test_static_replacement_explicit_and_capacity_change_rejected():
    value = split_hold()
    value["definition"]["states"][2]["initial_fluid_temperature_C"] = 20.0
    s = parse_scenario(value)
    data = wall_dataset(s, s.definition.columns[-1])
    right = data["events"][2]["right_index"]
    assert data["fluid_node_temperature_C"][right] == 20.0
    exchange = data["statistics"]["external_reservoir_inventory_changes_J_m"][0]
    assert exchange["change_J_m"] == pytest.approx(
        100000.0 * (20.0 - data["fluid_node_temperature_C"][right - 1])
    )
    value["definition"]["states"][2]["fluid_capacity_J_m_K"] = 200000.0
    with pytest.raises(CalculationError, match="capacity fixed"):
        validate(parse_scenario(value))
