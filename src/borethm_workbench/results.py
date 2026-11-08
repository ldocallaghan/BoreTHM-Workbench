"""Results pages for completed runs, built only from the saved run.

Values are SI as the model returned them. The only arithmetic applied is the
difference between two returned values where a page shows a width or a
margin; each such entry is labelled as a difference. Figures are inline SVG
in the requested display units.
"""

from . import evidence, svg, units
from .qualifications import with_sentences

STATES = ("drilling", "connection", "connection, fresh rock", "trip")
STATE_LABELS = {
    "drilling": "Drilling",
    "connection": "Connection",
    "connection, fresh rock": "Connection, fresh rock",
    "fresh rock": "Connection, fresh rock",
    "trip": "Trip",
}
FLUID_LABELS = {"drilling_fluid": "Drilling fluid", "trip_fluid": "Trip fluid"}


def _difference(a, b):
    return None if a is None or b is None else a - b


def _bound(by):
    if not by:
        return None
    return {
        "state": by.get("state"),
        "case": by.get("case"),
        "md_m": by.get("md"),
    }


def assessment_summary(manifest):
    report = manifest["assessment"]
    status = report["status"]
    windows = report.get("windows") or {}
    fluids = {}
    for key in ("drilling_fluid", "trip_fluid"):
        window = windows.get(key) or {}
        fluids[key] = {
            "label": FLUID_LABELS[key],
            "open": window.get("open"),
            "lower_kg_m3": window.get("lo"),
            "upper_kg_m3": window.get("hi"),
            "width_kg_m3": _difference(window.get("hi"), window.get("lo")),
            "lower_by": _bound(window.get("lo_by")),
            "upper_by": _bound(window.get("hi_by")),
            "chosen_kg_m3": window.get("fluid"),
            "note": window.get("note"),
        }
    by_case = []
    for state, cases in (windows.get("states") or {}).items():
        for case, values in cases.items():
            by_case.append(
                {
                    "state": state,
                    "case": case,
                    "lower_kg_m3": values.get("lo"),
                    "lower_md_m": values.get("lo_md"),
                    "upper_kg_m3": values.get("hi"),
                    "upper_md_m": values.get("hi_md"),
                }
            )
    tools = report.get("tools") or {}
    rating = tools.get("limit")
    tool_rows = []

    def tool(label, peak, ok, detail=None):
        tool_rows.append(
            {
                "item": label,
                "peak_C": peak,
                "rating_C": rating,
                "margin_C": _difference(rating, peak),
                "ok": ok,
                "detail": detail,
            }
        )

    if "drilling" in tools:
        tool("Tool while drilling", tools["drilling"].get("T"), tools["drilling"].get("ok"))
    if "connection" in tools:
        tool("Tool during connections", tools["connection"].get("peak"), tools["connection"].get("ok"))
    trip = tools.get("trip_in") or {}
    if trip.get("unstaged"):
        tool(
            "Tool on run-in without staging",
            trip["unstaged"].get("peak"),
            None,
            {"md_m": trip["unstaged"].get("md")},
        )
    if trip.get("staged"):
        schedule = trip.get("schedule") or {}
        tool(
            "Tool on run-in with staging",
            trip["staged"].get("peak"),
            trip.get("ok"),
            {
                "md_m": trip["staged"].get("md"),
                "stages": schedule.get("stages"),
                "stage_spacing_m": schedule.get("spacing_m"),
                "stage_minutes": schedule.get("minutes"),
                "staging_h": schedule.get("staging_h"),
            },
        )
    coating_rows = []
    coating = tools.get("coating") or {}
    if coating:
        coating_rating = coating.get("rating")
        for label, peak, minutes in (
            ("Coating while circulating", coating.get("circulating_max"), None),
            (
                "Coating during connections",
                (coating.get("connection") or {}).get("max"),
                (coating.get("connection") or {}).get("minutes_above"),
            ),
            (
                "Coating on run-in",
                (coating.get("trip_in") or {}).get("max"),
                (coating.get("trip_in") or {}).get("minutes_above"),
            ),
        ):
            coating_rows.append(
                {
                    "item": label,
                    "peak_C": peak,
                    "rating_C": coating_rating,
                    "margin_C": _difference(coating_rating, peak),
                    "minutes_above_rating": minutes,
                }
            )
    cycle = report.get("cycle") or {}
    cases = [
        {
            "case": case.get("label"),
            "verdict": case.get("verdict"),
            "states": case.get("states") or {},
        }
        for case in cycle.get("cases") or []
    ]
    components = report.get("components") or {}
    pumps = components.get("pumps") or {}
    programme = report.get("programme") or {}
    return {
        "verdict": {
            "status": status.get("label"),
            "reasons": status.get("reasons") or [],
            "controlling_constraint": (report.get("reading") or {}).get(
                "controlling_constraint"
            ),
            "reading": report.get("reading") or {},
        },
        "site": report.get("site") or {},
        "programme": {
            "pipe": programme.get("pipe"),
            "mass_flow_kg_s": programme.get("mass_flow_kg_s"),
            "flow": programme.get("flow"),
            "flow_unit": programme.get("flow_unit"),
            "fluid_kg_m3": (programme.get("fluid") or {}).get("rho_surface")
            if programme.get("fluid")
            else None,
            "water": not programme.get("fluid"),
            "backpressure_Pa": programme.get("backpressure_Pa"),
            "bit_C": programme.get("bit_C"),
            "return_C": programme.get("return_C"),
        },
        "windows": {
            "fluids": fluids,
            "by_case": by_case,
            "evaluated_depths_m": windows.get("depths") or [],
            "profiles_returned": False,
        },
        "tools": {
            "rating_C": rating,
            "status": tools.get("tool"),
            "rows": tool_rows,
            "coating": {
                "pipe": coating.get("pipe"),
                "status": coating.get("status"),
                "rows": coating_rows,
            },
        },
        "stability": {
            "verdicts": cycle.get("verdicts") or {},
            "deciding_case": cycle.get("deciding_case"),
            "cases": cases,
            "site_verdict": cycle.get("site_verdict"),
        },
        "pressure": {
            "standpipe_Pa": pumps.get("spp", programme.get("spp_Pa")),
            "trip_standpipe_Pa": pumps.get("trip_spp"),
            "within_rig_pumps": pumps.get("within_rig"),
            "pumps_ok": pumps.get("ok"),
            "pump_limit_Pa": None,
            "volume_flow_gpm": pumps.get("gpm"),
        },
        "input_warnings": report.get("input_warnings") or [],
        "differences": "Window width is upper bound minus lower bound; margin is rating minus peak. Both use values returned by the model.",
    }


def circulation_summary(manifest):
    summary = manifest.get("summary") or {}
    definition = manifest["resolved_scenario"]["definition"]

    def value(key):
        return (summary.get(key) or {}).get("value")

    return {
        "bit_bore_C": value("bit_bore_delivery_temperature"),
        "bit_annulus_C": value("bit_annulus_temperature"),
        "return_C": value("surface_return_temperature"),
        "standpipe_Pa": value("spp"),
        "mass_flow_kg_s": value("mass_flow"),
        "bit_md_m": definition["well"]["bit_md_m"],
        "inlet_C": definition["operating"]["inlet_temperature_C"],
        "fluid": definition["fluid"]["kind"],
    }


# ---------------------------------------------------------------- figures
def _axis(quantity, system, text):
    return f"{text} ({units.symbol(quantity, system)})"


def temperature_figure(manifest, system, title="Circulating temperatures"):
    p = manifest["profile"]

    def t(values):
        return [units.from_si(v, "temperature", system) for v in values]

    depth = [units.from_si(v, "length", system) for v in p["md_m"]]
    return svg.depth_chart(
        [
            {"name": "Formation", "x": t(p["formation_temperature_C"]), "y": depth, "colour": "#8c979e", "dash": "6 4", "width": 1.5},
            {"name": "Bore (down the pipe)", "x": t(p["bore_temperature_C"]), "y": depth, "colour": "#18807a"},
            {"name": "Annulus (returning)", "x": t(p["annulus_temperature_C"]), "y": depth, "colour": "#d0703c"},
        ],
        _axis("temperature", system, "Temperature"),
        _axis("length", system, "Measured depth"),
        title,
    )


def window_figure(summary, system):
    rows = []
    state = None
    for item in summary["windows"]["by_case"]:
        if item["state"] != state:
            state = item["state"]
            rows.append({"label": STATE_LABELS.get(state, state), "heading": True})
        rows.append(
            {
                "label": item["case"],
                "low": None if item["lower_kg_m3"] is None else units.from_si(item["lower_kg_m3"], "density", system),
                "high": None if item["upper_kg_m3"] is None else units.from_si(item["upper_kg_m3"], "density", system),
                "status": "fail"
                if item["lower_kg_m3"] is not None
                and item["upper_kg_m3"] is not None
                and item["upper_kg_m3"] < item["lower_kg_m3"]
                else "ok",
            }
        )
    rows.append({"label": "Common window over every state and case", "heading": True})
    for key, fluid in summary["windows"]["fluids"].items():
        rows.append(
            {
                "label": fluid["label"],
                "low": None if fluid["lower_kg_m3"] is None else units.from_si(fluid["lower_kg_m3"], "density", system),
                "high": None if fluid["upper_kg_m3"] is None else units.from_si(fluid["upper_kg_m3"], "density", system),
                "marker": None if fluid["chosen_kg_m3"] is None else units.from_si(fluid["chosen_kg_m3"], "density", system),
                "status": "ok" if fluid["open"] else "fail",
                "colour": "#344c63" if fluid["open"] else "#b4483a",
            }
        )
    return svg.interval_chart(
        rows,
        _axis("density", system, "Surface fluid density at 20 °C"),
        "Mud-weight window by state and case",
        width=860,
        label_width=440,
    )


def tools_figure(summary, system):
    rows = []
    for row in summary["tools"]["rows"] + summary["tools"]["coating"]["rows"]:
        peak = row.get("peak_C")
        rating = row.get("rating_C")
        rows.append(
            {
                "label": row["item"],
                "low": None if peak is None else units.from_si(peak, "temperature", system),
                "reference": None if rating is None else units.from_si(rating, "temperature", system),
                "status": "fail" if peak is not None and rating is not None and peak > rating else "ok",
            }
        )
    return svg.interval_chart(
        rows,
        _axis("temperature", system, "Peak temperature; red line: rating"),
        "Peak temperature against rating",
        width=720,
        label_width=260,
    )


def results(manifest, system="SI"):
    """The results-page payload for a completed run."""
    mode = manifest.get("mode", "circulation_state")
    payload = {
        "mode": mode,
        "units": system,
        "run_id": manifest["run_id"],
        "title": manifest.get("title"),
        "flags": evidence.run_flags(manifest, system),
        "qualifications": with_sentences(manifest.get("warnings") or []),
    }
    if mode == "site_assessment":
        summary = assessment_summary(manifest)
        payload["summary"] = summary
        payload["figures"] = {
            "window": window_figure(summary, system),
            "tools": tools_figure(summary, system),
            "temperature": temperature_figure(
                manifest, system, "Circulating temperatures of the selected programme"
            ),
        }
    elif mode == "circulation_state":
        payload["summary"] = circulation_summary(manifest)
        payload["figures"] = {"temperature": temperature_figure(manifest, system)}
    else:
        raise ValueError("Results pages cover circulation runs and assessments")
    return payload
