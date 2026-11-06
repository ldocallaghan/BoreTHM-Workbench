"""Translate application inputs into BoreTHM calculations.

The adapter uses pressure.column, LossModel and fluid-property functions from
the installed model package. Physical equations remain in BoreTHM.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from importlib import metadata
from pathlib import Path
import platform
import subprocess
import time

import numpy as np

from .contracts import (
    Scenario,
    canonical_bytes,
    digest,
    numerical_profile,
    physical_hash,
)

POLICY = "component-acceptance-v1"
HISTORY_MESSAGE = "The installed model lacks required history arguments. Install BoreTHM 1.8.0 or later."


class CalculationError(Exception):
    def __init__(self, code, message, diagnostics=None):
        super().__init__(message)
        self.code = code
        self.diagnostics = diagnostics or {}


def sha256_file(path):
    data = Path(path).read_bytes()
    if Path(path).suffix in {
        ".json",
        ".py",
        ".txt",
        ".md",
        ".csv",
        ".html",
        ".css",
        ".js",
    }:
        data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(data).hexdigest()


def cache_status():
    from borethm import water_table as wt
    from .cache_reference import fingerprints

    if not wt._CACHE.is_file():
        return {
            "ready": False,
            "reason": "Prepare the water-property cache with borethm-workbench prepare.",
        }
    try:
        with np.load(wt._CACHE, allow_pickle=False) as table:
            if not np.array_equal(table["T"], wt.T_GRID) or not np.array_equal(
                table["P"], wt.P_GRID
            ):
                raise ValueError("Property grid differs from the installed generator")
            for key in ("rho", "cp", "mu", "k"):
                if (
                    table[key].shape != (len(wt.T_GRID), len(wt.P_GRID))
                    or not np.all(np.isfinite(table[key]))
                    or np.any(table[key] <= 0)
                ):
                    raise ValueError(f"Invalid {key} property array")
        arrays = fingerprints(wt._CACHE)
        return {
            "ready": True,
            "sha256": digest(arrays),
            "arrays": arrays,
            "identity_kind": "array_fingerprints",
            "shape": [len(wt.T_GRID), len(wt.P_GRID)],
        }
    except (ValueError, OSError, KeyError) as error:
        return {"ready": False, "reason": str(error)}


def history_capabilities():
    from borethm import wall_thermal, tool_thermal

    wall = {"observe", "radius_m", "carry_static_node"} <= set(
        inspect.signature(wall_thermal.run).parameters
    )
    wall = wall and "radius_m" in inspect.signature(wall_thermal.Grid.build).parameters
    tool = {"retain_stage_samples", "T_s0", "T_f0"} <= set(
        inspect.signature(tool_thermal.trip_in).parameters
    )
    return {
        "wall_history": wall,
        "tool_history": tool,
        "scheduled_interventions": wall,
        "checkpoints": wall,
    }


def engine_identity():
    commit = None
    try:
        distribution = metadata.distribution("borethm")
        direct = json.loads(distribution.read_text("direct_url.json") or "{}")
        if direct.get("dir_info", {}).get("editable"):
            from urllib.parse import urlparse, unquote
            from urllib.request import url2pathname

            root = Path(url2pathname(unquote(urlparse(direct["url"]).path)))
            commit = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        elif direct.get("vcs_info", {}).get("vcs") == "git":
            commit = direct["vcs_info"].get("commit_id")
    except (OSError, ValueError, subprocess.CalledProcessError, KeyError):
        pass
    from .distribution import dependency_versions

    version = metadata.version("borethm")
    return {
        "core_version": version,
        "core_commit": commit,
        "app_version": metadata.version("borethm-workbench"),
        "adapter_version": "borethm-1-bridge",
        "adapter_compatible": version.split(".")[0] == "1",
        "history_capabilities": history_capabilities(),
        "history_message": HISTORY_MESSAGE,
        "physics_values": ["v1.8.0", "v1.7.0"],
        "schema_version": "1.1",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "libraries": dependency_versions(),
    }


def make_geometry(scenario):
    import borethm as core
    from borethm.hydraulic_losses import LossModel

    d = scenario.definition
    layers = lambda items: tuple(
        core.Layer(x.r_in_m, x.r_out_m, x.conductivity_W_m_K) for x in items
    )
    segments = []
    for segment in d.well.string:
        p, j, local, tool = (
            segment.pipe,
            segment.pipe.hydraulic_joint,
            segment.local_loss,
            segment.tool_loss,
        )
        joint = (
            None
            if j is None
            else core.HydraulicJoint(
                j.outer_diameter_m,
                j.bore_diameter_m,
                j.restriction_length_m,
                j.spacing_m,
                j.transition,
                j.source,
                taper_angle_deg=j.taper_angle_deg,
                kind=j.kind,
            )
        )
        ll = (
            None
            if local is None
            else core.LocalLoss(
                local.bore_coefficient,
                local.annulus_coefficient,
                local.bore_reference_area_m2,
                local.annulus_reference_area_m2,
                local.source,
            )
        )
        tl = (
            None
            if tool is None
            else core.ReferenceToolLoss(
                tool.reference_pressure_Pa,
                tool.reference_flow_m3_s,
                tool.reference_density_kg_m3,
                tool.basis,
                tool.span_segments,
                tool.source,
                tool.flow_range_m3_s,
                tool.temperature_max_C,
                tool.include_boundary_changes,
                tool.unresolved,
            )
        )
        pipe = core.PipeType(
            p.name, layers(p.layers), p.thermal_joint_fraction, p.source, joint
        )
        from borethm.hydraulic_losses import DownholeTool

        tools = (
            ()
            if segment.tool_class is None or tl is not None
            else (
                DownholeTool(
                    segment.tool_class,
                    segment.collar_in,
                    basis=segment.tool_basis,
                    span_segments=segment.tool_span_segments,
                ),
            )
        )
        segments.append(core.StringSegment(segment.length_m, pipe, ll, tl, tools))
    from .trajectories import core_survey

    geometry = core.WellGeometry(
        segments,
        [
            core.HoleInterval(h.md_top_m, h.md_bottom_m, h.r_wall_m, layers(h.layers))
            for h in d.well.hole
        ],
        survey=core_survey(d.well.survey, d.well.bit_md_m),
    )
    LossModel(geometry, **loss_settings(scenario))  # stricter joint/tool span checks
    return geometry


def loading_warnings(scenario):
    message = scenario.metadata.get("schema_loading")
    return (
        [warning("PREVIOUS_SCHEMA_PHYSICS", message, path="/model/physics")]
        if message
        else []
    )


def loss_settings(scenario):
    from borethm.physics import resolve

    settings = resolve(scenario.model.physics)
    current = settings["annulus_model"] == "herschel-bulkley-erge"
    return dict(
        physics=scenario.model.physics,
        friction=settings["friction"],
        roughness=settings["roughness"],
        transitions=scenario.definition.hydraulics.transitions,
        regime="herschel-bulkley" if current else "apparent-viscosity",
        band="erge" if current else "founargiotakis",
    )


def physics_record(scenario, result=None, geometry=None, reported=None):
    from dataclasses import asdict
    from borethm.physics import resolve

    settings = dict(
        reported
        or (result or {}).get("physics_settings")
        or resolve(scenario.model.physics)
    )
    if settings.get("physics") != scenario.model.physics:
        raise CalculationError(
            "PHYSICS_INCONSISTENT", "The model reported a different physics choice."
        )
    if result:
        for key in ("physics", "annulus_model", "friction", "roughness"):
            if key in result and result[key] != settings[key]:
                raise CalculationError(
                    "PHYSICS_INCONSISTENT",
                    f"The model reported inconsistent {key} settings.",
                )
    settings["hydraulics_model"] = (result or {}).get("hydraulics_model")
    settings["scope"] = (
        "thermal and hydraulic calculation"
        if geometry
        else "prescribed thermal boundaries; hydraulic settings are recorded but unused"
    )
    settings["joints"] = [
        {
            "segment": i,
            **asdict(segment.pipe.hydraulic_joint.resolved(scenario.model.physics)),
        }
        for i, segment in enumerate(geometry.string if geometry else [])
        if segment.pipe.hydraulic_joint is not None
    ]
    settings["tools"] = [
        {
            "segment": i,
            "tool_class": tool.tool_class,
            "collar_in": tool.collar_in,
            "resolved_loss": asdict(tool.resolved(scenario.model.physics))
            if tool.resolved(scenario.model.physics)
            else None,
            "warning": tool.warning(scenario.model.physics),
        }
        for i, segment in enumerate(geometry.string if geometry else [])
        for tool in segment.tools
    ]
    settings["supplied_tool_losses"] = [
        {"segment": i, **asdict(segment.tool_loss)}
        for i, segment in enumerate(geometry.string if geometry else [])
        if segment.tool_loss is not None
    ]
    return settings


def make_fluid(definition):
    if definition.kind == "iapws_water":
        return None
    if definition.kind == "barite_mud":
        from borethm.fluids import Mud

        return Mud(
            definition.surface_density_kg_m3,
            definition.plastic_viscosity_Pa_s,
            definition.yield_point_Pa,
            definition.viscosity_reference_temperature_C,
            definition.conserve_mass,
        )
    return dict(
        rho=definition.density_kg_m3,
        cp=definition.heat_capacity_J_kg_K,
        mu=definition.viscosity_Pa_s,
        k=definition.conductivity_W_m_K,
    )


def make_settings(scenario):
    import borethm as core

    d = scenario.definition
    geotherm = core.layered_geotherm(
        d.formation.geotherm.points_tvd_m_temperature_C,
        d.formation.geotherm.gradient_below_K_m,
    )
    from .trajectories import core_survey, geotherm_breaks

    survey = core_survey(d.well.survey, d.well.bit_md_m)
    exposure, years, breaks = (
        None,
        1.0,
        geotherm_breaks(
            d.well.survey,
            d.well.bit_md_m,
            [z for z, t in d.formation.geotherm.points_tvd_m_temperature_C],
        ),
    )
    e = d.operating.exposure
    if e.kind == "legacy_uniform_years":
        years = e.years
    elif e.kind == "uniform_seconds":
        exposure = lambda z: np.full_like(np.asarray(z, float), e.seconds)
    else:
        points = np.asarray(e.points_md_m_seconds)
        exposure = lambda z: np.interp(z, points[:, 0], points[:, 1])
        breaks.extend(points[1:-1, 0])
    source = None
    if d.heat.annulus_source:
        points_heat = np.asarray(d.heat.annulus_source.points_md_m_W_m)
        source = lambda z: np.interp(z, points_heat[:, 0], points_heat[:, 1])
        source.knots = points_heat[:, 0].copy()
        breaks.extend(points_heat[1:-1, 0])
    sl = d.hydraulics.surface_loss
    if sl.kind == "none":
        surface = core.SurfaceLoss.coefficient(0, "Explicitly declared no surface loss")
    elif sl.kind == "api_case":
        surface = core.SurfaceLoss.api_case(sl.case)
    else:
        surface = core.SurfaceLoss.coefficient(sl.coefficient, sl.source)
    return dict(
        T_inj=d.operating.inlet_temperature_C,
        G=0.035,
        T_surf=float(geotherm(0)),
        target_rock_T=float(geotherm(survey.tvd(d.well.bit_md_m))),
        Q_face=d.heat.face_heat_W,
        t_years=years,
        k_rock=d.formation.conductivity_W_m_K,
        n_nodes=160,
        verbose=False,
        geotherm=geotherm,
        exposure=exposure,
        rhocp_rock=d.formation.volumetric_heat_capacity_J_m3_K,
        friction_heat=d.heat.friction_heating,
        tfa=d.hydraulics.nozzle_area_m2,
        film_mult=1.0,
        breaks=tuple(sorted(set(breaks))),
        init=None,
        P_surface=d.operating.surface_pressure.pressure_absolute_Pa,
        physics=scenario.model.physics,
        surface=surface,
        transitions=d.hydraulics.transitions,
        rock_response=scenario.model.rock_response,
        annulus_heat=source,
    )


def mass_rate(scenario, fluid):
    from borethm.model1_coupled import fluid_props

    f = scenario.definition.operating.flow
    if f.kind == "mass":
        return f.mass_rate_kg_s
    rho = float(
        fluid_props(
            np.array([f.temperature_C]), np.array([f.pressure_absolute_Pa]), fluid
        )[0][0]
    )
    return rho * f.volume_rate_m3_s


def warning(code, message, path="/definition", **evidence):
    return dict(
        code=code, severity="warning", message=message, path=path, evidence=evidence
    )


def validate(scenario):
    if scenario.mode in ("wall_history", "tool_history"):
        if not history_capabilities()[scenario.mode]:
            raise CalculationError("CAPABILITY_UNSUPPORTED", HISTORY_MESSAGE)
        from .histories import validate_history

        return validate_history(scenario)
    if scenario.mode == "site_assessment":
        from .assessment import validate_assessment

        return loading_warnings(scenario) + validate_assessment(scenario)
    try:
        make_geometry(scenario)
    except (ValueError, TypeError) as error:
        raise CalculationError("GEOMETRY_INVALID", str(error)) from error
    warnings = loading_warnings(scenario)
    from .trajectories import manifest_trajectory

    trajectory = manifest_trajectory(scenario, [])
    if not trajectory["calculation_supported"]:
        raise CalculationError("CAPABILITY_UNSUPPORTED", trajectory["applicability"])
    if scenario.definition.well.survey.kind != "vertical":
        warnings.append(
            warning(
                "INCLINED_THERMAL_HYDRAULICS",
                "Non-decreasing TVD path; MD-indexed friction and core-linear TVD. Deviated stability/programme assessment is unavailable.",
            )
        )
        if not trajectory["fully_positioned"]:
            warnings.append(
                warning(
                    "SURVEY_INCOMPLETE",
                    "MD/TVD input supports calculation and a labelled 2D section, but has no unique 3D position.",
                )
            )
    if not scenario.provenance or not any(
        p.source.strip() for p in scenario.provenance
    ):
        warnings.append(
            warning("PROVENANCE_MISSING", "Input basis/source is incomplete.")
        )
    for i, s in enumerate(scenario.definition.well.string):
        if s.tool_loss and s.tool_loss.unresolved:
            warnings.append(
                warning(
                    "INTERNAL_TOOL_LOSS_UNKNOWN",
                    "Unresolved tool loss is omitted; pump conclusions are qualified.",
                    f"/definition/well/string/{i}/tool_loss",
                )
            )
    fluid = scenario.definition.fluid
    if fluid.kind == "constant_properties":
        warnings.append(
            warning("CONSTANT_PROPERTY_APPROXIMATION", fluid.approximation_label)
        )
    elif fluid.kind == "barite_mud":
        warnings.append(
            warning(
                "MUD_FORMULATION_QUALIFIED",
                "Barite-mixture/Bingham approximation; field-validation limits of the core apply.",
            )
        )
    op = scenario.definition.operating
    if fluid.kind != "constant_properties":
        temperatures = [op.inlet_temperature_C]
        pressures = [op.surface_pressure.pressure_absolute_Pa]
        if op.flow.kind == "surface_volume":
            temperatures.append(op.flow.temperature_C)
            pressures.append(op.flow.pressure_absolute_Pa)
        if any(not 1 <= t <= 479 for t in temperatures) or any(
            not 1e5 <= p <= 218.1e6 for p in pressures
        ):
            raise CalculationError(
                "INPUT_DOMAIN_INVALID",
                "Declared temperature/absolute pressure is outside the 1–479 °C / 0.1–218.1 MPa property table.",
            )
    return warnings


def validate_thermal(scenario, geometry, result, settings):
    from borethm import model1_coupled as m1
    from borethm.hydraulic_losses import LossModel

    sol = result["sol"]
    if not sol.success:
        raise CalculationError("SOLVER_NOT_CONVERGED", str(sol.message))
    model = LossModel(geometry, transitions=scenario.definition.hydraulics.transitions)
    hz, _ = model.integration_grid(result)
    audit_z = np.unique(
        np.r_[sol.x, result["z"], hz, model.top, model.bottom, settings["breaks"]]
    )
    audit_t = sol.sol(audit_z)
    if not np.all(np.isfinite(audit_t)):
        raise CalculationError(
            "RESULT_NONFINITE", "Accepted thermal output contains non-finite values."
        )
    raw_p = m1.P_of_z(geometry.survey.tvd(audit_z), settings["P_surface"])
    outside = scenario.definition.fluid.kind != "constant_properties" and (
        audit_t.min() < 1
        or audit_t.max() > 479
        or raw_p.min() < 1e5
        or raw_p.max() > 218.1e6
    )
    if outside or not result["success"]:
        raise CalculationError(
            "PROPERTY_DOMAIN_OUTSIDE",
            "Accepted solution exceeds the declared property domain; no physical profile published.",
            {
                "temperature_range_C": [float(audit_t.min()), float(audit_t.max())],
                "pressure_range_absolute_Pa": [float(raw_p.min()), float(raw_p.max())],
            },
        )
    residual = float(np.max(sol.rms_residuals))
    inlet = float(sol.sol(np.array([0.0]))[0, 0] - settings["T_inj"])
    if abs(inlet) > 1e-3 or residual > 1.01e-4:
        raise CalculationError(
            "SOLVER_NOT_CONVERGED", "Boundary/residual acceptance check failed."
        )
    return audit_z


def run_circulation(
    scenario: Scenario,
    stage=lambda *_: None,
    seed=None,
    notify=lambda *_: None,
    seed_provider=None,
):
    import borethm as core
    from borethm import model1_coupled as m1
    from borethm.hydraulic_losses import LossModel
    from borethm.pressure import column

    warnings = validate(scenario)
    identity = engine_identity()
    if not identity["adapter_compatible"]:
        raise CalculationError(
            "CAPABILITY_UNSUPPORTED",
            "Installed BoreTHM version is outside the supported major version.",
        )
    from .dependencies import property_identity

    cache = property_identity(scenario)
    if not cache["ready"]:
        raise CalculationError("PROPERTY_CACHE_UNAVAILABLE", cache["reason"])
    if (
        seed is None
        and seed_provider is not None
        and scenario.numerics.initialisation == "interactive-bounded-v1"
    ):
        seed = seed_provider(identity, cache)
    d = scenario.definition
    geometry, fluid, settings = (
        make_geometry(scenario),
        make_fluid(d.fluid),
        make_settings(scenario),
    )
    rate = mass_rate(scenario, fluid)
    started = time.perf_counter()
    stage("running", {"name": "Circulation boundary-value solve", "attempt": 1})
    from .initialisation import compatibility_key, solve

    result, attempts, accepted_seed = solve(
        scenario,
        geometry,
        fluid,
        settings,
        identity,
        cache,
        lambda result, options: validate_thermal(scenario, geometry, result, options),
        seed,
        notify,
    )
    sol = result["sol"]
    stage(
        "postprocessing",
        {"name": "Validate, reconstruct pressure and publish artifacts"},
    )
    # Include accepted adaptive nodes and every physical breakpoint in audit.
    model = LossModel(geometry, **loss_settings(scenario))
    hz, _ = model.integration_grid(result)
    audit_z = np.unique(
        np.r_[sol.x, result["z"], hz, model.top, model.bottom, settings["breaks"]]
    )
    audit_t = sol.sol(audit_z)
    if not np.all(np.isfinite(audit_t)):
        raise CalculationError(
            "RESULT_NONFINITE",
            "Accepted thermal output contains non-finite values.",
            {"attempts": attempts},
        )
    raw_p = m1.P_of_z(geometry.survey.tvd(audit_z), settings["P_surface"])
    uses_table = d.fluid.kind != "constant_properties"
    outside = uses_table and (
        audit_t.min() < 1
        or audit_t.max() > 479
        or raw_p.min() < 1e5
        or raw_p.max() > 218.1e6
    )
    if outside or not result["success"]:
        raise CalculationError(
            "PROPERTY_DOMAIN_OUTSIDE",
            "Accepted solution exceeds the declared property domain; no physical profile published.",
            {
                "attempts": attempts,
                "temperature_range_C": [float(audit_t.min()), float(audit_t.max())],
                "pressure_range_absolute_Pa": [float(raw_p.min()), float(raw_p.max())],
            },
        )
    hyd = core.hydraulics(
        rate,
        geometry,
        result=result,
        fluid=fluid,
        tfa=settings["tfa"],
        bit=True,
        P_surface=settings["P_surface"],
        physics=scenario.model.physics,
        surface=settings["surface"],
        transitions=settings["transitions"],
    )
    # Keep baseline hydraulic result interpolation/quadrature untouched.
    md = np.unique(np.r_[result["z"], model.top, model.bottom, settings["breaks"]])
    if len(md) > 5000:
        raise CalculationError(
            "RESOURCE_LIMIT", "Boundary-rich display sampling exceeds 5,000 depths."
        )
    td, tu = sol.sol(md)
    wellhead = settings["P_surface"] - 1e5
    tvd = geometry.survey.tvd(md)
    wall_column = column(tvd, tu, fluid=fluid, P_surface=wellhead)
    # Audit the final column pressures separately from the approximate property basis.
    if uses_table and (
        wall_column.min() + 1e5 < 1e5 or wall_column.max() + 1e5 > 218.1e6
    ):
        raise CalculationError(
            "PROPERTY_DOMAIN_OUTSIDE",
            "Wall-pressure column exceeds the property domain.",
            {"attempts": attempts},
        )
    annulus_friction = np.interp(
        md, hyd["annulus_profile"]["md"], hyd["annulus_profile"]["dp"]
    )
    guard_active = bool(np.any(raw_p < 5e6))
    if guard_active:
        warnings.append(
            warning(
                "PROPERTY_GUARD_ACTIVE",
                "Hydraulic liquid-property pressure floor of 5 MPa absolute is active.",
                floor_absolute_Pa=5e6,
            )
        )
    warnings.append(
        warning(
            "PROPERTY_AUDIT_INCOMPLETE",
            "Accepted adaptive nodes, output depths, breakpoints and hydraulic grid audited. Internal trial lookups are not instrumented; wall-column intermediate passes use the baseline guards.",
        )
    )
    for flag in hyd["flags"]:
        code = {
            "internal_tool_loss_unknown": "INTERNAL_TOOL_LOSS_UNKNOWN",
            "tool_temperature_outside_published_rating": "TOOL_RATING_EXCEEDED",
        }.get(flag, "HYDRAULIC_QUALIFICATION")
        warnings.append(warning(code, flag))
    energy = result.get("energy_check", {})
    energy_status = (
        "not_applicable"
        if not d.heat.friction_heating
        else "pass"
        if energy.get("passed")
        else "unavailable"
        if "error" in energy
        else "flagged"
    )
    if energy_status in ("flagged", "unavailable"):
        warnings.append(
            warning(
                "ENERGY_DIAGNOSTIC_" + energy_status.upper(),
                "The implemented dissipation diagnostic is " + energy_status + ".",
            )
        )
    profile = dict(
        md_m=md.tolist(),
        tvd_m=tvd.tolist(),
        bore_temperature_C=td.tolist(),
        annulus_temperature_C=tu.tolist(),
        formation_temperature_C=np.asarray(settings["geotherm"](tvd)).tolist(),
        annulus_wall_pressure_gauge_Pa=(wall_column + annulus_friction).tolist(),
        annulus_hydrostatic_pressure_difference_Pa=(wall_column - wellhead).tolist(),
        annulus_friction_Pa=annulus_friction.tolist(),
        property_pressure_absolute_Pa=m1.P_of_z(tvd, settings["P_surface"]).tolist(),
        component_id=[d.well.string[int(i)].id for i in geometry.segment_index(md)],
        hole_interval_id=[d.well.hole[int(i)].id for i in geometry.hole_index(md)],
    )
    boundaries = dict(
        inlet_temperature_residual_C=float(td[0] - settings["T_inj"]),
        maximum_bvp_rms_residual=float(np.max(sol.rms_residuals)),
        adaptive_nodes=len(sol.x),
        reported_solver_message=str(sol.message),
    )
    if (
        abs(boundaries["inlet_temperature_residual_C"]) > 1e-3
        or boundaries["maximum_bvp_rms_residual"] > 1.01e-4
    ):
        raise CalculationError(
            "SOLVER_NOT_CONVERGED",
            "Boundary/residual acceptance check failed.",
            boundaries,
        )
    exposure = d.operating.exposure
    resolved_exposure = {
        "kind": exposure.kind,
        "seconds_per_year": m1.SECONDS_PER_YEAR,
        "requested": exposure.model_dump(),
        "effective_seconds_at_display_depths": m1.exposure_seconds(
            md, settings["t_years"], settings["exposure"]
        ).tolist(),
    }
    summary = {}

    def scalar(name, value, unit, basis):
        summary[name] = {
            "value": float(value),
            "unit": unit,
            "basis": basis,
            "validity": "qualified" if warnings else "accepted",
        }

    scalar("bit_bore_delivery_temperature", td[-1], "°C", "bulk bore fluid")
    scalar("bit_annulus_temperature", tu[-1], "°C", "bulk annulus above bit")
    scalar("surface_return_temperature", tu[0], "°C", "bulk annulus fluid")
    scalar("mass_flow", rate, "kg/s", "resolved flow")
    for name in (
        "spp",
        "dp_string",
        "dp_annulus",
        "dp_bit",
        "dp_buoyancy",
        "dp_surface",
    ):
        scalar(name, hyd[name], "Pa difference", "hydraulic_budget_v160")
    scalar("minimum_annulus_velocity", hyd["v_ann_min"], "m/s", "hydraulic_budget_v160")
    scalar("pump_power", hyd["pump_power"], "W", "hydraulic_budget_v160")
    source_power = 0 if settings["annulus_heat"] is None else result["annulus_heat_W"]
    ledger = {
        "base_face_W": d.heat.face_heat_W,
        "rotary_bit_W": 0.0,
        "rotary_annulus_W": 0.0,
        "prescribed_annulus_W": source_power,
        "friction_heating_enabled": d.heat.friction_heating,
        "assigned_dissipation": energy.get("assigned_terms_W"),
        "nozzle_W": energy.get("nozzle_W"),
        "surface_hydraulic_loss_Pa": hyd["dp_surface"],
        "surface_W_in_hole": 0.0,
        "diagnostic_scope": "Core assigned/applied dissipation; not a complete well thermodynamic balance",
    }

    def bounds(values):
        values = np.asarray(values, dtype=float)
        return [float(values.min()), float(values.max())]

    from borethm import water_table as wt

    property_audit = dict(
        coverage="Accepted adaptive nodes, returned/display samples, physical/source breakpoints and hydraulic integration points",
        internal_trial_lookups_instrumented=False,
        wall_column_intermediate_passes_instrumented=False,
        table_temperature_C=[float(wt.T_GRID[0]), float(wt.T_GRID[-1])],
        table_pressure_absolute_Pa=[float(wt.P_GRID[0]), float(wt.P_GRID[-1])],
        paths={
            "thermal_fluid_props": dict(
                raw_temperature_C=bounds(audit_t),
                raw_pressure_absolute_Pa=bounds(raw_p),
                effective_table_temperature_C=bounds(
                    np.clip(audit_t, wt.T_GRID[0], wt.T_GRID[-1])
                ),
                effective_table_pressure_absolute_Pa=bounds(
                    np.clip(raw_p, wt.P_GRID[0], wt.P_GRID[-1])
                ),
                behaviour="water_table.props clips to actual table bounds; constant fluid bypasses table",
            ),
            "thermal_water_cp": dict(
                behaviour="water-only _cp clips to 1–480 C / 0.1–220 MPa before direct CP interpolator; last interval may extrapolate",
                raw_temperature_C=bounds(audit_t),
                raw_pressure_absolute_Pa=bounds(raw_p),
            ),
            "hydraulic_liquid_properties": dict(
                raw_pressure_absolute_Pa=bounds(
                    m1.P_of_z(geometry.survey.tvd(hz), settings["P_surface"])
                ),
                effective_guarded_pressure_absolute_Pa=bounds(
                    np.maximum(
                        m1.P_of_z(geometry.survey.tvd(hz), settings["P_surface"]), 5e6
                    )
                ),
                behaviour="LossModel clips temperature to 1–480 C and floors pressure at 5 MPa before fluid_props/table guards",
            ),
            "wall_column": dict(
                final_temperature_C=bounds(tu),
                final_pressure_absolute_Pa=bounds(wall_column + 1e5),
                behaviour="pressure.column clips temperature to 1–480 C, makes three density passes through fluid_props; intermediate pass inputs not instrumented",
            ),
        },
    )
    effective_numerics = numerical_profile(scenario)
    payload = dict(
        schema_version="1.0",
        mode="circulation_state",
        acceptance="qualified" if warnings else "accepted",
        acceptance_policy=POLICY,
        physical_request_hash=physical_hash(scenario),
        resolved_scenario_hash=digest(scenario.model_dump()),
        build=identity,
        physics=scenario.model.physics,
        physics_settings=physics_record(scenario, result, geometry),
        property_table=cache,
        resolved_scenario=scenario.model_dump(),
        numerical_profile=effective_numerics,
        numerical_profile_hash=digest(effective_numerics),
        initialisation={
            "policy": scenario.numerics.initialisation,
            "accepted_seed": accepted_seed,
        },
        seed_compatibility_key=compatibility_key(scenario),
        execution_identity=digest(
            {
                "physical_request": physical_hash(scenario),
                "engine": identity,
                "property_table": cache["sha256"],
                "numerics": effective_numerics,
                "seed_profile_sha256": accepted_seed["profile_sha256"]
                if accepted_seed
                else None,
            }
        ),
        time={"kind": "operating_state", "sim_time_s": None},
        pressure_reference={
            "atmosphere_absolute_Pa": 1e5,
            "wall_basis": "wall_budget_v1",
            "budget_basis": "hydraulic_budget_v160",
            "property_basis": "property_approximation_v160",
        },
        wall_reconstruction={
            "method": "baseline pressure.column; trapezoidal, three density passes",
            "integration_grid": "MD-aligned profile with core-linear TVD; zero dTVD across horizontal segments",
            "friction": "MD-indexed core cumulative annulus profile",
        },
        property_audit=property_audit,
        sampling=dict(
            solver_initial_nodes=160,
            returned_core_samples=len(result["z"]),
            display_samples=len(md),
            physical_breakpoints_md_m=np.unique(
                np.r_[model.top, model.bottom, settings["breaks"]]
            ).tolist(),
            boundary_sides="String component lookup selects the shallower side at an exact boundary; hole lookup selects the deeper side. The geometry inspector shows both adjacent records.",
            hydraulic_local_losses="Core distributed/blended kernel retained; cumulative friction uses its original integration/interpolation. No matched bore/nozzle-jump pressure curve is invented.",
        ),
        exposure=resolved_exposure,
        validity=dict(
            solver_converged=True,
            fields_finite=True,
            domain_status="qualified" if warnings else "within",
            energy_status=energy_status,
            assessment_status="not_evaluated",
            boundary_residuals=boundaries,
        ),
        warnings=warnings,
        attempts=attempts,
        summary=summary,
        hydraulics=to_json(hyd),
        energy_diagnostic=to_json(energy),
        source_ledger=ledger,
        profile=profile,
        limitations=[
            "1D bulk-fluid circulation with axisymmetric rock response",
            "Operating state, not transient axial transport",
            "No whole-programme assessment was performed",
            "No matched bore-pressure curve is enabled",
            "Core geometry validation requires every pipe to fit every hole interval",
        ],
    )
    from .trajectories import manifest_trajectory

    payload["trajectory"] = manifest_trajectory(scenario, md)
    if d.well.survey.kind != "vertical":
        payload["pressure_reference"]["wall_basis"] = "wall_budget_md_path_v1"
        payload["limitations"].append(
            "Inclined thermal/hydraulics only; deviated stability and programme conclusions unavailable"
        )
    payload["wall_clock_duration_s"] = time.perf_counter() - started
    canonical_bytes(payload)  # final finite/serialization gate
    return payload


def to_json(value):
    if isinstance(value, dict):
        return {k: to_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value
