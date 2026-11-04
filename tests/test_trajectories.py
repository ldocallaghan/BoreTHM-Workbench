import copy
import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from borethm_workbench.adapter import (
    CalculationError,
    make_geometry,
    make_settings,
    run_circulation,
    validate,
)
from borethm_workbench.contracts import Scenario, TrajectoryRequest, parse_scenario_json
from borethm_workbench.trajectories import reconstruct, core_survey, geotherm_breaks
from borethm_workbench.service import create_app
from borethm_workbench.bundles import inspect_bundle
from test_jobs import wait_terminal

ROOT = Path(__file__).resolve().parents[1]


def stations(inc=(0.0, 30.0, 90.0, 90.0), azi=(350.0, 5.0, 25.0, 25.0)):
    return dict(
        kind="stations",
        stations=[
            dict(md_m=float(i * 1000), inclination_deg=a, azimuth_deg=b)
            for i, (a, b) in enumerate(zip(inc, azi))
        ],
        north_reference="true",
        north_reference_source="Synthetic geometry fixture",
        north_correction_deg=0.0,
    )


@pytest.mark.parametrize(
    "inc,azi", [(0.0, 0.0), (30.0, 0.0), (60.0, 90.0), (90.0, 45.0)]
)
def test_straight_station_coordinates_match_analytic(inc, azi):
    r = reconstruct(
        TrajectoryRequest(
            bit_md_m=3000.0,
            survey=stations((inc,) * 4, (azi,) * 4),
            sample_md_m=[500.0, 1500.0],
        )
    )
    md = np.asarray(r["md_m"])
    i, a = np.radians([inc, azi])
    np.testing.assert_allclose(
        r["easting_m"], md * np.sin(i) * np.sin(a), atol=1.0e-6, rtol=0
    )
    np.testing.assert_allclose(
        r["northing_m"], md * np.sin(i) * np.cos(a), atol=1.0e-6, rtol=0
    )
    np.testing.assert_allclose(r["tvd_m"], md * np.cos(i), atol=1.0e-6, rtol=0)


def test_curved_minimum_curvature_wrap_and_core_tvd_parity():
    request = TrajectoryRequest(
        bit_md_m=3000.0, survey=stations(), sample_md_m=[250.0, 1750.0, 2500.0]
    )
    r = reconstruct(request)
    core = core_survey(request.survey, 3000.0)
    np.testing.assert_array_equal(r["tvd_m"], core.tvd(r["md_m"]))
    # Independent vector minimum-curvature reference at stations.
    directions = []
    for station in request.survey.stations:
        i, a = np.radians([station.inclination_deg, station.azimuth_deg])
        directions.append(
            np.array([np.sin(i) * np.sin(a), np.sin(i) * np.cos(a), np.cos(i)])
        )
    points = [np.zeros(3)]
    for u, v in zip(directions, directions[1:]):
        beta = np.arccos(np.clip(np.dot(u, v), -1, 1))
        rf = 1.0 if beta < 1.0e-9 else np.tan(beta / 2) / (beta / 2)
        points.append(points[-1] + 500 * rf * (u + v))
    np.testing.assert_allclose(
        np.c_[r["station_easting_m"], r["station_northing_m"], r["station_tvd_m"]],
        points,
        atol=1.0e-6,
        rtol=0,
    )
    wrapped = request.model_dump()
    wrapped["survey"]["stations"][0]["azimuth_deg"] -= 360
    np.testing.assert_allclose(
        reconstruct(wrapped)["easting_m"], r["easting_m"], atol=1.0e-6
    )
    assert r["tvd_m"][-1] == r["station_tvd_m"][-1]


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "missing_surface", "missing_bottom", "antipodal", "nonfinite"],
)
def test_invalid_station_geometry_is_not_repaired(mutation):
    value = dict(bit_md_m=3000.0, survey=stations())
    s = value["survey"]["stations"]
    if mutation == "duplicate":
        s[1]["md_m"] = 0.0
    elif mutation == "missing_surface":
        s[0]["md_m"] = 1.0
    elif mutation == "missing_bottom":
        s[-1]["md_m"] = 2900.0
    elif mutation == "antipodal":
        s[1]["inclination_deg"] = 180.0
    else:
        s[1]["azimuth_deg"] = float("nan")
    with pytest.raises(ValidationError):
        TrajectoryRequest.model_validate(value)


def test_origin_and_north_correction_are_explicit():
    s = stations((30.0,) * 4, (0.0,) * 4)
    s.update(origin_easting_m=100.0, origin_northing_m=200.0, north_correction_deg=90.0)
    r = reconstruct(dict(bit_md_m=3000.0, survey=s))
    assert r["easting_m"][0] == 100.0 and r["northing_m"][0] == 200.0
    assert r["easting_m"][-1] == pytest.approx(1600.0)
    assert r["northing_m"][-1] == pytest.approx(200.0)


def test_horizontal_pressure_uses_tvd_and_md_friction(scenario):
    data = scenario.model_dump()
    data["definition"]["well"]["survey"] = stations()
    data["definition"]["fluid"] = dict(
        kind="constant_properties",
        density_kg_m3=1000.0,
        heat_capacity_J_kg_K=4200.0,
        viscosity_Pa_s=0.001,
        conductivity_W_m_K=0.6,
        approximation_label="Analytic thermal history fixture",
    )
    s = Scenario.model_validate(data)
    r = run_circulation(s)
    p = r["profile"]
    import borethm

    direct = borethm.circulating_temperatures(
        m_dot=30.0,
        geometry=make_geometry(s),
        fluid=dict(rho=1000.0, cp=4200.0, mu=0.001, k=0.6),
        **make_settings(s),
    )
    np.testing.assert_allclose(
        p["bore_temperature_C"], direct["sol"].sol(p["md_m"])[0], atol=0.001, rtol=0
    )
    np.testing.assert_allclose(
        p["annulus_temperature_C"], direct["sol"].sol(p["md_m"])[1], atol=0.001, rtol=0
    )
    from borethm.model1_coupled import G_GRAV, P_of_z

    tvd = np.asarray(p["tvd_m"])
    md = np.asarray(p["md_m"])
    hydro = np.asarray(p["annulus_hydrostatic_pressure_difference_Pa"])
    np.testing.assert_allclose(hydro, 1000 * G_GRAV * tvd, atol=1.0e-6, rtol=0)
    np.testing.assert_array_equal(
        p["property_pressure_absolute_Pa"], P_of_z(tvd, 1.0e6)
    )
    horizontal = md >= 2000.0
    assert np.ptp(hydro[horizontal]) < 1.0e-6
    assert np.ptp(np.asarray(p["annulus_friction_Pa"])[horizontal]) > 1000
    assert {1000.0, 2000.0} <= set(p["md_m"])
    np.testing.assert_allclose(
        np.asarray(p["annulus_wall_pressure_gauge_Pa"])
        - np.asarray(p["annulus_friction_Pa"]),
        hydro + 9.0e5,
        atol=1.0e-6,
    )
    assert r["pressure_reference"]["wall_basis"] == "wall_budget_md_path_v1"
    vertical = copy.deepcopy(data)
    vertical["definition"]["well"]["survey"] = {"kind": "vertical"}
    v = run_circulation(Scenario.model_validate(vertical))
    assert v["profile"]["md_m"][-1] == p["md_m"][-1]
    assert v["profile"]["annulus_hydrostatic_pressure_difference_Pa"][-1] > hydro[-1]
    assert v["pressure_reference"]["wall_basis"] == "wall_budget_v1"
    incomplete = copy.deepcopy(data)
    incomplete["definition"]["well"]["survey"] = dict(
        kind="md_tvd",
        source="Equivalent TVD fixture",
        points_md_m_tvd_m=list(
            zip(r["trajectory"]["station_md_m"], r["trajectory"]["station_tvd_m"])
        ),
    )
    mapped = run_circulation(Scenario.model_validate(incomplete))
    for key in (
        "bore_temperature_C",
        "annulus_temperature_C",
        "annulus_wall_pressure_gauge_Pa",
    ):
        np.testing.assert_allclose(mapped["profile"][key], p[key], atol=0.001, rtol=0)


def test_md_tvd_has_no_invented_azimuth_and_upgoing_is_display_only(scenario):
    request = TrajectoryRequest(
        bit_md_m=3000.0,
        survey=dict(
            kind="md_tvd",
            points_md_m_tvd_m=[
                (0.0, 0.0),
                (1000.0, 800.0),
                (2000.0, 800.0),
                (3000.0, 700.0),
            ],
            source="Synthetic overhanging fixture",
        ),
    )
    preview = reconstruct(request)
    assert not preview["fully_positioned"] and preview["easting_m"] is None
    assert not preview["calculation_supported"]
    value = scenario.model_dump()
    value["definition"]["well"]["survey"] = request.survey.model_dump()
    with pytest.raises(CalculationError, match="Display geometry only"):
        validate(Scenario.model_validate(value))


def test_geotherm_breaks_preserve_station_and_horizontal_boundaries():
    request = TrajectoryRequest(
        bit_md_m=3000.0,
        survey=dict(
            kind="md_tvd",
            points_md_m_tvd_m=[
                (0.0, 0.0),
                (1000.0, 500.0),
                (2000.0, 500.0),
                (3000.0, 1000.0),
            ],
            source="Fixture",
        ),
    )
    assert geotherm_breaks(request.survey, 3000.0, [250.0, 500.0, 750.0]) == [
        500.0,
        1000.0,
        2000.0,
        2500.0,
    ]


def test_upgoing_station_direction_is_geometry_only_even_if_chord_descends(scenario):
    value = scenario.model_dump()
    value["definition"]["well"]["survey"] = stations((0.0, 20.0, 70.0, 100.0))
    parsed = Scenario.model_validate(value)
    preview = reconstruct(
        dict(bit_md_m=3000.0, survey=value["definition"]["well"]["survey"])
    )
    assert preview["fully_positioned"] and not preview["calculation_supported"]
    with pytest.raises(CalculationError):
        validate(parsed)


def test_real_inclined_job_export_import_and_preview(tmp_path):
    s = parse_scenario_json(
        (ROOT / "src/borethm_workbench/presets/inclined-water.json").read_bytes()
    )
    with TestClient(create_app(tmp_path)) as client:
        preview = client.post(
            "/api/v1/trajectories/preview",
            json={"bit_md_m": 3000.0, "survey": s.definition.well.survey.model_dump()},
        )
        assert preview.status_code == 200, preview.text
        assert (
            preview.json()["fully_positioned"]
            and preview.json()["calculation_supported"]
        )
        assert not client.get("/api/v1/runs").json()["items"]
        submitted = client.post("/api/v1/runs", json={"scenario": s.model_dump()})
        assert submitted.status_code == 202, submitted.text
        run = wait_terminal(client, submitted.json()["id"], 30)
        assert run["state"] == "completed", run
        response = client.get("/api/v1/runs/" + run["id"] + "/export")
        assert response.status_code == 200, response.text
        imported = inspect_bundle(response.content)
        assert imported["manifest"]["trajectory"] == run["manifest"]["trajectory"]
        assert (
            imported["manifest"]["trajectory"]["survey"]
            == s.definition.well.survey.model_dump()
        )
        import subprocess, sys, io, zipfile, hashlib

        archive = tmp_path / "inclined.zip"
        archive.write_bytes(response.content)
        replay = subprocess.run(
            [sys.executable, "-m", "borethm_workbench.cli", "reproduce", str(archive)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert replay.returncode == 0, replay.stdout + replay.stderr
        assert json.loads(replay.stdout)["passed"]
        # Even a consistently rehashed archive cannot invent coordinates.
        with zipfile.ZipFile(io.BytesIO(response.content)) as source:
            members = {n: source.read(n) for n in source.namelist()}
        trajectory = json.loads(members["trajectory.json"])
        trajectory["easting_m"][-1] += 10
        from borethm_workbench.contracts import canonical_bytes

        members["trajectory.json"] = canonical_bytes(trajectory)
        manifest = json.loads(members["manifest.json"])
        manifest["trajectory"] = trajectory
        for item in manifest["artifacts"]:
            if item["filename"] == "trajectory.json":
                item.update(
                    sha256=hashlib.sha256(members["trajectory.json"]).hexdigest(),
                    byte_count=len(members["trajectory.json"]),
                )
        members["manifest.json"] = canonical_bytes(manifest)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as target:
            for name, value in members.items():
                target.writestr(name, value)
        with pytest.raises(ValueError, match="declared survey"):
            inspect_bundle(output.getvalue())
