"""Results pages and the HTML report are built from the saved run alone."""

import json
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient

from borethm_workbench.report import report_html
from borethm_workbench.results import results
from borethm_workbench.service import create_app

FIXTURES = Path(__file__).resolve().parent / "fixtures/results"
NAMES = ["circulation", "assessment-go", "assessment-conditional", "assessment-no-go"]


def record(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", NAMES)
def test_payload_fixtures_match_the_saved_runs(name):
    payload = json.loads((FIXTURES / f"{name}.results.json").read_text(encoding="utf-8"))
    assert json.loads(json.dumps(results(record(name)["manifest"], "SI"))) == payload


@pytest.mark.parametrize("name", ["assessment-go", "assessment-conditional", "assessment-no-go"])
def test_assessment_summary_uses_the_evaluator_result(name):
    manifest = record(name)["manifest"]
    report = manifest["assessment"]
    summary = results(manifest)["summary"]
    assert summary["verdict"]["status"] == report["status"]["label"]
    assert summary["verdict"]["controlling_constraint"] == report["reading"]["controlling_constraint"]
    for key in ("drilling_fluid", "trip_fluid"):
        window, shown = report["windows"][key], summary["windows"]["fluids"][key]
        assert (shown["lower_kg_m3"], shown["upper_kg_m3"], shown["open"]) == (window["lo"], window["hi"], window["open"])
        assert shown["width_kg_m3"] == window["hi"] - window["lo"]
        assert shown["upper_by"]["md_m"] == window["hi_by"]["md"]
    assert summary["windows"]["profiles_returned"] is False
    for row in summary["tools"]["rows"]:
        assert row["rating_C"] == report["tools"]["limit"]
        assert row["margin_C"] == row["rating_C"] - row["peak_C"]
    assert [c["verdict"] for c in summary["stability"]["cases"]] == [c["verdict"] for c in report["cycle"]["cases"]]
    assert summary["pressure"]["pump_limit_Pa"] is None


def test_verdicts_cover_go_conditional_and_no_go():
    labels = [results(record(n)["manifest"])["summary"]["verdict"]["status"] for n in NAMES[1:]]
    assert labels == ["GO", "CONDITIONAL", "NO-GO"]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("system", ["SI", "oilfield"])
def test_report_is_deterministic_and_self_contained(name, system):
    saved = record(name)
    first = report_html(saved, system)
    second = report_html(json.loads(json.dumps(saved)), system)
    assert first == second
    text = first.decode("utf-8")
    assert text.startswith("<!doctype html>")
    assert not re.search(r"<(script|link|img|iframe)\b", text)
    links = text.replace('xmlns="http://www.w3.org/2000/svg"', "")
    assert "http://" not in links and "https://" not in links
    manifest = saved["manifest"]
    for required in (manifest["run_id"], manifest["physical_request_hash"], manifest["build"]["core_version"], manifest["physics"], "Field-tested range", "Qualifications", "<svg"):
        assert required in text
    assert ("°F" in text) == (system == "oilfield")
    if name.startswith("assessment"):
        assert manifest["assessment"]["status"]["label"] in text
        assert "Controlling constraint" in text and "Mud-weight window" in text


def test_report_and_results_routes(tmp_path):
    app = create_app(tmp_path, start_jobs=False)
    saved = record("assessment-conditional")
    with TestClient(app) as client:
        store = app.state.store
        scenario = saved["manifest"]["resolved_scenario"]
        from borethm_workbench.contracts import parse_scenario

        run, _ = store.submit(parse_scenario(scenario), "report-test", None, None)
        with store.connection() as conn:
            conn.execute(
                "UPDATE runs SET state='completed', manifest=? WHERE id=?",
                (json.dumps({**saved["manifest"], "run_id": run["id"]}), run["id"]),
            )
        first = client.get(f"/api/v1/runs/{run['id']}/report?units=oilfield")
        second = client.get(f"/api/v1/runs/{run['id']}/report?units=oilfield")
        assert first.status_code == 200 and first.content == second.content
        assert first.headers["content-type"].startswith("text/html")
        assert "attachment" in first.headers["content-disposition"]
        page = client.get(f"/api/v1/runs/{run['id']}/results?units=SI").json()
        assert page["summary"]["verdict"]["status"] == "CONDITIONAL"
        posted = client.post("/api/v1/results?units=SI", json={"manifest": saved["manifest"]}).json()
        assert posted["summary"] == results(saved["manifest"])["summary"]
        assert client.get("/api/v1/runs/missing/report").status_code == 404
    app.state.store.close()
