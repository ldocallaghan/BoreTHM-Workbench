"""Programme and operating choices: detection of the BoreTHM interface,
model defaults, validation messages, site-file round trips and the earlier
assessment for a site without the new tables."""

import json
from pathlib import Path
import tomllib

import pytest

from borethm_workbench import programme as P
from borethm_workbench import sites
from borethm_workbench.contracts import (
    ProgrammeDefinition,
    canonical_bytes,
    parse_scenario,
    physical_hash,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/results"
AVAILABLE = P.interface_available()
needs_interface = pytest.mark.skipif(not AVAILABLE, reason="needs a BoreTHM with Operations")
without_interface = pytest.mark.skipif(AVAILABLE, reason="needs BoreTHM 1.8.0 without Operations")
# physical_hash of the Newberry assessment preset before the programme field existed.
EARLIER_HASH = "48d330040011d1c37ebfea1c82bd95b7f00c58cadb3d83fc03f03f2a3e0afb48"


def preset():
    return json.loads((ROOT / "src/borethm_workbench/presets/newberry-assessment.json").read_text())


def site_toml(key="soultz"):
    from borethm.site_files import site_text

    return site_text(key)


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient
    from borethm_workbench.service import create_app

    return TestClient(create_app(tmp_path / "data", start_jobs=False))


# ---------------------------------------------------------------- detection
def test_capabilities_report_the_interface(client):
    capabilities = client.get("/api/v1/capabilities").json()
    assert capabilities["programme_interface"]["available"] is AVAILABLE
    for name in ("programme", "operations", "design_levers"):
        assert capabilities["enabled"][name] is AVAILABLE
    if not AVAILABLE:
        assert capabilities["programme_interface"]["message"] == P.UNAVAILABLE


@without_interface
def test_without_the_interface_the_new_features_are_refused(client):
    for path in ("/api/v1/programme/pipes", "/api/v1/operations/fields"):
        response = client.get(path)
        assert response.status_code == 422
        assert response.json()["detail"][0]["code"] == "CAPABILITY_UNSUPPORTED"
    data = preset()
    data["definition"]["programme"] = {"kind": "reference"}
    response = client.post("/api/v1/scenarios/validate", json=data)
    assert response.status_code == 422
    assert P.UNAVAILABLE in response.text


def test_a_scenario_without_a_programme_keeps_its_earlier_hash():
    scenario = parse_scenario(preset())
    assert "programme" not in scenario.model_dump()["definition"]
    assert b'"programme"' not in canonical_bytes(scenario.model_dump())
    assert physical_hash(scenario) == EARLIER_HASH
    with_programme = preset()
    with_programme["definition"]["programme"] = {"kind": "reference"}
    assert physical_hash(parse_scenario(with_programme)) != EARLIER_HASH


@needs_interface
@pytest.mark.parametrize("label", ["go", "conditional"])
def test_a_site_without_the_new_tables_gives_the_earlier_assessment(label):
    """Saved runs of the same site, one with BoreTHM 1.8.0 and one
    with the interface installed and no programme or operations tables."""
    earlier = json.loads((FIXTURES / f"assessment-{label}.json").read_text())["manifest"]
    now = json.loads((FIXTURES / f"interface-{label}.json").read_text())["manifest"]
    assert now["physical_request_hash"] == earlier["physical_request_hash"]
    assert now["assessment"]["status"] == earlier["assessment"]["status"]
    for key in ("windows", "tools", "components", "programme", "cycle"):
        assert now["assessment"][key] == earlier["assessment"][key], key
    assert earlier["assessment"].get("operations") is None
    assert not any(e["stated"] for e in now["assessment"]["operations"].values())


# ---------------------------------------------------------------- operations
@needs_interface
def test_operation_fields_come_from_operation_defaults():
    from borethm import operation_defaults

    model = operation_defaults()
    fields = P.operation_fields()
    assert [row["name"] for row in fields["fields"]] == [name for name, _, _ in P.FIELDS]
    assert {row["name"] for row in fields["fields"]} == set(model)
    for row in fields["fields"]:
        entry = model[row["name"]]
        assert row["default"] == entry["value"] and row["source"] == entry["source"] and row["unit"] == entry["unit"]
        assert row["rule"].rstrip(".").lower() == entry["rule"].rstrip(".").lower()
    assert fields["groups"] == {"flow": ("mass_flow_kg_s", "volume_flow_L_s"),
                                "flows": ("mass_flows_kg_s", "volume_flows_L_s")}
    quantities = {row["name"]: row["quantity"] for row in fields["fields"]}
    assert quantities["mass_flow_kg_s"] == "mass_flow" and quantities["volume_flow_L_s"] == "volume_flow"


def test_the_application_reads_no_internal_model_constants():
    """Defaults come from operation_defaults; the application names none of
    the model's internal constants."""
    import ast

    banned = {"SITE_FLOWS", "SITE_PIPES", "COATING_RATINGS", "RIG_PUMPS", "BHA_SURVIVAL_TEMP",
              "PUMP_LIMIT", "HOLE_CLEANING_V", "forge_timings"}
    found = []
    for path in sorted((ROOT / "src/borethm_workbench").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            name = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else None
            if isinstance(node, ast.ImportFrom):
                found += [(path.name, a.name) for a in node.names if a.name in banned]
            if name in banned:
                found.append((path.name, name))
    assert found == []


@needs_interface
@pytest.mark.parametrize(
    "table,kind,field",
    [
        ({"mass_flow_kg_s": 0.5}, "errors", "mass_flow_kg_s"),
        ({"mass_flow_kg_s": 30.0, "volume_flow_L_s": 30.0}, "errors", "operations"),
        ({"trip_hours": 3.0, "trip_scale": 2.0}, "errors", "operations"),
        ({"pipes": ["steel"]}, "errors", "pipes"),
        ({"flow": 30.0}, "errors", ""),
        ({"tool_limit_C": 175.0}, "warnings", "tool_limit_C"),
        ({"pump_pressure_limit_Pa": 60e6, "sources": {"pump_pressure_limit_Pa": ["assumed", "x"]}}, "warnings", "pump_pressure_limit_Pa"),
        ({"volume_flow_L_s": 150.0, "sources": {"volume_flow_L_s": ["assumed", "x"]}}, "warnings", "volume_flow_L_s"),
        ({"coating_ratings_C": {"dual-wall": 150.0}, "sources": {"coating_ratings_C": ["assumed", "x"]}}, "warnings", "coating_ratings_C"),
    ],
)
def test_operation_messages_carry_the_models_field_names(table, kind, field):
    result = P.check_operations(table)
    assert result[kind], result
    assert result[kind][0]["field"] == field


@needs_interface
def test_site_check_attaches_operation_messages():
    document = sites.document_from_toml(site_toml())
    document["operations"] = {"tool_limit_C": 175.0, "mass_flow_kg_s": 20.0, "sources": {"mass_flow_kg_s": ["assumed", "pump sheet"]}}
    result = sites.check(document)
    assert result["valid"]
    assert {"field": "operations.tool_limit_C", "message": "no source is given"} in result["warnings"]
    assert not any(w["message"].startswith("operations.") for w in result["warnings"])
    document["operations"] = {"mass_flow_kg_s": 0.5}
    result = sites.check(document)
    assert not result["valid"]
    assert result["errors"] == [{"field": "operations.mass_flow_kg_s", "message": "0.5 is outside 1 to 300 kg/s"}]
    document["operations"] = {"tool_limit": 175.0}
    result = sites.check(document)
    assert result["errors"] == [{"field": "operations", "message": "tool_limit: unknown key"}]


@needs_interface
def test_operations_round_trip_through_the_site_file():
    from borethm.operations import operations_from_dict

    table = {
        "pipes": ["TK-Drakon coated", "conventional"],
        "volume_flow_L_s": 25.0,
        "tool_limit_C": 180.0,
        "pump_pressure_limit_Pa": 45e6,
        "rig_pumps": 2,
        "connection_p90_min": 10.0,
        "stage_spacing_m": 300.0,
        "stage_minutes": 10.0,
        "drilling_fluid_kg_m3": 1050.0,
        "coating_ratings_C": {"TK-Drakon coated": 190.0},
        "sources": {"volume_flow_L_s": ["assumed", "pump sheet"], "tool_limit_C": ["measured", 'rating "sheet"']},
    }
    document = sites.document_from_toml(site_toml())
    document["operations"] = table
    text = sites.document_to_toml(document)
    assert tomllib.loads(text)["operations"] == {**table, "sources": table["sources"]}
    site = sites.load_text(text)
    assert site.operations == operations_from_dict(table)


# ---------------------------------------------------------------- programme
def custom_definition(**changes):
    layer = {"r_in_m": 0.05, "r_out_m": 0.0635, "conductivity_W_m_K": 45.0}
    data = {
        "kind": "custom",
        "string": [
            {"id": "a", "length_m": 1000.0, "pipe": {"name": "insulated", "layers": [{**layer, "conductivity_W_m_K": 0.2}],
                                                    "source": "test pipe",
                                                    "hydraulic_joint": {"outer_diameter_m": 0.168, "bore_diameter_m": 0.1, "restriction_length_m": 0.7,
                                                                        "spacing_m": 9.5, "kind": "insulated", "source": "test joint"}}},
            {"id": "b", "length_m": 9690.0, "pipe": {"name": "steel", "layers": [layer], "source": "test pipe"}},
            {"id": "c", "length_m": 10.0, "pipe": {"name": "collar", "layers": [{"r_in_m": 0.036, "r_out_m": 0.0857, "conductivity_W_m_K": 45.0}], "source": "test collar"},
             "tool_class": "mwd_pulser", "collar_in": 6.75},
        ],
        "hole": [
            {"id": "h1", "md_top_m": 0.0, "md_bottom_m": 2000.0, "r_wall_m": 0.12,
             "layers": [{"r_in_m": 0.12, "r_out_m": 0.14, "conductivity_W_m_K": 45.0}, {"r_in_m": 0.14, "r_out_m": 0.17, "conductivity_W_m_K": 1.0}]},
            {"id": "h2", "md_top_m": 2000.0, "md_bottom_m": 10700.0, "r_wall_m": 0.108},
        ],
        "transitions": "gradual",
        "surface_loss": {"kind": "coefficient", "coefficient": 4.2, "unit_convention": "psi_ppg_US_gpm", "source": "test rig"},
    }
    data.update(changes)
    return ProgrammeDefinition.model_validate(data)


@needs_interface
@pytest.mark.parametrize("definition", [
    ProgrammeDefinition(kind="reference"),
    ProgrammeDefinition(kind="reference", transitions="gradual", joints=False, tools=True,
                        surface_loss={"kind": "api_case", "case": 3},
                        bha_tools=[{"tool_class": "rotary_steerable", "collar_in": 6.75}]),
    ProgrammeDefinition(kind="reference", surface_loss={"kind": "none"}),
    custom_definition(),
])
def test_programme_round_trips_through_the_site_file(definition):
    """Form to [programme] table to load_site gives the programme the form builds."""
    document = sites.document_from_toml(site_toml())
    document["programme"] = P.to_table(definition)
    site = sites.load_text(sites.document_to_toml(document))
    assert site.programme == P.build(definition)
    back, notes = P.from_site(site.programme)
    assert notes == []
    assert P.build(ProgrammeDefinition.model_validate(back)) == site.programme


def named_string(top_length=1000.0):
    """The walkthrough's string: vacuum-insulated pipe over the top, then
    TK-Drakon coated pipe and the reference assembly below it, from the well
    the model builds for the reference programme at Soultz."""
    reference = P.check(ProgrammeDefinition(kind="reference"), site_toml())["drawing"]
    top = reference["string"][0]
    vacuum = next(p for p in P.model_pipes() if p["name"].startswith("vacuum-insulated"))
    insulated = {**top, "length_m": top_length,
                 "pipe": {**top["pipe"], **{k: vacuum[k] for k in ("name", "layers", "thermal_joint_fraction", "source")}}}
    return [insulated, {**top, "length_m": top["length_m"] - top_length}, *reference["string"][1:]], reference["hole"]


@needs_interface
def test_a_string_of_named_pipes_round_trips_through_the_site_file():
    """The walkthrough's vacuum-insulated and TK-Drakon sections are written
    with the pipe key and read back as the model's named pipes."""
    string, hole = named_string()
    definition = ProgrammeDefinition.model_validate({"kind": "custom", "string": string[:2] + [
        {**string[2], "length_m": sum(s["length_m"] for s in string[2:])}], "hole": hole})
    table = P.to_table(definition)
    assert [s.get("pipe") for s in table["segments"]] == ["vacuum-insulated (sensitivity)", "TK-Drakon coated", None]
    assert "bore_diameter" not in table["segments"][0] and "hydraulic_joint" in table["segments"][1]
    document = sites.document_from_toml(site_toml())
    document["programme"] = table
    site = sites.load_text(sites.document_to_toml(document))
    assert site.programme == P.build(definition)
    vacuum = P.named_pipes()["vacuum-insulated (sensitivity)"]
    top = site.programme.segments[0].pipe
    assert (top.name, top.layers, top.joint_fraction, top.source) == (vacuum.name, vacuum.layers, vacuum.joint_fraction, vacuum.source)
    back, notes = P.from_site(site.programme)
    assert notes == [] and P.build(ProgrammeDefinition.model_validate(back)) == site.programme
    result = P.check(definition, site_toml())
    assert result["valid"] and result["toml_table"] == table and result["toml_note"] is None


@needs_interface
def test_the_reference_pulser_keeps_the_walkthrough_string_in_the_scenario():
    """The reference assembly's MWD pulser is declared over two sections with
    the total-internal basis, which a site file's tools entry cannot state."""
    string, hole = named_string()
    definition = ProgrammeDefinition.model_validate({"kind": "custom", "string": string, "hole": hole})
    result = P.check(definition, site_toml())
    assert result["valid"] and result["toml_table"] is None
    assert "mwd pulser with the total internal basis over 2 sections" in result["toml_note"]
    assert "cannot state a tool's basis or span" in result["toml_note"]


@needs_interface
def test_user_layers_cannot_be_written_to_the_site_file():
    two = custom_definition()
    two.string[1].pipe.layers.append(two.string[1].pipe.layers[0].model_copy(
        update={"r_in_m": 0.0635, "r_out_m": 0.07, "conductivity_W_m_K": 0.2}))
    result = P.check(two, site_toml())
    assert result["valid"] and result["toml_table"] is None
    assert "steel has 2 user layers" in result["toml_note"]


@needs_interface
def test_programme_check_reports_the_string_length_and_draws_the_well():
    result = P.check(custom_definition(), site_toml())
    assert result["valid"], result["errors"]
    assert [s["length_m"] for s in result["drawing"]["string"]] == [1000.0, 9690.0, 10.0]
    assert result["drawing"]["bit_md_m"] == pytest.approx(10700.0)
    short = custom_definition()
    short.string[1].length_m = 9000.0
    result = P.check(short, site_toml())
    assert not result["valid"]
    assert result["errors"] == ["The string section lengths must add up to the target depth, 10700.00 m."]


@needs_interface
def test_a_custom_string_cannot_also_state_pipe_candidates():
    from borethm_workbench.adapter import CalculationError
    from borethm_workbench.assessment import validate_assessment

    data = preset()
    data["definition"]["site"]["content"] = site_toml() + '\n[operations]\npipes = ["conventional"]\n'
    data["definition"]["programme"] = custom_definition().model_dump()
    with pytest.raises(CalculationError, match="cannot also state pipe candidates"):
        validate_assessment(parse_scenario(data))
    data["definition"]["site"]["content"] = site_toml()
    assert validate_assessment(parse_scenario(data)) is not None


@needs_interface
def test_programme_routes(client):
    pipes = client.get("/api/v1/programme/pipes").json()["pipes"]
    assert [p["name"] for p in pipes][:4] == ["TK-Drakon coated", "internally coated", "externally coated", "conventional"]
    assert pipes[0]["coating_rating_C"] == 204.0
    assert len(pipes[0]["layers"]) == 2
    text = site_toml() + '\n[operations]\nmass_flow_kg_s = 30.0\n'
    document = sites.document_from_toml(site_toml())
    document["programme"] = P.to_table(custom_definition())
    imported = client.post("/api/v1/programme/import", json={"toml": sites.document_to_toml(document)}).json()
    assert imported["programme"]["kind"] == "custom" and imported["operations"] is None
    imported = client.post("/api/v1/programme/import", json={"toml": text}).json()
    assert imported["programme"] is None and imported["operations"] == {"mass_flow_kg_s": 30.0}
    checked = client.post("/api/v1/programme/check", json={"programme": {"kind": "reference"}, "site_toml": text}).json()
    assert checked["valid"] and checked["toml_table"] == {"surface": {"case": 5}, "joints": True, "tools": True}
