"""Frozen bounded circulation batches, independent cold starts and scalar summaries."""

import copy
import hashlib
import io
import itertools
import json
import zipfile

from .adapter import CalculationError, validate, physics_record, make_geometry
from .contracts import Scenario, canonical_bytes, digest, physical_hash
from .store import Conflict, TERMINAL, new_id, now

PARAMETERS = {
    "mass_flow_kg_s": ("kg/s", ("flow", "mass_rate_kg_s")),
    "inlet_temperature_C": ("°C", ("inlet_temperature_C",)),
    "wellhead_pressure_absolute_Pa": (
        "Pa absolute",
        ("surface_pressure", "pressure_absolute_Pa"),
    ),
    "exposure_years": ("years", ("exposure", "years")),
}
BUILD_KEYS = ("core_version", "app_version", "core_commit", "libraries")


def plan(request):
    cases = []
    base = request.scenario.model_dump()
    for axis in request.axes:
        if (
            axis.parameter == "mass_flow_kg_s"
            and request.scenario.definition.operating.flow.kind != "mass"
        ):
            raise CalculationError(
                "SWEEP_INVALID",
                "Mass-flow sweep requires an explicit mass-flow base; surface-volume input is not silently replaced",
            )
        if (
            axis.parameter == "exposure_years"
            and request.scenario.definition.operating.exposure.kind
            != "legacy_uniform_years"
        ):
            raise CalculationError(
                "SWEEP_INVALID",
                "Exposure-years sweep requires a legacy-uniform-years base",
            )
    for i, values in enumerate(itertools.product(*(a.values for a in request.axes))):
        raw = copy.deepcopy(base)
        op = raw["definition"]["operating"]
        coordinates = {}
        for axis, value in zip(request.axes, values):
            node = op
            path = PARAMETERS[axis.parameter][1]
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = value
            coordinates[axis.parameter] = value
        raw["numerics"]["initialisation"] = "deterministic-default-v1"
        raw["title"] = (request.title + " · case " + str(i + 1))[:200]
        try:
            scenario = Scenario.model_validate(raw)
            warnings = validate(scenario)
        except (ValueError, CalculationError) as error:
            raise CalculationError(
                "SWEEP_INVALID",
                f"Case {i + 1} rejected before creating any jobs: {error}",
            ) from error
        cases.append(
            dict(
                index=i,
                parameters=coordinates,
                scenario=scenario.model_dump(),
                physical_request_hash=physical_hash(scenario),
                warnings=warnings,
            )
        )
    if len(canonical_bytes(cases)) > 5 * 1024**2:
        raise CalculationError(
            "RESOURCE_LIMIT",
            "Frozen sweep cases exceed the 5 MiB interactive plan budget",
        )
    return dict(
        schema_version="sweep-plan-1",
        physics=request.scenario.model.physics,
        physics_settings=physics_record(
            request.scenario, geometry=make_geometry(request.scenario)
        ),
        title=request.title,
        axes=[
            dict(
                parameter=a.parameter, unit=PARAMETERS[a.parameter][0], values=a.values
            )
            for a in request.axes
        ],
        case_count=len(cases),
        cases=cases,
        seed_strategy="Independent deterministic cold start per case; no traversal-direction continuation or accepted-run seed lookup",
        admission="Dedicated interactive batch, at most 25 frozen queued runs; admission requires an idle queue; one worker",
        interpretation="Computed cases only; no optimizer, probability/uncertainty band or interpolated programme status",
    )


def submit(store, request, idempotency_key, build, cache):
    frozen = plan(request)
    request_hash = digest(request.model_dump())
    with store.connection() as conn:
        if idempotency_key:
            existing = conn.execute(
                "SELECT id,request_hash FROM sweeps WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise Conflict(
                        "Sweep idempotency key was already used for different input",
                        "IDEMPOTENCY_CONFLICT",
                    )
                return existing["id"], False
        active = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE state NOT IN ('completed','failed','cancelled','timed_out')"
        ).fetchone()[0]
        if active:
            raise Conflict(
                "Wait for the local queue to become idle before admitting an interactive sweep",
                "SWEEP_QUEUE_BUSY",
            )
        store.check_storage(0, conn=conn)
        sid = new_id("sweep")
        stamp = now()
        frozen.update(
            build={k: build.get(k) for k in BUILD_KEYS},
            property_table_sha256=cache["sha256"],
            request_hash=request_hash,
        )
        conn.execute(
            "INSERT INTO sweeps VALUES (?,?,?,?,?)",
            (
                sid,
                stamp,
                request_hash,
                idempotency_key,
                canonical_bytes(frozen).decode(),
            ),
        )
        for case in frozen["cases"]:
            scenario = Scenario.model_validate(case["scenario"])
            scenario_id = new_id("scenario")
            rid = new_id("run")
            conn.execute("INSERT INTO scenarios VALUES (?,1,?)", (scenario_id, stamp))
            conn.execute(
                "INSERT INTO revisions VALUES (?,?,?,?,?)",
                (
                    scenario_id,
                    1,
                    canonical_bytes(case["scenario"]).decode(),
                    case["physical_request_hash"],
                    stamp,
                ),
            )
            conn.execute(
                "INSERT INTO runs(id,scenario_id,revision,state,created_at,updated_at,request_hash) VALUES (?,?,1,'queued',?,?,?)",
                (
                    rid,
                    scenario_id,
                    stamp,
                    stamp,
                    digest(
                        {
                            "scenario": scenario.model_dump(),
                            "scenario_id": None,
                            "revision": None,
                        }
                    ),
                ),
            )
            conn.execute(
                "INSERT INTO sweep_cases VALUES (?,?,?,?)",
                (sid, case["index"], rid, canonical_bytes(case["parameters"]).decode()),
            )
            row = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
            store.event(conn, row, "queued")
    return sid, True


def verify_execution(store, rid, build=None, cache=None):
    with store.connection(write=False) as conn:
        row = conn.execute(
            "SELECT sweeps.definition FROM sweeps JOIN sweep_cases ON sweeps.id=sweep_cases.sweep_id WHERE run_id=?",
            (rid,),
        ).fetchone()
    if row:
        if build is None or cache is None:
            from .adapter import engine_identity, cache_status

            build, cache = engine_identity(), cache_status()
        frozen = json.loads(row[0])
        if frozen["build"] != {k: build.get(k) for k in BUILD_KEYS} or frozen[
            "property_table_sha256"
        ] != cache.get("sha256"):
            raise CalculationError(
                "SWEEP_BUILD_CHANGED",
                "Frozen sweep build/cache identity changed; original cases are retained as failed",
            )


def list_sweeps(store):
    with store.connection(write=False) as conn:
        rows = conn.execute(
            "SELECT id,created_at,definition FROM sweeps ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
    return [
        dict(
            id=r["id"],
            created_at=r["created_at"],
            title=json.loads(r["definition"])["title"],
        )
        for r in rows
    ]


def read(store, sid, baseline_index=0):
    with store.connection(write=False) as conn:
        row = conn.execute("SELECT * FROM sweeps WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise KeyError(sid)
        definition = json.loads(row["definition"])
        members = conn.execute(
            "SELECT * FROM sweep_cases WHERE sweep_id=? ORDER BY case_index", (sid,)
        ).fetchall()
    cases = []
    for member in members:
        run = store.get(member["run_id"])
        m = run["manifest"] or {}
        integrity = "not_applicable"
        accepted = run["state"] == "completed" and m.get("acceptance") in (
            "accepted",
            "qualified",
        )
        if accepted:
            integrity = store.check_integrity(run["id"])["status"]
            accepted = integrity == "verified"
        cases.append(
            dict(
                index=member["case_index"],
                run_id=run["id"],
                parameters=json.loads(member["coordinates"]),
                state=run["state"],
                accepted=accepted,
                acceptance=m.get("acceptance"),
                validity=m.get("validity"),
                integrity=integrity,
                physics=m.get("physics"),
                physics_settings=m.get("physics_settings"),
                summary=m.get("summary") if accepted else None,
                warnings=m.get("warnings", []),
                failure=m.get("failure") if not accepted else None,
                failure_kind="ARTIFACT_INVALID"
                if integrity == "invalid"
                else run.get("failure_kind"),
            )
        )
    if not 0 <= baseline_index < len(cases):
        raise CalculationError(
            "SWEEP_INVALID", "Baseline must select an existing case index"
        )
    baseline = cases[baseline_index]
    ranges = {}
    for case in cases:
        deltas = {}
        for key, value in (case["summary"] or {}).items():
            ref = (baseline["summary"] or {}).get(key)
            if ref and all(ref.get(k) == value.get(k) for k in ("unit", "basis")):
                deltas[key] = dict(
                    value=value["value"] - ref["value"],
                    unit=value["unit"],
                    basis=value["basis"],
                )
            ranges.setdefault(key, []).append(value["value"])
        case["differences_from_baseline"] = deltas
    counts = {
        state: sum(c["state"] == state for c in cases)
        for state in sorted({c["state"] for c in cases})
    }
    return dict(
        schema_version="sweep-result-1",
        id=sid,
        created_at=row["created_at"],
        title=definition["title"],
        state="finished" if all(c["state"] in TERMINAL for c in cases) else "running",
        total_cases=len(cases),
        accepted_cases=sum(c["accepted"] for c in cases),
        state_counts=counts,
        baseline_index=baseline_index,
        axes=definition["axes"],
        cases=cases,
        scalar_ranges={
            k: dict(minimum=min(v), maximum=max(v), computed_cases=len(v))
            for k, v in ranges.items()
        },
        seed_strategy=definition["seed_strategy"],
        admission=definition["admission"],
        interpretation=definition["interpretation"],
        physics=definition["physics"],
        physics_settings=definition["physics_settings"],
        build=definition["build"],
        property_table_sha256=definition["property_table_sha256"],
        request_hash=row["request_hash"],
    )


def cancel(store, sid):
    with store.connection() as conn:
        if conn.execute("SELECT id FROM sweeps WHERE id=?", (sid,)).fetchone() is None:
            raise KeyError(sid)
        rows = conn.execute(
            "SELECT runs.* FROM runs JOIN sweep_cases ON runs.id=sweep_cases.run_id WHERE sweep_id=?",
            (sid,),
        ).fetchall()
        for row in rows:
            if row["state"] not in TERMINAL and row["state"] != "cancel_requested":
                conn.execute(
                    "UPDATE runs SET state='cancel_requested',updated_at=? WHERE id=?",
                    (now(), row["id"]),
                )
                store.event(conn, row, "cancel_requested")


def export(store, sid):
    from .bundles import export_bundle

    result = read(store, sid)
    if result["state"] != "finished":
        raise Conflict(
            "Wait for all cases to finish or cancel before exporting a sweep"
        )
    with store.connection(write=False) as conn:
        definition = json.loads(
            conn.execute("SELECT definition FROM sweeps WHERE id=?", (sid,)).fetchone()[
                0
            ]
        )
        size = (
            conn.execute(
                "SELECT SUM(artifact_bytes) FROM runs JOIN sweep_cases ON runs.id=sweep_cases.run_id WHERE sweep_id=?",
                (sid,),
            ).fetchone()[0]
            or 0
        )
    if (
        size + len(canonical_bytes(definition)) + len(canonical_bytes(result))
        > 100 * 1024**2
    ):
        raise CalculationError(
            "RESOURCE_LIMIT",
            "Sweep export exceeds 100 MiB; download individual accepted runs",
        )
    buffer = io.BytesIO()
    files = {
        "sweep.json": canonical_bytes(result),
        "frozen-plan.json": canonical_bytes(definition),
    }
    for case in result["cases"]:
        if case["accepted"]:
            files[f"case-{case['index'] + 1}.zip"] = export_bundle(
                store, case["run_id"]
            )
    files["README.md"] = (
        b"Frozen computed cases and summaries. Failed/cancelled cases remain in sweep.json with no fabricated field. Reproduce each accepted case ZIP using the matching wheel pair. This batch archive is not a single-run import bundle. SHA256SUMS.json establishes internal consistency, not publisher authenticity.\n"
    )
    files["SHA256SUMS.json"] = canonical_bytes(
        {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}
    )
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()
