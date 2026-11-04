"""Supported CLI data selection and first-run recovery."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest
from fastapi.testclient import TestClient
from borethm_workbench.service import create_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("selection", ["argument", "environment", "default"])
def test_cli_data_selection_reports_absolute_path_without_creating_store(
    tmp_path, selection
):
    target = tmp_path / "selected data"
    environment = os.environ.copy()
    environment.pop("BORETHM_WORKBENCH_DATA", None)
    args = [sys.executable, "-I", "-m", "borethm_workbench.cli", "data-info"]
    if selection == "argument":
        environment["BORETHM_WORKBENCH_DATA"] = str(tmp_path / "other")
        args += ["--data-dir", str(target)]
    elif selection == "environment":
        environment["BORETHM_WORKBENCH_DATA"] = str(target)
    else:
        target = tmp_path / ".workbench-data"
    result = subprocess.run(
        args, cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr
    assert Path(json.loads(result.stdout)["data_directory"]) == target
    assert not target.exists()


def test_cli_invalid_command_returns_argument_error():
    result = subprocess.run(
        [sys.executable, "-I", "-m", "borethm_workbench.cli", "unknown-command"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


def test_missing_table_becomes_ready_without_service_restart(tmp_path, monkeypatch):
    from borethm import water_table as wt

    original = wt._CACHE
    target = tmp_path / "cache/water_table.npz"
    monkeypatch.setattr(wt, "_CACHE", target)
    monkeypatch.setenv("BORETHM_CACHE_DIR", str(target.parent))
    with TestClient(create_app(tmp_path, start_jobs=False)) as client:
        assert client.get("/api/v1/health/ready").status_code == 503
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(original, target)
        assert client.get("/api/v1/health/ready").status_code == 200
        assert client.get("/api/v1/capabilities").json()["property_cache"]["ready"]


def test_preparation_preserves_existing_table(tmp_path):
    from borethm import water_table as wt

    table = tmp_path / "cache/water_table.npz"
    table.parent.mkdir()
    shutil.copyfile(wt._CACHE, table)
    before = table.stat().st_mtime_ns
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "borethm_workbench.cli",
            "prepare",
            "--data-dir",
            str(tmp_path),
        ],
        env={**os.environ, "BORETHM_CACHE_DIR": str(table.parent)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert table.stat().st_mtime_ns == before
