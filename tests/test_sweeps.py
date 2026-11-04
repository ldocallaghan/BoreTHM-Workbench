import io
import json
import os
import subprocess
import sys
import time
import zipfile

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from borethm_workbench.adapter import (
    CalculationError,
    engine_identity,
    cache_status,
    run_circulation,
)
from borethm_workbench.contracts import SweepRequest, Scenario
from borethm_workbench.sweeps import (
    plan,
    submit,
    read,
    cancel,
    verify_execution,
    export,
)
from borethm_workbench.store import Store, Conflict
from borethm_workbench.service import create_app
from borethm_workbench.bundles import inspect_bundle


def request(scenario, values=(28.0, 32.0), second=False):
    axes = [{"parameter": "mass_flow_kg_s", "values": list(values)}]
    if second:
        axes.append({"parameter": "inlet_temperature_C", "values": [40.0, 45.0]})
    return SweepRequest(
        title="Synthetic flow/temperature sweep", scenario=scenario, axes=axes
    )


@pytest.mark.parametrize(
    "mutation", ["size", "axes", "duplicates", "nonfinite", "unsupported"]
)
def test_sweep_contract_bounds(scenario, mutation):
    value = request(scenario).model_dump()
    if mutation == "size":
        value["axes"] = [
            {"parameter": "mass_flow_kg_s", "values": list(map(float, range(1, 7)))},
            {
                "parameter": "inlet_temperature_C",
                "values": list(map(float, range(1, 7))),
            },
        ]
    elif mutation == "axes":
        value["axes"] *= 2
    elif mutation == "duplicates":
        value["axes"][0]["values"] = [28.0, 28.0]
    elif mutation == "nonfinite":
        value["axes"][0]["values"] = [float("nan")]
    else:
        value["axes"][0]["parameter"] = "pipe_radius_m"
    with pytest.raises(ValidationError):
        SweepRequest.model_validate(value)


def test_all_cases_validated_before_any_mutation_and_original_unchanged(
    tmp_path, scenario
):
    original = scenario.model_dump()
    store = Store(tmp_path)
    with pytest.raises(CalculationError):
        submit(
            store,
            request(scenario, (28.0, 0.0)),
            None,
            engine_identity(),
            cache_status(),
        )
    assert not store.list() and scenario.model_dump() == original
    cases = plan(request(scenario, second=True))["cases"]
    assert len(cases) == 4
    assert all(
        c["scenario"]["numerics"]["initialisation"] == "deterministic-default-v1"
        for c in cases
    )
    assert cases[0]["parameters"] == {
        "mass_flow_kg_s": 28.0,
        "inlet_temperature_C": 40.0,
    }


def test_25_case_dedicated_admission_ids_idempotency_and_cancel(tmp_path, scenario):
    store = Store(tmp_path)
    r = request(scenario, (25.0, 27.0, 29.0, 31.0, 33.0))
    raw = r.model_dump()
    raw["axes"].append(
        {"parameter": "inlet_temperature_C", "values": [35.0, 37.0, 39.0, 41.0, 43.0]}
    )
    r = SweepRequest.model_validate(raw)
    build, cache = engine_identity(), cache_status()
    sid, created = submit(store, r, "stable-key", build, cache)
    assert created
    again, created = submit(store, r, "stable-key", build, cache)
    assert again == sid and not created
    result = read(store, sid)
    assert (
        len(result["cases"]) == 25 and len({c["run_id"] for c in result["cases"]}) == 25
    )
    assert len(store.list(100)) == 25
    with pytest.raises(Conflict):
        submit(store, request(scenario), "another", build, cache)
    with pytest.raises(Conflict):
        submit(store, request(scenario), "stable-key", build, cache)
    cancel(store, sid)
    assert read(store, sid)["state_counts"] == {"cancel_requested": 25}


def test_atomic_batch_rollback_does_not_leave_revisions_or_jobs(
    tmp_path, scenario, monkeypatch
):
    store = Store(tmp_path)
    original = store.event
    count = 0

    def fail_second(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("simulated metadata failure")
        return original(*args)

    monkeypatch.setattr(store, "event", fail_second)
    with pytest.raises(OSError):
        submit(store, request(scenario), None, engine_identity(), cache_status())
    with store.connection(write=False) as conn:
        for table in (
            "sweeps",
            "sweep_cases",
            "runs",
            "scenarios",
            "revisions",
            "events",
        ):
            assert conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0


def test_frozen_build_changed_rejected_and_failed_case_remains_visible(
    tmp_path, scenario
):
    store = Store(tmp_path)
    build, cache = engine_identity(), cache_status()
    sid, _ = submit(store, request(scenario), None, build, cache)
    rid = store.claim()
    with pytest.raises(CalculationError, match="identity changed"):
        verify_execution(store, rid, {**build, "app_version": "different"}, cache)
    store.finish(
        rid,
        "failed",
        {
            "failure": {
                "code": "SWEEP_BUILD_CHANGED",
                "message": "Frozen build changed",
            },
            "acceptance": "rejected",
        },
    )
    result = read(store, sid)
    assert len(result["cases"]) == 2 and result["cases"][0]["state"] == "failed"
    assert (
        result["cases"][0]["summary"] is None
        and result["cases"][0]["failure_kind"] == "sweep_build_changed"
    )
    with pytest.raises(Conflict):
        export(store, sid)


def test_reverse_order_cold_start_results_have_same_coordinates_and_fields(scenario):
    r = request(scenario)
    forward = {
        c["parameters"]["mass_flow_kg_s"]: run_circulation(
            Scenario.model_validate(c["scenario"])
        )
        for c in plan(r)["cases"]
    }
    for c in reversed(plan(r)["cases"]):
        current = run_circulation(Scenario.model_validate(c["scenario"]))
        prior = forward[c["parameters"]["mass_flow_kg_s"]]
        assert (
            current["profile"] == prior["profile"]
            and current["summary"] == prior["summary"]
        )
        assert (
            current["numerical_profile"]["initialisation"] == "deterministic-default-v1"
        )


def test_actual_sweep_worker_summary_export_reproduction_and_restore(
    tmp_path, scenario
):
    data = tmp_path / "data"
    with TestClient(create_app(data)) as client:
        preview = client.post(
            "/api/v1/sweeps/preview", json=request(scenario, second=True).model_dump()
        )
        assert preview.status_code == 200 and preview.json()["case_count"] == 4
        assert not client.get("/api/v1/runs").json()["items"]
        submitted = client.post(
            "/api/v1/sweeps",
            json=request(scenario, second=True).model_dump(),
            headers={"Idempotency-Key": "live-sweep"},
        )
        assert submitted.status_code == 202, submitted.text
        sid = submitted.json()["id"]
        end = time.monotonic() + 60
        while time.monotonic() < end:
            result = client.get("/api/v1/sweeps/" + sid).json()
            if result["state"] == "finished":
                break
            time.sleep(0.1)
        assert result["state"] == "finished" and result["accepted_cases"] == 4, result
        assert result["state_counts"] == {"completed": 4}
        assert all(c["integrity"] == "verified" for c in result["cases"])
        assert result["cases"][0]["differences_from_baseline"]["spp"]["value"] == 0.0
        other = client.get("/api/v1/sweeps/" + sid + "?baseline_index=3").json()
        assert other["cases"][3]["differences_from_baseline"]["spp"]["value"] == 0.0
        assert len(client.get("/api/v1/runs").json()["items"]) == 4
        response = client.get("/api/v1/sweeps/" + sid + "/export")
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert len([n for n in archive.namelist() if n.endswith(".zip")]) == 4
            blob = archive.read("case-1.zip")
            assert inspect_bundle(blob)["manifest"]["acceptance"] in (
                "accepted",
                "qualified",
            )
        path = tmp_path / "case.zip"
        path.write_bytes(blob)
        replay = subprocess.run(
            [
                sys.executable,
                "-I",
                "-m",
                "borethm_workbench.cli",
                "reproduce",
                str(path),
            ],
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert replay.returncode == 0, replay.stdout + replay.stderr
    with TestClient(create_app(data)) as client:
        restored = client.get("/api/v1/sweeps/" + sid).json()
        assert restored == result


def test_finished_failed_and_cancelled_sweep_exports_every_case_record(
    tmp_path, scenario
):
    store = Store(tmp_path)
    sid, _ = submit(store, request(scenario), None, engine_identity(), cache_status())
    result = read(store, sid)
    for case in result["cases"]:
        store.cancel(case["run_id"])
        store.finish(
            case["run_id"],
            "cancelled",
            {
                "failure": {"code": "USER_CANCELLED", "message": "Cancelled"},
                "acceptance": "rejected",
            },
        )
    result = read(store, sid)
    assert (
        result["state"] == "finished"
        and result["accepted_cases"] == 0
        and len(result["cases"]) == 2
    )
    with zipfile.ZipFile(io.BytesIO(export(store, sid))) as archive:
        assert not any(n.endswith(".zip") for n in archive.namelist())
        assert len(json.loads(archive.read("sweep.json"))["cases"]) == 2
