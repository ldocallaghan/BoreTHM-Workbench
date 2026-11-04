"""Compare complete accepted circulation profiles without realigning coordinates.

Reproduction is distinct from an exploratory common-MD comparison: the saved
coordinate, conduit labels, units and pressure conventions must be preserved.
"""

import math

import numpy as np


POLICY = {
    "name": "reproduction-v2",
    "temperature_absolute_C": 0.001,
    "pressure_absolute_Pa": 1.0,
    "pressure_relative": 1.0e-6,
    "other_scalar_absolute": 0.001,
    "coordinates_and_conduit_ids": "Exact saved coordinates and IDs; no interpolation or extrapolation",
    "assessment": "Pinned core release_v134 reference_row projection/reference thresholds, plus exact status/pipe/flow",
}


def _reference_errors(actual, reference, path=""):
    """Apply the pinned reference-row rules using checks retained under python -O."""
    if isinstance(reference, dict):
        if not isinstance(actual, dict) or actual.keys() != reference.keys():
            return [{"path": path, "reason": "Different reference fields"}]
        return [
            error
            for key in reference
            for error in _reference_errors(
                actual[key], reference[key], path + "/" + key
            )
        ]
    if isinstance(reference, list):
        if not isinstance(actual, list) or len(actual) != len(reference):
            return [{"path": path, "reason": "Different reference array length"}]
        return [
            error
            for i, (a, b) in enumerate(zip(actual, reference))
            for error in _reference_errors(a, b, path + "/" + str(i))
        ]
    if isinstance(reference, (bool, str)) or reference is None:
        return (
            []
            if actual == reference
            else [{"path": path, "reason": "Different reference identity"}]
        )
    tolerance = (
        0.002
        if "_SG" in path
        else 0.1
        if "_C" in path
        else 10000.0
        if "_Pa" in path
        else 100.0
        if path.endswith("/md")
        else 0.1
        if path.endswith("/target_m")
        else 1.0e-8
    )
    delta = abs(actual - reference)
    return (
        []
        if math.isfinite(delta) and delta <= tolerance
        else [
            {
                "path": path,
                "reason": "Outside reference tolerance",
                "tolerance": tolerance,
            }
        ]
    )


def compare_results(original, current):
    failures = []
    identities = {
        key: original["build"].get(key) == current["build"].get(key)
        for key in (
            "core_version",
            "core_commit",
            "adapter_version",
            "app_version",
            "libraries",
        )
    }
    identities["property_table"] = (
        original["property_table"]["sha256"] == current["property_table"]["sha256"]
    )
    for key in (
        "mode",
        "physical_request_hash",
        "resolved_scenario_hash",
        "numerical_profile_hash",
        "pressure_reference",
        "acceptance_policy",
        "acceptance",
    ):
        identities[key] = original.get(key) == current.get(key)
    for key in (
        "solver_converged",
        "fields_finite",
        "domain_status",
        "energy_status",
        "assessment_status",
    ):
        identities["validity." + key] = original["validity"].get(key) == current[
            "validity"
        ].get(key)
    failures.extend(
        "identity." + key
        for key, equal in identities.items()
        if not equal and key != "property_table"
    )

    def ledger_equal(a, b):
        if isinstance(a, dict):
            return (
                isinstance(b, dict)
                and a.keys() == b.keys()
                and all(ledger_equal(a[k], b[k]) for k in a)
            )
        if isinstance(a, list):
            return (
                isinstance(b, list)
                and len(a) == len(b)
                and all(ledger_equal(x, y) for x, y in zip(a, b))
            )
        if type(a) in (int, float):
            return (
                type(b) in (int, float)
                and math.isfinite(b)
                and abs(a - b) <= max(0.001, 1.0e-9 * abs(a))
            )
        return a == b

    ledger_check = ledger_equal(
        original.get("source_ledger"), current.get("source_ledger")
    )
    if not ledger_check:
        failures.append("source_ledger")
    if original.get("trajectory") != current.get("trajectory"):
        failures.append("trajectory.coordinates_or_policy")

    scalars = {}
    if original["summary"].keys() != current["summary"].keys():
        failures.append("summary.fields")
    for key, reference in original["summary"].items():
        actual = current["summary"].get(key)
        if actual is None:
            continue
        if any(
            reference[name] != actual[name] for name in ("unit", "basis", "validity")
        ):
            failures.append("summary." + key + ".metadata")
        delta = abs(reference["value"] - actual["value"])
        scalars[key] = delta if math.isfinite(delta) else None
        tolerance = (
            max(1.0, 1.0e-6 * abs(reference["value"]))
            if reference["unit"].startswith("Pa")
            else 0.001
        )
        if not math.isfinite(delta) or delta > tolerance:
            failures.append("summary." + key)

    checks, differences = {}, {}
    before, after = original["profile"], current["profile"]
    if before.keys() != after.keys():
        failures.append("profile.fields")
    for key, values in before.items():
        actual = after.get(key)
        if actual is None or len(values) != len(actual):
            checks[key] = {
                "passed": False,
                "reason": "Missing field or different array length",
            }
        elif key in ("md_m", "tvd_m") or key.endswith("_id"):
            checks[key] = {
                "passed": values == actual,
                "comparison": "exact coordinate/conduit identity",
            }
        else:
            reference, candidate = (
                np.asarray(values, dtype=float),
                np.asarray(actual, dtype=float),
            )
            finite = bool(
                np.all(np.isfinite(reference)) and np.all(np.isfinite(candidate))
            )
            delta = np.abs(reference - candidate)
            maximum = float(np.max(delta)) if finite and delta.size else None
            differences[key] = maximum
            if key.endswith("_Pa"):
                tolerance = np.maximum(1.0, 1.0e-6 * np.abs(reference))
                passed = finite and bool(np.all(delta <= tolerance))
            elif key.endswith("_C"):
                passed = finite and bool(np.all(delta <= 0.001))
            else:
                passed = (
                    False  # New physical fields need an explicit comparison policy.
                )
            checks[key] = {"passed": passed, "maximum_absolute_difference": maximum}
        if not checks[key]["passed"]:
            failures.append("profile." + key)

    if original.get("mode") == "site_assessment":
        from borethm.studies.release_v134 import reference_row

        reference, actual = original["assessment"], current["assessment"]
        identities["assessment_status_and_selection"] = reference["status"][
            "label"
        ] == actual["status"]["label"] and all(
            reference["programme"][key] == actual["programme"][key]
            for key in ("pipe", "flow", "flow_unit")
        )
        if not identities["assessment_status_and_selection"]:
            failures.append("assessment.status_and_selection")
        try:
            errors = _reference_errors(reference_row(actual), reference_row(reference))
            assessment_check = {"passed": not errors, "errors": errors}
            if errors:
                failures.append("assessment.reference_reference")
        except (KeyError, TypeError, ValueError) as error:
            assessment_check = {"passed": False, "message": str(error)[:2000]}
            failures.append("assessment.reference_reference")
    else:
        assessment_check = {"passed": True, "applicable": False}
    return {
        "passed": not failures,
        "comparison_policy": POLICY,
        "identity_matches": identities,
        "scalar_absolute_differences": scalars,
        "profile_maximum_absolute_differences": differences,
        "profile_checks": checks,
        "source_ledger_check": {
            "passed": ledger_check,
            "power_tolerance_W": "max(0.001, 1e-9 × saved magnitude)",
        },
        "assessment_reference_check": assessment_check,
        "failed_checks": failures,
    }
