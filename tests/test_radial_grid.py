import copy
import json
from pathlib import Path

import numpy as np
import pytest

from borethm_workbench.contracts import parse_scenario
from borethm_workbench.interventions import (
    BUILD_KEYS,
    checkpoint_digest,
    run_intervention,
    validate_checkpoint,
    CheckpointError,
)

ROOT = Path(__file__).resolve().parents[1]
PRESETS = ROOT / "src/borethm_workbench/presets"
IDENTITY = {k: "test-" + k for k in BUILD_KEYS}


def preset(name):
    return json.loads((PRESETS / (name + ".json")).read_bytes())


def intervention():
    value = preset("scheduled-intervention")
    value["intervention"]["stop_after_event_id"] = "connection"
    return parse_scenario(value)


def test_one_ulp_grid_inspects_and_is_authoritative_through_resume():
    s = intervention()
    _, records, _ = run_intervention(s, IDENTITY)
    cp = copy.deepcopy(records[-1])
    for state in cp["columns"].values():
        state["radius_m"][5] = float(np.nextafter(state["radius_m"][5], np.inf))
    cp["sha256"] = checkpoint_digest({k: v for k, v in cp.items() if k != "sha256"})
    validate_checkpoint(s, cp)
    value = s.model_dump()
    value["intervention"].update(
        stop_after_event_id=None,
        continuation={
            "action": "resume",
            "parent_run_id": "run_" + "a" * 32,
            "checkpoint": cp,
        },
    )
    datasets, new, _ = run_intervention(parse_scenario(value), IDENTITY)
    for d in datasets:
        stored = cp["columns"][d["id"]]
        assert d["grid"]["radius_m"] == stored["radius_m"]
        assert d["rock_temperature_C"][0] == stored["rock_temperature_C"]
        assert d["fluid_node_temperature_C"][0] == stored["fluid_node_temperature_C"]
        assert d["statistics"]["energy_passed"]
        assert all(c["columns"][d["id"]]["radius_m"] == stored["radius_m"] for c in new)


@pytest.mark.parametrize(
    "mutation",
    ["large_grid", "nonmonotonic", "missing_field", "energy", "checksum", "geometry"],
)
def test_portability_bound_does_not_weaken_integrity(mutation):
    s = intervention()
    _, records, _ = run_intervention(s, IDENTITY)
    cp = copy.deepcopy(records[-1])
    col = cp["columns"]["column-1"]
    if mutation == "large_grid":
        col["radius_m"][5] += 0.000001
    elif mutation == "nonmonotonic":
        col["radius_m"][5] = col["radius_m"][4]
    elif mutation == "missing_field":
        col.pop("rock_temperature_C")
    elif mutation == "energy":
        col["energy_in_J_m"] += 1.0e6
    elif mutation == "geometry":
        cp["geometry_hash"] = "0" * 64
    if mutation != "checksum":
        cp["sha256"] = checkpoint_digest({k: v for k, v in cp.items() if k != "sha256"})
    else:
        cp["sha256"] = "0" * 64
    with pytest.raises(CheckpointError):
        validate_checkpoint(s, cp)
