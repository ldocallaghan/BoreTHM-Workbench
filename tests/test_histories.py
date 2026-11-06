from scenario_inputs import preset_bytes
import copy
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from borethm_workbench.adapter import CalculationError, validate
from borethm_workbench.bundles import inspect_bundle
from borethm_workbench.contracts import HistoryDataset, parse_scenario
from borethm_workbench.histories import (
    compare_histories,
    run_history,
    sample_in_state,
    tool_dataset,
    wall_dataset,
    wall_states,
)
from borethm_workbench.service import create_app
from test_jobs import wait_terminal

PRESETS = Path(__file__).resolve().parents[1] / "src/borethm_workbench/presets"


def preset(name):
    return parse_scenario(json.loads(preset_bytes(PRESETS / (name + ".json"))))


def test_wall_observation_matches_unobserved_core_and_columns():
    from borethm import wall_thermal as core

    scenario = preset("open-hole-history")
    outputs = []
    for column in scenario.definition.columns:
        dataset = wall_dataset(scenario, column)
        raw = core.run(
            wall_states(scenario, column),
            column.far_field_temperature_C,
            column.wall_radius_m,
            column.conductivity_W_m_K,
            column.volumetric_heat_capacity_J_m3_K,
            n=scenario.numerics.radial_nodes,
            r_max=column.outer_radius_m,
            T0=np.full(
                scenario.numerics.radial_nodes, column.initial_rock_temperature_C
            ),
            dt0=scenario.numerics.initial_step_s,
            growth=scenario.numerics.step_growth,
        )
        np.testing.assert_array_equal(dataset["rock_temperature_C"][-1], raw.fields[-1])
        assert dataset["energy_in_J_m"][-1] == raw.energy_in
        assert dataset["stored_J_m"][-1] == raw.stored
        assert dataset["statistics"]["energy_passed"]
        assert all(z == column.md_m for z in dataset["md_m"])
        assert len(dataset["events"]) == 3
        boundary = dataset["events"][1]
        a, b = boundary["left_index"], boundary["right_index"]
        assert dataset["time_s"][a] == dataset["time_s"][b]
        assert dataset["event_side"][a : b + 1] == ["left", "right"]
        np.testing.assert_array_equal(
            dataset["rock_temperature_C"][a], dataset["rock_temperature_C"][b]
        )
        outputs.append(dataset)
    # Changing one independent column cannot change another's state.
    scenario.definition.columns[0].conductivity_W_m_K *= 2
    assert wall_dataset(scenario, scenario.definition.columns[1]) == outputs[1]
    assert outputs[0]["rock_temperature_C"][-1] != outputs[1]["rock_temperature_C"][-1]


def test_tool_interior_staging_and_core_statistics():
    from borethm import tool_thermal as core

    scenario = preset("tool-trip-history")
    d = tool_dataset(scenario)
    for phase in d["statistics"]["phases"]:
        if phase["id"].startswith("stage-"):
            samples = [
                t for t, s in zip(d["time_s"], d["state_id"]) if s == phase["id"]
            ]
            assert len(samples) == 12 and samples[0] == phase["start_s"] + 10
            assert samples[-1] == phase["end_s"]
    history = core.History(
        np.asarray(d["time_s"]),
        np.asarray(d["steel_temperature_C"]),
        np.asarray(d["internal_fluid_temperature_C"]),
        d["state_label"],
    )
    assert history.peak() == (
        d["statistics"]["peak_internal_fluid_C"],
        d["statistics"]["peak_internal_fluid_time_s"],
    )
    for threshold in scenario.definition.threshold_temperatures_C:
        assert (
            history.time_above(threshold)
            == d["statistics"]["time_above_s"][str(threshold)]
        )
    assert (
        d["steel_temperature_C"][0] == 25 and d["internal_fluid_temperature_C"][0] == 30
    )
    assert d["time_s"][-1] == 3140


@pytest.mark.parametrize(
    "mutation",
    ["cased", "static_initial", "override", "initial_grid", "too_many_steps"],
)
def test_history_invalid_and_resource_requests_fail_before_work(mutation):
    data = preset("open-hole-history").model_dump()
    if mutation == "cased":
        data["definition"]["columns"][0]["boundary"] = "cased"
    elif mutation == "static_initial":
        data["definition"]["states"] = data["definition"]["states"][1:2]
    elif mutation == "override":
        data["definition"]["columns"][0]["boundary_overrides"] = {
            "not-a-state": {"film_W_m2_K": 10}
        }
    elif mutation == "initial_grid":
        data["definition"]["columns"][0]["initial_profile_C"] = [60.0]
    else:
        data["numerics"].update(initial_step_s=0.001, step_growth=1.0)
    with pytest.raises((ValidationError, CalculationError)):
        validate(parse_scenario(data))


@pytest.mark.parametrize("mutation", ["field", "grid", "bracket", "event_side"])
def test_snapshot_contract_rejects_forged_coordinates(mutation):
    d = wall_dataset(
        preset("open-hole-history"), preset("open-hole-history").definition.columns[0]
    )
    if mutation == "field":
        d["rock_temperature_C"][1][0] += 1
    elif mutation == "grid":
        d["grid"]["radius_m"][1] += 0.01
    elif mutation == "bracket":
        d["snapshots"][1]["source_timestep_bracket_s"] = [-1, 1]
    else:
        d["event_side"][d["events"][1]["right_index"]] = "interior"
    with pytest.raises(ValidationError):
        HistoryDataset.model_validate(d)


def test_comparison_interpolates_only_within_same_state():
    a = tool_dataset(preset("tool-trip-history"))
    value, bracket = sample_in_state(
        a, "steel_temperature_C", 505.0, "stage-0", "interior"
    )
    assert (
        value is None and bracket is None
    )  # 500 event -> first stage sample 510: no invented right state.
    value, bracket = sample_in_state(
        a, "steel_temperature_C", 515.0, "stage-0", "interior"
    )
    assert value is not None and bracket == [510.0, 520.0]
    equal = compare_histories(a, a)
    assert all(v == 0 or v is None for v in equal["fields"]["steel_temperature_C"])
    b = copy.deepcopy(a)
    b["events"][1]["time_s"] += 1
    with pytest.raises(CalculationError, match="matching event"):
        compare_histories(a, b)


@pytest.mark.parametrize("delta,passed", [(0.0005, True), (0.01, False)])
def test_history_reproduction_applies_temperature_tolerance_to_peaks(delta, passed):
    from borethm_workbench.histories import compare_history_results

    original = run_history(preset("tool-trip-history"))
    current = copy.deepcopy(original)
    current["datasets"][0]["statistics"]["peak_internal_fluid_C"] += delta
    assert (
        compare_history_results(original, original["datasets"], current)["passed"]
        is passed
    )


@pytest.mark.parametrize("name", ["open-hole-history", "tool-trip-history"])
def test_history_real_job_dataset_export_import_and_public_reproduce(tmp_path, name):
    scenario = preset(name)
    with TestClient(create_app(tmp_path)) as client:
        submitted = client.post(
            "/api/v1/runs", json={"scenario": scenario.model_dump()}
        )
        assert submitted.status_code == 202, submitted.text
        run = wait_terminal(client, submitted.json()["id"], limit=30)
        assert run["state"] == "completed", run
        rid = run["id"]
        m = run["manifest"]
        assert "datasets" not in m and "profile" not in m
        assert m["build"]["history_capabilities"][scenario.mode]
        entry = m["history"]["columns"][0]
        response = client.get(f"/api/v1/runs/{rid}/history/{entry['id']}")
        assert response.status_code == 200, response.text
        dataset = response.json()
        HistoryDataset.model_validate(dataset)
        assert client.get(f"/api/v1/runs/{rid}/history/missing").status_code == 404
        compared = client.post(
            "/api/v1/history-comparisons",
            json={
                "baseline_run_id": rid,
                "baseline_dataset_id": entry["id"],
                "variant_run_id": rid,
                "variant_dataset_id": entry["id"],
            },
        )
        assert compared.status_code == 200, compared.text
        bundle = client.get(f"/api/v1/runs/{rid}/export")
        assert bundle.status_code == 200, bundle.text
        inspected = inspect_bundle(bundle.content)
        assert inspected["datasets"][0] == dataset
        imported = client.post(
            "/api/v1/imports",
            content=bundle.content,
            headers={"Content-Type": "application/zip"},
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["datasets"][0] == dataset
        archive = tmp_path / "history.zip"
        archive.write_bytes(bundle.content)
        command = subprocess.run(
            [sys.executable, "-m", "borethm_workbench.cli", "reproduce", str(archive)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert command.returncode == 0, command.stdout + command.stderr
        assert json.loads(command.stdout)["passed"]
        # Reopening data does not enqueue calculations.
        assert len(client.get("/api/v1/runs").json()["items"]) == 1
