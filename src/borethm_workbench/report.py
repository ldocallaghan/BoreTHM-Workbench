"""Self-contained HTML report for a completed circulation run or assessment.

The report is generated from the saved run record alone: its manifest and
creation time. It contains no generation time, so the same run and unit
choice give the same bytes. It uses no PDF library; the page prints to A4
or Letter from a browser.
"""

from html import escape

from . import results as results_page
from . import units

STYLE = """
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:#fff;color:#25313b;font:13px/1.45 "Segoe UI",Helvetica,Arial,sans-serif}
main{max-width:190mm;margin:0 auto;padding:12mm 0}
h1{font-size:21px;margin:0 0 4px}
h2{font-size:15px;margin:22px 0 8px;padding-bottom:4px;border-bottom:1px solid #cfd8dc}
h3{font-size:13px;margin:14px 0 6px}
p{margin:4px 0}
.meta{color:#5b6873}
.verdict{display:inline-block;padding:6px 14px;border-radius:4px;font-weight:700;font-size:17px;margin:6px 0}
.GO{background:#dff1ea;color:#155c43}.CONDITIONAL{background:#fbf0d9;color:#7a5408}.NO-GO{background:#f8e0dc;color:#8b2a1d}
table{border-collapse:collapse;width:100%;margin:6px 0 10px}
th,td{border:1px solid #d5dde1;padding:4px 6px;text-align:left;vertical-align:top}
th{background:#f2f5f6;font-weight:600}
td.n{text-align:right;font-variant-numeric:tabular-nums}
ul{margin:4px 0 8px 18px;padding:0}
figure{margin:8px 0 14px;break-inside:avoid;page-break-inside:avoid}
figcaption{color:#5b6873;font-size:12px}
section{break-inside:auto}
code{font-family:Consolas,Menlo,monospace;font-size:12px;word-break:break-all}
blockquote{margin:4px 0 8px;padding:4px 10px;border-left:3px solid #cfd8dc;color:#3d4a55}
@page{margin:14mm}
@media print{main{padding:0;max-width:none}h2{break-after:avoid;page-break-after:avoid}tr{break-inside:avoid}}
"""


def _t(value):
    return escape("" if value is None else str(value))


def _fmt(value, quantity, system, places=None):
    if value is None:
        return "not returned"
    return units.fmt(value, quantity, system, places)


def _rows(pairs):
    return "".join(f"<tr><th>{_t(k)}</th><td>{v}</td></tr>" for k, v in pairs)


def _list(items):
    if not items:
        return "<p>None.</p>"
    return "<ul>" + "".join(f"<li>{_t(item)}</li>" for item in items) + "</ul>"


def _circulation_inputs(manifest, system):
    d = manifest["resolved_scenario"]["definition"]
    op, well = d["operating"], d["well"]
    flow = op["flow"]
    if flow["kind"] == "mass":
        flow_text = _fmt(flow["mass_rate_kg_s"], "mass_flow", system)
    else:
        flow_text = _fmt(flow["volume_rate_m3_s"], "volume_flow", system)
    fluid = {
        "iapws_water": "Water (IAPWS-95 properties)",
        "barite_mud": "Barite-weighted mud",
        "constant_properties": "Constant-property fluid",
    }.get(d["fluid"]["kind"], d["fluid"]["kind"])
    if d["fluid"]["kind"] == "barite_mud":
        fluid += ", " + _fmt(d["fluid"]["surface_density_kg_m3"], "density", system) + " at surface"
    geotherm = d["formation"]["geotherm"]
    points = ", ".join(
        f"{_fmt(z, 'length', system)}: {_fmt(t, 'temperature', system)}"
        for z, t in geotherm["points_tvd_m_temperature_C"]
    )
    pairs = [
        ("Bit depth (measured)", _t(_fmt(well["bit_md_m"], "length", system))),
        ("Survey", _t({"vertical": "Vertical", "stations": "Station survey"}.get(well["survey"]["kind"], "Measured and vertical depth table"))),
        ("String", _t("; ".join(f"{s['id']}: {s['pipe']['name'] or 'pipe'}, {_fmt(s['length_m'], 'length', system)}" for s in well["string"]))),
        ("Hole", _t("; ".join(f"{h['id']}: {_fmt(2 * h['r_wall_m'], 'diameter', system)} to {_fmt(h['md_bottom_m'], 'length', system)}" for h in well["hole"]))),
        ("Flow", _t(flow_text)),
        ("Inlet temperature", _t(_fmt(op["inlet_temperature_C"], "temperature", system))),
        ("Wellhead pressure (absolute)", _t(_fmt(op["surface_pressure"]["pressure_absolute_Pa"], "pressure", system, 3 if system == "SI" else 0))),
        ("Fluid", _t(fluid)),
        ("Formation temperature points (vertical depth)", _t(points)),
        ("Gradient below the last point", _t(_fmt(geotherm["gradient_below_K_m"], "temperature_gradient", system))),
        ("Rock conductivity", _t(_fmt(d["formation"]["conductivity_W_m_K"], "conductivity", system))),
        ("Friction heating", "Included" if d["heat"]["friction_heating"] else "Excluded"),
        ("Physics", _t(manifest.get("physics"))),
    ]
    return "<table>" + _rows(pairs) + "</table>"


def _assessment_inputs(summary, manifest, system):
    site = summary["site"]
    programme = summary["programme"]
    pairs = [
        ("Site", _t(site.get("name"))),
        ("Target depth", _t(_fmt(site.get("target_depth_m"), "length", system))),
        ("Target temperature", _t(_fmt(site.get("target_T_C"), "temperature", system))),
        ("Pump inlet temperature", _t(_fmt(site.get("T_inj_C"), "temperature", system))),
        ("Rock conductivity", _t(_fmt(site.get("k_rock"), "conductivity", system))),
        ("Friction coefficient", _t(site.get("mu"))),
        ("Evidence tier", _t(site.get("tier"))),
        ("Stress cases", _t(len(site.get("stress_cases") or []))),
        ("Strength cases", _t(len(site.get("strength_cases") or []))),
        ("Pipe", _t(programme.get("pipe"))),
        ("Flow", _t(f"{programme.get('flow'):g} {programme.get('flow_unit')}" if programme.get("flow") is not None else None)),
        ("Mass flow", _t(_fmt(programme.get("mass_flow_kg_s"), "mass_flow", system))),
        ("Physics", _t(manifest.get("physics"))),
    ]
    basis = site.get("data_basis") or {}
    table = "".join(
        f"<tr><td>{_t(k)}</td><td>{_t(v.get('basis'))}</td><td>{_t(v.get('source'))}</td></tr>"
        for k, v in basis.items()
    )
    return (
        "<table>"
        + _rows(pairs)
        + "</table><h3>Data basis</h3><table><tr><th>Input</th><th>Basis</th><th>Source</th></tr>"
        + table
        + "</table>"
    )


def _verdict(summary, system):
    verdict = summary["verdict"]
    status = verdict["status"] or "NOT EVALUATED"
    html = [
        f'<p><span class="verdict {escape(status)}">{escape(status)}</span></p>',
        f"<p><strong>Controlling constraint, in the model's wording:</strong> {_t(verdict['controlling_constraint'])}</p>",
    ]
    if verdict["reasons"]:
        html.append("<p><strong>Reasons given by the evaluator:</strong></p>" + _list(verdict["reasons"]))
    reading = verdict.get("reading") or {}
    lines = [reading[k] for k in ("calculation", "scenarios_at_target", "whole_programme", "equipment") if reading.get(k)]
    if lines:
        html.append("<p><strong>The model's summary, in its own wording and units:</strong></p>")
        html.append("<blockquote>" + "".join(f"<p>{_t(line)}</p>" for line in lines) + "</blockquote>")
    return "".join(html)


def _windows(summary, system):
    rows = []
    for fluid in summary["windows"]["fluids"].values():
        def by(bound):
            if not bound:
                return "not returned"
            depth = "no binding depth returned" if bound.get("md_m") is None else _fmt(bound["md_m"], "length", system)
            return f"{bound.get('state')}, {bound.get('case')}, {depth}"

        rows.append(
            "<tr>"
            f"<td>{_t(fluid['label'])}</td>"
            f"<td>{'Open' if fluid['open'] else 'Closed'}</td>"
            f"<td class=n>{_t(_fmt(fluid['lower_kg_m3'], 'density', system))}<br>{_t(_fmt(fluid['lower_kg_m3'], 'specific_gravity', system))}</td>"
            f"<td class=n>{_t(_fmt(fluid['upper_kg_m3'], 'density', system))}<br>{_t(_fmt(fluid['upper_kg_m3'], 'specific_gravity', system))}</td>"
            f"<td class=n>{_t(_fmt(fluid['width_kg_m3'], 'density', system) if fluid['width_kg_m3'] is not None else ('no upper bound' if fluid.get('upper_unbounded') else results_page.NOT_RETURNED))}</td>"
            f"<td>{_t(by(fluid['lower_by']))}</td><td>{_t(by(fluid['upper_by']))}</td>"
            f"<td class=n>{_t(_fmt(fluid['chosen_kg_m3'], 'density', system))}</td>"
            "</tr>"
        )
    return (
        "<table><tr><th>Fluid</th><th>Window</th><th>Lower bound</th><th>Upper bound</th>"
        "<th>Width</th><th>Lower bound set by</th><th>Upper bound set by</th><th>Fluid chosen</th></tr>"
        + "".join(rows)
        + "</table><p class=meta>Surface densities at 20 °C. "
        + ("Widths and the bounds against depth are the model's results."
           if summary["windows"]["profiles_returned"] else
           "The installed BoreTHM returns each bound and its binding depth; it does not return the widths or the bounds against depth.")
        + "</p>"
    )


def _window_table(summary, system):
    profile = summary["windows"]["profile"]
    if not profile:
        return ""

    def by(entry):
        return "-" if not entry else f"{entry.get('state')}, {entry.get('case')}: {entry.get('mechanism')}"

    rows = []
    for i, depth in enumerate(profile["depths_m"]):
        cells = [f"<td class=n>{_t(_fmt(depth, 'length', system))}</td>"]
        for key in ("drilling_fluid", "trip_fluid"):
            c = profile["common"][key]
            cells.append(f"<td class=n>{_t(_fmt(c['lower_kg_m3'][i], 'density', system))}</td><td>{_t(by(c['lower_by'][i]))}</td>"
                         f"<td class=n>{_t(_fmt(c['upper_kg_m3'][i], 'density', system))}</td><td>{_t(by(c['upper_by'][i]))}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        "<h3>Common windows at each evaluated depth</h3><table><tr><th>Measured depth</th>"
        "<th>Drilling fluid lower</th><th>Set by</th><th>Drilling fluid upper</th><th>Set by</th>"
        "<th>Trip fluid lower</th><th>Set by</th><th>Trip fluid upper</th><th>Set by</th></tr>"
        + "".join(rows) + "</table><p class=meta>A lower bound below water's density means that water keeps the breakout within its limit at that depth.</p>"
    )


def _tools(summary, system):
    rows = []
    for row in summary["tools"]["rows"] + summary["tools"]["coating"]["rows"]:
        ok = row.get("ok")
        note = " <small>(for comparison; does not set the status)</small>" if row.get("sets_status") is False else ""
        rows.append(
            "<tr>"
            f"<td>{_t(row['item'])}{note}</td>"
            f"<td class=n>{_t(_fmt(row['peak_C'], 'temperature', system))}</td>"
            f"<td class=n>{_t(_fmt(row['rating_C'], 'temperature', system))}</td>"
            f"<td class=n>{_t(_fmt(row['margin_C'], 'temperature_difference', system))}</td>"
            f"<td>{'' if ok is None else ('Pass' if ok else 'Fail')}</td>"
            "</tr>"
        )
    coating = summary["tools"]["coating"]
    return (
        f"<p>Tool check: {_t(summary['tools']['status'])}. Coating ({_t(coating['pipe'])}): {_t(coating['status'])}.</p>"
        "<table><tr><th>Item</th><th>Peak</th><th>Rating</th><th>Margin</th><th>Check</th></tr>"
        + "".join(rows)
        + "</table><p class=meta>"
        + ("Limits, margins and pass flags are the model's results. Rows the model marks for comparison do not set the tool or coating status."
           if summary["tools"]["margins_returned"] else
           "The installed BoreTHM does not return margins or a pass flag for the unstaged run-in.")
        + "</p>"
    )


def _stability(summary):
    states = [s for s in results_page.STATES]
    header = "".join(f"<th>{_t(results_page.STATE_LABELS[s])}</th>" for s in states)
    rows = "".join(
        f"<tr><td>{_t(case['case'])}</td>"
        + "".join(f"<td>{_t(case['states'].get(s, '-'))}</td>" for s in states)
        + f"<td><strong>{_t(case['verdict'])}</strong></td></tr>"
        for case in summary["stability"]["cases"]
    )
    return (
        f"<table><tr><th>Stress and strength case</th>{header}<th>Overall</th></tr>{rows}</table>"
        f"<p>Deciding case: {_t(summary['stability']['deciding_case'])}.</p>"
    )


def _pressure(summary, system):
    pressure = summary["pressure"]
    if pressure.get("returned"):
        rows = []
        for key, label in (("drilling", "Drilling"), ("trip", "Trip")):
            p = pressure[key]
            rows.append(
                f"<tr><td>{label}</td><td class=n>{_t(_fmt(p['standpipe_Pa'], 'pressure', system))}</td>"
                f"<td class=n>{_t(_fmt(pressure['pump_limit_Pa'], 'pressure', system))}</td>"
                f"<td class=n>{_t(_fmt(p['margin_Pa'], 'pressure', system))}</td>"
                f"<td>{'Pass' if p['ok'] else 'Fail'}</td><td>{'Yes' if p['within_rig'] else 'No'}</td></tr>"
            )
        return (
            f"<p>Pump pressure limit {_t(_fmt(pressure['pump_limit_Pa'], 'pressure', system))}; {_t(pressure['pumps'])} rig pumps.</p>"
            "<table><tr><th>State</th><th>Standpipe pressure</th><th>Limit</th><th>Margin</th><th>Check</th><th>Within the rig's pumps</th></tr>"
            + "".join(rows) + "</table>"
        )
    within = pressure.get("within_rig_pumps")
    pairs = [
        ("Standpipe pressure (drilling)", _t(_fmt(pressure.get("standpipe_Pa"), "pressure", system))),
        ("Standpipe pressure (trip)", _t(_fmt(pressure.get("trip_standpipe_Pa"), "pressure", system))),
        ("Within the rig's pumps", "not returned" if within is None else ("Yes" if within else "No")),
        ("Pump pressure limit", results_page.NOT_RETURNED),
    ]
    return "<table>" + _rows(pairs) + "</table>"


def _stated(summary, system):
    stated = summary["stated"]
    if not stated["returned"]:
        return "<p>The installed BoreTHM chose every operating value; it does not accept stated ones.</p>"
    programme = {"reference": "the model's reference programme with stated options",
                 "custom": "a stated custom string"}.get(stated["programme"], "the site file's programme, or the model's reference programme")
    html = [f"<p>Hydraulic programme: {_t(programme)}.</p>"]
    if stated["items"]:
        rows = "".join(
            f"<tr><td>{_t(i['label'])}</td><td>{_t(_value(i, system))}</td><td>{_t(i.get('basis'))}</td><td>{_t(i.get('source'))}</td></tr>"
            for i in stated["items"]
        )
        html.append("<table><tr><th>Operating choice</th><th>Stated value</th><th>Basis</th><th>Source</th></tr>" + rows + "</table>")
    else:
        html.append("<p>No operating value is stated; the model chose each one.</p>")
    html.append(
        f"<p>In the model's wording: {_t(stated['controlling_sentence'])}</p>" if stated["sets_controlling_constraint"]
        else "<p>No stated value is used in the check behind the controlling constraint.</p>"
    )
    return "".join(html)


def _value(item, system):
    value, unit, quantity = item["value"], item.get("unit"), item.get("quantity")
    if quantity and isinstance(value, (int, float)):
        return _fmt(value, quantity, system)
    if quantity and isinstance(value, list):
        return ", ".join(_fmt(v, quantity, system) for v in value)
    if quantity and isinstance(value, dict):
        return "; ".join(f"{k} {_fmt(v, quantity, system)}" for k, v in value.items()) or "no pipe rated"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) + (f" {unit}" if unit else "")
    if isinstance(value, dict):
        return "; ".join(f"{k} {v} {unit or ''}".strip() for k, v in value.items()) or "none"
    return f"{value} {unit or ''}".strip()


def _circulation_figures(summary, system):
    pairs = [
        ("Fluid at the bit", _t(_fmt(summary["bit_bore_C"], "temperature", system))),
        ("Annulus temperature above the bit", _t(_fmt(summary["bit_annulus_C"], "temperature", system))),
        ("Surface return temperature", _t(_fmt(summary["return_C"], "temperature", system))),
        ("Standpipe pressure", _t(_fmt(summary["standpipe_Pa"], "pressure", system))),
        ("Mass flow", _t(_fmt(summary["mass_flow_kg_s"], "mass_flow", system))),
    ]
    return "<table>" + _rows(pairs) + "</table>"


def _flags(flags):
    if flags is None:
        return "<p>Not applicable.</p>"
    outside = [f["message"] for f in flags["outside"]]
    html = "<h3>Inputs outside the field-tested range</h3>" + (
        _list(outside) if outside else "<p>All compared inputs are inside the field-tested range.</p>"
    )
    html += "<h3>Notes on the field comparisons</h3>" + _list([n["message"] for n in flags["notes"]])
    return html


def _qualifications(items):
    if not items:
        return "<p>None.</p>"
    return (
        "<table><tr><th>Qualification</th><th>Code</th></tr>"
        + "".join(
            f"<tr><td>{_t(w.get('plain') or w.get('message'))}</td><td class=meta><code>{_t(w.get('code'))}</code></td></tr>"
            for w in items
        )
        + "</table>"
    )


def report_html(record, system="SI"):
    manifest = record["manifest"]
    page = results_page.results(manifest, system)
    mode = page["mode"]
    created = str(record.get("created_at") or "")
    date = created[:10] if created else "not recorded"
    build = manifest.get("build") or {}
    title = (
        page["summary"]["site"].get("name") if mode == "site_assessment" else None
    ) or manifest.get("title") or "BoreTHM run"
    kind = "Site assessment" if mode == "site_assessment" else "Circulation run"
    parts = [
        "<!doctype html>",
        '<html lang="en-GB"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(kind)}: {escape(title)}</title>",
        f"<style>{STYLE}</style></head><body><main>",
        f"<h1>{escape(title)}</h1>",
        f"<p class=meta>{escape(kind)} · run date {escape(date)} · units: {escape('SI' if system == 'SI' else 'oilfield')}</p>",
    ]
    if mode == "site_assessment":
        summary = page["summary"]
        parts += [
            "<h2>Verdict</h2>",
            _verdict(summary, system),
            "<h2>Inputs</h2>",
            _assessment_inputs(summary, manifest, system),
            "<h2>Mud-weight window</h2>",
            _windows(summary, system),
            (f"<figure>{page['figures']['window_depth']}<figcaption>Bounds against depth for each state and case (thin), the common windows (bold) and the fluids chosen (dashed).</figcaption></figure>"
             if page["figures"].get("window_depth") else
             f"<figure>{page['figures']['window']}<figcaption>Bounds for each state and stress and strength case. Dark marker: fluid chosen.</figcaption></figure>"),
            _window_table(summary, system),
            "<h2>Tools and equipment</h2>",
            _tools(summary, system),
            f"<figure>{page['figures']['tools']}</figure>",
            "<h2>Stability at the target</h2>",
            _stability(summary),
            "<h2>Pumps</h2>",
            _pressure(summary, system),
            "<h2>Stated values</h2>",
            _stated(summary, system),
            "<h2>Circulating temperatures</h2>",
            f"<figure>{page['figures']['temperature']}</figure>",
            "<h2>Input warnings</h2>",
            _list(summary["input_warnings"]),
        ]
    else:
        parts += [
            "<h2>Inputs</h2>",
            _circulation_inputs(manifest, system),
            "<h2>Results</h2>",
            _circulation_figures(page["summary"], system),
            f"<figure>{page['figures']['temperature']}</figure>",
        ]
    parts += [
        "<h2>Field-tested range</h2>",
        _flags(page["flags"]),
        "<h2>Qualifications</h2>",
        _qualifications(page["qualifications"]),
        "<h2>Provenance</h2>",
        "<table>"
        + _rows(
            [
                ("BoreTHM Workbench version", _t(build.get("app_version"))),
                ("BoreTHM version", _t(build.get("core_version"))),
                ("Physics", _t(manifest.get("physics"))),
                ("Run ID", f"<code>{_t(manifest.get('run_id'))}</code>"),
                ("Input hash (SHA-256)", f"<code>{_t(manifest.get('physical_request_hash'))}</code>"),
            ]
        )
        + "</table>",
        "<p class=meta>Generated by BoreTHM Workbench from the saved run. Values are model results or unit conversions of them.</p>",
        "</main></body></html>",
    ]
    return ("\n".join(parts) + "\n").encode("utf-8")
