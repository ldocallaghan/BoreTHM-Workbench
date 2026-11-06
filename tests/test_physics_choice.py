from scenario_inputs import preset_bytes

"""Scenario physics is explicit and checkpoint compatibility includes it."""

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from borethm_workbench.adapter import run_circulation
from borethm_workbench.contracts import parse_scenario, Model
from borethm_workbench.histories import run_history, compare_history_results
from borethm_workbench.interventions import validate_checkpoint, checkpoint_digest
from borethm_workbench.service import create_app

PRESETS = Path(__file__).resolve().parents[1] / "src/borethm_workbench/presets"


def test_new_model_uses_current_physics():
    assert Model().physics == "v1.8.0"


def test_old_model_archive_explains_how_to_rerun():
    import io
    import zipfile
    from borethm_workbench.bundles import inspect_bundle

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps({"build": {"core_version": "1.6.0", "app_version": "0.2.0rc2"}}),
        )
    with pytest.raises(ValueError, match='Re-run the scenario with physics="v1.7.0"'):
        inspect_bundle(stream.getvalue())


def test_previous_schema_loads_with_visible_explanation(tmp_path):
    raw = json.loads(preset_bytes(PRESETS / "synthetic-water.json"))
    raw["schema_version"] = "1.0"
    raw["model"] = {"supported_release": "1.6.0", "hydraulics_model": "v1.6.0"}
    with TestClient(create_app(tmp_path, start_jobs=False)) as client:
        response = client.post("/api/v1/scenarios/validate", json=raw)
        assert response.status_code == 200, response.text
        value = response.json()
        assert value["resolved_scenario"]["schema_version"] == "1.1"
        assert value["resolved_scenario"]["model"]["physics"] == "v1.7.0"
        assert any(w["code"] == "PREVIOUS_SCHEMA_PHYSICS" for w in value["warnings"])


@pytest.mark.parametrize("physics", ["v1.8.0", "v1.7.0"])
def test_checkpoint_reproduction_and_physics_rejection(physics):
    scenario = parse_scenario(
        json.loads(preset_bytes(PRESETS / "scheduled-intervention.json"))
    )
    scenario.model.physics = physics
    result = run_history(scenario)
    cp = result["_checkpoints"][0]
    assert cp["physics"] == physics
    validate_checkpoint(scenario, cp)
    replay = run_history(scenario)
    assert compare_history_results(
        result, result["datasets"], replay, result["_checkpoints"]
    )["passed"]
    other = scenario.model_copy(deep=True)
    other.model.physics = "v1.7.0" if physics == "v1.8.0" else "v1.8.0"
    with pytest.raises(ValueError, match="physics differs"):
        validate_checkpoint(other, cp)
    old = copy.deepcopy(cp)
    old["build"]["core_version"] = "1.6.0"
    old["sha256"] = checkpoint_digest({k: v for k, v in old.items() if k != "sha256"})
    with pytest.raises(ValueError, match="Re-run the scenario"):
        validate_checkpoint(scenario, old)


@pytest.mark.parametrize("physics", ["v1.8.0", "v1.7.0"])
def test_circulation_calls_use_selected_physics(scenario, monkeypatch, physics):
    import borethm
    from borethm.physics import resolve

    scenario.model.physics = physics
    expected = resolve(physics)
    calls = []
    original = borethm.hydraulics

    def observed(*args, **kwargs):
        result = kwargs["result"]
        assert kwargs["physics"] == physics
        for key in ("annulus_model", "friction", "roughness"):
            assert result[key] == expected[key]
            assert key not in kwargs
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(borethm, "hydraulics", observed)
    result = run_circulation(scenario)
    assert len(calls) == 1
    assert result["physics"] == physics
    assert all(result["physics_settings"][k] == v for k, v in expected.items())
