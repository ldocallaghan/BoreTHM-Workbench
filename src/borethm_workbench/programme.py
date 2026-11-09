"""Hydraulic programmes and operating choices for site assessments.

BoreTHM 1.8.0 evaluates a site with its own pipe, flow, limits, timings and
fluids. A later BoreTHM accepts an ``Operations`` record and a site file's
``[operations]`` table, which state those choices, and describes each
choice's default with ``operation_defaults``. This module detects that
interface, lists the model's named pipes and builds the model's
``HydraulicProgramme`` from an assessment's programme definition. Defaults,
rules and sources come from ``operation_defaults``; validation messages
come from ``validate_operations`` with their field names. Nothing here
calculates a physical value.
"""

from .adapter import core_hole, core_segment, core_surface

UNAVAILABLE = (
    "The installed BoreTHM does not accept a drilling programme or operating "
    "choices. Install a BoreTHM release with Operations and operation_defaults "
    "to state them."
)
# Operations fields of the settled interface; an earlier interface lacks them.
REQUIRED_FIELDS = ("mass_flow_kg_s", "volume_flow_L_s", "mass_flows_kg_s", "volume_flows_L_s")


def interface_available():
    """True when the installed BoreTHM provides the settled operations
    interface: Operations with mass and volume flows, operation_defaults,
    validation records and evaluate's operations argument."""
    try:
        import inspect

        import borethm

        fields = getattr(getattr(borethm, "Operations", None), "__dataclass_fields__", {})
        return (
            hasattr(borethm, "operation_defaults")
            and hasattr(borethm, "validate_operations")
            and all(name in fields for name in REQUIRED_FIELDS)
            and "operations" in inspect.signature(borethm.evaluate).parameters
        )
    except (ImportError, AttributeError, ValueError, TypeError):
        return False


def capability():
    available = interface_available()
    return {
        "available": available,
        "message": None if available else UNAVAILABLE,
    }


def defaults(site=None):
    """operation_defaults of the installed model, for a site where given."""
    from borethm import operation_defaults

    return operation_defaults(site)


# ---------------------------------------------------------------- model pipes
def _layers(pipe):
    return [
        {"r_in_m": layer.r_in, "r_out_m": layer.r_out, "conductivity_W_m_K": layer.k}
        for layer in pipe.layers
    ]


def named_pipes():
    """The model's named pipe types, as the site file's pipe key names them."""
    from borethm.programme import NAMED_PIPES

    return NAMED_PIPES


def model_pipes():
    """The model's named drill pipes, with their layers and coating ratings.
    A pipe counts as on sale where the model's default candidates list it."""
    d = defaults()
    commercial = set(d["pipes"]["value"])
    ratings = d["coating_ratings_C"]["value"]
    return [
        {
            "name": pipe.name,
            "layers": _layers(pipe),
            "thermal_joint_fraction": pipe.joint_fraction,
            "source": pipe.source,
            "commercial": pipe.name in commercial,
            "coating_rating_C": ratings.get(pipe.name),
        }
        for pipe in named_pipes().values()
    ]


# ---------------------------------------------------------------- operations
# Each operating choice: its display quantity (None: shown as given) and
# label. Values are SI; volume flows are L/s, which the page converts to the
# volume_flow quantity's m3/s.
FIELDS = (
    ("pipes", "pipes", "Pipe candidates, tried in order"),
    ("mass_flow_kg_s", "mass_flow", "Flow, as a mass flow"),
    ("volume_flow_L_s", "volume_flow", "Flow, as a volume flow at surface"),
    ("mass_flows_kg_s", "mass_flow", "Flows the search tries, as mass flows"),
    ("volume_flows_L_s", "volume_flow", "Flows the search tries, as volume flows at surface"),
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
# Fields shown together in one row, with a choice between them.
GROUPS = {"flow": ("mass_flow_kg_s", "volume_flow_L_s"),
          "flows": ("mass_flows_kg_s", "volume_flows_L_s")}


def _sentence(rule):
    return rule[:1].upper() + rule[1:].rstrip(".") + "."


def operation_fields(site=None):
    """One row per operating choice: name, label, quantity, and the model's
    default value, rule and source from operation_defaults."""
    d = defaults(site)
    rows = []
    for name, quantity, label in FIELDS:
        entry = d[name]
        rows.append(
            {
                "name": name,
                "label": label,
                "quantity": quantity,
                "unit": entry["unit"],
                "rule": _sentence(entry["rule"]),
                "source": entry["source"],
                "default": entry["value"],
            }
        )
    return {
        "fields": rows,
        "groups": GROUPS,
        "pipe_names": list(named_pipes()),
    }


def check_operations(table):
    """Parse and validate an [operations] table with the model.

    Returns errors and warnings, each with the field the model names.
    Errors never raise.
    """
    from borethm.operations import operations_from_dict, validate_operations

    result = {"errors": [], "warnings": [], "stated": []}
    try:
        operations = operations_from_dict(table)
    except (ValueError, TypeError) as error:
        # The site-file parser names the key first: "<key>: <message>".
        message = str(error)
        key = message.split(":", 1)[0].split("[", 1)[0].split(".", 1)[0]
        known = {name for name, _, _ in FIELDS} | {"sources"}
        result["errors"].append({"field": key if key in known else "", "message": message})
        return result
    result["stated"] = list(operations.stated())
    for record in validate_operations(operations):
        kind = "errors" if record["level"] == "error" else "warnings"
        result[kind].append({"field": record["field"], "message": record["message"]})
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

    operations = getattr(site, "operations", None)
    if operations is not None and operations.pipes:
        pipe = operations.pipes[0]
    else:
        pipe = named_pipes()[defaults(site)["pipes"]["value"][0]]
    return se.site_well(site.target(), pipe, programme)


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


def _named(pipe):
    """The model's pipe type name a contract pipe matches, or None."""
    model = named_pipes().get(pipe.name)
    if model is None:
        return None
    same = (
        [(l.r_in_m, l.r_out_m, l.conductivity_W_m_K) for l in pipe.layers]
        == [(l.r_in, l.r_out, l.k) for l in model.layers]
        and pipe.thermal_joint_fraction == model.joint_fraction
        and pipe.source == model.source
    )
    return pipe.name if same else None


def _segment_table(segment):
    pipe = segment.pipe
    name = _named(pipe)
    if name is not None:
        table = {"length": segment.length_m, "pipe": name}
    elif len(pipe.layers) != 1:
        raise ValueError(
            f"A site file's [programme] table gives a pipe one wall layer or a model pipe name; "
            f"{pipe.name} has {len(pipe.layers)} user layers. The programme is kept with the "
            "assessment."
        )
    else:
        layer = pipe.layers[0]
        table = {
            "length": segment.length_m,
            "name": pipe.name,
            "bore_diameter": 2 * layer.r_in_m,
            "outer_diameter": 2 * layer.r_out_m,
            "conductivity": layer.conductivity_W_m_K,
            "source": pipe.source,
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
        t = segment.tool_loss
        if not t.include_boundary_changes or t.unresolved:
            raise ValueError(
                f"Section {segment.id} has a tool loss curve with include_boundary_changes or unresolved "
                "set, which a site file's [programme] table cannot state. The programme is kept with "
                "the assessment."
            )
        loss = {"dp_ref": t.reference_pressure_Pa, "flow_ref": t.reference_flow_m3_s,
                "density_ref": t.reference_density_kg_m3, "basis": t.basis,
                "span_segments": t.span_segments, "source": t.source}
        if t.flow_range_m3_s is not None:
            loss["flow_range"] = list(t.flow_range_m3_s)
        if t.temperature_max_C is not None:
            loss["temperature_max_C"] = t.temperature_max_C
        table["tool_loss"] = loss
    if segment.tool_class is not None:
        if segment.tool_basis != "additional" or segment.tool_span_segments != 1:
            raise ValueError(
                f"Section {segment.id} names a {segment.tool_class.replace('_', ' ')} with the "
                f"{segment.tool_basis.replace('_', ' ')} basis over {segment.tool_span_segments} sections. "
                "A site file's [programme] tools entry cannot state a tool's basis or span, so the "
                "programme is kept with the assessment."
            )
        table["tools"] = [{"tool_class": segment.tool_class, "collar_in": segment.collar_in}]
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
