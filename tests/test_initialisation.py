import copy
import json
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from borethm_workbench.adapter import (
    CalculationError,
    cache_status,
    engine_identity,
    make_fluid,
    make_geometry,
    make_settings,
    run_circulation,
)
from borethm_workbench.contracts import Numerics, digest
from borethm_workbench.initialisation import compatible_seed, seed_snapshot, solve
from borethm_workbench.service import create_app


def interactive(scenario):
    scenario = scenario.model_copy(deep=True)
    scenario.numerics = Numerics(initialisation="interactive-bounded-v1")
    return scenario


def test_compatible_accepted_seed_reproduces_cold_target(scenario):
    current = interactive(scenario)
    first = run_circulation(current)
    first["run_id"] = "run_" + "a" * 32
    seed = seed_snapshot(first)
    current.definition.operating.flow.mass_rate_kg_s = 35.0
    warm = run_circulation(current, seed=seed)
    cold = run_circulation(current)
    assert warm["attempts"][0]["strategy"] == "compatible-accepted-profile-v1"
    assert warm["attempts"][0]["seed"]["run_id"] == first["run_id"]
    assert warm["initialisation"]["accepted_seed"]["profile_sha256"] == digest(
        seed["profile"]
    )
    for name in ("bore_temperature_C", "annulus_temperature_C"):
        np.testing.assert_allclose(
            warm["profile"][name], cold["profile"][name], rtol=0, atol=0.001
        )
    for name, value in cold["summary"].items():
        tolerance = (
            max(1.0, abs(value["value"]) * 1e-6)
            if value["unit"] in ("Pa difference", "W")
            else 0.001
        )
        assert abs(warm["summary"][name]["value"] - value["value"]) <= tolerance
    assert warm["execution_identity"] != cold["execution_identity"]


@pytest.mark.parametrize(
    "mutation", ["geometry", "fluid", "rejected", "nonfinite", "build", "checksum"]
)
def test_incompatible_or_rejected_seed_is_never_used(scenario, mutation):
    current = interactive(scenario)
    first = run_circulation(current)
    first["run_id"] = "run_" + "a" * 32
    seed = seed_snapshot(first)
    if mutation == "geometry":
        current.definition.well.string[0].pipe.layers[0].r_out_m += 0.001
    elif mutation == "fluid":
        seed["compatibility_key"] = "0" * 64
    elif mutation == "rejected":
        seed["acceptance"] = "rejected"
    elif mutation == "nonfinite":
        seed["profile"]["Td"][0] = float("nan")
    elif mutation == "build":
        seed["build"]["app_version"] = "different"
    else:
        seed["profile_sha256"] = "0" * 64
    assert not compatible_seed(seed, current, engine_identity(), cache_status())
    result = run_circulation(current, seed=seed)
    assert result["attempts"][0]["strategy"] == "deterministic-default-v1"
    assert result["initialisation"]["accepted_seed"] is None


def test_retry_budget_and_target_settings_do_not_change(scenario, monkeypatch):
    current = interactive(scenario)
    settings = make_settings(current)
    calls, events = [], []

    def fake(**values):
        calls.append(values)
        intermediate = values["t_years"] in (1 / 365, 1 / 52)
        return {
            "sol": SimpleNamespace(
                success=intermediate,
                status=0 if intermediate else 1,
                message="Injected target failure",
            ),
            "z": np.array([0.0, 3000.0]),
            "Td": np.array([40.0, 100.0]),
            "Tu": np.array([50.0, 101.0]),
        }

    def check(result, values):
        if not result["sol"].success:
            raise CalculationError("SOLVER_NOT_CONVERGED", "Injected target failure")

    monkeypatch.setattr("borethm.circulating_temperatures", fake)
    with pytest.raises(CalculationError) as failure:
        solve(
            current,
            make_geometry(current),
            make_fluid(current.definition.fluid),
            settings,
            engine_identity(),
            cache_status(),
            check,
            notify=lambda kind, data: events.append((kind, data)),
        )
    records = failure.value.diagnostics["attempts"]
    assert len(records) == len(calls) == 5
    assert sum(r["role"] == "target" for r in records) == 3
    assert sum(r["role"] == "continuation_seed" for r in records) == 2
    for record, call in zip(records, calls):
        assert call["n_nodes"] == 160 and call["film_mult"] == 1.0
        if record["role"] == "target":
            assert (
                call["t_years"] == settings["t_years"]
                and call["exposure"] is settings["exposure"]
            )
    assert len(events) == 10 and all(
        "duration_s" in data for kind, data in events if kind == "attempt_finished"
    )


def test_nonconvergence_is_rejected_deterministically(scenario, monkeypatch):
    monkeypatch.setattr(
        "borethm.circulating_temperatures",
        lambda **kwargs: {
            "sol": SimpleNamespace(
                success=False, status=1, message="Injected mesh nodes exhausted"
            )
        },
    )
    with pytest.raises(CalculationError) as failure:
        run_circulation(scenario)
    assert failure.value.code == "SOLVER_NOT_CONVERGED"
    attempt = failure.value.diagnostics["attempts"][0]
    assert attempt["solver_status"] == 1 and "mesh nodes" in attempt["solver_message"]
    assert attempt["outcome"] == "rejected"


def test_actual_job_uses_accepted_seed_and_exports_reproducible_initialisation(
    tmp_path, scenario
):
    from test_jobs import wait_terminal
    from borethm_workbench.bundles import inspect_bundle

    current = interactive(scenario)
    with TestClient(create_app(tmp_path)) as client:
        first = wait_terminal(
            client,
            client.post("/api/v1/runs", json={"scenario": current.model_dump()}).json()[
                "id"
            ],
        )
        assert first["state"] == "completed"
        current.definition.operating.flow.mass_rate_kg_s = 35.0
        second = wait_terminal(
            client,
            client.post("/api/v1/runs", json={"scenario": current.model_dump()}).json()[
                "id"
            ],
        )
        assert second["state"] == "completed"
        manifest = second["manifest"]
        assert manifest["attempts"][0]["strategy"] == "compatible-accepted-profile-v1"
        bundle = client.get("/api/v1/runs/" + second["id"] + "/export")
        imported = inspect_bundle(bundle.content)
        replay = run_circulation(
            current, seed=imported["manifest"]["initialisation"]["accepted_seed"]
        )
        np.testing.assert_allclose(
            replay["profile"]["bore_temperature_C"],
            manifest["profile"]["bore_temperature_C"],
            atol=0.001,
        )
        text = client.get("/api/v1/runs/" + second["id"] + "/events").text
        for kind in (
            "attempt_started",
            "attempt_finished",
            "warning",
            "artifact_available",
            "completed",
        ):
            assert "event: " + kind + "\n" in text


@pytest.mark.parametrize(
    "mismatch", ["build", "cache", "corrupt_artifact", "incomplete_metadata"]
)
def test_seed_search_skips_incompatible_newer_results(
    tmp_path, scenario, physical_result, mismatch
):
    from borethm_workbench.store import Store
    from borethm_workbench.worker import publish

    store = Store(tmp_path)

    def complete(payload):
        run, _ = store.submit(scenario, None)
        rid = store.claim()
        store.stage(rid, "running", {})
        store.stage(rid, "postprocessing", {})
        publish(store, rid, copy.deepcopy(payload))
        return rid

    earlier = complete(physical_result)
    current = copy.deepcopy(physical_result)
    if mismatch == "build":
        current["build"]["app_version"] = "0" * 64
    elif mismatch == "cache":
        current["property_table"]["sha256"] = "0" * 64
    # Exceed the scan limit with nonmatching builds/caches; the compatible
    # result must still be found because identity filtering precedes the limit.
    for _ in range(21 if mismatch in ("build", "cache") else 1):
        newer = complete(current)
    if mismatch == "corrupt_artifact":
        (store.artifact_path(newer) / "profile.csv").write_bytes(b"corrupted")
    elif mismatch == "incomplete_metadata":
        manifest = store.get(newer)["manifest"]
        del manifest["profile"]
        with store.connection() as conn:
            conn.execute(
                "UPDATE runs SET manifest=? WHERE id=?", (json.dumps(manifest), newer)
            )
    seed = store.find_seed(scenario, engine_identity(), cache_status())
    assert seed["run_id"] == earlier
