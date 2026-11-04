import copy
import io
import json
import time
import zipfile
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from borethm_workbench.bundles import export_bundle, inspect_bundle
from borethm_workbench.service import create_app
from borethm_workbench.store import Conflict, Store
from borethm_workbench.worker import publish


def wait_terminal(client, rid, limit=15):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        run = client.get("/api/v1/runs/" + rid).json()
        if run["state"] in {"completed", "failed", "cancelled", "timed_out"}:
            return run
        time.sleep(0.05)
    raise AssertionError("Job did not become terminal")


def test_revision_conflict_and_idempotency(tmp_path, scenario):
    store = Store(tmp_path)
    saved = store.create_scenario(scenario)
    store.append_revision(saved["id"], scenario, "1")
    with pytest.raises(Conflict):
        store.append_revision(saved["id"], scenario, "1")
    original, created = store.submit(scenario, "same-key")
    duplicate, created_again = store.submit(scenario, "same-key")
    assert created and not created_again and original["id"] == duplicate["id"]
    scenario.definition.operating.inlet_temperature_C += 1
    with pytest.raises(Conflict):
        store.submit(scenario, "same-key")


def test_cancellation_wins_publication(tmp_path, scenario, physical_result):
    store = Store(tmp_path)
    run, _ = store.submit(scenario, None)
    rid = store.claim()
    store.stage(rid, "running", {})
    store.stage(rid, "postprocessing", {})
    store.cancel(rid)
    publish(store, rid, copy.deepcopy(physical_result))
    assert store.get(rid)["state"] == "cancelled"
    assert not (tmp_path / "artifacts" / rid).exists()
    assert not store.finish(rid, "completed", {})


def test_orphan_recovery(tmp_path, scenario):
    store = Store(tmp_path)
    run, _ = store.submit(scenario, None)
    store.claim()
    Store(tmp_path).recover()
    assert store.get(run["id"])["state"] == "failed"
    assert store.get(run["id"])["manifest"]["failure"]["code"] == "WORKER_FAILED"


def test_bundle_integrity_and_unsafe_paths(tmp_path, scenario, physical_result):
    store = Store(tmp_path)
    run, _ = store.submit(scenario, None)
    rid = store.claim()
    store.stage(rid, "running", {})
    store.stage(rid, "postprocessing", {})
    publish(store, rid, copy.deepcopy(physical_result))
    bundle = export_bundle(store, rid)
    inspected = inspect_bundle(bundle)
    assert inspected["manifest"]["profile"] == physical_result["profile"]
    assert inspected["scenario"] == scenario.model_dump(mode="json")
    for mutation in ("corrupt", "missing", "traversal", "duplicate", "coordinate"):
        output = io.BytesIO()
        with (
            zipfile.ZipFile(io.BytesIO(bundle)) as source,
            zipfile.ZipFile(output, "w") as target,
        ):
            for name in source.namelist():
                if mutation == "missing" and name == "profile.json":
                    continue
                content = source.read(name)
                if mutation == "corrupt" and name == "profile.json":
                    content = b"{}"
                if mutation == "coordinate" and name == "manifest.json":
                    m = json.loads(content)
                    m["profile"]["md_m"][0] = 1.0
                    content = json.dumps(m).encode()
                target.writestr(name, content)
            if mutation == "traversal":
                target.writestr("../escape", b"no")
            if mutation == "duplicate":
                target.writestr("profile.json", b"{}")
        with pytest.raises(ValueError):
            inspect_bundle(output.getvalue())


def test_origin_and_upload_validation(tmp_path, scenario):
    with TestClient(create_app(tmp_path, start_jobs=False)) as client:
        response = client.post(
            "/api/v1/scenarios/validate",
            json=scenario.model_dump(),
            headers={"Origin": "https://evil.example"},
        )
        assert response.status_code == 403
        response = client.post("/api/v1/scenarios/validate", json=scenario.model_dump())
        assert response.status_code == 200
        response = client.post(
            "/api/v1/imports",
            content=b'{"bad":true}',
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422
        response = client.post(
            "/api/v1/imports",
            content=b"not a zip file",
            headers={"Content-Type": "application/zip"},
        )
        assert response.status_code == 422


def test_unicode_preset_labels_and_csv_formula_text(tmp_path, scenario):
    import csv
    from borethm_workbench.adapter import run_circulation
    from borethm_workbench.contracts import digest

    scenario.title = "<img src=x onerror=alert(1)> · 1200 kg/m³ · °C"
    scenario.definition.well.string[0].id = "=1+1"
    scenario.definition.well.hole[0].id = "@SUM(1)"
    store = Store(tmp_path)
    job, _ = store.submit(scenario, None)
    rid = store.claim()
    store.stage(rid, "running", {})
    result = run_circulation(scenario)
    store.stage(rid, "postprocessing", {})
    publish(store, rid, result)
    profile = store.artifact_path(rid) / "profile.csv"
    with profile.open(newline="", encoding="utf-8") as handle:
        first = next(csv.DictReader(handle))
    assert first["component_id"] == "'=1+1" and first["hole_interval_id"] == "'@SUM(1)"
    assert float(first["md_m"]) == 0
    with TestClient(create_app(tmp_path, start_jobs=False)) as client:
        presets = client.get("/api/v1/presets").json()
        mud = next(p for p in presets if p["id"] == "weighted-mud")
        assert (
            "kg/m³" in mud["title"] and "°C" in mud["title"] and "Â" not in mud["title"]
        )
        response = client.get("/api/v1/runs/" + rid)
        assert response.json()["manifest"]["title"] == scenario.title
        assert response.json()["manifest"]["resolved_scenario_hash"] == digest(
            scenario.model_dump()
        )


def test_exact_saved_revision_is_frozen(tmp_path, scenario):
    with TestClient(create_app(tmp_path, start_jobs=False)) as client:
        sid = client.post("/api/v1/scenarios", json=scenario.model_dump()).json()["id"]
        scenario.definition.operating.flow.mass_rate_kg_s = 35.0
        assert (
            client.post(
                "/api/v1/scenarios/" + sid + "/revisions",
                json=scenario.model_dump(),
                headers={"If-Match": "1"},
            ).status_code
            == 201
        )
        response = client.post(
            "/api/v1/runs",
            json={"scenario_id": sid, "revision": 1},
            headers={"Idempotency-Key": "frozen"},
        )
        assert response.status_code == 202, response.text
        run = response.json()
        assert run["scenario_revision"] == 1
        original = client.get(f"/api/v1/scenarios/{sid}/revisions/1").json()
        assert original["definition"]["operating"]["flow"]["mass_rate_kg_s"] == 30.0
        duplicate = client.post(
            "/api/v1/runs",
            json={"scenario_id": sid, "revision": 1},
            headers={"Idempotency-Key": "frozen"},
        )
        assert duplicate.json()["id"] == run["id"]
        assert (
            client.post(
                "/api/v1/runs",
                json={"scenario_id": sid, "revision": 2},
                headers={"Idempotency-Key": "frozen"},
            ).status_code
            == 409
        )


def test_actual_backend_run_compare_export_reproduce(tmp_path, scenario):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/v1/health/ready").status_code == 200
        submitted = client.post(
            "/api/v1/runs",
            json={"scenario": scenario.model_dump()},
            headers={"Idempotency-Key": "actual-run"},
        )
        assert submitted.status_code == 202, submitted.text
        baseline = wait_terminal(client, submitted.json()["id"])
        assert baseline["state"] == "completed", baseline
        scenario.definition.operating.flow.mass_rate_kg_s = 35.0
        second = client.post("/api/v1/runs", json={"scenario": scenario.model_dump()})
        variant = wait_terminal(client, second.json()["id"])
        assert variant["state"] == "completed", variant
        from borethm_workbench.contracts import digest

        assert variant["manifest"]["resolved_scenario"] == scenario.model_dump(
            mode="json"
        )
        assert (
            digest(variant["manifest"]["resolved_scenario"])
            == variant["manifest"]["resolved_scenario_hash"]
        )
        comparison = client.post(
            "/api/v1/comparisons",
            json={"baseline_run_id": baseline["id"], "variant_run_id": variant["id"]},
        )
        assert comparison.status_code == 200, comparison.text
        assert comparison.json()["scalar_differences"]["spp"] > 0
        assert any(
            x["path"].endswith("mass_rate_kg_s")
            for x in comparison.json()["input_diff"]
        )
        bundle = client.get("/api/v1/runs/" + variant["id"] + "/export")
        assert bundle.status_code == 200
        inspected = inspect_bundle(bundle.content)
        from borethm_workbench.adapter import engine_identity

        assert (
            inspected["manifest"]["build"]["core_commit"]
            == engine_identity()["core_commit"]
        )
        replay = adapter_reproduce(inspected)
        assert (
            abs(
                replay["summary"]["spp"]["value"]
                - variant["manifest"]["summary"]["spp"]["value"]
            )
            <= 1.0
        )
        # SSE reconnect after an earlier event returns only later sequence IDs.
        events = client.get(
            "/api/v1/runs/" + variant["id"] + "/events", headers={"Last-Event-ID": "2"}
        )
        assert events.status_code == 200
        ids = [
            int(line[4:])
            for line in events.text.splitlines()
            if line.startswith("id: ")
        ]
        assert ids == sorted(set(ids)) and min(ids) > 2
        # The documented public reproduction command must also work.
        bundle_path = tmp_path / "reproduction.zip"
        bundle_path.write_bytes(bundle.content)
        command = subprocess.run(
            [
                sys.executable,
                "-m",
                "borethm_workbench.cli",
                "reproduce",
                str(bundle_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert command.returncode == 0, command.stdout + command.stderr
        assert json.loads(command.stdout)["passed"] is True


def adapter_reproduce(inspected):
    from borethm_workbench.adapter import run_circulation
    from borethm_workbench.contracts import Scenario

    return run_circulation(
        Scenario.model_validate(inspected["scenario"]),
        seed=inspected["manifest"].get("initialisation", {}).get("accepted_seed"),
    )


def test_real_worker_deadline_and_queue_cancel(tmp_path, scenario):
    with TestClient(create_app(tmp_path, deadline=0.001)) as client:
        queued = client.post(
            "/api/v1/runs", json={"scenario": scenario.model_dump()}
        ).json()
        result = wait_terminal(client, queued["id"])
        assert result["state"] == "timed_out"
        assert result["manifest"]["failure"]["code"] == "NUMERICAL_TIMEOUT"
        assert not (tmp_path / "artifacts" / queued["id"]).exists()
    with TestClient(create_app(tmp_path / "cancel", start_jobs=False)) as client:
        queued = client.post(
            "/api/v1/runs", json={"scenario": scenario.model_dump()}
        ).json()
        cancelled = client.post("/api/v1/runs/" + queued["id"] + "/cancel")
        assert cancelled.json()["state"] == "cancel_requested"
