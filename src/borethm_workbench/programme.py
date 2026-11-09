"""Hydraulic programmes and operating choices for site assessments.

BoreTHM 1.8.0 evaluates a site with its own pipe, flow, limits, timings and
fluids. A later BoreTHM accepts an ``Operations`` record and a site file's
``[operations]`` table, which state those choices. This module detects that
interface, describes each operating choice with the model's own default
and builds the model's ``HydraulicProgramme`` from an assessment's
programme definition. Defaults are read from the installed model; nothing
here calculates a physical value.
"""

import re

from .adapter import core_hole, core_segment, core_surface

UNAVAILABLE = (
    "The installed BoreTHM does not accept a drilling programme or operating "
    "choices. Install a BoreTHM release with Operations to state them."
)


def interface_available():
    """True when the installed BoreTHM accepts Operations."""
    try:
        import inspect

        import borethm

        return hasattr(borethm, "Operations") and "operations" in inspect.signature(
            borethm.evaluate
        ).parameters
    except (ImportError, AttributeError, ValueError, TypeError):
        return False


def capability():
    available = interface_available()
    return {
        "available": available,
        "message": None if available else UNAVAILABLE,
    }


# ---------------------------------------------------------------- model pipes
def _layers(pipe):
    return [
        {"r_in_m": layer.r_in, "r_out_m": layer.r_out, "conductivity_W_m_K": layer.k}
        for layer in pipe.layers
    ]


def model_pipes():
    """The model's named drill pipes, with their layers and coating ratings."""
    from borethm import site_evaluation as se
    from borethm import well_geometry as wg

    commercial = {p.name for p in se.SITE_PIPES}
    pipes = []
    for pipe in (
        wg.TK_DRAKON,
        wg.INTERNALLY_COATED,
        wg.EXTERNALLY_COATED,
        wg.CONVENTIONAL,
        wg.DUAL_WALL,
        wg.VACUUM_INSULATED,
    ):
        pipes.append(
            {
                "name": pipe.name,
                "layers": _layers(pipe),
                "thermal_joint_fraction": pipe.joint_fraction,
                "source": pipe.source,
                "commercial": pipe.name in commercial,
                "coating_rating_C": se.COATING_RATINGS.get(pipe.name),
            }
        )
    return pipes


# ---------------------------------------------------------------- operations
# Each operating choice: its quantity for display units (None: no conversion)
# and how the model chooses it when it is not stated.
FIELDS = (
    ("pipes", "pipes", "Pipe candidates, tried in order"),
    ("flow", "model_flow", "Flow"),
    ("flows", "model_flow", "Flows the search tries"),
    ("tool_limit_C", "temperature", "Tool temperature limit"),
    ("coating_ratings_C", "temperature", "Coating ratings"),
    ("pump_pressure_limit_Pa", "pressure", "Standpipe pressure limit"),
    ("rig_pumps", "count", "Rig pumps"),
    ("min_annular_velocity_m_s", "velocity", "Hole-cleaning annular velocity"),
    ("precirculation_min", "minutes", "Circulation before a connection"),
    ("connection_median_min", "minutes", "Median connection, pumps off"),
    ("connection_p90_min", "minutes", "90th-percentile connection, pumps off"),
    ("trip_hours", "hours", "Trip time with the bottom of the hole static"),
    ("trip_scale", "factor", "Factor on the trip-time rule"),
    ("stage_spacing_m", "length", "Run-in staging interval"),
    ("stage_minutes", "minutes", "Circulation at each run-in stage"),
    ("rop_m_h", "rate_of_penetration", "Rate of penetration"),
    ("drilling_fluid_kg_m3", "density", "Drilling fluid density at 20 °C"),
    ("trip_fluid_kg_m3", "density", "Trip fluid density at 20 °C"),
)
FLOW_UNIT = "kg/s with water; L/s at surface with a weighted fluid"


def model_defaults():
    """The model's own values for the operating choices it makes by a fixed value."""
    from borethm import drilling_cycle as dc
    from borethm import geo_constants as C
    from borethm import model1_coupled as m1
    from borethm import site_evaluation as se

    t = dc.forge_timings()
    return {
        "pipes": [p.name for p in se.SITE_PIPES],
        "flows": list(se.SITE_FLOWS),
        "tool_limit_C": C.BHA_SURVIVAL_TEMP,
        "coating_ratings_C": dict(se.COATING_RATINGS),
        "pump_pressure_limit_Pa": m1.PUMP_LIMIT,
        "rig_pumps": se.RIG_PUMPS,
        "min_annular_velocity_m_s": m1.HOLE_CLEANING_V,
        "precirculation_min": t.fresh_circ_p10_min,
        "connection_median_min": t.connection_median_min,
        "connection_p90_min": t.connection_p90_min,
        "stage_spacing_ft": t.stage_spacing_ft,
        "stage_minutes": t.stage_minutes,
    }


def _rules(d, fmt):
    pipes = ", ".join(d["pipes"])
    flows = ", ".join(f"{f:g}" for f in d["flows"])
    ratings = "; ".join(f"{k} {fmt(v, 'temperature')}" for k, v in d["coating_ratings_C"].items())
    return {
        "pipes": f"The model tries {pipes}, in that order, and takes the first for which a flow passes.",
        "flow": "The model takes the lowest flow on its grid that keeps the tool below its limit and cleans the hole within the pump limit.",
        "flows": f"The model's flow grid: {flows}.",
        "tool_limit_C": f"{fmt(d['tool_limit_C'], 'temperature')}, the model's high-temperature tool class.",
        "coating_ratings_C": f"{ratings}, the coating maker's rating.",
        "pump_pressure_limit_Pa": f"{fmt(d['pump_pressure_limit_Pa'], 'pressure')}, the limit of the model's reference well design.",
        "rig_pumps": f"{d['rig_pumps']} pumps of the reference rig's model.",
        "min_annular_velocity_m_s": f"{fmt(d['min_annular_velocity_m_s'], 'velocity')}, the annular velocity around the drill pipe at FORGE 16B.",
        "precirculation_min": f"{d['precirculation_min']:g} min, the FORGE 16B 10th percentile; if the tool needs it, the model then searches longer circulation and shorter connections.",
        "connection_median_min": f"{d['connection_median_min']:g} min, the FORGE 16B median.",
        "connection_p90_min": f"{d['connection_p90_min']:g} min, the FORGE 16B 90th percentile.",
        "trip_hours": "FORGE 16B tripping speeds and surface time, scaled to the target depth.",
        "trip_scale": "No factor on the trip-time rule.",
        "stage_spacing_m": f"Every {fmt(d['stage_spacing_ft'] * 0.3048, 'length')}, as at FORGE 16B; if the tool needs it, the model then searches closer and longer stages.",
        "stage_minutes": f"{d['stage_minutes']:g} min, as at FORGE 16B; if the tool needs it, the model then searches longer stages.",
        "rop_m_h": "The model's rate of penetration for the circulation, iterated with the wall's exposure for a coupled site.",
        "drilling_fluid_kg_m3": "The lower bound of the drilling fluid's common window, never below water, iterated with the circulation.",
        "trip_fluid_kg_m3": "The larger of the drilling fluid and the lower bound of the trip fluid's common window.",
    }


def operation_fields(system="SI"):
    """One row per operating choice: name, label, quantity and the model's rule."""
    from . import units

    defaults = model_defaults()
    rules = _rules(defaults, lambda v, q: units.fmt(v, q, system))
    from borethm.operations import PIPES, RANGES

    rows = []
    for name, quantity, label in FIELDS:
        rows.append(
            {
                "name": name,
                "label": label,
                "quantity": quantity,
                "unit_note": FLOW_UNIT if quantity == "model_flow" else None,
                "rule": rules[name],
                "range_si": list(RANGES[name]) if name in RANGES else None,
            }
        )
    return {
        "fields": rows,
        "pipe_names": list(PIPES),
        "defaults": {
            k: v for k, v in defaults.items() if k != "stage_spacing_ft"
        },
    }


_FIELD_NAMES = [name for name, _, _ in FIELDS]
_PHRASES = (
    ("coating rating", "coating_ratings_C"),
    ("tool limit", "tool_limit_C"),
    ("pump limit", "pump_pressure_limit_Pa"),
    ("operations pipe", "pipes"),
    ("operations flow", "flow"),
    ("flows", "flows"),
)


def operation_field(message):
    """The operating choice a validation message is about, or ''."""
    for name in sorted(_FIELD_NAMES, key=len, reverse=True):
        if re.search(rf"(?<![A-Za-z_]){re.escape(name)}(?![A-Za-z_])", message):
            return name
    for phrase, name in _PHRASES:
        if phrase in message:
            return name
    return ""


def check_operations(table):
    """Parse and validate an [operations] table with the model.

    Returns field-attached errors and warnings. Errors never raise.
    """
    from borethm.operations import operations_from_dict, validate_operations

    result = {"errors": [], "warnings": [], "stated": []}
    try:
        operations = operations_from_dict(table)
    except (ValueError, TypeError) as error:
        message = str(error)
        result["errors"].append({"field": operation_field(message), "message": message})
        return result
    result["stated"] = list(operations.stated())
    try:
        warnings = validate_operations(operations)
    except ValueError as error:
        for part in str(error).removeprefix("operations: ").split("; "):
            result["errors"].append({"field": operation_field(part), "message": part})
        return result
    result["warnings"] = [
        {"field": operation_field(w), "message": w} for w in warnings
    ]
    return result


# ---------------------------------------------------------------- programme
def build(definition):
    """The model's HydraulicProgramme for a programme definition."""
    from borethm import HydraulicProgramme
    from borethm.hydraulic_losses import DownholeTool

    surface = core_surface(definition.surface_loss)
    if definition.kind == "reference":
        return HydraulicProgramme.reference_programme(
            surface=surface,
            transitions=definition.transitions,
            joints=definition.joints,
            tools=definition.tools,
            bha_tools=tuple(
                DownholeTool(t.tool_class, t.collar_in) for t in definition.bha_tools
            ),
        )
    return HydraulicProgramme(
        reference=None,
        segments=tuple(core_segment(s) for s in definition.string),
        hole=tuple(core_hole(h) for h in definition.hole),
        surface=surface,
        transitions=definition.transitions,
    )


def geometry(site, programme):
    """The well the model builds at the site's target for a programme, with
    the first candidate pipe where the model chooses the pipe."""
    from borethm import site_evaluation as se

    pipes = (site.operations.pipes if getattr(site, "operations", None) and site.operations.pipes else se.SITE_PIPES)
    return se.site_well(site.target(), pipes[0], programme)


def _joint_dict(j):
    return None if j is None else {
        "outer_diameter_m": j.outer_diameter,
        "bore_diameter_m": j.bore_diameter,
        "restriction_length_m": j.restriction_length,
        "spacing_m": j.spacing,
        "transition": j.transition,
        "kind": j.kind,
        "taper_angle_deg": j.taper_angle_deg,
        "source": j.source,
    }


def segment_dict(segment, index, notes=None):
    """A model StringSegment as a contract Segment dictionary."""
    p = segment.pipe
    out = {
        "id": f"section-{index + 1}",
        "length_m": segment.length,
        "pipe": {
            "name": p.name,
            "layers": _layers(p),
            "thermal_joint_fraction": p.joint_fraction,
            "source": p.source,
            "hydraulic_joint": _joint_dict(p.hydraulic_joint),
        },
    }
    if segment.local_loss is not None:
        l = segment.local_loss
        out["local_loss"] = {
            "bore_coefficient": l.bore_coefficient,
            "annulus_coefficient": l.annulus_coefficient,
            "bore_reference_area_m2": l.bore_reference_area,
            "annulus_reference_area_m2": l.annulus_reference_area,
            "source": l.source,
        }
    if segment.tool_loss is not None:
        t = segment.tool_loss
        out["tool_loss"] = {
            "reference_pressure_Pa": t.dp_ref,
            "reference_flow_m3_s": t.flow_ref,
            "reference_density_kg_m3": t.density_ref,
            "basis": t.basis,
            "span_segments": t.span_segments,
            "include_boundary_changes": t.include_boundary_changes,
            "unresolved": t.unresolved,
            "flow_range_m3_s": None if t.flow_range is None else list(t.flow_range),
            "temperature_max_C": t.temperature_max_C,
            "source": t.source,
        }
        if notes is not None and (t.exponent != 2.0 or t.differential != 0.0):
            notes.append(f"Section {index + 1}: the tool loss exponent and differential are not shown in the form.")
    if segment.tools:
        tool = segment.tools[0]
        out.update(tool_class=tool.tool_class, collar_in=tool.collar_in,
                   tool_basis=tool.basis, tool_span_segments=tool.span_segments)
        if notes is not None and (len(segment.tools) > 1 or tool.dp_ref is not None):
            notes.append(f"Section {index + 1}: tools with entered figures are not shown in the form.")
    return out


def hole_dict(h, index):
    return {
        "id": f"hole-{index + 1}",
        "md_top_m": h.md_top,
        "md_bottom_m": h.md_bottom,
        "r_wall_m": h.r_wall,
        "layers": [
            {"r_in_m": l.r_in, "r_out_m": l.r_out, "conductivity_W_m_K": l.k}
            for l in h.layers
        ],
    }


def drawing(geometry_):
    """The model well as contract string and hole lists, surface to bit."""
    return {
        "bit_md_m": geometry_.md_bit,
        "string": [segment_dict(s, i) for i, s in enumerate(geometry_.string)],
        "hole": [hole_dict(h, i) for i, h in enumerate(geometry_.hole)],
    }


def check(definition, site_toml):
    """Build a programme and its well at the site's target with the model."""
    from .sites import load_text

    result = {"valid": False, "errors": [], "drawing": None, "toml_table": None,
              "toml_note": None, "target_depth_m": None}
    try:
        site = load_text(site_toml)
    except ValueError as error:
        result["errors"].append(f"Site file: {error}")
        return result
    result["target_depth_m"] = float(site.target())
    try:
        programme = build(definition)
        well = geometry(site, programme)
    except (ValueError, TypeError) as error:
        message = str(error)
        if "sum to the circulation depth" in message:
            message = (
                "The string section lengths must add up to the target depth, "
                f"{site.target():.2f} m."
            )
        result["errors"].append(message)
        return result
    result["valid"] = True
    result["drawing"] = drawing(well)
    try:
        result["toml_table"] = to_table(definition)
    except ValueError as error:
        result["toml_note"] = str(error)
    return result


# ---------------------------------------------------------------- site files
def _surface_table(surface_loss):
    if surface_loss.kind == "none":
        # The same zero coefficient the application builds for "none".
        return {"coefficient": 0.0, "source": "Explicitly declared no surface loss"}
    if surface_loss.kind == "api_case":
        return {"case": surface_loss.case}
    return {"coefficient": surface_loss.coefficient, "source": surface_loss.source}


def _segment_table(segment):
    pipe = segment.pipe
    if len(pipe.layers) != 1:
        raise ValueError(
            f"A site file's [programme] table gives each pipe one wall layer; "
            f"{pipe.name} has {len(pipe.layers)}. The programme is kept with the "
            "assessment and cannot be written to the site file."
        )
    layer = pipe.layers[0]
    table = {
        "length": segment.length_m,
        "name": pipe.name,
        "bore_diameter": 2 * layer.r_in_m,
        "outer_diameter": 2 * layer.r_out_m,
        "conductivity": layer.conductivity_W_m_K,
        "source": pipe.source or "site-supplied geometry",
        "joint_fraction": pipe.thermal_joint_fraction,
    }
    if pipe.hydraulic_joint is not None:
        j = pipe.hydraulic_joint
        joint = {
            "outer_diameter": j.outer_diameter_m,
            "bore_diameter": j.bore_diameter_m,
            "restriction_length": j.restriction_length_m,
            "spacing": j.spacing_m,
            "source": j.source,
            "kind": j.kind,
        }
        if j.transition is not None:
            joint["transition"] = j.transition
        if j.taper_angle_deg is not None:
            joint["taper_angle_deg"] = j.taper_angle_deg
        table["hydraulic_joint"] = joint
    if segment.local_loss is not None:
        l = segment.local_loss
        local = {"bore_coefficient": l.bore_coefficient, "annulus_coefficient": l.annulus_coefficient,
                 "source": l.source}
        if l.bore_reference_area_m2 is not None:
            local["bore_reference_area"] = l.bore_reference_area_m2
        if l.annulus_reference_area_m2 is not None:
            local["annulus_reference_area"] = l.annulus_reference_area_m2
        table["local_loss"] = local
    if segment.tool_loss is not None:
        raise ValueError(
            "A tool pressure-loss curve entered on a section cannot be written to the "
            "site file's [programme] table by this application. The programme is kept "
            "with the assessment."
        )
    if segment.tool_class is not None:
        tool = {"tool_class": segment.tool_class, "collar_in": segment.collar_in}
        if segment.tool_basis != "additional" or segment.tool_span_segments != 1:
            raise ValueError(
                "A named tool with a stated basis or span cannot be written to the site "
                "file's [programme] table. The programme is kept with the assessment."
            )
        table["tools"] = [tool]
    return table


def to_table(definition):
    """The site file's [programme] table for a programme definition.

    Raises ValueError, with the reason, for a definition the table cannot
    hold, such as a pipe with more than one wall layer.
    """
    table = {"surface": _surface_table(definition.surface_loss)}
    if definition.transitions != "abrupt":
        table["transitions"] = definition.transitions
    if definition.kind == "reference":
        table["joints"] = definition.joints
        table["tools"] = definition.tools
        if definition.bha_tools:
            table["bha_tools"] = [
                {"tool_class": t.tool_class, "collar_in": t.collar_in}
                for t in definition.bha_tools
            ]
        return table
    table["segments"] = [_segment_table(s) for s in definition.string]
    if definition.hole:
        table["hole"] = [
            {
                "md_top": h.md_top_m,
                "md_bottom": h.md_bottom_m,
                "r_wall": h.r_wall_m,
                **(
                    {"layers": [[l.r_in_m, l.r_out_m, l.conductivity_W_m_K] for l in h.layers]}
                    if h.layers
                    else {}
                ),
            }
            for h in definition.hole
        ]
    return table


def from_site(programme):
    """A programme definition (as a dictionary) for a site's HydraulicProgramme.

    Returns (definition, notes). Physics settings in the table are not part
    of the definition; the notes name any that the definition leaves out.
    """
    from borethm.hydraulic_losses import SurfaceLoss

    notes = []
    for name in ("physics", "annulus_model", "friction", "roughness", "joint_transition",
                 "taper_angle_deg"):
        if getattr(programme, name) is not None:
            notes.append(f"The site file's programme sets {name}; the form leaves it out and the assessment's physics choice applies.")
    surface = programme.surface
    if surface is None or surface.value == 0:
        surface_loss = {"kind": "none"}
    else:
        case = next((c for c in range(1, 6) if SurfaceLoss.api_case(c) == surface), None)
        surface_loss = (
            {"kind": "api_case", "case": case}
            if case is not None
            else {"kind": "coefficient", "coefficient": surface.value,
                  "unit_convention": "psi_ppg_US_gpm", "source": surface.source}
        )
    holes = [hole_dict(h, i) for i, h in enumerate(programme.hole)]
    if not programme.segments:
        return (
            {
                "kind": "reference",
                "transitions": programme.transitions,
                "surface_loss": surface_loss,
                "joints": programme.joints,
                "tools": programme.tools,
                "bha_tools": [
                    {"tool_class": t.tool_class, "collar_in": t.collar_in}
                    for t in programme.bha_tools
                ],
            },
            notes + (["The site file's hole intervals are kept by the reference programme only through the site file."] if holes else []),
        )
    string = [segment_dict(seg, i, notes) for i, seg in enumerate(programme.segments)]
    return (
        {
            "kind": "custom",
            "string": string,
            "hole": holes,
            "transitions": programme.transitions,
            "surface_loss": surface_loss,
        },
        notes,
    )
