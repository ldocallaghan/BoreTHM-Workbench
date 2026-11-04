import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from borethm_workbench.adapter import make_settings, run_circulation
from borethm_workbench.bundles import inspect_bundle
from borethm_workbench.contracts import parse_scenario
from borethm_workbench.reproduction import compare_results
from borethm_workbench.service import create_app
from test_jobs import wait_terminal

ROOT = Path(__file__).resolve().parents[1]
PRESETS = ROOT / "src/borethm_workbench/presets"


def preset(name):
    return json.loads((PRESETS / (name + ".json")).read_bytes())


@pytest.mark.parametrize(
    "points,expected",
    [
        (
            [
                (0.0, 0.0),
                (1500.10, 0.0),
                (1500.25, 10000.0),
                (1500.40, 0.0),
                (3000.0, 0.0),
            ],
            1500.0,
        ),
        (
            [(0.0, 0.0), (1000.0, 0.0), (1500.0, 10.0), (2000.0, 0.0), (3000.0, 0.0)],
            5000.0,
        ),
        (
            [(0.0, 0.0), (1500.10, 0.0), (1500.25, 0.0), (1500.40, 0.0), (3000.0, 0.0)],
            0.0,
        ),
    ],
)
def test_source_integral_ledger_and_reproduction(points, expected):
    value = preset("constant-properties")
    value["definition"]["heat"]["annulus_source"] = {
        "kind": "prescribed_heating",
        "interpolation": "linear",
        "source": "Prescribed triangular heat source",
        "points_md_m_W_m": points,
    }
    s = parse_scenario(value)
    from borethm.model1_coupled import _source_integral

    assert _source_integral(make_settings(s)["annulus_heat"], 3000.0) == pytest.approx(
        expected, abs=1.0e-6
    )
    result = run_circulation(s)
    assert result["source_ledger"]["prescribed_annulus_W"] == pytest.approx(
        expected, abs=1.0e-6
    )
    replay = run_circulation(s)
    assert compare_results(result, replay)["source_ledger_check"]["passed"]
    replay["source_ledger"]["prescribed_annulus_W"] += 1.0
    assert "source_ledger" in compare_results(result, replay)["failed_checks"]


def test_source_export_retains_exact_prescribed_area(tmp_path):
    value = preset("constant-properties")
    value["definition"]["heat"]["annulus_source"] = {
        "kind": "prescribed_heating",
        "interpolation": "linear",
        "source": "Prescribed triangular heat source",
        "points_md_m_W_m": [
            [0.0, 0.0],
            [1500.10, 0.0],
            [1500.25, 10000.0],
            [1500.40, 0.0],
            [3000.0, 0.0],
        ],
    }
    with TestClient(create_app(tmp_path)) as client:
        submitted = client.post("/api/v1/runs", json={"scenario": value})
        assert submitted.status_code == 202, submitted.text
        run = wait_terminal(client, submitted.json()["id"])
        assert run["state"] == "completed", run
        imported = inspect_bundle(
            client.get("/api/v1/runs/" + run["id"] + "/export").content
        )
        assert imported["manifest"]["source_ledger"][
            "prescribed_annulus_W"
        ] == pytest.approx(1500.0, abs=1.0e-6)
