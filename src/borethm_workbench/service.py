from contextlib import asynccontextmanager
import asyncio
import hashlib
import json
import os
from pathlib import Path
import zipfile

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from .adapter import CalculationError, cache_status, engine_identity, validate
from .bundles import export_bundle, inspect_bundle
from .contracts import (
    AnyScenario,
    Comparison,
    NUMERICAL_PROFILE,
    RunEnvelope,
    RunRequest,
    SCENARIO_ADAPTER,
    Submit,
    digest,
    physical_hash,
    parse_scenario,
    parse_scenario_json,
)
from .jobs import Coordinator
from .contracts import (
    ErrorEnvelope,
    JobEvent,
    HistoryComparison,
    HistoryDataset,
    TrajectoryRequest,
    InterventionBranch,
    SweepRequest,
)
from .store import Conflict, Store, TERMINAL, now
from .interventions import CheckpointError

PACKAGE = Path(__file__).parent


def create_app(data_dir=None, start_jobs=True, deadline=30, policy=None):
    root = Path(
        data_dir or os.environ.get("BORETHM_WORKBENCH_DATA", ".workbench-data")
    ).resolve()
    os.environ.setdefault("BORETHM_CACHE_DIR", str(root / "cache"))
    store = Store(root, policy=policy)
    coordinator = Coordinator(store, deadline)

    @asynccontextmanager
    async def lifespan(app):
        app.state.engine = engine_identity()
        app.state.cache = cache_status()
        if start_jobs:
            coordinator.start()
        try:
            yield
        finally:
            if start_jobs:
                coordinator.close()

    descriptions = {
        400: "Invalid request",
        403: "Forbidden",
        404: "Resource not found",
        409: "State conflict",
        413: "Request too large",
        422: "Invalid input",
        429: "Resource limit",
        503: "Service unavailable",
    }
    app = FastAPI(
        title="BoreTHM Workbench",
        version="0.3.0.dev0",
        description="BoreTHM 1.8 supports circulation, assessment and prescribed thermal histories. Installed capabilities are reported by /api/v1/capabilities.",
        lifespan=lifespan,
        responses={
            code: {"model": ErrorEnvelope, "description": description}
            for code, description in descriptions.items()
        },
    )
    app.state.store = store
    app.state.coordinator = coordinator
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
    )

    def error_response(status, code, message, path="/", headers=None):
        body = ErrorEnvelope(detail=[dict(code=code, message=message, path=path)])
        return JSONResponse(
            status_code=status, content=body.model_dump(), headers=headers
        )

    @app.middleware("http")
    async def local_boundary(request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            permitted = {
                str(request.base_url).rstrip("/"),
                "http://127.0.0.1:5173",
                "http://localhost:5173",
            }
            if (
                origin
                and origin not in permitted
                or request.headers.get("sec-fetch-site") == "cross-site"
            ):
                return error_response(
                    403, "ORIGIN_REJECTED", "Cross-origin mutations are not permitted"
                )
            maximum = (
                100 * 1024**2
                if request.url.path == "/api/v1/imports"
                and request.headers.get("content-type", "").startswith(
                    "application/zip"
                )
                else 5 * 1024**2
            )
            # Bound actual streamed bytes as well as Content-Length.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > maximum:
                    return error_response(
                        413, "RESOURCE_LIMIT", "Request body too large"
                    )
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = (
            "no-store" if request.url.path.startswith("/api") else "no-cache"
        )
        return response

    @app.exception_handler(KeyError)
    async def missing(request, error):
        return error_response(404, "RESOURCE_NOT_FOUND", "Resource not found")

    @app.exception_handler(Conflict)
    async def conflict(request, error):
        return error_response(409, error.code, str(error))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, error):
        message = str(error.detail)
        prefix = message.split(":", 1)[0]
        code = (
            prefix
            if prefix.replace("_", "").isupper() and " " not in prefix
            else {
                404: "RESOURCE_NOT_FOUND",
                409: "STATE_CONFLICT",
                422: "SCHEMA_INVALID",
                429: "RESOURCE_LIMIT",
                503: "ENGINE_NOT_READY",
            }.get(error.status_code, "REQUEST_REJECTED")
        )
        return error_response(error.status_code, code, message, headers=error.headers)

    @app.exception_handler(CheckpointError)
    @app.exception_handler(CalculationError)
    async def invalid(request, error):
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {
                        "code": error.code,
                        "severity": "error",
                        "path": "/definition",
                        "message": str(error),
                    }
                ]
            },
        )

    @app.exception_handler(RequestValidationError)
    async def schema_invalid(request, error):
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {
                        "code": "SCHEMA_INVALID",
                        "severity": "error",
                        "path": "/" + "/".join(str(x) for x in e["loc"] if x != "body"),
                        "message": e["msg"],
                    }
                    for e in error.errors()
                ]
            },
        )

    @app.get("/api/v1/capabilities")
    def capabilities():
        app.state.engine = engine_identity()
        app.state.cache = cache_status()
        return dict(
            engine=app.state.engine,
            physics_values=app.state.engine["physics_values"],
            property_cache=app.state.cache,
            schema_versions=["1.0", "1.1"],
            modes=["circulation_state", "site_assessment"]
            + [
                mode
                for mode in ("wall_history", "tool_history")
                if app.state.engine["history_capabilities"][mode]
            ],
            unavailable_modes={
                mode: app.state.engine["history_message"]
                for mode in ("wall_history", "tool_history")
                if not app.state.engine["history_capabilities"][mode]
            },
            fluids=["iapws_water", "barite_mud", "constant_properties"],
            deployment="local_single_user",
            release_status="Local application; results have not been compared with field measurements",
            enabled={
                "comparison": True,
                "sweeps": True,
                "export": True,
                "scenario_import": True,
                "bundle_inspection": True,
                "assessment": True,
                "histories": all(app.state.engine["history_capabilities"].values()),
                "three_d": True,
                "survey_stations": True,
                "inclined_thermal_hydraulics": True,
                "deviated_stability": False,
                "checkpoints": app.state.engine["history_capabilities"]["checkpoints"],
                "scheduled_wall_interventions": app.state.engine[
                    "history_capabilities"
                ]["scheduled_interventions"],
                "warm_start": True,
                "continuation": True,
            },
            checkpoint_scope="Fixed-domain open-hole wall events only; completed event boundaries; results have not been compared with field measurements",
            initialisation=dict(
                policies=["deterministic-default-v1", "interactive-bounded-v1"],
                maximum_target_attempts=3,
                maximum_continuation_solves=2,
                continuation_uniform_exposure_years=[1 / 365, 1 / 52],
            ),
            numerical_profile=NUMERICAL_PROFILE,
            numerical_profile_hash=digest(NUMERICAL_PROFILE),
            operational_policy=store.policy.as_dict(),
            storage=store.storage_status(),
            limits=dict(
                scenario_upload_bytes=5 * 1024**2,
                bundle_upload_bytes=100 * 1024**2,
                concurrent_jobs=1,
                queue_jobs=20,
                circulation_deadline_s=deadline,
                history_deadline_s=300,
                history_columns=20,
                history_samples=20000,
                radial_field_values=2000000,
                sweep_cases=25,
                sweep_admission="idle queue; dedicated at-most-25-case batch",
                components=500,
                hole_intervals=500,
                display_depths=5000,
            ),
        )

    @app.get("/api/v1/schemas/scenario.json")
    def schema():
        return SCENARIO_ADAPTER.json_schema()

    @app.post("/api/v1/sweeps/preview")
    def preview_sweep(request: SweepRequest):
        from .sweeps import plan

        return plan(request)

    @app.post("/api/v1/sweeps", status_code=202)
    def submit_sweep(
        request: SweepRequest,
        idempotency_key: str | None = Header(default=None, max_length=200),
    ):
        from .sweeps import submit, read

        if (
            not app.state.cache["ready"]
            or not app.state.engine["adapter_compatible"]
            or coordinator.error
        ):
            raise HTTPException(503, "Engine/cache is not ready. See /health/ready.")
        sid, created = submit(
            store, request, idempotency_key, app.state.engine, app.state.cache
        )
        return {**read(store, sid), "newly_created": created}

    @app.get("/api/v1/sweeps")
    def sweeps():
        from .sweeps import list_sweeps

        return {"items": list_sweeps(store)}

    @app.get("/api/v1/sweeps/{sid}")
    def sweep(sid: str, baseline_index: int = Query(default=0, ge=0, le=24)):
        from .sweeps import read

        return read(store, sid, baseline_index)

    @app.post("/api/v1/sweeps/{sid}/cancel")
    def cancel_sweep(sid: str):
        from .sweeps import cancel, read

        cancel(store, sid)
        return read(store, sid)

    @app.get("/api/v1/sweeps/{sid}/export")
    def export_sweep(sid: str):
        from .sweeps import export

        return Response(
            export(store, sid),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{sid}.zip"'},
        )

    @app.get("/api/v1/schemas/job-event.json")
    def event_schema():
        return JobEvent.model_json_schema()

    @app.get("/api/v1/schemas/error.json")
    def error_schema():
        return ErrorEnvelope.model_json_schema()

    @app.get("/api/v1/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/api/v1/operations/metrics")
    def metrics():
        process = coordinator.process
        return {
            **store.metrics(),
            "worker_pid": process.pid if process and process.poll() is None else None,
            "active_run_id": coordinator.active_run,
            "coordinator_error": coordinator.error,
        }

    @app.get("/api/v1/health/ready")
    def ready():
        app.state.engine = engine_identity()
        app.state.cache = cache_status()
        ok = (
            app.state.engine["adapter_compatible"]
            and app.state.cache["ready"]
            and not coordinator.error
        )
        return JSONResponse(
            status_code=200 if ok else 503,
            content={
                "ready": ok,
                "cache": app.state.cache,
                "coordinator_error": coordinator.error,
            },
        )

    @app.get("/api/v1/presets")
    def presets():
        return [
            {
                "id": p.stem,
                "title": json.loads(p.read_text(encoding="utf-8"))["title"],
                "mode": json.loads(p.read_text(encoding="utf-8"))["mode"],
                "version": "1.0",
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            }
            for p in sorted((PACKAGE / "presets").glob("*.json"))
        ]

    @app.get("/api/v1/presets/{preset_id}", response_model=AnyScenario)
    def preset(preset_id: str):
        for p in (PACKAGE / "presets").glob("*.json"):
            if p.stem == preset_id:
                return parse_scenario_json(p.read_bytes())
        raise KeyError(preset_id)

    @app.post("/api/v1/scenarios/validate")
    def validation(scenario: AnyScenario):
        warnings = validate(scenario)
        return {
            "resolved_scenario": scenario.model_dump(),
            "warnings": warnings,
            "physical_hash": physical_hash(scenario),
        }

    @app.post("/api/v1/trajectories/preview")
    def trajectory_preview(request: TrajectoryRequest):
        from .trajectories import reconstruct

        return reconstruct(request)

    @app.post("/api/v1/scenarios", status_code=201)
    def create_scenario(scenario: AnyScenario):
        validate(scenario)
        return store.create_scenario(scenario)

    @app.get("/api/v1/scenarios/{sid}")
    def scenario(sid: str):
        return store.get_scenario(sid)

    @app.get("/api/v1/scenarios/{sid}/revisions/{revision}", response_model=AnyScenario)
    def revision(sid: str, revision: int):
        return store.revision(sid, revision)

    @app.post("/api/v1/scenarios/{sid}/revisions", status_code=201)
    def append(sid: str, scenario: AnyScenario, if_match: str = Header()):
        validate(scenario)
        return store.append_revision(sid, scenario, if_match.strip('"'))

    @app.post("/api/v1/runs", status_code=202)
    def submit(
        request: RunRequest,
        idempotency_key: str | None = Header(default=None, max_length=200),
    ):
        inline = isinstance(request, Submit)
        scenario = (
            request.scenario
            if inline
            else parse_scenario(store.revision(request.scenario_id, request.revision))
        )
        validate(scenario)
        from .interventions import verify_stored_continuation

        verify_stored_continuation(store, scenario)
        from .dependencies import property_identity

        dependency = property_identity(scenario)
        if (
            not dependency["ready"]
            or not app.state.engine["adapter_compatible"]
            or coordinator.error
        ):
            raise HTTPException(
                503,
                "Engine or property table is unavailable. Prepare the table, then select Recheck engine.",
            )
        try:
            run, created = store.submit(
                scenario,
                idempotency_key,
                None if inline else request.scenario_id,
                None if inline else request.revision,
            )
        except OverflowError as error:
            raise HTTPException(
                429, str(error), headers={"Retry-After": "5"}
            ) from error
        return {**run, "newly_created": created}

    @app.get("/api/v1/runs")
    def runs(
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        mode: str | None = Query(
            default=None,
            pattern="^(circulation_state|site_assessment|wall_history|tool_history)$",
        ),
    ):
        items = store.list(limit + 1, offset, mode)
        return {
            "items": items[:limit],
            "offset": offset,
            "limit": limit,
            "has_more": len(items) > limit,
            "mode": mode,
        }

    @app.get("/api/v1/runs/{rid}", response_model=RunEnvelope)
    def run(rid: str):
        return store.get(rid)

    @app.post("/api/v1/runs/{rid}/cancel")
    def cancel(rid: str):
        return store.cancel(rid)

    @app.get(
        "/api/v1/runs/{rid}/history/{dataset_id:path}",
        response_model=HistoryDataset,
        response_model_exclude_none=True,
    )
    def history_dataset(rid: str, dataset_id: str):
        from .histories import read_dataset

        return read_dataset(store, rid, dataset_id)

    @app.post("/api/v1/history-comparisons")
    def history_comparison(request: HistoryComparison):
        from .histories import read_dataset, compare_histories

        a = read_dataset(store, request.baseline_run_id, request.baseline_dataset_id)
        b = read_dataset(store, request.variant_run_id, request.variant_dataset_id)
        result = compare_histories(a, b)
        result["runs"] = [
            store.get(rid)["manifest"]["resolved_scenario"]
            for rid in (request.baseline_run_id, request.variant_run_id)
        ]
        result["builds"] = [
            store.get(rid)["manifest"]["build"]
            for rid in (request.baseline_run_id, request.variant_run_id)
        ]
        return result

    @app.get("/api/v1/runs/{rid}/integrity")
    def integrity(rid: str):
        return store.check_integrity(rid)

    @app.get("/api/v1/runs/{rid}/checkpoints")
    def checkpoints(rid: str):
        from .interventions import stored_checkpoints

        manifest, _ = stored_checkpoints(store, rid)
        return manifest["intervention"]

    @app.post("/api/v1/runs/{rid}/branches", status_code=201)
    def branch(rid: str, request: InterventionBranch):
        from .interventions import continuation_scenario

        scenario = continuation_scenario(
            store,
            rid,
            request.checkpoint_id,
            request.action,
            [s.model_dump() for s in request.future_states]
            if request.future_states
            else None,
            request.title,
        )
        validate(scenario)
        record = store.create_scenario(scenario)
        return {**record, "scenario": scenario.model_dump(), "parent_run_id": rid}

    @app.get(
        "/api/v1/runs/{rid}/events",
        responses={
            200: {
                "content": {
                    "text/event-stream": {
                        "schema": {
                            "type": "string",
                            "x-event-schema": {"$ref": "#/components/schemas/JobEvent"},
                        }
                    }
                }
            }
        },
    )
    async def events(
        rid: str, request: Request, last_event_id: str = Header(default="0")
    ):
        record = store.get(rid)
        try:
            after = int(last_event_id)
            if after < 0:
                raise ValueError()
        except ValueError:
            raise HTTPException(422, "Last-Event-ID must be a nonnegative integer")

        async def stream():
            nonlocal after
            while True:
                window = await asyncio.to_thread(store.event_window, rid, after)
                if window["reset_reason"]:
                    after = window["latest"]
                    reset = JobEvent(
                        sequence=after,
                        timestamp=now(),
                        type="reset",
                        run_id=rid,
                        scenario_id=record["scenario_id"],
                        scenario_revision=record["scenario_revision"],
                        reason=window["reset_reason"],
                        status_route=f"/api/v1/runs/{rid}",
                    )
                    yield f"id: {after}\nevent: reset\ndata: {reset.model_dump_json(exclude_none=True)}\n\n"
                batch = await asyncio.to_thread(store.events, rid, after)
                if batch and batch[0]["sequence"] > after + 1:
                    # Retention may advance between the window and batch reads.
                    continue
                for event in batch:
                    after = event["sequence"]
                    envelope = JobEvent.model_validate(
                        {
                            **event["payload"],
                            "sequence": after,
                            "timestamp": event["timestamp"],
                            "type": event["type"],
                        }
                    )
                    yield f"id: {after}\nevent: {event['type']}\ndata: {envelope.model_dump_json(exclude_none=True)}\n\n"
                state = await asyncio.to_thread(store.get, rid)
                window = await asyncio.to_thread(store.event_window, rid, after)
                if state["state"] in TERMINAL and window["latest"] <= after:
                    break
                if await request.is_disconnected():
                    break
                if not batch:
                    yield ": keepalive\n\n"
                if not batch:
                    await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/runs/{rid}/artifacts/{artifact_id}")
    def artifact(rid: str, artifact_id: str):
        record = store.get(rid)
        if record["state"] != "completed":
            raise KeyError(artifact_id)
        for descriptor in record["manifest"]["artifacts"]:
            if descriptor["id"] == artifact_id:
                path = store.artifact_path(rid) / descriptor["filename"]
                if (
                    path.name != descriptor["filename"]
                    or path.is_symlink()
                    or not path.is_file()
                    or hashlib.sha256(path.read_bytes()).hexdigest()
                    != descriptor["sha256"]
                ):
                    raise HTTPException(
                        409, "ARTIFACT_INVALID: artifact is missing or corrupt"
                    )
                return FileResponse(
                    path,
                    filename=descriptor["filename"],
                    media_type=descriptor["media_type"],
                )
        raise KeyError(artifact_id)

    @app.get("/api/v1/runs/{rid}/export")
    def export(rid: str):
        try:
            bundle = export_bundle(store, rid)
        except (ValueError, OSError) as error:
            raise HTTPException(409, "ARTIFACT_INVALID: " + str(error)) from error
        return Response(
            bundle,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{rid}.zip"'},
        )

    @app.post("/api/v1/imports")
    async def imports(request: Request):
        data = await request.body()
        try:
            if request.headers.get("content-type", "").startswith("application/zip"):
                return await asyncio.to_thread(inspect_bundle, data)
            imported = parse_scenario_json(data)
            warnings = validate(imported)
            return {
                "kind": "scenario",
                "scenario": imported.model_dump(),
                "warnings": warnings,
                "source_sha256": hashlib.sha256(data).hexdigest(),
            }
        except (
            ValueError,
            KeyError,
            TypeError,
            ValidationError,
            OSError,
            zipfile.BadZipFile,
        ) as error:
            raise HTTPException(
                422, "ARTIFACT_INVALID: " + str(error)[:2000]
            ) from error

    @app.post("/api/v1/comparisons")
    def comparison(request: Comparison):
        import numpy as np

        first, second = (
            store.get(rid) for rid in (request.baseline_run_id, request.variant_run_id)
        )
        if first["state"] != "completed" or second["state"] != "completed":
            raise HTTPException(
                422, "Comparisons require accepted/qualified completed runs"
            )
        a, b = first["manifest"], second["manifest"]
        if a["mode"] != "circulation_state" or b["mode"] != "circulation_state":
            raise HTTPException(
                422,
                "CAPABILITY_UNSUPPORTED: circulation difference view requires circulation-state runs",
            )
        if a["pressure_reference"] != b["pressure_reference"]:
            raise HTTPException(422, "PRESSURE_BASIS_MISMATCH")
        pa, pb = a["profile"], b["profile"]
        end = min(pa["md_m"][-1], pb["md_m"][-1])
        # v0.1 refuses topology changes to avoid interpolation across discontinuities.
        if (
            a["resolved_scenario"]["definition"]["well"]
            != b["resolved_scenario"]["definition"]["well"]
        ):
            raise HTTPException(
                422,
                "CAPABILITY_UNSUPPORTED: difference view currently requires identical geometry; absolute overlays remain available",
            )
        md = np.unique(np.r_[pa["md_m"], pb["md_m"]])
        md = md[md <= end]
        differences = {
            key: (
                np.interp(md, pb["md_m"], pb[key]) - np.interp(md, pa["md_m"], pa[key])
            ).tolist()
            for key in (
                "bore_temperature_C",
                "annulus_temperature_C",
                "annulus_wall_pressure_gauge_Pa",
            )
        }

        def diff(x, y, path=""):
            if isinstance(x, dict) and isinstance(y, dict):
                return sum(
                    (
                        diff(x.get(k), y.get(k), path + "/" + k)
                        for k in sorted(x.keys() | y.keys())
                    ),
                    [],
                )
            return [] if x == y else [{"path": path, "baseline": x, "variant": y}]

        return {
            "alignment": "common_md",
            "difference": "variant minus baseline",
            "md_m": md.tolist(),
            "fields": differences,
            "input_diff": diff(a["resolved_scenario"], b["resolved_scenario"]),
            "scalar_differences": {
                key: b["summary"][key]["value"] - value["value"]
                for key, value in a["summary"].items()
            },
            "builds": [a["build"], b["build"]],
            "pressure_reference": a["pressure_reference"],
        }

    if (PACKAGE / "static" / "index.html").is_file():
        app.mount(
            "/", StaticFiles(directory=PACKAGE / "static", html=True), name="frontend"
        )
    else:

        @app.get("/", include_in_schema=False)
        def no_ui():
            return {"message": "Build frontend to enable the UI", "docs": "/docs"}

    def openapi():
        if app.openapi_schema is None:
            result = get_openapi(
                title=app.title, version=app.version, routes=app.routes
            )
            event = JobEvent.model_json_schema(
                ref_template="#/components/schemas/{model}"
            )
            result.setdefault("components", {}).setdefault("schemas", {}).update(
                event.pop("$defs", {})
            )
            result["components"]["schemas"]["JobEvent"] = event
            app.openapi_schema = result
        return app.openapi_schema

    app.openapi = openapi
    return app
