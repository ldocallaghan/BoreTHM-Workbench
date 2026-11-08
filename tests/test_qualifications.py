"""Plain sentences for qualifications on the results page and in the report."""

import json
from pathlib import Path

import pytest

from borethm_workbench.qualifications import CODES, FLAGS, plain
from borethm_workbench.report import report_html
from borethm_workbench.results import results

FIXTURES = Path(__file__).resolve().parent / "fixtures/results"


@pytest.mark.parametrize("flag", sorted(FLAGS))
def test_every_model_hydraulic_flag_has_a_sentence(flag):
    sentence = plain({"code": "HYDRAULIC_QUALIFICATION", "message": flag})
    assert sentence == FLAGS[flag] and "_" not in sentence and sentence.endswith(".")


@pytest.mark.parametrize("code", sorted(CODES))
def test_every_application_code_has_a_sentence(code):
    sentence = plain({"code": code, "message": "x_y"})
    assert "_" not in sentence and sentence.endswith(".")


def test_named_tool_flags_and_fallbacks():
    zero = plain({"code": "HYDRAULIC_QUALIFICATION", "message": "tool_default_zero_no_source:mud_motor:6.75"})
    assert zero == "No published pressure-loss figure exists for the mud motor in the 6.75 in size band, so its allowance is zero."
    assert plain({"code": "SITE_INPUT_QUALIFICATION", "message": "no source stated for the rock's stiffness"}) == (
        "Site input check, in the model's wording: no source stated for the rock's stiffness."
    )
    assert plain({"code": "HYDRAULIC_QUALIFICATION", "message": "new_model_flag"}) == "New model flag."
    assert plain({"code": "SOMETHING_NEW", "message": "A complete message"}) == "A complete message."


@pytest.mark.parametrize("name", ["circulation", "assessment-go", "assessment-conditional", "assessment-no-go"])
def test_results_and_report_show_sentences(name):
    record = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    page = results(record["manifest"])
    assert page["qualifications"]
    for item in page["qualifications"]:
        assert item["code"] and item["plain"] and "_" not in item["plain"]
    text = report_html(record).decode("utf-8")
    for item in page["qualifications"]:
        assert item["plain"].replace("'", "&#x27;") in text
    if name.startswith("assessment"):
        assert "Controlling constraint, in the model's wording" in text
        assert "in its own wording and units" in text
