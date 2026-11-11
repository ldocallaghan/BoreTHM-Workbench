"""Installed model capabilities and result identity."""

import json

from fastapi.testclient import TestClient

from borethm_workbench.adapter import engine_identity, history_capabilities
from borethm_workbench.service import create_app


def test_history_features_follow_function_signatures(monkeypatch):
    import borethm

    monkeypatch.setattr(borethm, "wall_temperatures", lambda: None)
    monkeypatch.setattr(borethm, "tool_trip_in", lambda: None)
    assert not any(history_capabilities().values())


def test_missing_history_does_not_disable_standard_modes(tmp_path, monkeypatch):
    import borethm

    monkeypatch.setattr(borethm, "wall_temperatures", lambda: None)
    monkeypatch.setattr(borethm, "tool_trip_in", lambda: None)
    with TestClient(create_app(tmp_path, start_jobs=False)) as client:
        result = client.get("/api/v1/capabilities").json()
        assert result["modes"] == ["circulation_state", "site_assessment"]
        assert result["enabled"]["assessment"] and result["enabled"]["sweeps"]
        assert result["enabled"]["survey_stations"]
        assert not result["enabled"]["checkpoints"]
        scenario = client.get("/api/v1/presets/open-hole-history").json()
        denied = client.post("/api/v1/runs", json={"scenario": scenario})
        assert denied.status_code == 422
        assert denied.json()["detail"][0]["code"] == "CAPABILITY_UNSUPPORTED"
        assert "Install BoreTHM 1.8.0 or later" in denied.text
        standard = client.get("/api/v1/presets/constant-properties").json()
        assert (
            client.post("/api/v1/runs", json={"scenario": standard}).status_code == 202
        )


def test_saved_identity_has_installed_versions_and_dependencies():
    from importlib.metadata import version

    identity = engine_identity()
    assert identity["core_version"] == version("borethm")
    assert identity["app_version"] == version("borethm-workbench")
    assert identity["libraries"]["numpy"] == version("numpy")
    assert identity["core_commit"] is None or len(identity["core_commit"]) == 40


def test_property_identity_ignores_archive_encoding(tmp_path):
    import numpy as np
    from borethm import water_table as wt
    from borethm_workbench.cache_reference import fingerprints

    with np.load(wt._CACHE, allow_pickle=False) as source:
        arrays = {key: source[key] for key in source.files}
    plain = tmp_path / "plain.npz"
    compressed = tmp_path / "compressed.npz"
    np.savez(plain, **arrays)
    np.savez_compressed(compressed, **arrays)
    assert plain.read_bytes() != compressed.read_bytes()
    assert fingerprints(plain) == fingerprints(compressed)


def test_other_archive_versions_are_rejected():
    import io
    import zipfile
    import pytest
    from borethm_workbench.bundles import inspect_bundle

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "manifest.json", json.dumps({"build": {"app_version": "0.1.0"}})
        )
    with pytest.raises(ValueError, match="other application versions"):
        inspect_bundle(stream.getvalue())
