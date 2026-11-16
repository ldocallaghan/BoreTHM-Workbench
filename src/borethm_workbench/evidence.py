"""Field comparisons published with BoreTHM, and the conditions they cover.

The data files in ``evidence/`` are written by ``deploy/build_evidence.py``
from a BoreTHM checkout at a recorded tag. This module reads them and compares
a run's inputs with the tested range. The comparison never blocks a run.
"""

from functools import lru_cache
import json
from pathlib import Path

from . import units

DIRECTORY = Path(__file__).parent / "evidence"
INSULATED_BELOW_W_M_K = 10.0


@lru_cache(maxsize=None)
def _load(name):
    return json.loads((DIRECTORY / name).read_text(encoding="utf-8"))


def comparisons():
    return _load("comparisons.json")


def tested_range():
    return _load("tested-range.json")


def sources():
    return _load("sources.json")


def _row(name):
    for row in tested_range()["rows"]:
        if row["input"] == name:
            return row
    raise KeyError(name)


def pipe_kind(layer_conductivities):
    """'insulated' when any pipe layer conducts below 10 W/(m K), else 'steel'."""
    values = list(layer_conductivities)
    if not values:
        return None
    return (
        "insulated"
        if any(value < INSULATED_BELOW_W_M_K for value in values)
        else "steel"
    )


# ---------------------------------------------------------------- inputs
def circulation_inputs(manifest):
    """Inputs of a completed circulation run, all from its saved record."""
    scenario = manifest["resolved_scenario"]
    definition = scenario["definition"]
    operating = definition["operating"]
    flow = operating["flow"]
    profile = manifest.get("profile") or {}
    formation = profile.get("formation_temperature_C") or []
    layers = [
        layer["conductivity_W_m_K"]
        for component in definition["well"]["string"]
        for layer in component["pipe"]["layers"]
    ]
    fluid = definition["fluid"]["kind"]
    return {
        "depth_m": definition["well"]["bit_md_m"],
        "formation_C": formation[-1] if formation else None,
        "volume_flow_m3_s": flow.get("volume_rate_m3_s")
        if flow["kind"] == "surface_volume"
        else None,
        "mass_flow_kg_s": flow.get("mass_rate_kg_s") if flow["kind"] == "mass" else None,
        "inlet_C": operating["inlet_temperature_C"],
        "fluid": {"iapws_water": "water", "barite_mud": "mud"}.get(
            fluid, "constant-property fluid"
        ),
        "pipe": pipe_kind(layers),
    }


def assessment_inputs(manifest):
    """Inputs of a completed site assessment, from the evaluator's result."""
    report = manifest["assessment"]
    site, programme = report["site"], report["programme"]
    from borethm import named_pipes

    # The drill pipes the field comparisons describe; vacuum-insulated tubing
    # is a sensitivity and is not classed.
    pipes = {name: p for name, p in named_pipes().items() if not name.startswith("vacuum-insulated")}
    pipe = pipes.get(programme.get("pipe"))
    kind = (
        "mass" if programme.get("flow_unit") == "kg/s" else "volume"
    )
    return {
        "depth_m": site.get("target_depth_m"),
        "formation_C": report.get("T_rock_C", site.get("target_T_C")),
        "volume_flow_m3_s": programme["flow"] / 1000.0
        if kind == "volume" and programme.get("flow_unit") == "L/s"
        else None,
        "mass_flow_kg_s": programme.get("mass_flow_kg_s") if kind == "mass" else None,
        "inlet_C": site.get("T_inj_C"),
        "fluid": "mud" if programme.get("fluid") else "water",
        "pipe": pipe_kind(layer.k for layer in pipe.layers) if pipe else None,
        "pipe_name": programme.get("pipe"),
    }


# ---------------------------------------------------------------- flags
LABELS = {
    "depth_m": ("Bit depth", "bit depth"),
    "formation_C": ("Formation temperature", "formation temperature"),
    "volume_flow_m3_s": ("Flow", "flow"),
    "mass_flow_kg_s": ("Mass flow", "mass flow"),
    "inlet_C": ("Inlet temperature", "inlet temperature"),
}


def flags(inputs, system="SI"):
    """Plain statements for each input outside the field-tested range.

    Returns {"outside": [...], "notes": [...]}; each item has input, value
    (SI), limit (SI) where numeric, and message in the requested units.
    """
    outside, notes = [], []
    for key, (label, lower) in LABELS.items():
        value = inputs.get(key)
        if value is None:
            continue
        row = _row(key)
        limits = row["range"]
        quantity = row["quantity"]
        if limits is None:
            continue
        shown = units.fmt(value, quantity, system)
        if value > limits[1]:
            outside.append(
                {
                    "input": key,
                    "value": value,
                    "limit": limits[1],
                    "direction": "above",
                    "message": f"{label} {shown} is above the highest field-tested {lower} "
                    f"({units.fmt(limits[1], quantity, system)}).",
                }
            )
        elif value < limits[0]:
            outside.append(
                {
                    "input": key,
                    "value": value,
                    "limit": limits[0],
                    "direction": "below",
                    "message": f"{label} {shown} is below the lowest field-tested {lower} "
                    f"({units.fmt(limits[0], quantity, system)}).",
                }
            )
    if inputs.get("formation_C") is not None:
        notes.append(
            {
                "input": "formation_C",
                "message": "Formation temperature was recorded for the FORGE 16A comparison only.",
            }
        )
    if inputs.get("mass_flow_kg_s") is not None:
        notes.append(
            {
                "input": "mass_flow_kg_s",
                "message": "Mass flow is compared with the modelled mass rates of the FORGE 16B comparison; FORGE 16A records volume flow only.",
            }
        )
    for key, label in (("fluid", "Fluid"), ("pipe", "Pipe")):
        value = inputs.get(key)
        row = _row(key)
        if value is None:
            notes.append(
                {"input": key, "message": f"{label} type could not be classified."}
            )
            continue
        if value not in row["values"]:
            outside.append(
                {
                    "input": key,
                    "value": value,
                    "direction": "not tested",
                    "message": f"{label} type '{value}' was not part of any field comparison.",
                }
            )
        elif value in row.get("notes", {}):
            notes.append(
                {
                    "input": key,
                    "message": f"{label} type '{value}' was field-tested only "
                    f"{row['notes'][value]}.",
                }
            )
    notes.append({"input": "conditions", "message": tested_range()["conditions"]})
    return {"outside": outside, "notes": notes}


def run_flags(manifest, system="SI"):
    if manifest.get("mode") == "site_assessment":
        inputs = assessment_inputs(manifest)
    elif manifest.get("mode", "circulation_state") == "circulation_state":
        inputs = circulation_inputs(manifest)
    else:
        return None
    result = flags(inputs, system)
    result["inputs"] = inputs
    return result


def comparison_figure(comparison, system="SI"):
    """Measured and modelled temperatures against bit depth, as inline SVG."""
    from . import svg

    samples = comparison["sample_values"]
    depth = [units.from_si(s["depth_m"], "length", system) for s in samples]
    measured = [units.from_si(s["measured_C"], "temperature", system) for s in samples]
    low = [units.from_si(s["modelled_C"][0], "temperature", system) for s in samples]
    high = [units.from_si(s["modelled_C"][1], "temperature", system) for s in samples]
    return svg.depth_chart(
        [],
        f"Temperature ({units.symbol('temperature', system)})",
        f"Bit measured depth ({units.symbol('length', system)})",
        comparison["title"],
        points=[
            {"name": "Measured", "x": measured, "y": depth, "colour": "#25313b"},
            {"name": "Modelled, lowest reading", "x": low, "y": depth, "colour": "#18807a"},
            {"name": "Modelled, highest reading", "x": high, "y": depth, "colour": "#d0703c"},
        ],
    )
