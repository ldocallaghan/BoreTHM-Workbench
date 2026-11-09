"""Design levers: computed assessments that change one or two inputs of an
assessed site and programme.

Each case is a complete site assessment with every other input at the base
run's value. A study is a frozen list of cases queued as ordinary runs, so
each case opens and exports on its own. Margins, widths, verdicts and
controlling constraints are read from the saved results. The only
arithmetic is the change of a returned value from the base run's value,
labelled as a difference. These are computed cases: no optimisation,
interpolation or probability is implied.
"""

import copy
import hashlib
from html import escape
import io
import itertools
import json
import tomllib
import zipfile

from .adapter import CalculationError
from .contracts import AssessmentScenario, canonical_bytes, digest, physical_hash
from .store import Conflict, TERMINAL, new_id, now
from . import units

MAX_CASES = 25
INTERPRETATION = (
    "Computed cases only. Each case is a complete assessment with one or two "
    "inputs changed and every other input at the base run's value. This is "
    "not an optimisation and gives no probability."
)

# key: label, quantity for display units (None: shown as given), kind of value
LEVERS = {
    "flow": ("Flow", None, "number"),
    "inlet_temperature": ("Pump inlet temperature", "temperature", "number"),
    "pipe": ("Pipe type", None, "pipe"),
    "insulated_length": ("Length of the top string section", "length", "number"),
    "drilling_fluid": ("Drilling fluid density at 20 °C", "density", "number"),
    "connection": ("Connection duration, pumps off (90th percentile)", "minutes", "number"),
    "stage_spacing": ("Run-in staging interval", "length", "number"),
    "stage_minutes": ("Circulation at each run-in stage", "minutes", "number"),
    "tool_limit": ("Tool temperature limit", "temperature", "number"),
}
OPERATION_FIELD = {
    "flow": "flow",
    "drilling_fluid": "drilling_fluid_kg_m3",
    "connection": "connection_p90_min",
    "stage_spacing": "stage_spacing_m",
    "stage_minutes": "stage_minutes",
    "tool_limit": "tool_limit_C",
}
METRICS = (
    ("tool_margin_C", "Tool margin", "temperature_difference"),
    ("drilling_window_width_kg_m3", "Drilling fluid window width", "density"),
    ("trip_window_width_kg_m3", "Trip fluid window width", "density"),
    ("pump_margin_Pa", "Pump pressure margin", "pressure"),
)
VERDICT_COLOURS = {"GO": "#dff1ea", "CONDITIONAL": "#fbf0d9", "NO-GO": "#f8e0dc"}


# ---------------------------------------------------------------- base values
def _site_document(scenario):
    return tomllib.loads(scenario["definition"]["site"]["content"])


def base_values(manifest):
    """Each lever's value in the base run, from the saved result and inputs,
    with whether the lever applies to that base."""
    scenario = manifest["resolved_scenario"]
    report = manifest["assessment"]
    operations = report.get("operations")
    programme = scenario["definition"].get("programme")
    document = _site_document(scenario)
    custom = programme is not None and programme.get("kind") == "custom"
    string = (programme or {}).get("string") or []

    def op(name):
        return None if operations is None else operations[name]["value"]

    values = {
        "flow": {"value": (report.get("programme") or {}).get("flow"),
                 "unit": (report.get("programme") or {}).get("flow_unit")},
        "inlet_temperature": {"value": document.get("T_inj", report["site"].get("T_inj_C"))},
        "pipe": {"value": (report.get("programme") or {}).get("pipe")},
        "insulated_length": {"value": string[0]["length_m"] if custom and len(string) > 1 else None,
                             "pipe": string[0]["pipe"]["name"] if custom and string else None},
        "drilling_fluid": {"value": op("drilling_fluid_kg_m3")},
        "connection": {"value": op("connection_p90_min")},
        "stage_spacing": {"value": op("stage_spacing_m")},
        "stage_minutes": {"value": op("stage_minutes")},
        "tool_limit": {"value": op("tool_limit_C")},
    }
    reasons = {}
    if operations is None:
        for key in OPERATION_FIELD:
            reasons[key] = "The base run was made without the operations interface."
        reasons["pipe"] = reasons["flow"]
    if not (custom and len(string) > 1):
        reasons["insulated_length"] = (
            "Needs a base run with a custom string of two or more sections; the "
            "top section's length changes and the next section takes up the difference."
        )
    for key, entry in values.items():
        label, quantity, kind = LEVERS[key]
        entry.update(key=key, label=label, quantity=quantity, kind=kind,
                     available=key not in reasons, reason=reasons.get(key))
    return values


# ---------------------------------------------------------------- planning
def _apply(scenario, lever, value):
    """A copy of an assessment scenario with one lever at a value."""
    from .sites import document_to_toml
    from .programme import model_pipes

    scenario = copy.deepcopy(scenario)
    definition = scenario["definition"]
    if lever == "insulated_length" or (lever == "pipe" and (definition.get("programme") or {}).get("kind") == "custom"):
        string = definition["programme"]["string"]
        if lever == "pipe":
            pipe = next((p for p in model_pipes() if p["name"] == value), None)
            if pipe is None:
                raise CalculationError("LEVER_INVALID", f"Unknown model pipe {value!r}")
            string[0]["pipe"].update(name=pipe["name"], layers=pipe["layers"],
                                     thermal_joint_fraction=pipe["thermal_joint_fraction"],
                                     source=pipe["source"])
        else:
            total = string[0]["length_m"] + string[1]["length_m"]
            if not 0 < value < total:
                raise CalculationError(
                    "LEVER_INVALID",
                    f"The top section's length must lie between 0 and {total:g} m, the length of the top two sections.",
                )
            string[0]["length_m"] = float(value)
            string[1]["length_m"] = total - float(value)
        return scenario
    document = tomllib.loads(definition["site"]["content"])
    if lever == "inlet_temperature":
        document["T_inj"] = float(value)
    else:
        operations = dict(document.get("operations") or {})
        if lever == "pipe":
            operations["pipes"] = [value]
        else:
            operations[OPERATION_FIELD[lever]] = float(value)
        document["operations"] = operations
    definition["site"] = {**definition["site"], "content": document_to_toml(document),
                          "source": "Design lever case"}
    return scenario


def _label_value(lever, value, system="SI"):
    _, quantity, kind = LEVERS[lever]
    if kind == "pipe" or quantity is None:
        return str(value) if kind == "pipe" else f"{value:g}"
    return units.fmt(value, quantity, system)


def plan(request, base_manifest):
    """Frozen cases for a study request on a completed base assessment."""
    from .assessment import validate_assessment

    if base_manifest.get("mode") != "site_assessment":
        raise CalculationError("LEVER_INVALID", "Design levers start from a completed site assessment")
    bases = base_values(base_manifest)
    levers = request["levers"]
    mode = request["mode"]
    if not levers:
        raise CalculationError("LEVER_INVALID", "Choose at least one lever")
    if mode == "grid" and len(levers) != 2:
        raise CalculationError("LEVER_INVALID", "A grid needs exactly two levers")
    seen = set()
    for item in levers:
        key = item["lever"]
        if key not in LEVERS:
            raise CalculationError("LEVER_INVALID", f"Unknown lever {key!r}")
        if key in seen:
            raise CalculationError("LEVER_INVALID", f"{LEVERS[key][0]} is chosen twice")
        seen.add(key)
        if not bases[key]["available"]:
            raise CalculationError("LEVER_INVALID", f"{LEVERS[key][0]}: {bases[key]['reason']}")
        if not 2 <= len(item["values"]) <= 5:
            raise CalculationError("LEVER_INVALID", f"{LEVERS[key][0]}: give 2 to 5 values")
        if len(set(map(str, item["values"]))) != len(item["values"]):
            raise CalculationError("LEVER_INVALID", f"{LEVERS[key][0]}: the values repeat")
    if mode == "one_at_a_time":
        combos = [((item["lever"], v),) for item in levers for v in item["values"]]
    elif mode == "grid":
        combos = list(itertools.product(
            [(levers[0]["lever"], v) for v in levers[0]["values"]],
            [(levers[1]["lever"], v) for v in levers[1]["values"]],
        ))
    else:
        raise CalculationError("LEVER_INVALID", "mode must be one_at_a_time or grid")
    if len(combos) > MAX_CASES:
        raise CalculationError("LEVER_INVALID", f"{len(combos)} cases exceed the limit of {MAX_CASES}")
    base_scenario = base_manifest["resolved_scenario"]
    title = request.get("title") or f"{base_scenario['title']} · design levers"
    cases = []
    for index, combo in enumerate(combos):
        raw = base_scenario
        for lever, value in combo:
            raw = _apply(raw, lever, value)
        raw = copy.deepcopy(raw)
        raw["title"] = (f"{title} · " + ", ".join(
            f"{LEVERS[k][0].lower()} {_label_value(k, v)}" for k, v in combo))[:200]
        try:
            scenario = AssessmentScenario.model_validate(raw)
            warnings = validate_assessment(scenario)
        except (ValueError, CalculationError) as error:
            raise CalculationError(
                "LEVER_INVALID", f"Case {index + 1} rejected before any run was queued: {error}"
            ) from error
        cases.append(dict(
            index=index,
            levers={k: v for k, v in combo},
            scenario=scenario.model_dump(),
            physical_request_hash=physical_hash(scenario),
            warning_count=len(warnings),
        ))
    duration = base_manifest.get("wall_clock_duration_s")
    return dict(
        schema_version="lever-study-1",
        title=title,
        mode=mode,
        base_run_id=base_manifest["run_id"],
        base_values={k: {kk: vv for kk, vv in v.items() if kk in ("value", "unit", "label", "quantity", "kind")}
                     for k, v in bases.items()},
        levers=[{"lever": i["lever"], "label": LEVERS[i["lever"]][0], "values": i["values"]} for i in levers],
        case_count=len(cases),
        cases=cases,
        estimate=dict(
            base_duration_s=duration,
            note="The base run's duration times the number of cases; cases can take longer or shorter.",
            total_s=None if duration is None else duration * len(cases),
        ),
        admission="One case at a time in the local queue; a study is admitted when the queue is idle",
        interpretation=INTERPRETATION,
    )


def submit(store, request, base_manifest, idempotency_key):
    frozen = plan(request, base_manifest)
    request_hash = digest(request)
    with store.connection() as conn:
        if idempotency_key:
            existing = conn.execute(
                "SELECT id,request_hash FROM studies WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise Conflict("Study idempotency key was already used for different input",
                                   "IDEMPOTENCY_CONFLICT")
                return existing["id"], False
        active = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE state NOT IN ('completed','failed','cancelled','timed_out')"
        ).fetchone()[0]
        if active:
            raise Conflict("Wait for the local queue to become idle before starting a study",
                           "STUDY_QUEUE_BUSY")
        store.check_storage(0, conn=conn)
        sid = new_id("study")
        stamp = now()
        frozen.update(request_hash=request_hash)
        conn.execute("INSERT INTO studies VALUES (?,?,?,?,?)",
                     (sid, stamp, request_hash, idempotency_key, canonical_bytes(frozen).decode()))
        for case in frozen["cases"]:
            scenario_id, rid = new_id("scenario"), new_id("run")
            conn.execute("INSERT INTO scenarios VALUES (?,1,?)", (scenario_id, stamp))
            conn.execute("INSERT INTO revisions VALUES (?,?,?,?,?)",
                         (scenario_id, 1, canonical_bytes(case["scenario"]).decode(),
                          case["physical_request_hash"], stamp))
            conn.execute(
                "INSERT INTO runs(id,scenario_id,revision,state,created_at,updated_at,request_hash) VALUES (?,?,1,'queued',?,?,?)",
                (rid, scenario_id, stamp, stamp,
                 digest({"scenario": case["scenario"], "scenario_id": None, "revision": None})),
            )
            conn.execute("INSERT INTO study_cases VALUES (?,?,?,?)",
                         (sid, case["index"], rid, canonical_bytes(case["levers"]).decode()))
            row = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
            store.event(conn, row, "queued")
    return sid, True


# ---------------------------------------------------------------- results
def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def outcome(manifest):
    """Verdict, controlling constraint and margins of a saved assessment.

    The tool margin is the smallest margin the model returns for the checks
    that set the tool status (drilling, connections and the staged run-in);
    the pump margin is the smaller of drilling and the trip. Both are
    selections of returned values.
    """
    report = (manifest or {}).get("assessment") or {}
    if not report:
        return None
    tool = report.get("tool_check") or {}
    tool_states = [(k, (tool.get(k) or {}).get("margin_C")) for k in ("drilling", "connection", "run_in_staged")]
    tool_states = [(k, v) for k, v in tool_states if _number(v) is not None]
    tool_limiting = min(tool_states, key=lambda x: x[1]) if tool_states else None
    pumps = report.get("pump_check") or {}
    pump_states = [(k, (pumps.get(k) or {}).get("margin_Pa")) for k in ("drilling", "trip")]
    pump_states = [(k, v) for k, v in pump_states if _number(v) is not None]
    pump_limiting = min(pump_states, key=lambda x: x[1]) if pump_states else None
    widths = report.get("window_width") or {}
    programme = report.get("programme") or {}
    return {
        "verdict": (report.get("status") or {}).get("label"),
        "controlling_constraint": (report.get("reading") or {}).get("controlling_constraint"),
        "tool_margin_C": None if tool_limiting is None else tool_limiting[1],
        "tool_margin_state": None if tool_limiting is None else tool_limiting[0],
        "drilling_window_width_kg_m3": _number((widths.get("drilling_fluid") or {}).get("width_kg_m3")),
        "trip_window_width_kg_m3": _number((widths.get("trip_fluid") or {}).get("width_kg_m3")),
        "pump_margin_Pa": None if pump_limiting is None else pump_limiting[1],
        "pump_margin_state": None if pump_limiting is None else pump_limiting[0],
        "flow": programme.get("flow"),
        "flow_unit": programme.get("flow_unit"),
        "pipe": programme.get("pipe"),
        "returned": "tool_check" in report,
    }


def _definition(conn, sid):
    row = conn.execute("SELECT * FROM studies WHERE id=?", (sid,)).fetchone()
    if row is None:
        raise KeyError(sid)
    return row, json.loads(row["definition"])


def read(store, sid):
    """A study's cases with their saved outcomes, the changes from the base
    run, and the tornado and grid built from them."""
    with store.connection(write=False) as conn:
        row, definition = _definition(conn, sid)
        members = conn.execute(
            "SELECT * FROM study_cases WHERE study_id=? ORDER BY case_index", (sid,)
        ).fetchall()
    base = store.get(definition["base_run_id"])
    base_outcome = outcome(base.get("manifest"))
    cases = []
    for member in members:
        run = store.get(member["run_id"])
        manifest = run.get("manifest") or {}
        finished = run["state"] == "completed"
        result = outcome(manifest) if finished else None
        changes = {}
        if result and base_outcome:
            for key, _, _ in METRICS:
                a, b = result.get(key), base_outcome.get(key)
                changes[key] = None if a is None or b is None else a - b
        cases.append(dict(
            index=member["case_index"],
            run_id=run["id"],
            levers=json.loads(member["coordinates"]),
            state=run["state"],
            outcome=result,
            change_from_base=changes,
            duration_s=manifest.get("wall_clock_duration_s") if finished else None,
            failure=None if finished else manifest.get("failure"),
        ))
    counts = {state: sum(c["state"] == state for c in cases) for state in sorted({c["state"] for c in cases})}
    done = [c["duration_s"] for c in cases if c["duration_s"]]
    remaining = sum(c["state"] not in TERMINAL for c in cases)
    return dict(
        schema_version="lever-study-result-1",
        id=sid,
        created_at=row["created_at"],
        title=definition["title"],
        mode=definition["mode"],
        base_run_id=definition["base_run_id"],
        base_outcome=base_outcome,
        base_values=definition["base_values"],
        levers=definition["levers"],
        state="finished" if remaining == 0 else "running",
        total_cases=len(cases),
        finished_cases=sum(c["state"] in TERMINAL for c in cases),
        state_counts=counts,
        estimate=definition["estimate"],
        remaining_estimate_s=None if not remaining else (
            (sum(done) / len(done)) * remaining if done else definition["estimate"]["total_s"]),
        cases=cases,
        tornado=tornado(definition, cases),
        grid=grid(definition, cases) if definition["mode"] == "grid" else None,
        interpretation=definition["interpretation"],
        differences="A change is a case's returned value minus the base run's returned value.",
    )


def tornado(definition, cases):
    """For each margin and lever: the smallest and largest change from the
    base run over the lever's computed values."""
    if definition["mode"] != "one_at_a_time":
        return None
    charts = {}
    for key, label, quantity in METRICS:
        rows = []
        for item in definition["levers"]:
            changes = [c["change_from_base"].get(key) for c in cases
                       if item["lever"] in c["levers"] and c["change_from_base"].get(key) is not None]
            rows.append(dict(lever=item["lever"], label=item["label"],
                             low=min(changes) if changes else None,
                             high=max(changes) if changes else None,
                             computed=len(changes)))
        charts[key] = dict(label=label, quantity=quantity, rows=rows)
    return charts


def grid(definition, cases):
    a, b = definition["levers"]
    cells = []
    for va in a["values"]:
        row = []
        for vb in b["values"]:
            case = next((c for c in cases if c["levers"].get(a["lever"]) == va and c["levers"].get(b["lever"]) == vb), None)
            row.append(None if case is None else dict(
                run_id=case["run_id"], state=case["state"],
                verdict=(case["outcome"] or {}).get("verdict"),
                controlling_constraint=(case["outcome"] or {}).get("controlling_constraint")))
        cells.append(row)
    return dict(rows=a, columns=b, cells=cells)


def list_studies(store):
    with store.connection(write=False) as conn:
        rows = conn.execute("SELECT id,created_at,definition FROM studies ORDER BY created_at DESC LIMIT 100").fetchall()
    return [dict(id=r["id"], created_at=r["created_at"], title=json.loads(r["definition"])["title"],
                 base_run_id=json.loads(r["definition"])["base_run_id"]) for r in rows]


def cancel(store, sid):
    """Request cancellation of every case that has not finished. Finished cases are kept."""
    with store.connection() as conn:
        _definition(conn, sid)
        rows = conn.execute(
            "SELECT runs.* FROM runs JOIN study_cases ON runs.id=study_cases.run_id WHERE study_id=?", (sid,)
        ).fetchall()
        for row in rows:
            # The coordinator stops a running case and closes queued ones.
            if row["state"] not in TERMINAL and row["state"] != "cancel_requested":
                conn.execute("UPDATE runs SET state='cancel_requested',updated_at=? WHERE id=?", (now(), row["id"]))
                store.event(conn, row, "cancel_requested")


# ---------------------------------------------------------------- report and export
def _metric(value, quantity, system):
    return "Not returned" if value is None else units.fmt(value, quantity, system)


def tornado_svg(chart, system):
    from . import svg

    convert = lambda v: None if v is None else units.from_si(v, chart["quantity"], system)
    rows = [
        dict(label=row["label"], low=convert(row["low"]), high=convert(row["high"]), reference=0.0,
             status="fail" if row["low"] is not None and row["low"] < 0 else "ok")
        for row in chart["rows"]
    ]
    return svg.interval_chart(rows, f"Change from the base run ({units.symbol(chart['quantity'], system)})",
                              f"{chart['label']}: change from the base run", width=720, label_width=300)


def report_html(study, system="SI"):
    """The study as one self-contained HTML page, from its saved results."""
    from .report import STYLE

    base = study["base_outcome"] or {}
    parts = [
        "<!doctype html><html lang=\"en-GB\"><head><meta charset=\"utf-8\">",
        f"<title>{escape(study['title'])}</title><style>{STYLE}</style></head><body><main>",
        f"<h1>{escape(study['title'])}</h1>",
        f"<p class=\"meta\">Design-lever study {escape(study['id'])}, created {escape(study['created_at'])}; base run {escape(study['base_run_id'])}. Units: {escape(system)}.</p>",
        f"<blockquote>{escape(study['interpretation'])}</blockquote>",
        "<h2>Base run</h2>",
        f"<p><span class=\"verdict {escape(str(base.get('verdict')))}\">{escape(str(base.get('verdict')))}</span></p>",
        f"<p>Controlling constraint, in the model's wording: {escape(str(base.get('controlling_constraint')))}</p>",
        "<table><tbody>",
    ]
    for key, entry in study["base_values"].items():
        if entry.get("value") is None:
            continue
        value = entry["value"]
        shown = (f"{value} {entry.get('unit') or ''}".strip() if entry.get("quantity") is None or entry.get("kind") == "pipe"
                 else units.fmt(value, entry["quantity"], system))
        parts.append(f"<tr><th>{escape(entry['label'])}</th><td>{escape(shown)}</td></tr>")
    parts.append("</tbody></table>")
    parts.append("<h2>Cases</h2><table><thead><tr><th>#</th><th>Levers</th><th>State</th><th>Verdict</th><th>Controlling constraint</th>"
                 + "".join(f"<th>{escape(label)}</th>" for _, label, _ in METRICS) + "</tr></thead><tbody>")
    for case in study["cases"]:
        o = case["outcome"] or {}
        levers = "; ".join(f"{escape(LEVERS[k][0])}: {escape(_label_value(k, v, system))}" for k, v in case["levers"].items())
        cells = "".join(
            f"<td class=\"n\">{escape(_metric(o.get(key), quantity, system))}"
            + (f"<br><small>change {escape(_metric(case['change_from_base'].get(key), quantity, system))}</small>" if case["change_from_base"].get(key) is not None else "")
            + "</td>"
            for key, _, quantity in METRICS)
        parts.append(f"<tr><td>{case['index'] + 1}</td><td>{levers}</td><td>{escape(case['state'])}</td>"
                     f"<td class=\"{escape(str(o.get('verdict')))}\">{escape(str(o.get('verdict') or '-'))}</td>"
                     f"<td>{escape(str(o.get('controlling_constraint') or '-'))}</td>{cells}</tr>")
    parts.append("</tbody></table>")
    parts.append(f"<p class=\"meta\">{escape(study['differences'])} The tool margin is the smallest of the drilling, connection and staged run-in margins; the pump margin is the smaller of drilling and the trip.</p>")
    if study.get("tornado"):
        parts.append("<h2>Change of each margin from the base run</h2>")
        for chart in study["tornado"].values():
            parts.append(f"<figure><figcaption><strong>{escape(chart['label'])}</strong>: change from the base run. "
                         "Each bar runs from the smallest to the largest change over the lever's computed values; "
                         f"red where a change is negative.</figcaption>{tornado_svg(chart, system)}</figure>")
    if study.get("grid"):
        g = study["grid"]
        parts.append(f"<h2>{escape(g['rows']['label'])} against {escape(g['columns']['label'])}</h2><table><thead><tr><th></th>")
        parts.extend(f"<th>{escape(_label_value(g['columns']['lever'], v, system))}</th>" for v in g["columns"]["values"])
        parts.append("</tr></thead><tbody>")
        for value, row in zip(g["rows"]["values"], g["cells"]):
            parts.append(f"<tr><th>{escape(_label_value(g['rows']['lever'], value, system))}</th>")
            for cell in row:
                verdict = (cell or {}).get("verdict")
                colour = VERDICT_COLOURS.get(verdict, "#eef1f2")
                text = escape(str(verdict or (cell or {}).get("state") or "-"))
                constraint = escape(str((cell or {}).get("controlling_constraint") or ""))
                parts.append(f"<td style=\"background:{colour}\"><strong>{text}</strong><br><small>{constraint}</small></td>")
            parts.append("</tr>")
        parts.append("</tbody></table>")
    parts.append("</main></body></html>")
    return "".join(parts)


def export(store, sid, system="SI"):
    from .bundles import export_bundle

    study = read(store, sid)
    if study["state"] != "finished":
        raise Conflict("Wait for every case to finish, or cancel the rest, before exporting a study")
    with store.connection(write=False) as conn:
        _, definition = _definition(conn, sid)
    files = {
        "study.json": canonical_bytes(study),
        "frozen-plan.json": canonical_bytes(definition),
        "report.html": report_html(study, system).encode("utf-8"),
    }
    for case in study["cases"]:
        if case["state"] == "completed":
            files[f"case-{case['index'] + 1}.zip"] = export_bundle(store, case["run_id"])
    files["README.md"] = (
        b"Design-lever study: computed cases, each a complete assessment saved as its own run. "
        b"Cancelled or failed cases stay in study.json without results. Each case ZIP is a single-run "
        b"archive that the application imports. SHA256SUMS.json establishes internal consistency, "
        b"not publisher authenticity.\n"
    )
    files["SHA256SUMS.json"] = canonical_bytes({k: hashlib.sha256(v).hexdigest() for k, v in files.items()})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[name])
    return buffer.getvalue()
