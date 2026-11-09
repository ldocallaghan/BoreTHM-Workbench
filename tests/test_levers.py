"""Design levers: case planning, the queue, cancellation, and the table,
tornado and grid built from saved results only."""

import copy
import io
import json
from pathlib import Path
import tomllib
import zipfile

import pytest

from borethm_workbench import levers as L
from borethm_workbench.adapter import CalculationError
from borethm_workbench.contracts import canonical_bytes, parse_scenario
from borethm_workbench.programme import interface_available
from borethm_workbench.store import Store, new_id, now

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/results"
pytestmark = pytest.mark.skipif(not interface_available(), reason="needs a BoreTHM with Operations")


def fixture(label):
    return json.loads((FIXTURES / f"interface-{label}.json").read_text())["manifest"]


def put_completed(store, manifest):
    """Store a saved assessment as a completed run without running it."""
    scenario = parse_scenario(manifest["resolved_scenario"])
    sid = store.create_scenario(scenario)["id"]
    rid = new_id("run")
    manifest = {**manifest, "run_id": rid}
    with store.connection() as conn:
        conn.execute(
            "INSERT INTO runs(id,scenario_id,revision,state,created_at,updated_at,request_hash,manifest) VALUES (?,?,1,'completed',?,?,?,?)",
            (rid, sid, now(), now(), rid, canonical_bytes(manifest).decode()),
        )
    return manifest


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "data")


# ---------------------------------------------------------------- base values
def test_base_values_come_from_the_saved_run():
    stated = L.base_values(fixture("stated"))
    assert stated["flow"]["value"] == 20.0 and stated["flow"]["unit"] == "kg/s"
    assert stated["tool_limit"]["value"] == 90.0
    assert stated["insulated_length"]["value"] == 1000.0
    assert stated["insulated_length"]["pipe"].startswith("vacuum-insulated")
    assert all(entry["available"] for entry in stated.values())
    go = L.base_values(fixture("go"))
    assert go["flow"]["value"] == 30 and go["connection"]["value"] == 6.33
    assert go["inlet_temperature"]["value"] == 40.0
    assert not go["insulated_length"]["available"]
    assert "custom string" in go["insulated_length"]["reason"]


def test_base_values_without_the_interface_result():
    earlier = json.loads((FIXTURES / "assessment-go.json").read_text())["manifest"]
    values = L.base_values(earlier)
    assert not values["tool_limit"]["available"]
    assert "without the operations interface" in values["tool_limit"]["reason"]


# ---------------------------------------------------------------- planning
def request(mode, *levers):
    return {"mode": mode, "levers": [{"lever": k, "values": v} for k, v in levers]}


def test_one_at_a_time_cases_change_one_input_each():
    base = fixture("stated")
    plan = L.plan(request("one_at_a_time", ("flow", [15.0, 25.0, 30.0]), ("insulated_length", [500.0, 2000.0]),
                          ("drilling_fluid", [1050.0, 1100.0]), ("inlet_temperature", [20.0, 30.0])), base)
    assert plan["case_count"] == 9
    assert [list(c["levers"]) for c in plan["cases"]] == [["flow"]] * 3 + [["insulated_length"]] * 2 + [["drilling_fluid"]] * 2 + [["inlet_temperature"]] * 2
    base_site = tomllib.loads(base["resolved_scenario"]["definition"]["site"]["content"])
    base_string = base["resolved_scenario"]["definition"]["programme"]["string"]
    for case in plan["cases"]:
        scenario = case["scenario"]
        site = tomllib.loads(scenario["definition"]["site"]["content"])
        string = scenario["definition"]["programme"]["string"]
        (lever, value), = case["levers"].items()
        expected_flow = value if lever == "flow" else 20.0
        assert site["operations"]["flow"] == expected_flow
        assert site["operations"]["tool_limit_C"] == 90.0
        assert site.get("T_inj") == (value if lever == "inlet_temperature" else base_site["T_inj"])
        assert site["operations"].get("drilling_fluid_kg_m3") == (value if lever == "drilling_fluid" else None)
        assert sum(s["length_m"] for s in string) == pytest.approx(sum(s["length_m"] for s in base_string), abs=1e-6)
        assert string[0]["length_m"] == (value if lever == "insulated_length" else 1000.0)
        assert string[2:] == base_string[2:]
        assert {k: v for k, v in site.items() if k not in ("operations", "T_inj")} == {
            k: v for k, v in base_site.items() if k not in ("operations", "T_inj")}
    assert plan["estimate"]["total_s"] == pytest.approx(base["wall_clock_duration_s"] * 9)
    assert "not an optimisation" in plan["interpretation"]


def test_grid_cases_are_every_pair():
    plan = L.plan(request("grid", ("flow", [15.0, 25.0, 30.0]), ("insulated_length", [500.0, 1000.0, 2000.0])), fixture("stated"))
    assert plan["case_count"] == 9
    assert [tuple(c["levers"].values()) for c in plan["cases"]] == [
        (f, l) for f in (15.0, 25.0, 30.0) for l in (500.0, 1000.0, 2000.0)]


def test_pipe_lever_without_a_custom_string_states_the_candidates():
    plan = L.plan(request("one_at_a_time", ("pipe", ["conventional", "internally coated"])), fixture("go"))
    for case, name in zip(plan["cases"], ("conventional", "internally coated")):
        site = tomllib.loads(case["scenario"]["definition"]["site"]["content"])
        assert site["operations"] == {"pipes": [name]}
        assert "programme" not in case["scenario"]["definition"]


def test_pipe_lever_on_a_custom_string_changes_the_top_section():
    plan = L.plan(request("one_at_a_time", ("pipe", ["conventional", "TK-Drakon coated"])), fixture("stated"))
    names = [c["scenario"]["definition"]["programme"]["string"][0]["pipe"]["name"] for c in plan["cases"]]
    assert names == ["conventional", "TK-Drakon coated"]


@pytest.mark.parametrize("mode,levers,message", [
    ("one_at_a_time", [], "at least one lever"),
    ("one_at_a_time", [("flow", [20.0])], "2 to 5 values"),
    ("one_at_a_time", [("flow", [10.0, 15.0, 20.0, 25.0, 30.0, 35.0])], "2 to 5 values"),
    ("one_at_a_time", [("flow", [20.0, 20.0])], "repeat"),
    ("grid", [("flow", [20.0, 25.0])], "exactly two levers"),
    ("grid", [("flow", [10.0, 15.0, 20.0, 25.0, 30.0]), ("tool_limit", [100.0, 120.0, 140.0, 160.0, 180.0]),
              ], None),
    ("one_at_a_time", [("speed", [1.0, 2.0])], "Unknown lever"),
    ("one_at_a_time", [("insulated_length", [0.0, 500.0])], "must lie between"),
])
def test_plan_limits(mode, levers, message):
    if message is None:
        assert L.plan(request(mode, *levers), fixture("stated"))["case_count"] == 25
        return
    with pytest.raises(CalculationError, match=message):
        L.plan(request(mode, *levers), fixture("stated"))


def test_more_than_25_cases_are_refused():
    levers = [(k, v) for k, v in (("flow", [10.0, 15.0, 20.0, 25.0, 30.0]), ("tool_limit", [100.0, 110.0, 120.0, 130.0, 140.0]),
                                  ("connection", [2.0, 4.0, 6.0, 8.0, 10.0]), ("stage_minutes", [5.0, 10.0, 15.0, 20.0, 25.0]),
                                  ("inlet_temperature", [20.0, 25.0, 30.0, 35.0, 45.0]), ("drilling_fluid", [1010.0, 1020.0]))]
    with pytest.raises(CalculationError, match="27 cases exceed the limit of 25"):
        L.plan(request("one_at_a_time", *levers), fixture("stated"))


def test_a_case_the_model_rejects_stops_the_plan():
    with pytest.raises(CalculationError, match="Case 1 rejected before any run was queued"):
        L.plan(request("one_at_a_time", ("tool_limit", [500.0, 150.0])), fixture("stated"))


# ---------------------------------------------------------------- queue and cancellation
def test_cases_queue_in_plan_order_and_cancellation_keeps_finished_cases(store):
    base = put_completed(store, fixture("stated"))
    sid, created = L.submit(store, request("one_at_a_time", ("flow", [15.0, 25.0, 30.0])), base, "key-1")
    assert created
    assert L.submit(store, request("one_at_a_time", ("flow", [15.0, 25.0, 30.0])), base, "key-1") == (sid, False)
    study = L.read(store, sid)
    runs = [c["run_id"] for c in study["cases"]]
    assert [c["state"] for c in study["cases"]] == ["queued"] * 3
    assert store.claim() == runs[0]
    # the first case finishes with a saved result; the others are still queued
    with store.connection() as conn:
        conn.execute("UPDATE runs SET state='completed', manifest=? WHERE id=?",
                     (canonical_bytes({**fixture("go"), "run_id": runs[0]}).decode(), runs[0]))
    with pytest.raises(Exception, match="queue to become idle"):
        L.submit(store, request("one_at_a_time", ("tool_limit", [95.0, 150.0])), base, "key-2")
    L.cancel(store, sid)
    study = L.read(store, sid)
    assert [c["state"] for c in study["cases"]] == ["completed", "cancel_requested", "cancel_requested"]
    assert study["cases"][0]["outcome"]["verdict"] == "GO"
    assert study["finished_cases"] == 1 and study["state"] == "running"


# ---------------------------------------------------------------- results from saved runs
def finished_study(store, mode, levers, outcomes):
    """A study whose cases are completed with the given saved manifests."""
    base = put_completed(store, fixture("stated"))
    sid, _ = L.submit(store, request(mode, *levers), base, None)
    study = L.read(store, sid)
    for case, manifest in zip(study["cases"], outcomes):
        with store.connection() as conn:
            conn.execute("UPDATE runs SET state='completed', manifest=? WHERE id=?",
                         (canonical_bytes({**manifest, "run_id": case["run_id"]}).decode(), case["run_id"]))
    return base, sid


def shifted(manifest, tool=0.0, pump=0.0, verdict=None):
    m = copy.deepcopy(manifest)
    a = m["assessment"]
    for state in ("drilling", "connection", "run_in_staged"):
        a["tool_check"][state]["margin_C"] += tool
    for state in ("drilling", "trip"):
        a["pump_check"][state]["margin_Pa"] += pump
    if verdict:
        a["status"]["label"] = verdict
    return m


def test_table_and_tornado_use_saved_values_only(store):
    base_manifest = fixture("stated")
    outcomes = [shifted(base_manifest, tool=-5.0), shifted(base_manifest, tool=7.0, pump=-1e5), shifted(base_manifest, pump=2e5)]
    base, sid = finished_study(store, "one_at_a_time", [("flow", [15.0, 25.0]), ("tool_limit", [95.0, 150.0])], outcomes)
    study = L.read(store, sid)
    base_outcome = L.outcome(base_manifest)
    assert study["base_outcome"] == base_outcome
    tool = base_manifest["assessment"]["tool_check"]
    assert base_outcome["tool_margin_C"] == min(tool[s]["margin_C"] for s in ("drilling", "connection", "run_in_staged"))
    first = study["cases"][0]
    assert first["outcome"]["tool_margin_C"] == pytest.approx(base_outcome["tool_margin_C"] - 5.0)
    assert first["change_from_base"]["tool_margin_C"] == pytest.approx(-5.0)
    assert first["change_from_base"]["drilling_window_width_kg_m3"] == 0.0
    assert study["cases"][3]["state"] == "queued" and study["cases"][3]["outcome"] is None
    flow = next(r for r in study["tornado"]["tool_margin_C"]["rows"] if r["lever"] == "flow")
    assert (flow["low"], flow["high"], flow["computed"]) == (pytest.approx(-5.0), pytest.approx(7.0), 2)
    limit = next(r for r in study["tornado"]["pump_margin_Pa"]["rows"] if r["lever"] == "tool_limit")
    assert (limit["low"], limit["high"], limit["computed"]) == (pytest.approx(2e5), pytest.approx(2e5), 1)
    assert study["grid"] is None
    svg = L.tornado_svg(study["tornado"]["tool_margin_C"], "oilfield")
    assert "°F" in svg and "Tool margin" in svg


def test_grid_cells_carry_verdict_and_constraint(store):
    base_manifest = fixture("stated")
    outcomes = [shifted(base_manifest, verdict=v) for v in ("GO", "CONDITIONAL", "NO-GO", "GO")]
    _, sid = finished_study(store, "grid", [("flow", [15.0, 25.0]), ("insulated_length", [500.0, 2000.0])], outcomes)
    study = L.read(store, sid)
    grid = study["grid"]
    assert [[c["verdict"] for c in row] for row in grid["cells"]] == [["GO", "CONDITIONAL"], ["NO-GO", "GO"]]
    constraint = base_manifest["assessment"]["reading"]["controlling_constraint"]
    assert all(c["controlling_constraint"] == constraint for row in grid["cells"] for c in row)
    assert study["tornado"] is None and study["state"] == "finished"


def test_report_and_archive_are_deterministic(store):
    base_manifest = fixture("stated")
    _, sid = finished_study(store, "one_at_a_time", [("flow", [15.0, 25.0])], [shifted(base_manifest, tool=-1.0), shifted(base_manifest)])
    study = L.read(store, sid)
    first, second = L.report_html(study, "oilfield"), L.report_html(L.read(store, sid), "oilfield")
    assert first == second
    assert "Computed cases only" in first and "°F" in first
    assert "not an optimisation" in first
    import borethm_workbench.bundles as bundles

    original = bundles.export_bundle
    bundles.export_bundle = lambda store_, rid: f"archive of {rid}".encode()
    try:
        a, b = L.export(store, sid), L.export(store, sid)
    finally:
        bundles.export_bundle = original
    assert a == b
    names = zipfile.ZipFile(io.BytesIO(a)).namelist()
    assert names == sorted(["README.md", "SHA256SUMS.json", "case-1.zip", "case-2.zip", "frozen-plan.json", "report.html", "study.json"])
