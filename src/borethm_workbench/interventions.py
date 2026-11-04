"""Experimental fixed-domain event continuation using the unchanged wall kernel.

Only accepted event boundaries are checkpoints. No in-event integrator restart,
circulation feedback, moving tool or remapping is implemented.
"""

import copy
import json
import math

import numpy as np

from .contracts import HistoryDataset, canonical_bytes, digest

BUILD_KEYS = (
    "core_version",
    "core_commit",
    "adapter_version",
    "app_version",
    "libraries",
)


class CheckpointError(ValueError):
    code = "CHECKPOINT_INCOMPATIBLE"


def fail(message):
    raise CheckpointError("CHECKPOINT_INCOMPATIBLE: " + message)


def checkpoint_digest(value):
    """JSON numbers retain their value when browsers rewrite 1.0 as 1.

    Checkpoint dictionaries are data-only, so normalize numeric representation
    for hashing rather than relying on Python's int/float spelling.
    """

    def numbers(item):
        if isinstance(item, dict):
            return {k: numbers(v) for k, v in item.items()}
        if isinstance(item, list):
            return [numbers(v) for v in item]
        if type(item) in (int, float):
            return float(item)
        return item

    return digest(numbers(value))


def geometry(scenario):
    return {
        "columns": [
            c.model_dump(exclude={"boundary_overrides"})
            for c in scenario.definition.columns
        ],
        "trajectory": scenario.definition.trajectory.model_dump()
        if scenario.definition.trajectory
        else None,
        "fluid_family": scenario.intervention.fluid_family,
        "maximum_time_s": scenario.intervention.maximum_time_s,
    }


def prefix(scenario, end):
    states = scenario.definition.states[:end]
    ids = {s.id for s in states}
    return {
        "states": [s.model_dump() for s in states],
        "overrides": {
            c.id: {k: v for k, v in c.boundary_overrides.items() if k in ids}
            for c in scenario.definition.columns
        },
    }


def effective(scenario, column, index):
    state = scenario.definition.states[index]
    return type(state).model_validate(
        {**state.model_dump(), **column.boundary_overrides.get(state.id, {})}
    )


def capacity(scenario, column):
    values = [
        effective(scenario, column, i).fluid_capacity_J_m_K
        for i, s in enumerate(scenario.definition.states)
        if s.kind == "static"
    ]
    return values[0] if values else None


def validate_checkpoint(scenario, cp, require_future=True):
    from borethm.wall_thermal import Grid

    if not isinstance(cp, dict) or cp.get("schema_version") != "wall-checkpoint-1":
        fail("unsupported checkpoint schema")
    if cp.get("sha256") != checkpoint_digest(
        {k: v for k, v in cp.items() if k != "sha256"}
    ):
        fail("checkpoint checksum mismatch")
    end = cp.get("next_event_index")
    if (
        type(end) is not int
        or not 0 < end <= len(scenario.definition.states)
        or (require_future and end == len(scenario.definition.states))
    ):
        fail("select a completed event with at least one future event")
    if cp.get("geometry_hash") != digest(geometry(scenario)) or cp.get(
        "numerics_hash"
    ) != digest(scenario.numerics.model_dump()):
        fail("geometry, fixed horizon, material or numerical policy changed")
    if cp.get("history_prefix") != prefix(scenario, end):
        fail("historical states or local overrides changed")
    expected_time = sum(s.duration_s for s in scenario.definition.states[:end])
    if (
        cp.get("time_s") != expected_time
        or cp.get("elapsed_event_s") != scenario.definition.states[end - 1].duration_s
    ):
        fail("checkpoint event time/index mismatch")
    controller = {
        "kind": "event_boundary_reset",
        "initial_step_s": scenario.numerics.initial_step_s,
        "step_growth": scenario.numerics.step_growth,
        "next_event_index": end,
    }
    if (
        cp.get("controller") != controller
        or cp.get("tool") is not None
        or cp.get("circulation_dependency") is not None
    ):
        fail("unsupported controller, tool or circulation dependency")
    if not isinstance(cp.get("build"), dict) or set(cp["build"]) != set(BUILD_KEYS):
        fail("checkpoint build identity missing")
    if not isinstance(cp.get("columns"), dict) or set(cp["columns"]) != {
        c.id for c in scenario.definition.columns
    }:
        fail("missing/reordered column state")
    previous_kind = scenario.definition.states[end - 1].kind
    for col in scenario.definition.columns:
        state = cp["columns"][col.id]
        if not isinstance(state, dict):
            fail("column state must be data")
        try:
            r, _, vol = Grid(
                col.wall_radius_m, col.outer_radius_m, scenario.numerics.radial_nodes
            ).build(state.get("radius_m", []))
        except (TypeError, ValueError):
            fail(
                "stored grid is missing or differs from the declared log grid beyond the 8-ULP portability bound"
            )
        reference = (
            col.initial_profile_C
            if col.initial_profile_C is not None
            else [col.initial_rock_temperature_C] * len(r)
        )
        field = state.get("rock_temperature_C", [])
        if (
            not isinstance(field, list)
            or state.get("reference_temperature_C") != reference
            or len(field) != len(r)
            or not all(type(x) in (float, int) and math.isfinite(x) for x in field)
            or state.get("fluid_capacity_J_m_K") != capacity(scenario, col)
            or state.get("previous_kind") != previous_kind
        ):
            fail("grid, reference field, capacity or boundary memory mismatch")
        node = state.get("fluid_node_temperature_C")
        if previous_kind == "static" and (
            type(node) not in (float, int) or not math.isfinite(node)
        ):
            fail("static-node temperature missing")
        if previous_kind == "circulating":
            if (
                node is not None
                or state.get("last_circulating_temperature_C")
                != effective(scenario, col, end - 1).fluid_temperature_C
            ):
                fail("circulating-boundary memory mismatch")
        last_circulating = next(
            (
                effective(scenario, col, i).fluid_temperature_C
                for i in range(end - 1, -1, -1)
                if scenario.definition.states[i].kind == "circulating"
            ),
            None,
        )
        if state.get("last_circulating_temperature_C") != last_circulating:
            fail("previous circulating boundary differs from history")
        stored = float(
            np.sum(
                (col.volumetric_heat_capacity_J_m3_K * vol)[:-1]
                * (np.asarray(field) - reference)[:-1]
            )
        )
        energies = [
            state.get(k) for k in ("energy_in_J_m", "energy_far_J_m", "stored_J_m")
        ]
        if any(type(x) not in (float, int) or not math.isfinite(x) for x in energies):
            fail("accumulated energy missing")
        tolerance = max(1.0, 1.0e-6 * max(map(abs, energies)))
        if (
            abs(stored - energies[2]) > tolerance
            or abs(energies[0] - energies[1] - stored) > tolerance
        ):
            fail("checkpoint energy/state inconsistency")
    canonical_bytes(cp)
    return cp


def validate_intervention(scenario):
    from borethm.wall_thermal import r_max_for

    p = scenario.intervention
    states = scenario.definition.states
    if sum(s.duration_s for s in states) > p.maximum_time_s:
        fail("schedule exceeds the declared fixed-domain maximum time")
    if any(s.kind not in ("circulating", "static") for s in states):
        fail(
            "Scheduled histories support prescribed circulating/static boundaries only"
        )
    if p.stop_after_event_id is not None and p.stop_after_event_id not in {
        s.id for s in states[:-1]
    }:
        fail("pause must select an event before the final event")
    for col in scenario.definition.columns:
        required = r_max_for(
            col.wall_radius_m,
            col.conductivity_W_m_K / col.volumetric_heat_capacity_J_m3_K,
            p.maximum_time_s,
        )
        if col.outer_radius_m is None or col.outer_radius_m < required:
            fail(
                f"{col.id}: explicitly declare outer_radius_m >= {required:g} m for the complete horizon"
            )
        capacities = {
            effective(scenario, col, i).fluid_capacity_J_m_K
            for i, s in enumerate(states)
            if s.kind == "static"
        }
        if len(capacities) > 1:
            fail("static-fluid capacity must remain fixed within a column")
        for i, state in enumerate(states):
            if (
                state.kind == "static"
                and i
                and effective(scenario, col, i).initial_fluid_temperature_C is not None
            ):
                fail(
                    "future static states carry node/boundary memory; reinitialization is forbidden"
                )
    if p.continuation:
        record = p.continuation
        if set(record) != {"action", "parent_run_id", "checkpoint"} or record[
            "action"
        ] not in ("resume", "branch"):
            fail("invalid continuation record")
        if not isinstance(record["parent_run_id"], str) or not record[
            "parent_run_id"
        ].startswith("run_"):
            fail("parent run identity missing")
        cp = validate_checkpoint(scenario, record["checkpoint"])
        if (
            p.stop_after_event_id
            and next(i for i, s in enumerate(states) if s.id == p.stop_after_event_id)
            < cp["next_event_index"]
        ):
            fail("pause must occur after the selected checkpoint")


def schedule_identity(scenario):
    value = scenario.model_dump(exclude={"title", "provenance", "metadata"})
    value["intervention"] = {
        k: v
        for k, v in value["intervention"].items()
        if k not in ("continuation", "stop_after_event_id")
    }
    return digest(value)


def run_intervention(scenario, identity, notify=lambda *_: None, stored_grids=None):
    from borethm import wall_thermal as core
    from .histories import snapshots, wall_states

    p, n = scenario.intervention, scenario.numerics
    cp = p.continuation["checkpoint"] if p.continuation else None
    if cp and cp["build"] != {k: identity.get(k) for k in BUILD_KEYS}:
        fail(
            "checkpoint core/app/dependency build differs; use its matching frozen distribution"
        )
    start = cp["next_event_index"] if cp else 0
    stop = next(
        (
            i + 1
            for i, s in enumerate(scenario.definition.states)
            if s.id == p.stop_after_event_id
        ),
        len(scenario.definition.states),
    )
    time_origin = cp["time_s"] if cp else 0.0
    memory = copy.deepcopy(cp["columns"]) if cp else {}
    data, checkpoints = {}, []
    for col in scenario.definition.columns:
        authoritative = (
            cp["columns"][col.id]["radius_m"]
            if cp
            else (stored_grids or {}).get(col.id)
        )
        r, _, _ = core.Grid(
            col.wall_radius_m, col.outer_radius_m, n.radial_nodes
        ).build(authoritative)
        reference = (
            col.initial_profile_C
            if col.initial_profile_C is not None
            else [col.initial_rock_temperature_C] * len(r)
        )
        if not cp:
            memory[col.id] = dict(
                radius_m=r.tolist(),
                rock_temperature_C=list(reference),
                reference_temperature_C=list(reference),
                fluid_node_temperature_C=None,
                fluid_capacity_J_m_K=capacity(scenario, col),
                previous_kind=None,
                last_circulating_temperature_C=None,
                energy_in_J_m=0.0,
                energy_far_J_m=0.0,
                stored_J_m=0.0,
            )
        data[col.id] = dict(
            schema_version="history-data-1",
            kind="wall",
            id=col.id,
            time_origin_s=time_origin,
            **{
                k: []
                for k in (
                    "time_s",
                    "state_index",
                    "state_id",
                    "state_label",
                    "event_side",
                    "sample_kind",
                    "md_m",
                    "rock_temperature_C",
                    "wall_temperature_C",
                    "fluid_node_temperature_C",
                    "wall_flux_W_m",
                    "energy_in_J_m",
                    "energy_far_J_m",
                    "stored_J_m",
                )
            },
            grid={
                "radius_m": r.tolist(),
                "sha256": digest({"radius_m": r.tolist()}),
                "radial_scale": "actual metres",
                "domain_policy": "Explicit fixed history horizon; no remeshing",
            },
            units={
                "time_s": "s",
                "md_m": "m MD",
                "radius_m": "m",
                "temperature_C": "°C",
                "wall_flux_W_m": "W/m into rock",
                "energy": "J/m",
            },
            events=[],
            statistics={
                "event_energy": [],
                "external_reservoir_inventory_changes_J_m": [],
            },
        )
    t = time_origin
    for event_index in range(start, stop):
        for col in scenario.definition.columns:
            mem, d = memory[col.id], data[col.id]
            state = wall_states(scenario, col)[event_index]
            initial_node = None
            if state.kind == "static":
                initial_node = (
                    mem["fluid_node_temperature_C"]
                    if mem["previous_kind"] == "static"
                    else mem["last_circulating_temperature_C"]
                    if mem["last_circulating_temperature_C"] is not None
                    else state.T_node0
                )
                state.T_node0 = initial_node
            elif mem["previous_kind"] == "static":
                d["statistics"]["external_reservoir_inventory_changes_J_m"].append(
                    dict(
                        time_s=t,
                        change_J_m=mem["fluid_capacity_J_m_K"]
                        * (state.T_fluid - mem["fluid_node_temperature_C"]),
                        basis="Prescribed reservoir replaces static inventory; separate from rock-wall energy",
                    )
                )
            frames = []
            raw = core.run(
                [state],
                col.far_field_temperature_C,
                col.wall_radius_m,
                col.conductivity_W_m_K,
                col.volumetric_heat_capacity_J_m3_K,
                n=n.radial_nodes,
                r_max=col.outer_radius_m,
                T0=mem["rock_temperature_C"],
                dt0=n.initial_step_s,
                growth=n.step_growth,
                observe=frames.append,
                radius_m=mem["radius_m"],
            )
            _, _, volumes = core.Grid(
                col.wall_radius_m, col.outer_radius_m, n.radial_nodes
            ).build(mem["radius_m"])
            caps = col.volumetric_heat_capacity_J_m3_K * volumes
            for j, frame in enumerate(frames):
                absolute = t + frame["time_s"]
                if j == len(frames) - 1:
                    absolute = sum(
                        s.duration_s
                        for s in scenario.definition.states[: event_index + 1]
                    )
                row = dict(
                    time_s=absolute,
                    state_index=event_index - start,
                    state_id=scenario.definition.states[event_index].id,
                    state_label=state.label,
                    event_side=frame["event_side"],
                    sample_kind=(
                        "initial" if event_index == start else "event_boundary"
                    )
                    if j == 0
                    else "accepted_step",
                    md_m=col.md_m,
                    rock_temperature_C=frame["temperature_C"].tolist(),
                    wall_temperature_C=frame["wall_temperature_C"],
                    fluid_node_temperature_C=frame["fluid_node_temperature_C"],
                    wall_flux_W_m=frame["wall_flux_W_m"],
                    energy_in_J_m=mem["energy_in_J_m"] + frame["energy_in_J_m"],
                    energy_far_J_m=mem["energy_far_J_m"] + frame["energy_far_J_m"],
                    stored_J_m=float(
                        np.sum(
                            caps[:-1]
                            * (frame["temperature_C"] - mem["reference_temperature_C"])[
                                :-1
                            ]
                        )
                    ),
                )
                for key, value in row.items():
                    d[key].append(value)
            right = len(d["time_s"]) - len(frames)
            d["events"].append(
                dict(
                    time_s=t,
                    state_index=event_index - start,
                    state_id=scenario.definition.states[event_index].id,
                    label=state.label,
                    left_index=right - 1 if right else None,
                    right_index=right,
                )
            )
            static_residual = (
                None
                if initial_node is None
                else raw.energy_in
                + state.C * (frames[-1]["fluid_node_temperature_C"] - initial_node)
            )
            tolerance = max(
                1.0,
                1.0e-6 * max(abs(raw.energy_in), abs(raw.energy_far), abs(raw.stored)),
            )
            d["statistics"]["event_energy"].append(
                dict(
                    event_id=scenario.definition.states[event_index].id,
                    wall_J_m=raw.energy_in,
                    far_J_m=raw.energy_far,
                    rock_change_J_m=raw.stored,
                    rock_residual_J_m=raw.energy_in - raw.energy_far - raw.stored,
                    static_node_plus_wall_residual_J_m=static_residual,
                    tolerance_J_m=tolerance,
                    passed=bool(
                        abs(raw.energy_in - raw.energy_far - raw.stored) <= tolerance
                        and (
                            static_residual is None or abs(static_residual) <= tolerance
                        )
                    ),
                )
            )
            mem.update(
                rock_temperature_C=frames[-1]["temperature_C"].tolist(),
                fluid_node_temperature_C=frames[-1]["fluid_node_temperature_C"],
                previous_kind=state.kind,
                energy_in_J_m=d["energy_in_J_m"][-1],
                energy_far_J_m=d["energy_far_J_m"][-1],
                stored_J_m=d["stored_J_m"][-1],
            )
            if state.kind == "circulating":
                mem["last_circulating_temperature_C"] = state.T_fluid
        t = sum(s.duration_s for s in scenario.definition.states[: event_index + 1])
        checkpoint = dict(
            schema_version="wall-checkpoint-1",
            id="event-" + str(event_index + 1),
            build={k: identity.get(k) for k in BUILD_KEYS},
            geometry_hash=digest(geometry(scenario)),
            numerics_hash=digest(n.model_dump()),
            schedule_hash=schedule_identity(scenario),
            time_s=t,
            next_event_index=event_index + 1,
            elapsed_event_s=scenario.definition.states[event_index].duration_s,
            history_prefix=prefix(scenario, event_index + 1),
            columns=copy.deepcopy(memory),
            controller={
                "kind": "event_boundary_reset",
                "initial_step_s": n.initial_step_s,
                "step_growth": n.step_growth,
                "next_event_index": event_index + 1,
            },
            tool=None,
            circulation_dependency=None,
            exposure_history="Completed event prefix at absolute physical time; no equivalent fresh-rock age",
            parent=p.continuation
            and {
                "run_id": p.continuation["parent_run_id"],
                "checkpoint_sha256": cp["sha256"],
            },
        )
        checkpoint["sha256"] = checkpoint_digest(checkpoint)
        checkpoints.append(checkpoint)
        notify(
            "progress",
            {"completed_work": event_index - start + 1, "total_work": stop - start},
        )
    datasets = []
    for d in data.values():
        residual = (
            d["energy_in_J_m"][-1] - d["energy_far_J_m"][-1] - d["stored_J_m"][-1]
        )
        tol = max(
            1.0,
            1.0e-6
            * max(
                abs(d[k][-1]) for k in ("energy_in_J_m", "energy_far_J_m", "stored_J_m")
            ),
        )
        d["statistics"].update(
            maximum_wall_temperature_C=max(d["wall_temperature_C"]),
            minimum_wall_temperature_C=min(d["wall_temperature_C"]),
            energy_residual_J_m=residual,
            energy_tolerance_J_m=tol,
            energy_passed=abs(residual) <= tol
            and all(e["passed"] for e in d["statistics"]["event_energy"]),
            initial_state="Complete carried checkpoint"
            if cp
            else "Declared initial field",
            checkpoint_resume=True,
        )
        d["snapshots"] = snapshots(d)
        datasets.append(HistoryDataset.model_validate(d).model_dump(exclude_none=True))
    return (
        datasets,
        checkpoints,
        dict(
            profile=p.profile,
            status="paused_at_event"
            if stop < len(scenario.definition.states)
            else "finished",
            time_origin_s=time_origin,
            time_end_s=t,
            schedule_hash=schedule_identity(scenario),
            parent=p.continuation
            and {
                "run_id": p.continuation["parent_run_id"],
                "action": p.continuation["action"],
                "checkpoint_id": cp["id"],
                "checkpoint_sha256": cp["sha256"],
            },
            checkpoints=[
                {
                    "id": c["id"],
                    "time_s": c["time_s"],
                    "next_event_index": c["next_event_index"],
                    "sha256": c["sha256"],
                    "has_future": c["next_event_index"]
                    < len(scenario.definition.states),
                }
                for c in checkpoints
            ],
            qualification="Prescribed local boundaries, fixed domain, no circulation feedback or pressure dynamics; results have not been compared with field measurements",
        ),
    )


def stored_checkpoints(store, rid):
    run = store.get(rid)
    if run["state"] != "completed" or not run["manifest"].get("intervention"):
        fail("a completed scheduled history is required")
    if store.check_integrity(rid)["status"] != "verified":
        fail("parent artifacts failed integrity")
    descriptor = next(
        a for a in run["manifest"]["artifacts"] if a["filename"] == "checkpoints.json"
    )
    return run["manifest"], json.loads(
        (store.artifact_path(rid) / descriptor["filename"]).read_bytes()
    )


def continuation_scenario(
    store, rid, checkpoint_id, action, future_states=None, title=None
):
    from .contracts import parse_scenario

    manifest, checkpoints = stored_checkpoints(store, rid)
    checkpoint = next((c for c in checkpoints if c["id"] == checkpoint_id), None)
    if checkpoint is None:
        fail("select an available exact event checkpoint")
    scenario = copy.deepcopy(manifest["resolved_scenario"])
    end = checkpoint["next_event_index"]
    if action == "branch":
        if not future_states:
            fail("a branch requires explicit future states")
        scenario["definition"]["states"] = (
            scenario["definition"]["states"][:end] + future_states
        )
        allowed = {s["id"] for s in scenario["definition"]["states"]}
        for col in scenario["definition"]["columns"]:
            col["boundary_overrides"] = {
                k: v for k, v in col["boundary_overrides"].items() if k in allowed
            }
    elif action != "resume":
        fail("action must be resume or branch")
    elif future_states is not None:
        fail("resume cannot change the physical schedule")
    scenario["title"] = title or manifest["title"] + (
        " · branch" if action == "branch" else " · resume"
    )
    scenario["intervention"].update(
        stop_after_event_id=None,
        continuation={"action": action, "parent_run_id": rid, "checkpoint": checkpoint},
    )
    return parse_scenario(scenario)


def verify_stored_continuation(store, scenario):
    if (
        scenario.mode != "wall_history"
        or not scenario.intervention
        or not scenario.intervention.continuation
    ):
        return
    c = scenario.intervention.continuation
    manifest, checkpoints = stored_checkpoints(store, c["parent_run_id"])
    if c["checkpoint"] not in checkpoints:
        fail("continuation must use an unchanged server-produced checkpoint")
    if c["action"] == "resume":
        from .contracts import parse_scenario

        if schedule_identity(scenario) != schedule_identity(
            parse_scenario(manifest["resolved_scenario"])
        ):
            fail("resume must retain the full original schedule")


def validate_bundle_checkpoints(manifest, scenario, files, datasets):
    if not manifest.get("intervention"):
        return
    if not scenario.intervention:
        fail("intervention manifest/scenario mismatch")
    records = json.loads(files["checkpoints.json"])
    expected = manifest["intervention"]["checkpoints"]
    if [c["id"] for c in records] != [c["id"] for c in expected]:
        fail("checkpoint index mismatch")
    for cp, entry in zip(records, expected):
        # Final checkpoints are retained for audit, but cannot launch an empty continuation.
        if (
            cp["sha256"]
            != checkpoint_digest({k: v for k, v in cp.items() if k != "sha256"})
            or cp["sha256"] != entry["sha256"]
        ):
            fail("checkpoint artifact checksum/index mismatch")
        validate_checkpoint(scenario, cp, require_future=False)
        if cp["build"] != {k: manifest["build"].get(k) for k in BUILD_KEYS}:
            fail("checkpoint build differs from manifest")
        for d in datasets:
            matches = [
                i
                for i, t in enumerate(d["time_s"])
                if t == cp["time_s"] and d["event_side"][i] == "left"
            ]
            if not matches:
                fail("checkpoint is not an accepted event-end sample")
            i = matches[-1]
            state = cp["columns"][d["id"]]
            if state["radius_m"] != d["grid"]["radius_m"]:
                fail("checkpoint grid differs from authoritative stored history grid")
            for key in (
                "rock_temperature_C",
                "fluid_node_temperature_C",
                "energy_in_J_m",
                "energy_far_J_m",
                "stored_J_m",
            ):
                if state[key] != d[key][i]:
                    fail("checkpoint field/energy differs from numerical history")
    return records
