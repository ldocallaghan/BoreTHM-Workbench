"""Field-comparison data files and tested-range flags."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from borethm_workbench import evidence
from borethm_workbench.service import create_app

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src/borethm_workbench/evidence"
FIXTURES = ROOT / "tests/fixtures/results"


def test_data_files_match_their_recorded_checksums():
    record = json.loads((DATA / "sources.json").read_text())
    assert record["tag"] == "v1.8.0" and len(record["commit"]) == 40
    for name, digest in record["outputs"].items():
        assert hashlib.sha256((DATA / name).read_bytes()).hexdigest() == digest
    for path, source in record["sources"].items():
        assert path.startswith(("docs/science/validation.md", "validation/forge16a/", "validation/forge16b/"))
        assert len(source["sha256"]) == 64 and source["bytes"] > 0


@pytest.mark.skipif(
    not os.environ.get("BORETHM_SOURCE"),
    reason="set BORETHM_SOURCE to a BoreTHM Git checkout to rebuild the data files",
)
def test_build_script_reproduces_the_data_files():
    result = subprocess.run(
        [sys.executable, str(ROOT / "deploy/build_evidence.py"), "--borethm", os.environ["BORETHM_SOURCE"], "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_published_tables_and_coverage_are_present():
    data = evidence.comparisons()
    ids = [c["id"] for c in data["comparisons"]]
    assert ids == ["forge16a", "forge16b"]
    a, b = data["comparisons"]
    assert a["samples"] == 135 and len(a["runs"]) == 9 and len(a["sample_values"]) == 135
    assert b["samples"] == 109 and len(b["runs"]) == 18 and len(b["sample_values"]) == 109
    assert round(a["pooled"]["rmse_C"][0], 2) == 8.93 and round(a["pooled"]["rmse_C"][1], 2) == 9.60
    eow4 = [r for r in b["runs"] if r["run"] == "EOW 4"]
    assert [round(r["bias_C"], 2) for r in eow4] == [-24.17, -24.27]
    assert {r["run"]: r["revised_bias_C"] for r in b["runs"] if r["revised_bias_C"] is not None} == {
        "EOW 11": -8.49,
        "EOW 12": -9.8,
        "EOW 16": 0.14,
        "EOW 17": 8.74,
        "EOW 18": 2.08,
    }
    for comparison in (a, b):
        assert any("Run-in, trip" in text for text in comparison["does_not_cover"])


def inside_inputs():
    rows = {row["input"]: row for row in evidence.tested_range()["rows"]}
    middle = {key: sum(rows[key]["range"]) / 2 for key in ("depth_m", "formation_C", "volume_flow_m3_s", "mass_flow_kg_s", "inlet_C")}
    return rows, {**middle, "fluid": "mud", "pipe": "steel"}


def test_inputs_inside_every_range_give_no_flags():
    _, inputs = inside_inputs()
    result = evidence.flags(inputs)
    assert result["outside"] == []


@pytest.mark.parametrize("key", ["depth_m", "formation_C", "volume_flow_m3_s", "mass_flow_kg_s", "inlet_C"])
@pytest.mark.parametrize("direction", ["above", "below"])
def test_each_numeric_range_flags_values_outside_it(key, direction):
    rows, inputs = inside_inputs()
    low, high = rows[key]["range"]
    inputs[key] = high * 1.5 if direction == "above" else low * 0.5
    result = evidence.flags(inputs, "SI")
    assert [f["input"] for f in result["outside"]] == [key]
    flag = result["outside"][0]
    assert flag["direction"] == direction
    word = "highest" if direction == "above" else "lowest"
    assert f"is {direction} the {word} field-tested" in flag["message"]


def test_example_message_and_oilfield_units():
    _, inputs = inside_inputs()
    inputs["formation_C"] = 450.0
    si = evidence.flags(inputs, "SI")["outside"][0]["message"]
    assert si.startswith("Formation temperature 450.0 °C is above the highest field-tested formation temperature")
    oilfield = evidence.flags(inputs, "oilfield")["outside"][0]["message"]
    assert oilfield.startswith("Formation temperature 842.0 °F is above")


def test_fluid_and_pipe_types():
    _, inputs = inside_inputs()
    inputs["fluid"] = "constant-property fluid"
    inputs["pipe"] = "insulated"
    result = evidence.flags(inputs)
    assert [f["input"] for f in result["outside"]] == ["fluid"]
    assert any("insulated" in n["message"] and "trial assemblies" in n["message"] for n in result["notes"])
    inputs["fluid"] = "water"
    result = evidence.flags(inputs)
    assert result["outside"] == []
    assert any("sensitivity case" in n["message"] for n in result["notes"])


def test_pipe_classification():
    assert evidence.pipe_kind([45.0]) == "steel"
    assert evidence.pipe_kind([0.162, 45.0]) == "insulated"
    assert evidence.pipe_kind([]) is None


def test_flags_for_saved_runs():
    circulation = json.loads((FIXTURES / "circulation.json").read_text())["manifest"]
    result = evidence.run_flags(circulation)
    assert result["inputs"]["depth_m"] == 3000.0 and result["inputs"]["fluid"] == "water"
    assessment = json.loads((FIXTURES / "assessment-go.json").read_text())["manifest"]
    result = evidence.run_flags(assessment)
    messages = [f["message"] for f in result["outside"]]
    assert any(m.startswith("Formation temperature 400.0 °C is above") for m in messages)
    assert result["inputs"]["pipe_name"] == "TK-Drakon coated"


def test_evidence_route(tmp_path):
    app = create_app(tmp_path, start_jobs=False)
    with TestClient(app) as client:
        body = client.get("/api/v1/evidence?units=oilfield").json()
        assert [c["id"] for c in body["comparisons"]] == ["forge16a", "forge16b"]
        assert all(c["figure"].startswith("<svg") and "°F" in c["figure"] for c in body["comparisons"])
        assert "sample_values" not in body["comparisons"][0]
        assert body["sources"]["tag"] == "v1.8.0"
    app.state.store.close()
