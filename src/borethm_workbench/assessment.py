"""Complete evaluator jobs with frozen TOML inputs and original programme status.

No user circulation flow is imposed on the evaluator. Typed site-file parsing
and all programme/window/tool decisions remain in the pinned core.
"""

from dataclasses import asdict, fields, is_dataclass
import hashlib
import math
import os
from pathlib import Path
import tempfile
import time
import tomllib

import numpy as np

from .adapter import CalculationError, cache_status, engine_identity, to_json, warning
from .contracts import canonical_bytes, digest, physical_hash


def load_frozen_site(scenario):
    from borethm import load_site

    # Only application-created temporary paths enter the core loader.
    scratch = (
        Path(os.environ.get("BORETHM_WORKBENCH_DATA", ".workbench-data")).resolve()
        / "site-validation"
    )
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="site-", dir=scratch) as directory:
        path = Path(directory) / "site.toml"
        path.write_text(scenario.definition.site.content, encoding="utf-8")
        return load_site(path)


def validate_assessment(scenario):
    from borethm import validate_site

    try:
        site = load_frozen_site(scenario)
        messages = validate_site(site)
    except (ValueError, TypeError, OSError) as error:
        raise CalculationError(
            "SCHEMA_INVALID", "Site definition: " + str(error)
        ) from error
    return [
        warning("SITE_INPUT_QUALIFICATION", str(message), "/definition/site/content")
        for message in messages
    ]


def resolved_site_data(site, content):
    """Materialise scalar/default/dataclass inputs without exporting callables."""
    source = tomllib.loads(content)
    result = {}
    for field in fields(site):
        value = getattr(site, field.name)
        if callable(value):
            result[field.name] = source.get(
                field.name, {"kind": "core_default", "field": field.name}
            )
        elif is_dataclass(value):
            result[field.name] = asdict(value)
        elif isinstance(value, tuple) and value and is_dataclass(value[0]):
            result[field.name] = [asdict(x) for x in value]
        else:
            result[field.name] = to_json(value)
    return result


def finite_data(value, path="", qualifications=None):
    """Optional evaluator infinities become null with an explicit reason.

    Required circulation fields are checked separately and never null-filled.
    """
    qualifications = qualifications if qualifications is not None else []
    if isinstance(value, dict):
        return {
            str(k): finite_data(v, path + "/" + str(k), qualifications)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple, np.ndarray)):
        return [
            finite_data(v, path + "/" + str(i), qualifications)
            for i, v in enumerate(value)
        ]
    if isinstance(value, (float, np.floating)):
        if not math.isfinite(value):
            qualifications.append(
                {
                    "path": path,
                    "reason": "non-finite optional diagnostic",
                    "original": str(value),
                }
            )
            return None
        return float(value)
    if is_dataclass(value):
        return finite_data(asdict(value), path, qualifications)
    if isinstance(value, np.generic):
        return value.item()
    if value is None or isinstance(value, (str, bool, int)):
        return value
    raise ValueError(f"Unsupported evaluator output at {path}: {type(value).__name__}")


def run_assessment(scenario, stage=lambda *_: None):
    import borethm as core
    from borethm import model1_coupled as m1
    from borethm.pressure import column

    warnings = validate_assessment(scenario)
    identity, cache = engine_identity(), cache_status()
    if not identity["adapter_compatible"]:
        raise CalculationError(
            "CAPABILITY_UNSUPPORTED",
            "Installed BoreTHM version is outside the supported major version",
        )
    if not cache["ready"]:
        raise CalculationError("PROPERTY_CACHE_UNAVAILABLE", cache["reason"])
    site = load_frozen_site(scenario)
    options = scenario.definition.options
    coupled = {"evaluator_default": None, "coupled": True, "uncoupled": False}[
        options.coupling
    ]
    stage(
        "running",
        {
            "name": "Complete site evaluation: programme, wall histories, whole-hole windows and equipment"
        },
    )
    started = time.perf_counter()
    raw = core.evaluate(
        site,
        v10=False,
        thermal=True,
        circulation=options.calculation_mode,
        coupled=coupled,
        programme=None,
        hydraulics_model="v1.6.0",
        surface=None,
    )
    duration = time.perf_counter() - started
    stage(
        "postprocessing",
        {"name": "Preserve evaluator status, selected programme and full profiles"},
    )
    optional_quality = []
    report = finite_data(core.result_dict(raw), qualifications=optional_quality)
    main = raw["m1"]
    solver_ok = bool(raw["components"]["solver"]["ok"])
    if not solver_ok or not main["sol"].success or not main["success"]:
        raise CalculationError(
            "SOLVER_NOT_CONVERGED",
            "The complete evaluator reported an unsuccessful numerical prerequisite.",
            {"assessment": report, "solver_converged": False},
        )
    geometry, fluid = main["geometry"], raw.get("fluid")
    md = np.asarray(main["z"])
    tvd = geometry.survey.tvd(md)
    td, tu = np.asarray(main["Td"]), np.asarray(main["Tu"])
    if not all(np.all(np.isfinite(x)) for x in (md, tvd, td, tu)):
        raise CalculationError(
            "RESULT_NONFINITE",
            "Required assessment circulation fields are not finite",
            {"assessment": report},
        )
    # Verify the accepted adaptive solution, not merely a success flag.
    accepted_t = main["sol"].sol(main["sol"].x)
    bp = float(raw["circ"].get("backpressure", 0.0))
    absolute_surface = bp + 1e5
    property_p = m1.P_of_z(tvd, absolute_surface)
    if (
        accepted_t.min() < 1
        or accepted_t.max() > 479
        or property_p.min() < 1e5
        or property_p.max() > 218.1e6
    ):
        raise CalculationError(
            "PROPERTY_DOMAIN_OUTSIDE",
            "Selected assessment circulation is outside the declared property domain",
            {"assessment": report},
        )
    hyd = main.get("hyd", raw["circ"]["hyd"])
    annulus_friction = np.interp(
        md, hyd["annulus_profile"]["md"], hyd["annulus_profile"]["dp"]
    )
    hydro = column(tvd, tu, fluid=fluid, P_surface=bp)
    profile = dict(
        md_m=md.tolist(),
        tvd_m=tvd.tolist(),
        bore_temperature_C=td.tolist(),
        annulus_temperature_C=tu.tolist(),
        formation_temperature_C=np.asarray(main["Trock"]).tolist(),
        annulus_wall_pressure_gauge_Pa=(hydro + annulus_friction).tolist(),
        annulus_hydrostatic_pressure_difference_Pa=(hydro - bp).tolist(),
        annulus_friction_Pa=annulus_friction.tolist(),
        property_pressure_absolute_Pa=property_p.tolist(),
        component_id=[f"selected-string-{i}" for i in geometry.segment_index(md)],
        hole_interval_id=[f"selected-hole-{i}" for i in geometry.hole_index(md)],
    )
    for code, message in (
        (
            "ASSESSMENT_MODEL_QUALIFICATIONS",
            "The core's model assumptions, evidence limits, status reasons and equipment qualifications are retained.",
        ),
        (
            "PROPERTY_AUDIT_INCOMPLETE",
            "Selected accepted circulation is audited; all internal evaluator trial states/history property lookups are not comprehensively instrumented.",
        ),
    ):
        warnings.append(warning(code, message))
    if np.any(property_p < 5e6):
        warnings.append(
            warning(
                "PROPERTY_GUARD_ACTIVE",
                "Baseline hydraulic liquid-property floor: 5 MPa absolute.",
            )
        )
    for flag in hyd.get("flags", []):
        warnings.append(warning("HYDRAULIC_QUALIFICATION", flag))
    energy = main.get("energy_check", {})
    energy_status = (
        "pass"
        if energy.get("passed")
        else "not_applicable"
        if energy.get("enabled") is False
        else "unavailable"
        if "error" in energy or not energy
        else "flagged"
    )
    if energy_status in ("flagged", "unavailable"):
        warnings.append(
            warning(
                "ENERGY_DIAGNOSTIC_" + energy_status.upper(),
                "Selected circulation dissipation diagnostic: " + energy_status,
            )
        )
    if optional_quality:
        warnings.append(
            warning(
                "OPTIONAL_DIAGNOSTIC_UNAVAILABLE",
                "Optional non-finite diagnostics are null with explicit path/reason records.",
            )
        )
    # Export actual radial samples already computed by the evaluator; no playback claim.
    wall = raw["cycle"]["result"]
    full = finite_data(
        dict(
            main_circulation={
                "profile": profile,
                "hydraulics": hyd,
                "effective_surface_pressure_absolute_Pa": absolute_surface,
                "geometry": {
                    "string": [asdict(x) for x in geometry.string],
                    "hole": [asdict(x) for x in geometry.hole],
                },
            },
            wall_history={
                name: getattr(wall, name)
                for name in (
                    "t",
                    "r",
                    "T_wall",
                    "T_node",
                    "fields",
                    "q_wall",
                    "energy_in",
                    "energy_far",
                    "stored",
                )
            },
            cycle_pressure_and_temperature_budget=raw["cycle"]["budget"],
            evaluated_cases=raw["cycle"]["cases"],
            effective_site_inputs=resolved_site_data(
                raw["site"], scenario.definition.site.content
            ),
        ),
        qualifications=optional_quality,
    )
    summary = {}
    for key, value, unit, basis in [
        ("bit_bore_delivery_temperature", td[-1], "°C", "selected bulk bore fluid"),
        ("bit_annulus_temperature", tu[-1], "°C", "selected annulus above bit"),
        ("surface_return_temperature", tu[0], "°C", "selected bulk return"),
        ("mass_flow", main["m_dot"], "kg/s", "evaluator-selected programme"),
        *[
            (name, hyd[name], "Pa difference", "hydraulic_budget_v160")
            for name in (
                "spp",
                "dp_string",
                "dp_annulus",
                "dp_bit",
                "dp_buoyancy",
                "dp_surface",
            )
        ],
    ]:
        summary[key] = dict(
            value=float(value), unit=unit, basis=basis, validity="qualified"
        )
    numerical_profile = {
        "name": "assessment-reference-v1",
        "calculation_mode": options.calculation_mode,
        "initialisation": "core-evaluator-policy-v160",
        "coupling_requested": options.coupling,
        "effective_coupled": bool(raw["site"].coupled),
        "settings": "Pinned complete core evaluator defaults; no app solver override",
    }
    payload = dict(
        schema_version="1.0",
        mode="site_assessment",
        acceptance="qualified",
        acceptance_policy="assessment-acceptance-v1",
        physical_request_hash=physical_hash(scenario),
        resolved_scenario_hash=digest(scenario.model_dump()),
        resolved_scenario=scenario.model_dump(),
        build=identity,
        property_table=cache,
        numerical_profile=numerical_profile,
        numerical_profile_hash=digest(numerical_profile),
        execution_identity=digest(
            {
                "scenario": physical_hash(scenario),
                "build": identity,
                "property_table": cache["sha256"],
                "numerics": numerical_profile,
            }
        ),
        time={"kind": "site_assessment", "sim_time_s": None},
        pressure_reference={
            "atmosphere_absolute_Pa": 1e5,
            "wall_basis": "wall_budget_v1",
            "budget_basis": "hydraulic_budget_v160",
            "property_basis": "property_approximation_v160",
        },
        validity=dict(
            solver_converged=solver_ok,
            fields_finite=True,
            domain_status="qualified",
            energy_status=energy_status,
            assessment_status=report["status"]["label"],
        ),
        warnings=warnings,
        summary=summary,
        profile=profile,
        hydraulics=to_json(hyd),
        assessment=report,
        assessment_full_data=full,
        optional_quality=optional_quality,
        attempts=[
            {
                "strategy": "complete-core-evaluator-v160",
                "duration_s": duration,
                "message": "Evaluator's internal selection policy retained",
            }
        ],
        energy_diagnostic=to_json(energy),
        source_ledger={
            "scope": "Selected circulation core diagnostic",
            "diagnostic": to_json(energy),
        },
        limitations=[
            "Vertical-hole stress/stability formulation",
            "Surface density at 20 °C differs from target SG-equivalent",
            "Selected operating programme is the evaluator's result",
            "Exported wall samples do not enable checkpoint/resume",
        ],
        frozen_site_toml_sha256=hashlib.sha256(
            scenario.definition.site.content.replace("\r\n", "\n")
            .replace("\r", "\n")
            .encode()
        ).hexdigest(),
    )
    payload["wall_clock_duration_s"] = time.perf_counter() - started
    canonical_bytes(payload)
    return payload
