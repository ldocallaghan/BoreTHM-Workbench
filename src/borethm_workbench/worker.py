"""One isolated subprocess per solve, with data-only SQLite/artifact output."""

import argparse
import csv
import hashlib
import io
import json
import os
import time
import traceback

from .adapter import CalculationError, engine_identity, run_circulation
from .contracts import canonical_bytes, parse_scenario, physical_hash
from .store import Store
from .operations import ArtifactInvalid, OperationalPolicy, ResourceLimit
from .interventions import CheckpointError


def publish(store, rid, payload):
    if payload.get("mode") in ("wall_history", "tool_history"):
        return publish_history(store, rid, payload)
    staged = store.artifact_path(rid, pending=True)
    profile, scenario = payload["profile"], payload["resolved_scenario"]
    buffer = io.StringIO(newline="")
    fields = list(profile)
    writer = csv.writer(buffer)
    writer.writerow(fields)
    for values in zip(*(profile[field] for field in fields)):
        writer.writerow(
            [
                ("'" + v if v.startswith(("=", "+", "-", "@", "\t", "\r")) else v)
                if isinstance(v, str)
                else v
                for v in values
            ]
        )
    summary = f"# {scenario['title'].replace(chr(10), ' ')}\n\nRun {rid}. {payload['acceptance']}.\n\n"
    summary += "\n".join(
        f"- {k}: {v['value']:.12g} {v['unit']} ({v['basis']})"
        for k, v in payload["summary"].items()
    )
    summary += "\n\nQualifications:\n" + "\n".join(
        f"- {w['code']}: {w['message']}" for w in payload["warnings"]
    )
    summary += (
        "\n\nPhysics: "
        + payload["physics"]
        + "\n\n```json\n"
        + json.dumps(payload["physics_settings"], indent=2)
        + "\n```\n"
    )
    files = {
        "scenario.json": (canonical_bytes(scenario), "application/json"),
        "profile.json": (canonical_bytes(profile), "application/json"),
        "profile.csv": (buffer.getvalue().encode("utf-8"), "text/csv"),
        "diagnostics.json": (
            canonical_bytes(
                {
                    k: payload[k]
                    for k in (
                        "validity",
                        "attempts",
                        "energy_diagnostic",
                        "source_ledger",
                        "hydraulics",
                    )
                }
            ),
            "application/json",
        ),
        "summary.md": (summary.encode("utf-8"), "text/markdown"),
        "README.md": (
            b"# BoreTHM Workbench portable run\n\nSee manifest.json for build, units, pressure bases, provenance and qualifications.\nAuthoritative physical values use float64 JSON/CSV. MD is metres. Pressure suffixes declare Pa and convention.\n\nFrom the declared BoreTHM checkout/environment:\n\n    borethm-workbench reproduce path/to/bundle.zip\n\nReproduction compares fields within documented numerical tolerances.\nThis export contains an operating-point solution.\nApplication and model code: Apache-2.0. Model documentation: CC BY 4.0; data retain their source notices.\n",
            "text/markdown",
        ),
    }
    if payload.get("mode") == "site_assessment":
        files["assessment.json"] = (
            canonical_bytes(payload["assessment"]),
            "application/json",
        )
        files["assessment-full.json"] = (
            canonical_bytes(payload["assessment_full_data"]),
            "application/json",
        )
        files["site.toml"] = (
            scenario["definition"]["site"]["content"].encode("utf-8"),
            "application/toml",
        )
    if payload.get("trajectory"):
        files["trajectory.json"] = (
            canonical_bytes(payload["trajectory"]),
            "application/json",
        )
    files["physics.json"] = (
        canonical_bytes(
            {"physics": payload["physics"], "settings": payload["physics_settings"]}
        ),
        "application/json",
    )
    files = {
        name: (data.replace(b"\r\n", b"\n").replace(b"\r", b"\n"), media)
        for name, (data, media) in files.items()
    }
    descriptors = []
    for index, (name, (data, media)) in enumerate(files.items()):
        descriptors.append(
            dict(
                id=f"artifact_{index}",
                filename=name,
                type=name.split(".")[0],
                schema_version="1.0",
                byte_count=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                media_type=media,
                compression="none",
                retrieval_route=f"/api/v1/runs/{rid}/artifacts/artifact_{index}",
            )
        )
    payload["artifacts"] = descriptors
    payload["title"] = scenario["title"]
    payload["operational_policy"] = store.policy.as_dict()
    store.check_storage(
        sum(len(v[0]) for v in files.values()) + len(canonical_bytes(payload)) + 4096
    )
    staged.mkdir()
    for name, (data, _) in files.items():
        (staged / name).write_bytes(data)
    store.finish(rid, "completed", payload, staged)
    if staged.exists():
        store.clear_pending(rid)


def publish_history(store, rid, payload):
    from .contracts import HistoryDataset

    datasets = payload.pop("datasets")
    files = {
        "scenario.json": (
            canonical_bytes(payload["resolved_scenario"]),
            "application/json",
        ),
        "history-index.json": (canonical_bytes(payload["history"]), "application/json"),
    }
    for i, dataset in enumerate(datasets):
        HistoryDataset.model_validate(dataset)
        files[f"column-{i}.json"] = (canonical_bytes(dataset), "application/json")
    if payload.get("trajectory"):
        files["trajectory.json"] = (
            canonical_bytes(payload["trajectory"]),
            "application/json",
        )
    if payload.get("intervention"):
        files["checkpoints.json"] = (
            canonical_bytes(payload.pop("_checkpoints")),
            "application/json",
        )
    summary = (
        "# "
        + payload["resolved_scenario"]["title"].replace("\n", " ")
        + "\n\nQualified physical history.\n\n"
    )
    summary += "\n".join(
        "- " + w["code"] + ": " + w["message"] for w in payload["warnings"]
    )
    summary += (
        "\n\nPhysics: "
        + payload["physics"]
        + "\n\n```json\n"
        + json.dumps(payload["physics_settings"], indent=2)
        + "\n```\n"
    )
    continuation = (
        "thermal history event-boundary checkpoints; fixed domain and prescribed local boundaries. Results have not been compared with field measurements."
        if payload.get("intervention")
        else "No checkpoint/resume."
    )
    files["README.md"] = (
        (
            summary
            + "\n\nAccepted steps and explicit wall event sides are authoritative. Radial columns are independent; no axial rock conduction. "
            + continuation
            + "\n\nUse borethm-workbench reproduce <bundle.zip> in the declared environment.\n"
        ).encode("utf-8"),
        "text/markdown",
    )
    files["physics.json"] = (
        canonical_bytes(
            {"physics": payload["physics"], "settings": payload["physics_settings"]}
        ),
        "application/json",
    )
    files = {
        name: (data.replace(b"\r\n", b"\n").replace(b"\r", b"\n"), media)
        for name, (data, media) in files.items()
    }
    payload["artifacts"] = [
        dict(
            id=f"artifact_{i}",
            filename=name,
            type="history" if name.startswith("column-") else name.split(".")[0],
            schema_version="1.0",
            byte_count=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            media_type=media,
            compression="none",
            retrieval_route=f"/api/v1/runs/{rid}/artifacts/artifact_{i}",
        )
        for i, (name, (data, media)) in enumerate(files.items())
    ]
    payload.update(
        title=payload["resolved_scenario"]["title"],
        operational_policy=store.policy.as_dict(),
    )
    store.check_storage(
        sum(len(v[0]) for v in files.values()) + len(canonical_bytes(payload)) + 4096
    )
    staged = store.artifact_path(rid, pending=True)
    staged.mkdir()
    for name, (data, _) in files.items():
        (staged / name).write_bytes(data)
    store.finish(rid, "completed", payload, staged)
    if staged.exists():
        store.clear_pending(rid)


def execute(root, rid, policy=None):
    store = Store(root, policy=policy)
    run = store.get(rid)
    scenario = parse_scenario(
        store.revision(run["scenario_id"], run["scenario_revision"])
    )
    started = time.perf_counter()
    try:
        from .sweeps import verify_execution

        verify_execution(store, rid)
        if scenario.mode in ("wall_history", "tool_history"):
            from .histories import run_history

            operation = run_history
        elif scenario.mode == "site_assessment":
            from .assessment import run_assessment

            operation = run_assessment
        else:
            operation = run_circulation
        options = (
            {"notify": lambda kind, data: store.notify(rid, kind, data)}
            if scenario.mode in ("wall_history", "tool_history")
            else {}
            if scenario.mode == "site_assessment"
            else {
                "seed_provider": lambda identity, cache: store.find_seed(
                    scenario, identity, cache
                ),
                "notify": lambda kind, data: store.notify(rid, kind, data),
            }
        )
        payload = operation(
            scenario,
            lambda state, data: store.stage(
                rid, state, {**data, "process_id": os.getpid()}
            ),
            **options,
        )
        payload["wall_clock_duration_s"] = time.perf_counter() - started
        publish(store, rid, payload)
    except InterruptedError:
        store.finish(
            rid,
            "cancelled",
            {
                "acceptance": "rejected",
                "failure": {
                    "code": "USER_CANCELLED",
                    "message": "Cancellation accepted",
                },
            },
        )
    except Exception as error:
        failure = {
            "code": error.code
            if isinstance(
                error,
                (ArtifactInvalid, CalculationError, ResourceLimit, CheckpointError),
            )
            else "WORKER_FAILED",
            "message": str(error)[:2000],
        }
        diagnostics = (
            error.diagnostics
            if isinstance(error, CalculationError)
            else {"traceback": traceback.format_exc()[-6000:]}
        )
        store.finish(
            rid,
            "failed",
            dict(
                schema_version="1.0",
                acceptance="rejected",
                failure=failure,
                resolved_scenario=scenario.model_dump(),
                physical_request_hash=physical_hash(scenario),
                build=engine_identity(),
                diagnostics=diagnostics,
                wall_clock_duration_s=time.perf_counter() - started,
            ),
        )
    finally:
        store.clear_pending(rid)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--policy", default="{}")
    args = parser.parse_args()
    execute(args.root, args.run, OperationalPolicy(**json.loads(args.policy)))
