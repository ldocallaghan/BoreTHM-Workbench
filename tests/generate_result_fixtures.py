"""Generate saved-run fixtures for the results page, report and range tests.

Runs the application's own assessment for three built-in sites, chosen for
their evaluator status (GO, CONDITIONAL, NO-GO), and one circulation preset.
Large detail arrays that the results page and report do not read are removed.
The Soultz assessment takes several minutes.

    python tests/generate_result_fixtures.py            # runs and payloads
    python tests/generate_result_fixtures.py payloads   # payloads only
    python tests/generate_result_fixtures.py interface [LABEL ...]

The interface runs need a BoreTHM with Operations. They write
interface-*.json: the GO and CONDITIONAL scenarios run with BoreTHM 1.8.0 again, unchanged,
whose results carry the result keys that BoreTHM adds with that interface,
and "stated" (a NO-GO), Newberry with a custom
string (vacuum-insulated pipe over the top 1,000 m), a fixed flow and a tool
limit stated in its operations table.

The payload files hold the results-page response for each saved run. The
frontend tests render them; a backend test checks they match the runs.
"""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "tests" / "fixtures" / "results"
SITES = {"go": "newberry", "conditional": "larderello", "no-go": "soultz"}
DROP = (
    "assessment_full_data",
    "hydraulics",
    "energy_diagnostic",
    "source_ledger",
    "optional_quality",
    "trajectory",
    "diagnostics",
)


def saved(manifest, run_id):
    from borethm_workbench.contracts import canonical_bytes

    manifest = {k: v for k, v in manifest.items() if k not in DROP}
    manifest["run_id"] = run_id
    # Round trip through the canonical encoding used for stored manifests.
    return json.loads(canonical_bytes(manifest))


def assessment(key):
    from borethm.site_files import site_text
    from borethm_workbench.assessment import run_assessment
    from borethm_workbench.contracts import parse_scenario

    data = json.loads(
        (ROOT / "src/borethm_workbench/presets/newberry-assessment.json").read_text()
    )
    data["title"] = f"{key} site assessment"
    data["definition"]["site"] = {
        "kind": "toml",
        "content": site_text(key),
        "source": f"BoreTHM built-in site {key}",
    }
    data["metadata"] = {"tags": ["assessment", key]}
    return run_assessment(parse_scenario(data))


INTERFACE = {"go": "newberry", "conditional": "larderello", "stated": "newberry"}
STATED_OPERATIONS = """
[operations]
flow = 20.0
tool_limit_C = 90.0

[operations.sources]
flow = ["assumed", "Fixture: a fixed flow"]
tool_limit_C = ["assumed", "Fixture: a low tool limit"]
"""


def stated_programme(site_toml):
    """A custom string: the reference string with its top section split into
    1,000 m of vacuum-insulated pipe over the rest."""
    from borethm_workbench.contracts import ProgrammeDefinition
    from borethm_workbench import programme

    reference = programme.check(ProgrammeDefinition(kind="reference"), site_toml)["drawing"]
    top = reference["string"][0]
    pipe = next(p for p in programme.model_pipes() if p["name"].startswith("vacuum-insulated"))
    insulated = {**top, "length_m": 1000.0,
                 "pipe": {**top["pipe"], "name": pipe["name"], "layers": pipe["layers"],
                          "thermal_joint_fraction": pipe["thermal_joint_fraction"], "source": pipe["source"]}}
    rest = {**top, "length_m": top["length_m"] - 1000.0}
    return {"kind": "custom", "string": [insulated, rest, *reference["string"][1:]], "hole": reference["hole"]}


def interface_assessment(label):
    from borethm.site_files import site_text
    from borethm_workbench.assessment import run_assessment
    from borethm_workbench.contracts import parse_scenario

    key = INTERFACE[label]
    if label != "stated":
        # The same scenario as the BoreTHM 1.8.0 run, so the two compare directly.
        earlier = json.loads((FOLDER / f"assessment-{label}.json").read_text(encoding="utf-8"))
        return run_assessment(parse_scenario(earlier["manifest"]["resolved_scenario"]))
    data = json.loads(
        (ROOT / "src/borethm_workbench/presets/newberry-assessment.json").read_text()
    )
    content = site_text(key) + STATED_OPERATIONS
    data["title"] = f"{key} site assessment, stated programme"
    data["definition"]["site"] = {"kind": "toml", "content": content, "source": f"BoreTHM built-in site {key}"}
    data["definition"]["programme"] = stated_programme(content)
    data["metadata"] = {"tags": ["assessment", key]}
    return run_assessment(parse_scenario(data))


def circulation():
    from borethm_workbench.adapter import run_circulation
    from borethm_workbench.contracts import parse_scenario_json

    scenario = parse_scenario_json(
        (ROOT / "src/borethm_workbench/presets/synthetic-water.json").read_bytes()
    )
    return run_circulation(scenario)


def dump(path, value):
    path.write_text(
        json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def payloads():
    from borethm_workbench.results import results

    for path in sorted(FOLDER.glob("*.json")):
        if path.name.endswith(".results.json"):
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        dump(path.with_suffix(".results.json"), results(record["manifest"], "SI"))


def main():
    if sys.argv[1:] == ["payloads"]:
        payloads()
        return
    if sys.argv[1:2] == ["interface"]:
        created = "2026-11-09T12:00:00+00:00"
        for label in sys.argv[2:] or list(INTERFACE):
            manifest = saved(interface_assessment(label), f"run-fixture-interface-{label}")
            dump(FOLDER / f"interface-{label}.json", {"created_at": created, "manifest": manifest})
            print(label, INTERFACE[label], manifest["assessment"]["status"]["label"], flush=True)
        return
    FOLDER.mkdir(parents=True, exist_ok=True)
    created = "2026-11-08T12:00:00+00:00"
    path = FOLDER / "circulation.json"
    path.write_text(
        json.dumps(
            {"created_at": created, "manifest": saved(circulation(), "run-fixture-circulation")},
            indent=1,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    for label, key in SITES.items():
        manifest = saved(assessment(key), f"run-fixture-{label}")
        path = FOLDER / f"assessment-{label}.json"
        path.write_text(
            json.dumps(
                {"created_at": created, "manifest": manifest},
                indent=1,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(label, key, manifest["assessment"]["status"]["label"], flush=True)
    payloads()


if __name__ == "__main__":
    main()
