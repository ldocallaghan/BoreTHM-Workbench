"""Units, mandatory balance checks and mode-specific replay dependencies."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from fastapi.testclient import TestClient
from borethm_workbench.contracts import parse_scenario
from borethm_workbench.histories import run_history, compare_history_results
from borethm_workbench.history_reproduction import numeric_difference
from borethm_workbench.service import create_app
from test_jobs import wait_terminal

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "field",
    ["rock_residual_J_m", "static_node_plus_wall_residual_J_m", "energy_residual_J_m"],
)
@pytest.mark.parametrize("delta,passed", [(0.000001098, True), (2.0, False)])
def test_nested_energy_residual_uses_declared_joule_per_metre_rule(
    field, delta, passed
):
    difference = numeric_difference(
        -0.000542897,
        -0.000542897 + delta,
        "datasets/0/statistics/event_energy/1/" + field,
    )
    assert (difference is None) == passed
    if difference:
        assert difference["unit"] == "J/m" and difference["tolerance"] == 1.0
        assert difference["difference"] == pytest.approx(delta)
        assert "saved" in difference and "recalculated" in difference


def test_unknown_numeric_replay_field_fails_closed():
    assert numeric_difference(1.0, 1.0, "datasets/new_quantity")["unit"] == "undeclared"


def test_failed_physical_balance_remains_a_replay_failure():
    scenario = parse_scenario(
        json.loads(
            (ROOT / "src/borethm_workbench/presets/open-hole-history.json").read_bytes()
        )
    )
    original = run_history(scenario)
    current = copy.deepcopy(original)
    current["datasets"][0]["statistics"]["event_energy"][1]["passed"] = False
    assert not compare_history_results(original, original["datasets"], current)[
        "passed"
    ]


def test_prescribed_wall_archive_replays_without_property_table(tmp_path, monkeypatch):
    from borethm import water_table as wt

    absent = tmp_path / "missing-cache/water_table.npz"
    monkeypatch.setattr(wt, "_CACHE", absent)
    monkeypatch.setenv("BORETHM_CACHE_DIR", str(absent.parent))
    scenario = json.loads(
        (ROOT / "src/borethm_workbench/presets/open-hole-history.json").read_bytes()
    )
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/v1/runs", json={"scenario": scenario})
        assert response.status_code == 202, response.text
        completed = wait_terminal(client, response.json()["id"])
        assert completed["state"] == "completed", completed
        assert completed["manifest"]["property_table"]["usage"] == "unused"
        archive = tmp_path / "wall.zip"
        archive.write_bytes(
            client.get("/api/v1/runs/" + completed["id"] + "/export").content
        )
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "borethm_workbench.cli",
            "reproduce",
            str(archive),
            "--data-dir",
            str(tmp_path),
        ],
        cwd=tmp_path,
        env={**os.environ, "BORETHM_CACHE_DIR": str(absent.parent)},
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["passed"] and not absent.exists()
