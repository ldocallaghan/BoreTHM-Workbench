import copy
import json
import subprocess
import sys

import pytest

from borethm_workbench.bundles import export_bundle, inspect_bundle
from borethm_workbench.reproduction import compare_results
from borethm_workbench.store import Store
from borethm_workbench.worker import publish


def test_property_fingerprint_difference_uses_numerical_tolerances(physical_result):
    changed = copy.deepcopy(physical_result)
    changed["property_table"]["sha256"] = "0" * 64
    changed["property_table"]["arrays"]["rho"]["sha256"] = "0" * 64
    report = compare_results(physical_result, changed)
    assert not report["identity_matches"]["property_table"]
    assert report["passed"]
    changed["profile"]["bore_temperature_C"][0] += 0.01
    assert not compare_results(physical_result, changed)["passed"]


def test_exported_text_uses_line_feed_before_checksums(
    tmp_path, scenario, physical_result
):
    import io
    import zipfile

    store = Store(tmp_path)
    run, _ = store.submit(scenario, None)
    rid = store.claim()
    store.stage(rid, "running", {})
    store.stage(rid, "postprocessing", {})
    publish(store, rid, copy.deepcopy(physical_result))
    bundle = export_bundle(store, run["id"])
    inspect_bundle(bundle)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        assert b"\r" not in archive.read("profile.csv")
        assert all(b"\r" not in archive.read(name) for name in archive.namelist())


def test_reproduction_preserves_all_profile_values(physical_result):
    report = compare_results(physical_result, copy.deepcopy(physical_result))
    assert report["passed"] and not report["failed_checks"]
    assert report["profile_checks"].keys() == physical_result["profile"].keys()


@pytest.mark.parametrize(
    "field",
    [
        "annulus_wall_pressure_gauge_Pa",
        "annulus_hydrostatic_pressure_difference_Pa",
        "annulus_friction_Pa",
        "property_pressure_absolute_Pa",
        "bore_temperature_C",
        "annulus_temperature_C",
        "formation_temperature_C",
        "md_m",
        "tvd_m",
        "component_id",
        "hole_interval_id",
    ],
)
def test_reproduction_rejects_profile_change_with_unchanged_scalars(
    physical_result, field
):
    changed = copy.deepcopy(physical_result)
    values = changed["profile"][field]
    index = len(values) // 2
    values[index] = (
        "different-conduit"
        if field.endswith("_id")
        else values[index] + (5000 if field.endswith("_Pa") else 0.01)
    )
    assert changed["summary"] == physical_result["summary"]
    report = compare_results(physical_result, changed)
    assert not report["passed"] and "profile." + field in report["failed_checks"]


@pytest.mark.parametrize(
    "mutation",
    ["shape", "missing", "nonfinite", "units", "pressure_basis", "numerical_policy"],
)
def test_reproduction_reports_mismatch_without_broadcast_or_nonfinite_json(
    physical_result, mutation
):
    changed = copy.deepcopy(physical_result)
    if mutation == "shape":
        changed["profile"]["bore_temperature_C"].pop()
    elif mutation == "missing":
        del changed["profile"]["annulus_friction_Pa"]
    elif mutation == "nonfinite":
        changed["profile"]["bore_temperature_C"][0] = float("nan")
    elif mutation == "units":
        changed["summary"]["spp"]["unit"] = "MPa difference"
    elif mutation == "pressure_basis":
        changed["pressure_reference"]["wall_basis"] = "different"
    else:
        changed["numerical_profile_hash"] = "0" * 64
    report = compare_results(physical_result, changed)
    assert not report["passed"] and report["failed_checks"]
    json.dumps(report, allow_nan=False)


def test_pressure_reproduction_uses_pointwise_declared_tolerances(physical_result):
    changed = copy.deepcopy(physical_result)
    name = "annulus_wall_pressure_gauge_Pa"
    changed["profile"][name] = [
        value + 0.9 * max(1.0, 1.0e-6 * abs(value))
        for value in changed["profile"][name]
    ]
    assert compare_results(physical_result, changed)["passed"]


@pytest.mark.parametrize(
    "field,tolerance",
    [
        ("weight_SG", 0.002),
        ("peak_C", 0.1),
        ("spp_Pa", 10000.0),
        ("md", 100.0),
        ("target_m", 0.1),
        ("flow", 1.0e-8),
    ],
)
def test_reference_runtime_thresholds_match_pinned_core(field, tolerance):
    from borethm_workbench.reproduction import _reference_errors

    reference = {field: 1.0}
    for ratio in (0.5, 2.0):
        actual = {field: 1.0 + ratio * tolerance}
        assert (not _reference_errors(actual, reference)) == (ratio < 1.0)


def test_reference_reproduction_checks_remain_enabled_under_optimized_python():
    code = "from borethm_workbench.reproduction import _reference_errors\nimport json\nprint(json.dumps(_reference_errors({'weight_SG':1.1},{'weight_SG':1.0})))"
    result = subprocess.run(
        [sys.executable, "-O", "-c", code], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)[0]["reason"] == "Outside reference tolerance"


def test_public_reproduction_rejects_consistent_bundle_with_changed_pressure(
    tmp_path, scenario, physical_result
):
    store = Store(tmp_path)
    job, _ = store.submit(scenario, None)
    rid = store.claim()
    store.stage(rid, "running", {})
    changed = copy.deepcopy(physical_result)
    changed["profile"]["annulus_wall_pressure_gauge_Pa"][200] += 5000
    store.stage(rid, "postprocessing", {})
    publish(store, rid, changed)
    bundle = export_bundle(store, rid)
    # Checksums are consistent; this is a numerical mismatch, not a broken ZIP.
    inspect_bundle(bundle)
    path = tmp_path / "changed-pressure.zip"
    path.write_bytes(bundle)
    result = subprocess.run(
        [sys.executable, "-m", "borethm_workbench.cli", "reproduce", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert not report["passed"]
    assert report["scalar_absolute_differences"]["spp"] == 0
    assert (
        report["profile_maximum_absolute_differences"]["annulus_wall_pressure_gauge_Pa"]
        == 5000
    )
    assert "profile.annulus_wall_pressure_gauge_Pa" in report["failed_checks"]
