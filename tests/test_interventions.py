import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from borethm_workbench.contracts import parse_scenario, canonical_bytes
from borethm_workbench.histories import wall_states
from borethm_workbench.interventions import (
    BUILD_KEYS,
    CheckpointError,
    run_intervention,
    schedule_identity,
    checkpoint_digest,
)
from borethm_workbench.service import create_app
from borethm_workbench.bundles import inspect_bundle
from test_jobs import wait_terminal

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = {key: "test-" + key for key in BUILD_KEYS}


def scenario(paused=False):
    s = json.loads(
        (
            ROOT / "src/borethm_workbench/presets/scheduled-intervention.json"
        ).read_bytes()
    )
    s["numerics"]["radial_nodes"] = 32
    s["intervention"]["stop_after_event_id"] = "connection" if paused else None
    return parse_scenario(s)


def continuing(s, cp, stop=None, action="resume"):
    value = s.model_dump()
    value["intervention"].update(
        stop_after_event_id=stop,
        continuation={
            "action": action,
            "parent_run_id": "run_" + "a" * 32,
            "checkpoint": cp,
        },
    )
    return parse_scenario(value)


def test_pause_resume_exact_full_fields_nodes_times_and_energy():
    full = scenario()
    all_data, all_cp, _ = run_intervention(full, IDENTITY)
    paused = scenario(True)
    _, cp, _ = run_intervention(paused, IDENTITY)
    resumed = continuing(full, cp[-1])
    rest, _, meta = run_intervention(resumed, IDENTITY)
    assert (
        schedule_identity(full)
        == schedule_identity(resumed)
        == schedule_identity(paused)
    )
    assert meta["time_origin_s"] == 2400.0
    for a, b in zip(all_data, rest):
        start = next(
            i
            for i, t in enumerate(a["time_s"])
            if t == 2400 and a["event_side"][i] == "right"
        )
        for key in (
            "time_s",
            "rock_temperature_C",
            "fluid_node_temperature_C",
            "energy_in_J_m",
            "energy_far_J_m",
            "stored_J_m",
        ):
            assert a[key][start:] == b[key], key
        assert b["statistics"]["energy_passed"]
    assert cp[-1]["columns"] == all_cp[1]["columns"]


def test_static_to_static_keeps_accepted_node_and_rock():
    datasets, _, _ = run_intervention(scenario(), IDENTITY)
    for d in datasets:
        right = next(
            i
            for i, t in enumerate(d["time_s"])
            if t == 2400 and d["event_side"][i] == "right"
        )
        assert d["rock_temperature_C"][right - 1] == d["rock_temperature_C"][right]
        assert (
            d["fluid_node_temperature_C"][right - 1]
            == d["fluid_node_temperature_C"][right]
        )
        assert d["fluid_node_temperature_C"][right] != 45.0
        assert all(e["passed"] for e in d["statistics"]["event_energy"])
        assert len(d["statistics"]["external_reservoir_inventory_changes_J_m"]) == 1


def test_direct_unchanged_core_reference_for_circulation_static_restart():
    from borethm import wall_thermal as core

    value = scenario().model_dump()
    del value["definition"]["states"][2]
    s = parse_scenario(value)
    datasets, _, _ = run_intervention(s, IDENTITY)
    for col, data in zip(s.definition.columns, datasets):
        frames = []
        raw = core.run(
            wall_states(s, col),
            col.far_field_temperature_C,
            col.wall_radius_m,
            col.conductivity_W_m_K,
            col.volumetric_heat_capacity_J_m3_K,
            n=s.numerics.radial_nodes,
            r_max=col.outer_radius_m,
            T0=np.full(s.numerics.radial_nodes, col.initial_rock_temperature_C),
            dt0=s.numerics.initial_step_s,
            growth=s.numerics.step_growth,
            observe=frames.append,
        )
        np.testing.assert_allclose(
            [f["temperature_C"] for f in frames],
            data["rock_temperature_C"],
            atol=1.0e-12,
            rtol=0,
        )
        assert abs(raw.energy_in - data["energy_in_J_m"][-1]) < 1.0e-7


def test_chained_continuations_preserve_absolute_time_and_state():
    s = scenario()
    full, _, _ = run_intervention(s, IDENTITY)
    _, cp, _ = run_intervention(scenario(True), IDENTITY)
    first = continuing(s, cp[-1], stop="hold")
    _, second_cp, meta = run_intervention(first, IDENTITY)
    assert meta["status"] == "paused_at_event" and meta["time_end_s"] == 2700
    final = continuing(s, second_cp[-1])
    data, _, meta = run_intervention(final, IDENTITY)
    assert meta["time_origin_s"] == 2700 and meta["status"] == "finished"
    for a, b in zip(full, data):
        assert a["rock_temperature_C"][-1] == b["rock_temperature_C"][-1]


@pytest.mark.parametrize(
    "mutation",
    [
        "domain",
        "horizon",
        "capacity",
        "reset",
        "flux",
        "history",
        "numerics",
        "field",
        "checksum",
        "node",
        "grid",
        "time",
    ],
)
def test_invalid_remapping_history_or_incomplete_checkpoint_rejected(mutation):
    s = scenario()
    _, records, _ = run_intervention(scenario(True), IDENTITY)
    value = continuing(s, records[-1]).model_dump()
    cp = value["intervention"]["continuation"]["checkpoint"]
    if mutation == "domain":
        value["definition"]["columns"][0]["outer_radius_m"] = 31.0
    elif mutation == "horizon":
        value["intervention"]["maximum_time_s"] = 100.0
    elif mutation == "capacity":
        value["definition"]["states"][2]["fluid_capacity_J_m_K"] = 200000.0
    elif mutation == "reset":
        value["definition"]["states"][2]["initial_fluid_temperature_C"] = 20.0
    elif mutation == "flux":
        value["definition"]["states"][2] = {
            "kind": "flux",
            "id": "hold",
            "label": "Invalid flux",
            "duration_s": 300.0,
            "flux_into_rock_W_m": 10.0,
        }
    elif mutation == "history":
        value["definition"]["states"][0]["fluid_temperature_C"] = 25.0
    elif mutation == "numerics":
        value["numerics"]["initial_step_s"] = 2.0
    elif mutation == "field":
        cp["columns"]["column-1"]["rock_temperature_C"].pop()
    elif mutation == "node":
        cp["columns"]["column-1"]["fluid_node_temperature_C"] = None
    elif mutation == "grid":
        cp["columns"]["column-1"]["radius_m"][0] = 0.2
    elif mutation == "time":
        cp["time_s"] = 2399.0
    else:
        cp["sha256"] = "0" * 64
    if mutation != "checksum":
        cp["sha256"] = checkpoint_digest({k: v for k, v in cp.items() if k != "sha256"})
    with pytest.raises(ValidationError):
        parse_scenario(value)


def test_checkpoint_build_incompatibility_rejected_before_integration():
    s = scenario()
    _, cp, _ = run_intervention(scenario(True), IDENTITY)
    with pytest.raises(CheckpointError, match="build differs"):
        run_intervention(continuing(s, cp[-1]), {**IDENTITY, "app_version": "other"})


def test_changed_future_branch_continuity_and_distinct_result():
    s = scenario()
    original, _, _ = run_intervention(s, IDENTITY)
    _, cp, _ = run_intervention(scenario(True), IDENTITY)
    value = continuing(s, cp[-1], action="branch").model_dump()
    value["definition"]["states"][-1]["fluid_temperature_C"] = 10.0
    value["definition"]["columns"][-1]["boundary_overrides"]["restart"][
        "fluid_temperature_C"
    ] = 15.0
    data, _, _ = run_intervention(parse_scenario(value), IDENTITY)
    for d, a in zip(data, original):
        assert (
            d["rock_temperature_C"][0]
            == cp[-1]["columns"][d["id"]]["rock_temperature_C"]
        )
        assert d["rock_temperature_C"][-1] != a["rock_temperature_C"][-1]
        assert d["statistics"]["energy_passed"]


def test_old_scenario_serialization_omits_intervention_and_origin():
    value = json.loads(
        (ROOT / "src/borethm_workbench/presets/open-hole-history.json").read_bytes()
    )
    s = parse_scenario(value)
    assert "intervention" not in s.model_dump()
    assert canonical_bytes(s.model_dump()) == canonical_bytes(
        parse_scenario(s.model_dump()).model_dump()
    )


def test_t36_actual_workers_branch_export_public_reproduction_and_tamper(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        s = scenario(True)
        job = client.post("/api/v1/runs", json={"scenario": s.model_dump()})
        assert job.status_code == 202, job.text
        parent = wait_terminal(client, job.json()["id"])
        assert parent["state"] == "completed", parent
        rid = parent["id"]
        assert parent["manifest"]["intervention"]["status"] == "paused_at_event"
        assert client.get("/api/v1/runs/" + rid + "/checkpoints").status_code == 200
        index = client.get("/api/v1/runs/" + rid + "/checkpoints").json()
        assert [x["time_s"] for x in index["checkpoints"]] == [1800.0, 2400.0]
        bad = client.post(
            "/api/v1/runs/" + rid + "/branches",
            json={"action": "resume", "checkpoint_id": "interpolated-2300"},
        )
        assert bad.status_code == 422
        for action in ("resume", "branch"):
            request = {"action": action, "checkpoint_id": "event-2"}
            if action == "branch":
                future = [x.model_dump() for x in s.definition.states[2:]]
                future[-1]["fluid_temperature_C"] = 10.0
                request["future_states"] = future
            response = client.post("/api/v1/runs/" + rid + "/branches", json=request)
            assert response.status_code == 201, response.text

            def browser_numbers(item):
                if isinstance(item, dict):
                    return {k: browser_numbers(v) for k, v in item.items()}
                if isinstance(item, list):
                    return [browser_numbers(v) for v in item]
                return (
                    int(item) if isinstance(item, float) and item.is_integer() else item
                )

            value = browser_numbers(response.json()["scenario"])
            forged = copy.deepcopy(value)
            cp = forged["intervention"]["continuation"]["checkpoint"]
            cp["parent"] = {"run_id": "invented", "checkpoint_sha256": "0" * 64}
            cp["sha256"] = checkpoint_digest(
                {k: v for k, v in cp.items() if k != "sha256"}
            )
            assert (
                client.post("/api/v1/runs", json={"scenario": forged}).status_code
                == 422
            )
            submitted = client.post("/api/v1/runs", json={"scenario": value})
            assert submitted.status_code == 202, submitted.text
            child = wait_terminal(client, submitted.json()["id"])
            assert child["state"] == "completed", child
            assert child["manifest"]["intervention"]["time_origin_s"] == 2400.0
            bundle = client.get("/api/v1/runs/" + child["id"] + "/export").content
            imported = inspect_bundle(bundle)
            assert all(d["time_s"][0] == 2400 for d in imported["datasets"])
            archive = tmp_path / (action + ".zip")
            archive.write_bytes(bundle)
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-m",
                    "borethm_workbench.cli",
                    "reproduce",
                    str(archive),
                ],
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            assert json.loads(result.stdout)["passed"]
        assert len(client.get("/api/v1/runs").json()["items"]) == 3
        events = app.state.store.events(rid, 0)
        assert len([e for e in events if e["type"] == "checkpoint_available"]) == 2
        assert events[-1]["type"] == "completed"
