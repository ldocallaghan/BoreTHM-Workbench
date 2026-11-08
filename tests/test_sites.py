"""Site editor: TOML round trips, field-attached checks and API routes."""

import json
import tomllib

import pytest
from fastapi.testclient import TestClient

from borethm import named_sites
from borethm.site_files import site_text

from borethm_workbench import sites
from borethm_workbench.service import create_app


@pytest.fixture(autouse=True)
def data_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("BORETHM_WORKBENCH_DATA", str(tmp_path / "data"))


def browser_copy(document):
    """The document as the browser holds and returns it: plain JSON."""
    return json.loads(json.dumps(document))


@pytest.mark.parametrize("key", sites.BUILT_IN)
def test_built_in_site_exports_and_loads_field_by_field(key):
    template = site_text(key)
    exported = sites.document_to_toml(browser_copy(sites.document_from_toml(template)))
    loaded = sites.load_text(exported)
    # The starting point the editor uses, as borethm-new-site --from writes it.
    assert sites.site_differences(sites.load_text(template), loaded) == []
    # The model's own built-in profile. The packaged files are snapshots of it:
    # recomputed fits can differ in their last digits, and the Soultz file adds
    # sources for rock conductivity and stiffness.
    builtin = named_sites()[key]
    differences = sites.site_differences(builtin, loaded, rel=1e-9)
    if key == "soultz":
        assert differences == ["data_basis"]
        extra = set(loaded.data_basis) - set(builtin.data_basis)
        assert extra == {"conductivity", "stiffness"}
        assert {k: loaded.data_basis[k] for k in builtin.data_basis} == builtin.data_basis
    else:
        assert differences == []


@pytest.mark.parametrize("key", sites.BUILT_IN)
def test_imported_file_exported_unchanged_loads_to_an_equal_site(key, tmp_path):
    text = site_text(key)
    once = sites.document_to_toml(browser_copy(sites.document_from_toml(text)))
    twice = sites.document_to_toml(browser_copy(sites.document_from_toml(once)))
    assert once == twice
    assert sites.site_differences(sites.load_text(text), sites.load_text(twice)) == []
    assert tomllib.loads(once) == tomllib.loads(text)


def test_writer_handles_programme_tables_and_special_values():
    document = {
        "name": 'Quote " and \\ backslash',
        "geotherm": {"form": "points", "points": [[0.0, 10.0], [1000.0, 50.0]], "gradient_below": 0.03},
        "programme": {"pipe": {"name": "x", "layers": [{"k": 1.0}, {"k": 45.0}]}, "flows": [10, 20]},
        "data_basis": {"well check": ["none", ""]},
        "big": 5e22,
        "small": 1e-07,
    }
    assert tomllib.loads(sites.document_to_toml(document)) == document


def test_check_attaches_errors_and_warnings_to_fields():
    document = sites.document_from_toml(site_text("newberry"))
    result = sites.check(document)
    assert result["valid"] and result["tier"] == "speculative"
    fields = {w["field"] for w in result["warnings"]}
    assert {"geotherm", "data_basis.conductivity", "data_basis.stiffness"} <= fields
    assert result["profiles"]["stress"] and len(result["profiles"]["depth_m"]) > 10

    document["k_rock"] = 70.0
    document["data_basis"]["stress"] = ["guessed", "x"]
    result = sites.check(document)
    assert not result["valid"]
    fields = {e["field"] for e in result["errors"]}
    assert "k_rock" in fields

    document["k_rock"] = 2.0
    result = sites.check(document)
    assert {e["field"] for e in result["errors"]} == {"data_basis.stress"}

    document["data_basis"]["stress"] = ["measured range", "x"]
    document["stress_cases"][1]["source"] = ""
    result = sites.check(document)
    assert result["valid"]
    assert "stress_cases[1]" in {w["field"] for w in result["warnings"]}


def test_blank_values_are_reported_at_their_paths():
    result = sites.check(
        {"name": "", "target_depth": None, "geotherm": {"form": "points", "points": [[0, None]], "gradient_below": None}}
    )
    assert not result["valid"] and result["toml"] is None
    assert {e["field"] for e in result["errors"]} == {
        "target_depth",
        "geotherm.points[0][1]",
        "geotherm.gradient_below",
    }


def test_schema_errors_name_the_key_without_local_paths(tmp_path):
    document = sites.document_from_toml(site_text("soultz"))
    document["unknown_input"] = 1.0
    result = sites.check(document)
    assert result["errors"] == [{"field": "unknown_input", "message": "unknown key"}]
    assert str(tmp_path) not in json.dumps(result)


def test_site_routes(tmp_path):
    app = create_app(tmp_path / "service", start_jobs=False)
    with TestClient(app) as client:
        listed = client.get("/api/v1/sites/templates").json()
        assert [t["key"] for t in listed["templates"]] == list(sites.BUILT_IN)
        assert "measured range" in listed["bases"]
        template = client.get("/api/v1/sites/templates/pannonian").json()
        assert template["check"]["valid"] and "stress_rule" in template["document"]
        assert client.get("/api/v1/sites/templates/unknown").status_code == 404
        exported = client.post("/api/v1/sites/export", json={"document": template["document"]})
        assert exported.status_code == 200
        assert exported.headers["content-type"].startswith("application/toml")
        imported = client.post("/api/v1/sites/import", json={"toml": exported.text}).json()
        assert imported["document"] == template["document"]
        assert client.post("/api/v1/sites/import", json={"toml": "name = "}).status_code == 422
        preview = client.post(
            "/api/v1/geotherm/preview",
            json={"points_tvd_m_temperature_C": [[0, 15], [1000, 45]], "gradient_below_K_m": 0.03, "depth_m": 2000},
        ).json()
        assert preview["depth_m"][0] == 0 and preview["depth_m"][-1] == 2000
        assert preview["temperature_C"][0] == 15
        assert abs(preview["temperature_C"][-1] - 75) < 1e-9
    app.state.store.close()
