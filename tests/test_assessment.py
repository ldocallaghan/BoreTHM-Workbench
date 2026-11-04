import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from borethm_workbench.contracts import parse_scenario_json, parse_scenario
from borethm_workbench.assessment import load_frozen_site, validate_assessment
from borethm_workbench.bundles import inspect_bundle
from borethm_workbench.service import create_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def assessment():
    return parse_scenario_json(
        (ROOT / "src/borethm_workbench/presets/newberry-assessment.json").read_bytes()
    )


def test_manual_circulation_fields_are_rejected(assessment):
    data = assessment.model_dump()
    data["definition"]["operating"] = {"mass_rate_kg_s": 5.0}
    with pytest.raises(ValidationError):
        parse_scenario(data)
    assert assessment.definition.options.calculation_mode == "v1.3.4"
    assert assessment.model.supported_release == "1.6.0"


def test_site_inputs_are_frozen_and_validated(assessment):
    assert all(
        w["code"] == "SITE_INPUT_QUALIFICATION" for w in validate_assessment(assessment)
    )
    site = load_frozen_site(assessment)
    assert site.name.startswith("Newberry")
    assert site.target() == pytest.approx(3629.54044732043)
    assert (
        site.programme is None
    )  # evaluator explicitly selects its effective programme


def test_complete_assessment_matches_existing_golden(tmp_path, assessment):
    from borethm.studies.release_v134 import reference_row

    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            "/api/v1/runs", json={"scenario": assessment.model_dump()}
        )
        assert response.status_code == 202, response.text
        rid = response.json()["id"]
        end = time.monotonic() + 240
        while time.monotonic() < end:
            run = client.get("/api/v1/runs/" + rid).json()
            if run["state"] in {"completed", "failed", "timed_out", "cancelled"}:
                break
            time.sleep(0.2)
        assert run["state"] == "completed", run
        manifest = run["manifest"]
        pin = json.loads(
            (ROOT / "tests/fixtures/assessment-reference.json").read_bytes()
        )
        from borethm_workbench.reproduction import _reference_errors

        assert not _reference_errors(reference_row(manifest["assessment"]), pin)
        assert (
            manifest["validity"]["assessment_status"]
            == manifest["assessment"]["status"]["label"]
        )
        assert manifest["numerical_profile"]["effective_coupled"] is True
        assert manifest["assessment"]["programme"]["flow"] == 30
        assert manifest["assessment_full_data"]["wall_history"]["r"]
        exported = client.get("/api/v1/runs/" + rid + "/export")
        assert exported.status_code == 200
        imported = inspect_bundle(exported.content)
        assert imported["manifest"]["assessment"] == manifest["assessment"]
        assert any(
            a["filename"] == "assessment-full.json" for a in manifest["artifacts"]
        )
        from borethm_workbench.reproduction import compare_results
        import copy

        assert compare_results(manifest, manifest)["passed"]
        changed = copy.deepcopy(manifest)
        changed["assessment"]["windows"]["drilling_fluid"]["lo"] += 100.0
        comparison = compare_results(manifest, changed)
        assert not comparison["passed"]
        assert "assessment.reference_reference" in comparison["failed_checks"]
