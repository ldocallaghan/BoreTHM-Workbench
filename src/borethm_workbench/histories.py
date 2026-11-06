import math
import time

import numpy as np

from .adapter import (
    CalculationError,
    engine_identity,
    warning,
    physics_record,
    loading_warnings,
)
from .contracts import HistoryDataset, canonical_bytes, digest, physical_hash

MAX_SAMPLES = 20000
MAX_FIELD_VALUES = 2_000_000


def step_count(duration, dt0, growth):
    # Bounded count of the existing core strategy, before it allocates a list.
    t, dt, count = 0.0, dt0, 0
    while t < duration - 1.0e-9:
        dt = min(dt, duration / 50.0, duration - t)
        if dt <= 0 or t + dt == t or count >= MAX_SAMPLES:
            raise CalculationError(
                "RESOURCE_LIMIT", "History timestep/sample limit exceeded"
            )
        t += dt
        count += 1
        dt *= growth
    return count


def validate_history(scenario):
    d, n = scenario.definition, scenario.numerics
    if scenario.model.rock_response != "cylinder":
        raise CalculationError(
            "CAPABILITY_UNSUPPORTED",
            "Radial histories use the declared cylindrical core domain",
        )
    if scenario.mode == "wall_history":
        for column in d.columns:
            wall_states(
                scenario, column
            )  # Validate effective, column-specific static continuity.
        samples = sum(
            step_count(s.duration_s, n.initial_step_s, n.step_growth) + 1
            for s in d.states
        )
        if (
            samples * len(d.columns) > MAX_SAMPLES
            or samples * len(d.columns) * n.radial_nodes > MAX_FIELD_VALUES
        ):
            raise CalculationError(
                "RESOURCE_LIMIT",
                "History exceeds 20,000 samples or 2,000,000 stored radial values",
            )
    else:
        depths = [0.0, *d.stage_depths_m, d.bottom_md_m]
        samples = 1 + sum(
            math.ceil((b - a) / d.speed_m_s / n.tool_step_s)
            for a, b in zip(depths, depths[1:])
        )
        samples += len(d.stage_depths_m) * math.ceil(d.stage_duration_s / 10.0)
        samples += sum(math.ceil(s.duration_s / n.tool_step_s) for s in d.stops)
        if samples > MAX_SAMPLES:
            raise CalculationError(
                "RESOURCE_LIMIT", "Tool history exceeds 20,000 numerical samples"
            )
    return loading_warnings(scenario) + [
        warning(
            "HISTORY_BOUNDARY_SCOPE",
            "Prescribed thermal histories; results have not been compared with field measurements.",
        ),
        warning(
            "PRESCRIBED_BOUNDARY",
            "Boundary films/temperatures and material capacities are prescribed; no coolant transport delay or new fluid physics is solved.",
        ),
        warning(
            "INDEPENDENT_HISTORY",
            "Independent radial columns have no axial rock conduction; the tool does not feed heat back into its prescribed surrounding column.",
        ),
    ]


def wall_states(scenario, column):
    from borethm.wall_thermal import State

    result = []
    for original in scenario.definition.states:
        state = type(original).model_validate(
            {**original.model_dump(), **column.boundary_overrides.get(original.id, {})}
        )
        args = {"kind": state.kind, "duration": state.duration_s, "label": state.label}
        if state.kind == "circulating":
            args.update(T_fluid=state.fluid_temperature_C, h=state.film_W_m2_K)
        elif state.kind == "static":
            args.update(
                h=state.film_W_m2_K,
                C=state.fluid_capacity_J_m_K,
                T_node0=state.initial_fluid_temperature_C,
            )
        elif state.kind == "fixed":
            args.update(T_wall=state.wall_temperature_C)
        else:
            args.update(q=state.flux_into_rock_W_m)
        if (
            result
            and state.kind == "static"
            and result[-1].kind == "static"
            and state.fluid_capacity_J_m_K != result[-1].C
        ):
            raise CalculationError(
                "STATIC_CAPACITY_CHANGE",
                "Consecutive static states must keep fluid capacity fixed. Use a separately defined fluid-exchange model for an inventory/capacity change.",
            )
        result.append(State(**args))
    return result


def snapshots(dataset):
    records = []
    for i, t in enumerate(dataset["time_s"]):
        thermal = (
            dataset["rock_temperature_C"][i]
            if dataset["kind"] == "wall"
            else [
                dataset["steel_temperature_C"][i],
                dataset["internal_fluid_temperature_C"][i],
            ]
        )
        records.append(
            dict(
                index=i,
                time_s=t,
                state_id=dataset["state_id"][i],
                event_side=dataset["event_side"][i],
                sample_kind=dataset["sample_kind"][i],
                source_timestep_bracket_s=[
                    dataset["time_s"][i - 1]
                    if i and dataset["sample_kind"][i] == "accepted_step"
                    else t,
                    t,
                ],
                grid_sha256=dataset["grid"]["sha256"],
                field_sha256=digest(thermal),
            )
        )
    return records


def wall_dataset(scenario, column, stored_grid=None):
    from borethm import wall_thermal as core

    frames = []
    n = scenario.numerics
    raw = core.run(
        wall_states(scenario, column),
        column.far_field_temperature_C,
        column.wall_radius_m,
        column.conductivity_W_m_K,
        column.volumetric_heat_capacity_J_m3_K,
        n=n.radial_nodes,
        r_max=column.outer_radius_m,
        T0=column.initial_profile_C
        if column.initial_profile_C is not None
        else np.full(n.radial_nodes, column.initial_rock_temperature_C),
        dt0=n.initial_step_s,
        growth=n.step_growth,
        observe=frames.append,
        carry_static_node=True,
        radius_m=stored_grid,
    )
    radii = raw.r.tolist()
    grid = {
        "radius_m": radii,
        "sha256": digest({"radius_m": radii}),
        "radial_scale": "actual metres",
        "domain_policy": "Explicit radius or core full-duration r_max_for; fixed for the complete history",
    }
    values = {
        name: [f[name] for f in frames]
        for name in (
            "time_s",
            "state_index",
            "sample_kind",
            "event_side",
            "wall_temperature_C",
            "fluid_node_temperature_C",
            "wall_flux_W_m",
            "energy_in_J_m",
            "energy_far_J_m",
            "stored_J_m",
        )
    }
    events = []
    for i, frame in enumerate(frames):
        if frame["sample_kind"] in ("initial", "event_boundary"):
            events.append(
                dict(
                    time_s=frame["time_s"],
                    state_index=frame["state_index"],
                    state_id=scenario.definition.states[frame["state_index"]].id,
                    label=scenario.definition.states[frame["state_index"]].label,
                    left_index=i - 1 if i else None,
                    right_index=i,
                )
            )
    residual = float(raw.energy_in - raw.energy_far - raw.stored)
    tolerance = float(
        max(1.0, 1.0e-6 * max(abs(raw.energy_in), abs(raw.energy_far), abs(raw.stored)))
    )
    event_energy = []
    exchanges = []
    states = wall_states(scenario, column)
    for i, state in enumerate(states):
        event_frames = [f for f in frames if f["state_index"] == i]
        first, last = event_frames[0], event_frames[-1]
        wall = last["energy_in_J_m"] - first["energy_in_J_m"]
        far = last["energy_far_J_m"] - first["energy_far_J_m"]
        stored = last["stored_J_m"] - first["stored_J_m"]
        fluid = (
            state.C
            * (last["fluid_node_temperature_C"] - first["fluid_node_temperature_C"])
            if state.kind == "static"
            else None
        )
        tol = max(1.0, 1.0e-6 * max(abs(wall), abs(far), abs(stored)))
        event_energy.append(
            dict(
                event_id=scenario.definition.states[i].id,
                rock_residual_J_m=wall - far - stored,
                static_node_plus_wall_residual_J_m=None
                if fluid is None
                else wall + fluid,
                tolerance_J_m=tol,
                passed=bool(
                    abs(wall - far - stored) <= tol
                    and (fluid is None or abs(wall + fluid) <= tol)
                ),
            )
        )
        if i and states[i - 1].kind == "static":
            previous = next(f for f in reversed(frames) if f["state_index"] == i - 1)
            target = (
                first["fluid_node_temperature_C"]
                if state.kind == "static"
                else state.T_fluid
                if state.kind == "circulating"
                else None
            )
            if target is not None and (
                state.kind == "circulating" or state.T_node0 is not None
            ):
                exchanges.append(
                    dict(
                        time_s=first["time_s"],
                        change_J_m=states[i - 1].C
                        * (target - previous["fluid_node_temperature_C"]),
                        basis="Explicit initial-fluid temperature replaces static inventory"
                        if state.kind == "static"
                        else "Prescribed circulating reservoir replaces static inventory",
                    )
                )
    data = dict(
        schema_version="history-data-1",
        kind="wall",
        id=column.id,
        **values,
        state_id=[scenario.definition.states[i].id for i in values["state_index"]],
        state_label=[
            scenario.definition.states[i].label for i in values["state_index"]
        ],
        md_m=[column.md_m] * len(frames),
        grid=grid,
        rock_temperature_C=[f["temperature_C"].tolist() for f in frames],
        units={
            "time_s": "s",
            "md_m": "m MD",
            "radius_m": "m",
            "temperature_C": "°C",
            "wall_flux_W_m": "W/m into rock",
            "energy": "J/m",
        },
        events=events,
        statistics={
            "maximum_wall_temperature_C": max(values["wall_temperature_C"]),
            "minimum_wall_temperature_C": min(values["wall_temperature_C"]),
            "energy_residual_J_m": residual,
            "energy_tolerance_J_m": tolerance,
            "energy_passed": abs(residual) <= tolerance
            and all(e["passed"] for e in event_energy),
            "event_energy": event_energy,
            "external_reservoir_inventory_changes_J_m": exchanges,
            "initial_state": "Accepted rock carried across every event; omitted static-to-static fluid temperature carries the accepted node; explicit initial temperature records replacement energy",
        },
    )
    data["snapshots"] = snapshots(data)
    return HistoryDataset.model_validate(data).model_dump(exclude_none=True)


def tool_dataset(scenario):
    from borethm import tool_thermal as core

    d = scenario.definition
    n = scenario.numerics
    l = d.lump
    lump = core.Lump(
        l.steel_capacity_J_m_K,
        l.fluid_capacity_J_m_K,
        l.inner_perimeter_m,
        l.outer_perimeter_m,
        l.inner_diameter_m,
    )
    points = np.asarray(d.column_md_m_temperature_C)
    column = lambda z: float(np.interp(z, points[:, 0], points[:, 1]))
    stage = d.staging_boundary
    circulating = (
        (
            lambda _: (
                stage.fluid_temperature_C,
                stage.inside_film_W_m2_K,
                stage.outside_film_W_m2_K,
            )
        )
        if stage
        else None
    )
    raw, md = core.trip_in(
        lump,
        column,
        d.bottom_md_m,
        d.speed_m_s,
        d.initial_fluid_temperature_C,
        d.static_outside_film_W_m2_K,
        d.static_inside_film_W_m2_K,
        stages=d.stage_depths_m,
        stage_s=d.stage_duration_s,
        circulating=circulating,
        dt=n.tool_step_s,
        retain_stage_samples=True,
        T_s0=d.initial_steel_temperature_C,
        T_f0=d.initial_fluid_temperature_C,
    )
    times, steel, fluid, depths = (
        raw.t.tolist(),
        raw.T_s.tolist(),
        raw.T_f.tolist(),
        md.tolist(),
    )
    phases = []
    t = 0.0
    previous_depth = 0.0
    for i, z in enumerate([*d.stage_depths_m, d.bottom_md_m]):
        end = t + (z - previous_depth) / d.speed_m_s
        phases.append(
            dict(
                id=f"run-in-{i}",
                kind="running_in",
                label="Running in",
                start_s=t,
                end_s=end,
            )
        )
        t = end
        previous_depth = z
        if z < d.bottom_md_m:
            phases.append(
                dict(
                    id=f"stage-{i}",
                    kind="circulating",
                    label=f"Staging at {z:g} m",
                    start_s=t,
                    end_s=t + d.stage_duration_s,
                )
            )
            t += d.stage_duration_s
    for i, stop in enumerate(d.stops):
        phases.append(
            dict(
                id=f"stop-{i}",
                kind=stop.kind,
                label=stop.label,
                start_s=t,
                end_s=t + stop.duration_s,
            )
        )
        hist = core.run(
            lump,
            [
                core.Segment(
                    stop.duration_s,
                    stop.outside_temperature_C,
                    stop.outside_film_W_m2_K,
                    stop.inside_film_W_m2_K,
                    T_flow=stop.flowing_temperature_C,
                    label=stop.label,
                )
            ],
            steel[-1],
            fluid[-1],
            dt=n.tool_step_s,
        )
        for j in range(1, len(hist.t)):
            times.append(
                t + float(hist.t[j]) if j < len(hist.t) - 1 else t + stop.duration_s
            )
            steel.append(float(hist.T_s[j]))
            fluid.append(float(hist.T_f[j]))
            depths.append(d.bottom_md_m)
        t += stop.duration_s
    indices = [
        next(
            (j for j, p in enumerate(phases) if time <= p["end_s"] + 1.0e-8),
            len(phases) - 1,
        )
        for time in times
    ]
    full = core.History(
        np.asarray(times),
        np.asarray(steel),
        np.asarray(fluid),
        [phases[i]["label"] for i in indices],
    )
    grid = {
        "kind": "two-node-lump",
        "sha256": digest(l.model_dump()),
        "lump": l.model_dump(),
    }
    events = []
    for i, p in enumerate(phases):
        left = [j for j, state in enumerate(indices) if state == i - 1]
        right = [j for j, state in enumerate(indices) if state == i]
        events.append(
            dict(
                time_s=p["start_s"],
                state_index=i,
                state_id=p["id"],
                label=p["label"],
                left_index=left[-1] if left else None,
                right_index=right[0] if right else None,
                right_sample_time_s=times[right[0]] if right else None,
            )
        )
    data = dict(
        schema_version="history-data-1",
        kind="tool",
        id="tool",
        time_s=times,
        md_m=depths,
        state_index=indices,
        state_id=[phases[i]["id"] for i in indices],
        state_label=[phases[i]["label"] for i in indices],
        event_side=[
            "right"
            if j == 0
            else "left"
            if abs(time - phases[indices[j]]["end_s"]) <= 1.0e-8
            else "interior"
            for j, time in enumerate(times)
        ],
        sample_kind=["initial", *["accepted_step"] * (len(times) - 1)],
        grid=grid,
        steel_temperature_C=steel,
        internal_fluid_temperature_C=fluid,
        units={"time_s": "s", "md_m": "m MD", "temperature_C": "°C"},
        events=events,
        statistics={
            "peak_internal_fluid_C": full.peak()[0],
            "peak_internal_fluid_time_s": full.peak()[1],
            "peak_steel_C": max(steel),
            "time_above_s": {
                str(threshold): full.time_above(threshold)
                for threshold in d.threshold_temperatures_C
            },
            "time_above_convention": "Core numerical intervals counted when either endpoint is above threshold; no interpolated threshold crossings",
            "staging_step_s": 10.0,
            "phases": phases,
            "surrounding_column": "Prescribed/frozen; tool heat does not feed back; no coolant transport delay",
        },
    )
    data["snapshots"] = snapshots(data)
    return HistoryDataset.model_validate(data).model_dump(exclude_none=True)


def run_history(
    scenario, stage=lambda *_: None, notify=lambda *_: None, stored_grids=None
):
    warnings = validate_history(scenario)
    from .dependencies import property_identity

    identity, cache = engine_identity(), property_identity(scenario)
    if (
        not identity["adapter_compatible"]
        or not identity["history_capabilities"][scenario.mode]
    ):
        raise CalculationError("CAPABILITY_UNSUPPORTED", identity["history_message"])
    started = time.perf_counter()
    stage("running", {"name": "Passive accepted-step history; prescribed boundaries"})
    datasets = []
    checkpoints = []
    intervention = None
    if scenario.mode == "wall_history" and scenario.intervention:
        from .interventions import run_intervention

        datasets, checkpoints, intervention = run_intervention(
            scenario, identity, notify, stored_grids=stored_grids
        )
        warnings.append(
            warning("SCHEDULED_HISTORY_SCOPE", intervention["qualification"])
        )
    elif scenario.mode == "wall_history":
        for i, column in enumerate(scenario.definition.columns):
            datasets.append(
                wall_dataset(scenario, column, (stored_grids or {}).get(column.id))
            )
            notify(
                "progress",
                {
                    "completed_work": i + 1,
                    "total_work": len(scenario.definition.columns),
                },
            )
    else:
        datasets.append(tool_dataset(scenario))
    stage(
        "postprocessing",
        {
            "name": "Validate finite histories, grids, event sides and snapshot identities"
        },
    )
    canonical_bytes(datasets)
    numerical = {
        **scenario.numerics.model_dump(),
        "observation": "Detached accepted steps plus explicit wall event sides; original core timesteps",
        "staging_step_s": 10.0,
        "checkpoint_resume": False,
    }
    if intervention:
        numerical.update(
            checkpoint_resume=True,
            continuation_policy="event-boundary-reset-v1",
            physical_schedule_hash=intervention["schedule_hash"],
        )
    energy = (
        (
            "pass"
            if all(d["statistics"]["energy_passed"] for d in datasets)
            else "flagged"
        )
        if scenario.mode == "wall_history"
        else "not_applicable"
    )
    if energy == "flagged":
        warnings.append(
            warning(
                "HISTORY_ENERGY_FLAG",
                "A radial-column energy diagnostic exceeds its declared tolerance; inspect each column independently",
            )
        )
    catalog = [
        dict(
            id=d["id"],
            kind=d["kind"],
            md_m=d["md_m"][0] if d["kind"] == "wall" else None,
            samples=len(d["time_s"]),
            grid_sha256=d["grid"]["sha256"],
            artifact_id=f"artifact_{i + 2}",
            statistics=d["statistics"],
        )
        for i, d in enumerate(datasets)
    ]
    total = max(d["time_s"][-1] for d in datasets)
    scalar = lambda value, unit, basis: dict(
        value=float(value), unit=unit, basis=basis, validity="qualified"
    )
    result = dict(
        schema_version="1.0",
        mode=scenario.mode,
        acceptance="qualified",
        acceptance_policy="passive-history-acceptance-v1",
        resolved_scenario=scenario.model_dump(mode="json"),
        physical_request_hash=physical_hash(scenario),
        resolved_scenario_hash=digest(scenario.model_dump()),
        build=identity,
        physics=scenario.model.physics,
        physics_settings=physics_record(scenario),
        property_table=cache,
        numerical_profile=numerical,
        numerical_profile_hash=digest(numerical),
        execution_identity=digest(
            {
                "scenario": physical_hash(scenario),
                "build": identity,
                "numerics": numerical,
            }
        ),
        time={
            "kind": "physical_history",
            "duration_s": total,
            "sampling": "accepted numerical times; no evenly spaced timestep assumption",
        },
        pressure_reference={
            "status": "not_computed",
            "scope": "Thermal history only; no pressure or programme conclusions",
        },
        validity=dict(
            solver_converged=True,
            fields_finite=True,
            domain_status="qualified",
            energy_status=energy,
            assessment_status="not_evaluated",
        ),
        history={
            "schema_version": "history-index-1",
            "columns": catalog,
            "coupling": "Independent radial columns; no axial rock conduction"
            if scenario.mode == "wall_history"
            else "Two-node tool in prescribed surrounding column",
            "partial_output": False,
            "checkpoint_resume": False,
            "boundary_source": scenario.definition.boundary_source,
        },
        summary={
            "duration": scalar(total, "s", "physical time"),
            "samples": scalar(
                sum(len(d["time_s"]) for d in datasets),
                "samples",
                "accepted steps and explicit wall event sides",
            ),
        },
        warnings=warnings,
        attempts=[{"strategy": "passive-core-history-v1"}],
        artifacts=[],
        limitations=[
            "Prescribed thermal boundaries; no axial coolant advection or transport delay",
            "Open-hole radial columns only; casing/cement resistance is not bypassed",
            "No checkpoint/resume/branch; display frames are not resumable physical state",
            "Thermal-history results have not been compared with field measurements",
        ],
        wall_clock_duration_s=time.perf_counter() - started,
        datasets=datasets,
    )
    from .trajectories import history_trajectory

    result["trajectory"] = history_trajectory(scenario)
    if intervention:
        result.update(intervention=intervention, _checkpoints=checkpoints)
        result["history"].update(
            checkpoint_resume=True, time_origin_s=intervention["time_origin_s"]
        )
        result["time"].update(
            origin_s=intervention["time_origin_s"],
            duration_s=intervention["time_end_s"] - intervention["time_origin_s"],
        )
        result["summary"]["duration"] = scalar(
            intervention["time_end_s"] - intervention["time_origin_s"],
            "s",
            "newly calculated physical interval",
        )
        result["summary"]["end_time"] = scalar(
            intervention["time_end_s"], "s", "absolute physical event clock"
        )
        result["limitations"] = [
            "Results have not been compared with field measurements; prescribed local circulating/static boundaries only",
            "Fixed open-hole geometry/domain; no axial coupling, circulation feedback, pressure dynamics or tool",
            "Resume/branch only at completed event checkpoints; failed/interrupted workers do not publish partial checkpoints",
        ]
    return result


def read_dataset(store, rid, dataset_id):
    import json

    run = store.get(rid)
    if run["state"] != "completed" or run["manifest"]["mode"] not in (
        "wall_history",
        "tool_history",
    ):
        raise CalculationError(
            "CAPABILITY_UNSUPPORTED", "An accepted completed history is required"
        )
    if store.check_integrity(rid)["status"] != "verified":
        raise CalculationError(
            "ARTIFACT_INVALID", "History failed stored integrity verification"
        )
    entry = next(
        (c for c in run["manifest"]["history"]["columns"] if c["id"] == dataset_id),
        None,
    )
    if entry is None:
        raise KeyError(dataset_id)
    artifact = next(
        a for a in run["manifest"]["artifacts"] if a["id"] == entry["artifact_id"]
    )
    return HistoryDataset.model_validate(
        json.loads((store.artifact_path(rid) / artifact["filename"]).read_bytes())
    ).model_dump(exclude_none=True)


def sample_in_state(dataset, field, time_s, state_id, event_side):
    """Display comparison only: never interpolate across state boundaries."""
    indices = [i for i, value in enumerate(dataset["state_id"]) if value == state_id]
    if not indices:
        return None, None
    times = [dataset["time_s"][i] for i in indices]
    exact = [
        i
        for i in indices
        if abs(dataset["time_s"][i] - time_s) <= 1.0e-9
        and dataset["event_side"][i] == event_side
    ]
    if not exact:
        exact = [i for i in indices if abs(dataset["time_s"][i] - time_s) <= 1.0e-9]
    if exact:
        index = exact[-1]
        return dataset[field][index], [dataset["time_s"][index]] * 2
    position = int(np.searchsorted(times, time_s))
    if position == 0 or position == len(indices):
        return None, None
    a, b = indices[position - 1], indices[position]
    x, y = dataset[field][a], dataset[field][b]
    if x is None or y is None:
        return None, None
    return float(
        x
        + (y - x)
        * (time_s - dataset["time_s"][a])
        / (dataset["time_s"][b] - dataset["time_s"][a])
    ), [dataset["time_s"][a], dataset["time_s"][b]]


def compare_histories(a, b):
    if a["kind"] != b["kind"] or (a["kind"] == "wall" and a["md_m"][0] != b["md_m"][0]):
        raise CalculationError(
            "CAPABILITY_UNSUPPORTED",
            "History difference requires the same kind and, for radial columns, the same MD",
        )
    events_a = [(e["state_id"], e["time_s"]) for e in a["events"]]
    events_b = [(e["state_id"], e["time_s"]) for e in b["events"]]
    if len(events_a) != len(events_b) or any(
        x[0] != y[0] or abs(x[1] - y[1]) > 1.0e-8 for x, y in zip(events_a, events_b)
    ):
        raise CalculationError(
            "CAPABILITY_UNSUPPORTED",
            "History difference requires matching event identities/times; absolute histories remain available",
        )
    fields = (
        ("wall_temperature_C", "fluid_node_temperature_C")
        if a["kind"] == "wall"
        else ("steel_temperature_C", "internal_fluid_temperature_C")
    )
    end = min(a["time_s"][-1], b["time_s"][-1])
    indices = [i for i, t in enumerate(a["time_s"]) if t <= end]
    output = {field: [] for field in fields}
    brackets = []
    for i in indices:
        row = {}
        for field in fields:
            value, bracket = sample_in_state(
                b, field, a["time_s"][i], a["state_id"][i], a["event_side"][i]
            )
            original = a[field][i]
            output[field].append(
                None if original is None or value is None else value - original
            )
            row[field] = bracket
        brackets.append(row)
    return dict(
        alignment="common_time_same_events",
        difference="variant minus baseline",
        time_s=[a["time_s"][i] for i in indices],
        state_id=[a["state_id"][i] for i in indices],
        event_side=[a["event_side"][i] for i in indices],
        fields=output,
        variant_source_brackets_s=brackets,
        qualification="Display interpolation only within the same continuous state; no extrapolation or interpolation across events",
    )


def compare_history_results(original, datasets, current, checkpoints=None):
    from .history_reproduction import numeric_difference

    failures = []
    differences = []
    maximum = 0.0
    identities = {
        key: original["build"].get(key) == current["build"].get(key)
        for key in ("core_version", "core_commit", "app_version", "libraries")
    }
    identities.update(
        {
            key: original.get(key) == current.get(key)
            for key in (
                "mode",
                "physics",
                "physics_settings",
                "physical_request_hash",
                "resolved_scenario_hash",
                "numerical_profile_hash",
                "pressure_reference",
                "validity",
            )
        }
    )

    def compare(a, b, path):
        nonlocal maximum
        if len(failures) >= 100:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            # These hashes audit each archive; numerical reproduction compares
            # their underlying fields at the declared tolerance instead.
            if a.keys() != b.keys():
                failures.append(path + "/fields")
                return
            for key in a:
                if key not in ("field_sha256",):
                    compare(a[key], b[key], path + "/" + key)
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                failures.append(path + "/length")
                return
            for i, (x, y) in enumerate(zip(a, b)):
                compare(x, y, path + "/" + str(i))
        elif (
            isinstance(a, (int, float))
            and not isinstance(a, bool)
            and isinstance(b, (int, float))
        ):
            delta = abs(a - b)
            maximum = max(maximum, delta)
            difference = numeric_difference(a, b, path)
            if difference:
                failures.append(path)
                differences.append(difference)
        elif a != b:
            failures.append(path)

    compare(datasets, current["datasets"], "datasets")
    if original.get("trajectory") != current.get("trajectory"):
        failures.append("trajectory.coordinates_or_policy")
    if original.get("intervention"):
        # Checkpoint hashes authenticate each artifact during inspection. A new
        # numerical solve can differ within the physical tolerances and must not
        # be mistaken for an identical-state restart.
        def without_checkpoint_sums(value):
            import copy

            value = copy.deepcopy(value)
            for entry in value.get("checkpoints", []):
                entry.pop("sha256", None)
            return value

        compare(
            without_checkpoint_sums(original["intervention"]),
            without_checkpoint_sums(current.get("intervention", {})),
            "intervention",
        )
        if checkpoints is not None:
            before = [
                {k: v for k, v in cp.items() if k != "sha256"} for cp in checkpoints
            ]
            after = [
                {k: v for k, v in cp.items() if k != "sha256"}
                for cp in current["_checkpoints"]
            ]
            compare(before, after, "checkpoints")
    elif current.get("intervention"):
        failures.append("intervention.unexpected")
    failures.extend("identity/" + key for key, valid in identities.items() if not valid)
    return dict(
        passed=not failures,
        comparison_policy="history-reproduction-v3",
        identity_matches=identities,
        failed_checks=failures,
        differences=differences,
        maximum_numeric_absolute_difference=maximum,
        tolerances={
            "temperature_C": 0.001,
            "energy_J_m": "max(1, 1e-6 × absolute saved value)",
            "time_s": "max(1e-8, 1e-9 × absolute saved value)",
            "coordinates_m": 0,
            "wall_flux_W_m": "max(0.001, 1e-9 × absolute saved value)",
            "state_and_input_identity": "exact",
        },
    )
