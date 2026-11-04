"""Local launch, explicit property-cache preparation and reproduction."""

import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="BoreTHM Workbench")
    parser.add_argument(
        "command",
        nargs="?",
        default="serve",
        choices=[
            "serve",
            "prepare",
            "inspect",
            "cache-info",
            "data-info",
            "reproduce",
            "contracts",
        ],
    )
    parser.add_argument("bundle", nargs="?")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--output", default="contracts")
    parser.add_argument("--artifact-budget-gib", default=10, type=int)
    parser.add_argument("--run-artifact-limit-mib", default=250, type=int)
    parser.add_argument("--free-disk-reserve-mib", default=256, type=int)
    parser.add_argument("--retained-events", default=2000, type=int)
    parser.add_argument("--event-retention-hours", default=168, type=int)
    args = parser.parse_args()
    configured_data = (
        args.data_dir or os.environ.get("BORETHM_WORKBENCH_DATA") or ".workbench-data"
    )
    selection = (
        "argument"
        if args.data_dir
        else "BORETHM_WORKBENCH_DATA"
        if os.environ.get("BORETHM_WORKBENCH_DATA")
        else "default"
    )
    root = Path(configured_data).expanduser().resolve()
    os.environ["BORETHM_WORKBENCH_DATA"] = str(root)
    os.environ.setdefault("BORETHM_CACHE_DIR", str(root / "cache"))
    for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(key, "1")
    if args.command == "data-info":
        print(
            json.dumps(
                {
                    "data_directory": str(root),
                    "selected_by": selection,
                    "cache_directory": os.environ["BORETHM_CACHE_DIR"],
                },
                indent=2,
            )
        )
    elif args.command == "prepare":
        from borethm import water_table
        from .adapter import cache_status

        print(
            "Preparing the IAPWS-95 water table; this can take several minutes.",
            flush=True,
        )
        water_table._interpolators()
        status = cache_status()
        print(json.dumps(status, indent=2))
        if not status["ready"]:
            raise SystemExit(1)
    elif args.command == "inspect":
        if not args.bundle:
            parser.error("inspect requires a bundle path")
        from .bundles import inspect_bundle

        imported = inspect_bundle(Path(args.bundle).read_bytes())
        print(
            json.dumps(
                {
                    "passed": True,
                    "operation": "inspect stored values; no solve or restart",
                    "run_id": imported["manifest"]["run_id"],
                    "build": imported["manifest"]["build"],
                    "verified_artifacts": imported["verified_artifacts"],
                    "source_sha256": imported["source_sha256"],
                },
                indent=2,
            )
        )
    elif args.command == "cache-info":
        from .cache_reference import cache_report

        report = cache_report()
        print(json.dumps(report, indent=2))
        if not report["status"]["ready"]:
            raise SystemExit(1)
    elif args.command == "contracts":
        from .contracts import (
            SCENARIO_ADAPTER,
            JobEvent,
            ErrorEnvelope,
            canonical_bytes,
        )
        from .service import create_app

        directory = Path(args.output)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "scenario.schema.json").write_bytes(
            canonical_bytes(SCENARIO_ADAPTER.json_schema())
        )
        (directory / "job-event.schema.json").write_bytes(
            canonical_bytes(JobEvent.model_json_schema())
        )
        (directory / "error.schema.json").write_bytes(
            canonical_bytes(ErrorEnvelope.model_json_schema())
        )
        (directory / "openapi.json").write_bytes(
            canonical_bytes(create_app(root, start_jobs=False).openapi())
        )
        print("Wrote scenario, event, error JSON Schemas and OpenAPI to", directory)
    elif args.command == "reproduce":
        if not args.bundle:
            parser.error("reproduce requires a bundle path")
        from .adapter import run_circulation
        from .bundles import inspect_bundle
        from .contracts import parse_scenario
        from .reproduction import compare_results

        imported = inspect_bundle(Path(args.bundle).read_bytes())
        original = imported["manifest"]
        from .adapter import engine_identity, cache_status

        identity = engine_identity()
        keys = ("core_version", "app_version", "core_commit", "libraries")
        mismatches = [k for k in keys if original["build"].get(k) != identity.get(k)]
        scenario = parse_scenario(imported["scenario"])
        from .dependencies import uses_property_table

        property_difference = uses_property_table(scenario) and original.get(
            "property_table", {}
        ).get("arrays") != cache_status().get("arrays")
        if mismatches:
            print(
                json.dumps(
                    {
                        "passed": False,
                        "operation": "reproduce",
                        "code": "REPRODUCTION_IDENTITY_MISMATCH",
                        "mismatches": mismatches,
                        "message": "Inspection passed. Numerical reproduction requires the matching build and the dependencies used by this calculation mode.",
                    },
                    indent=2,
                )
            )
            raise SystemExit(1)
        if scenario.mode in ("wall_history", "tool_history"):
            from .histories import run_history, compare_history_results

            grids = {
                d["id"]: d["grid"]["radius_m"]
                for d in imported["datasets"]
                if d["kind"] == "wall"
            }
            result = run_history(scenario, stored_grids=grids)
            report = compare_history_results(
                original, imported["datasets"], result, imported.get("checkpoints")
            )
            print(json.dumps({**report, "engine": result["build"]}, indent=2))
            raise SystemExit(0 if report["passed"] else 1)
        if scenario.mode == "site_assessment":
            from .assessment import run_assessment

            result = run_assessment(scenario)
        else:
            result = run_circulation(
                scenario, seed=original.get("initialisation", {}).get("accepted_seed")
            )
        report = compare_results(original, result)
        print(
            json.dumps(
                {
                    **report,
                    "property_array_difference": property_difference,
                    "engine": result["build"],
                },
                indent=2,
            )
        )
        raise SystemExit(0 if report["passed"] else 1)
    else:
        from .adapter import engine_identity
        from .service import create_app
        from .operations import OperationalPolicy
        import uvicorn

        try:
            policy = OperationalPolicy(
                max_artifact_storage_bytes=args.artifact_budget_gib * 1024**3,
                max_run_artifact_bytes=args.run_artifact_limit_mib * 1024**2,
                minimum_free_disk_bytes=args.free_disk_reserve_mib * 1024**2,
                retained_events_per_job=args.retained_events,
                event_retention_seconds=args.event_retention_hours * 3600,
            )
        except ValueError as error:
            parser.error(str(error))
        from . import __version__

        print(f"BoreTHM Workbench {__version__}: local application")
        print("Engine:", json.dumps(engine_identity(), indent=2))
        print("Data directory:", root)
        print(f"Local URL: http://127.0.0.1:{args.port}")
        uvicorn.run(create_app(root, policy=policy), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
