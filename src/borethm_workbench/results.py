"""Results pages for completed runs, built only from the saved run.

Values are SI as the model returned them, shown through a unit conversion.
Window widths, margins, pass flags, the pump limit and the window against
depth are the model's own result keys. A result from a BoreTHM without them
shows the bounds and peaks it does return and states that the rest is not
returned. Figures are inline SVG in the requested display units.
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
STATE_COLOURS = {"drilling": "#d0703c", "connection": "#18807a", "fresh rock": "#7a5ea8", "trip": "#3d6fb0"}
FLUID_LABELS = {"drilling_fluid": "Drilling fluid", "trip_fluid": "Trip fluid"}
NOT_RETURNED = "Not returned by the installed BoreTHM"


def _number(value):
    """A returned number, or None for a missing or non-finite value ("inf")."""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _bound(by):
    if not by:
        return None
    return {
        "state": by.get("state"),
        "case": by.get("case"),
        "md_m": by.get("md"),
    }


def _window_profile(report):
    profile = report.get("window_profile")
    if not profile:
        return None
    common = {}
    for key in ("drilling_fluid", "trip_fluid"):
        p = profile.get(key) or {}
        common[key] = {
            "label": FLUID_LABELS[key],
            "lower_kg_m3": [_number(v) for v in p.get("lo_kg_m3", [])],
            "upper_kg_m3": [_number(v) for v in p.get("hi_kg_m3", [])],
            "width_kg_m3": [_number(v) for v in p.get("width_kg_m3", [])],
            "lower_by": p.get("lo_by", []),
            "upper_by": p.get("hi_by", []),
        }
    states = []
    for state, cases in (profile.get("states") or {}).items():
        for case, values in cases.items():
            states.append({
                "state": state,
                "case": case,
                "lower_kg_m3": [_number(v) for v in values.get("lo_kg_m3", [])],
                "lower_mechanism": values.get("lo_mechanism", []),
                "upper_kg_m3": [_number(v) for v in values.get("hi_kg_m3", [])],
                "upper_mechanism": values.get("hi_mechanism", []),
            })
    return {"depths_m": profile.get("depths_m", []), "common": common, "states": states}


def _stated(manifest, report):
    """The operating choices the user stated, from the result's record."""
    from .programme import FIELDS

    labels = {name: label for name, _, label in FIELDS}
    quantities = {name: q for name, q, _ in FIELDS if q in units.QUANTITIES}
    record = report.get("operations")
    programme = (manifest.get("resolved_scenario") or {}).get("definition", {}).get("programme")
    constraint = (report.get("reading") or {}).get("controlling_constraint") or ""
    marker = "Stated operations used in this check:"
    return {
        "returned": record is not None,
        "items": [
            {
                "name": name,
                "label": labels.get(name, name),
                "quantity": quantities.get(name),
                "value": entry.get("value"),
                "unit": entry.get("unit"),
                "basis": entry.get("basis"),
                "source": entry.get("source"),
            }
            for name, entry in (record or {}).items()
            if entry.get("stated")
        ],
        "programme": None if programme is None else programme.get("kind"),
        "sets_controlling_constraint": marker in constraint,
        "controlling_sentence": constraint[constraint.index(marker):].strip() if marker in constraint else None,
    }


def assessment_summary(manifest):
    report = manifest["assessment"]
    status = report["status"]
    windows = report.get("windows") or {}
    widths = report.get("window_width")
    fluids = {}
    for key in ("drilling_fluid", "trip_fluid"):
        window = windows.get(key) or {}
        fluids[key] = {
            "label": FLUID_LABELS[key],
            "open": window.get("open"),
            "lower_kg_m3": _number(window.get("lo")),
            "upper_kg_m3": _number(window.get("hi")),
            "upper_unbounded": window.get("hi") == "inf",
            "width_kg_m3": None if widths is None else _number((widths.get(key) or {}).get("width_kg_m3")),
            "lower_by": _bound(window.get("lo_by")),
            "upper_by": _bound(window.get("hi_by")),
            "chosen_kg_m3": _number(window.get("fluid")),
            "note": window.get("note"),
        }
    by_case = []
    for state, cases in (windows.get("states") or {}).items():
        for case, values in cases.items():
            by_case.append(
                {
                    "state": state,
                    "case": case,
                    "lower_kg_m3": _number(values.get("lo")),
                    "lower_md_m": values.get("lo_md"),
                    "upper_kg_m3": _number(values.get("hi")),
                    "upper_md_m": values.get("hi_md"),
                }
            )
    tools = report.get("tools") or {}
    check = report.get("tool_check")
    tool_rows = []

    def tool(label, state, peak, ok, detail=None, limit=None):
        entry = (check or {}).get(state) if state else None
        tool_rows.append(
            {
                "item": label,
                "state": state,
                "peak_C": _number(peak),
                "rating_C": _number(limit if limit is not None else (check or {}).get("limit_C", tools.get("limit"))),
                "margin_C": None if entry is None else _number(entry.get("margin_C")),
                "ok": ok if entry is None else entry.get("ok"),
                "detail": detail,
            }
        )

    if "drilling" in tools:
        tool("Tool while drilling", "drilling", tools["drilling"].get("T"), tools["drilling"].get("ok"))
    if "connection" in tools:
        tool("Tool during connections", "connection", tools["connection"].get("peak"), tools["connection"].get("ok"))
    trip = tools.get("trip_in") or {}
    if trip.get("staged"):
        schedule = trip.get("schedule") or {}
        tool(
            "Tool on run-in with staging",
            "run_in_staged",
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
    if trip.get("unstaged"):
        tool(
            "Tool on run-in without staging",
            "run_in_unstaged",
            trip["unstaged"].get("peak"),
            None,
            {"md_m": trip["unstaged"].get("md")},
        )
    for state, label in (("connection_schedule", "Tool during connections, schedule found"),
                         ("run_in_schedule", "Tool on run-in, schedule found")):
        entry = (check or {}).get(state)
        practice = (check or {}).get("connection" if state == "connection_schedule" else "run_in_staged") or {}
        if entry and entry.get("peak_C") != practice.get("peak_C"):
            tool(label, state, entry.get("peak_C"), entry.get("ok"))
    coating_rows = []
    coating = tools.get("coating") or {}
    coating_check = report.get("coating_check")
    if coating:
        coating_rating = coating.get("rating")
        for label, state, peak, minutes in (
            ("Coating while circulating", "circulating", coating.get("circulating_max"), None),
            (
                "Coating during connections",
                "connection",
                (coating.get("connection") or {}).get("max"),
                (coating.get("connection") or {}).get("minutes_above"),
            ),
            (
                "Coating on run-in",
                "run_in",
                (coating.get("trip_in") or {}).get("max"),
                (coating.get("trip_in") or {}).get("minutes_above"),
            ),
        ):
            entry = (coating_check or {}).get(state) or {}
            coating_rows.append(
                {
                    "item": label,
                    "state": state,
                    "peak_C": _number(peak),
                    "rating_C": _number(coating_rating),
                    "margin_C": _number(entry.get("margin_C")),
                    "ok": entry.get("ok"),
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
    pump_check = report.get("pump_check")
    programme = report.get("programme") or {}

    def pumping(key):
        entry = (pump_check or {}).get(key) or {}
        gpm = lambda v: None if _number(v) is None else units.to_si(v, "volume_flow", "oilfield")
        return {
            "standpipe_Pa": _number(entry.get("spp_Pa")),
            "margin_Pa": _number(entry.get("margin_Pa")),
            "ok": entry.get("ok"),
            "flow_gpm": _number(entry.get("flow_gpm")),
            "rig_capacity_gpm": _number(entry.get("rig_capacity_gpm")),
            "flow_m3_s": gpm(entry.get("flow_gpm")),
            "rig_capacity_m3_s": gpm(entry.get("rig_capacity_gpm")),
            "within_rig": entry.get("within_rig"),
        }

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
            "rop_m_h": programme.get("rop_m_h"),
        },
        "windows": {
            "fluids": fluids,
            "by_case": by_case,
            "evaluated_depths_m": windows.get("depths") or [],
            "profiles_returned": report.get("window_profile") is not None,
            "widths_returned": widths is not None,
            "profile": _window_profile(report),
        },
        "tools": {
            "rating_C": _number((check or {}).get("limit_C", tools.get("limit"))),
            "status": tools.get("tool"),
            "rows": tool_rows,
            "margins_returned": check is not None,
            "coating": {
                "pipe": coating.get("pipe"),
                "status": coating.get("status"),
                "rows": coating_rows,
                "margins_returned": coating_check is not None,
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
            "pump_limit_Pa": None if pump_check is None else _number(pump_check.get("limit_Pa")),
            "pumps": None if pump_check is None else pump_check.get("pumps"),
            "volume_flow_gpm": pumps.get("gpm"),
            "drilling": pumping("drilling") if pump_check else None,
            "trip": (pumping("trip") | {"separate_circulation": (pump_check.get("trip") or {}).get("separate_circulation")}) if pump_check else None,
            "returned": pump_check is not None,
        },
        "stated": _stated(manifest, report),
        "input_warnings": report.get("input_warnings") or [],
        "not_returned": NOT_RETURNED,
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
                "status": "ok",
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


def window_depth_figure(summary, system):
    """The model's window bounds against depth: each state and case (thin),
    the common windows (bold) and the fluids chosen (dashed)."""
    profile = summary["windows"]["profile"]
    depth = [units.from_si(d, "length", system) for d in profile["depths_m"]]
    d = lambda values: [None if v is None else units.from_si(v, "density", system) for v in values]
    def mechanisms(values, names):
        """The mechanism over each run of depths, such as 'breakout width 1,815 to 3,000 m'."""
        runs = []
        for md, value, name in zip(profile["depths_m"], values, names):
            if value is None or name is None:
                continue
            if runs and runs[-1][0] == name:
                runs[-1][2] = md
            else:
                runs.append([name, md, md])
        return "; ".join(
            f"{name} {units.fmt(min(a, b), 'length', system)} to {units.fmt(max(a, b), 'length', system)}"
            for name, a, b in runs)

    series, shown = [], set()
    for item in profile["states"]:
        state = item["state"]
        colour = STATE_COLOURS.get(state, "#8c979e")
        for key, mech, kind in (("lower_kg_m3", "lower_mechanism", "lower bound"), ("upper_kg_m3", "upper_mechanism", "upper bound")):
            series.append({"name": f"{STATE_LABELS.get(state, state)}, each case", "x": d(item[key]), "y": depth,
                           "colour": colour, "width": 0.8, "legend": state not in shown,
                           "title": f"{STATE_LABELS.get(state, state)}, {item['case']}, {kind}: {mechanisms(item[key], item[mech])}"})
            shown.add(state)
    for key, colour in (("drilling_fluid", "#25313b"), ("trip_fluid", "#0f5a4f")):
        common = profile["common"][key]
        label = FLUID_LABELS[key]
        series.append({"name": f"{label}: common lower bound", "x": d(common["lower_kg_m3"]), "y": depth, "colour": colour, "width": 2.5})
        series.append({"name": f"{label}: common upper bound", "x": d(common["upper_kg_m3"]), "y": depth, "colour": colour, "width": 2.5, "dash": "8 3"})
        chosen = summary["windows"]["fluids"][key]["chosen_kg_m3"]
        if chosen is not None:
            series.append({"name": f"{label} chosen", "x": [units.from_si(chosen, "density", system)] * 2,
                           "y": [depth[0], depth[-1]], "colour": "#b4483a" if key == "drilling_fluid" else "#c48a1d", "dash": "3 3", "width": 1.5})
    def tip(kind, value, md, state, case, mechanism):
        return (f"{kind} {units.fmt(value, 'density', system)} at {units.fmt(md, 'length', system)}: "
                f"{STATE_LABELS.get(state, state)}, {case}, {mechanism}")

    points = []
    for key in ("drilling_fluid", "trip_fluid"):
        common = profile["common"][key]
        for values, by, kind in ((common["lower_kg_m3"], common["lower_by"], "lower bound"),
                                 (common["upper_kg_m3"], common["upper_by"], "upper bound")):
            points.append({"name": "Common bounds, hover for the state, case and mechanism", "x": d(values), "y": depth,
                           "titles": [None if v is None or not b else tip(f"{FLUID_LABELS[key]} common {kind}", v, md, b["state"], b["case"], b["mechanism"])
                                      for v, b, md in zip(values, by, profile["depths_m"])],
                           "colour": "#25313b", "radius": 2.4, "legend": key == "drilling_fluid" and kind == "lower bound"})
    return svg.depth_chart(
        series,
        _axis("density", system, "Surface fluid density at 20 °C"),
        _axis("length", system, "Measured depth"),
        "Mud-weight window against depth",
        points=points,
        width=760,
        height=560,
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
                "status": "fail" if row.get("ok") is False else "ok",
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
            "window_depth": window_depth_figure(summary, system) if summary["windows"]["profile"] else None,
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
