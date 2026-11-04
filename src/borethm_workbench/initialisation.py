"""Bounded numerical initialisation; every final solve retains target physics."""

import time

import numpy as np

from .contracts import digest, numerical_profile


def compatibility_key(scenario):
    data = scenario.model_dump(exclude={"title", "metadata", "provenance", "numerics"})
    data["definition"].pop("operating")
    return digest(data)


def seed_snapshot(manifest):
    from .contracts import parse_scenario

    p = manifest["profile"]
    profile = {
        "z": p["md_m"],
        "Td": p["bore_temperature_C"],
        "Tu": p["annulus_temperature_C"],
    }
    return {
        "run_id": manifest["run_id"],
        "compatibility_key": compatibility_key(
            parse_scenario(manifest["resolved_scenario"])
        ),
        "build": {
            k: manifest["build"].get(k)
            for k in ("core_version", "core_commit", "app_version", "libraries")
        },
        "property_table_sha256": manifest["property_table"]["sha256"],
        "acceptance": manifest["acceptance"],
        "validity": {
            k: manifest["validity"][k]
            for k in ("solver_converged", "fields_finite", "domain_status")
        },
        "profile": profile,
        "profile_sha256": digest(profile),
    }


def compatible_seed(seed, scenario, identity, cache):
    if not seed:
        return False
    try:
        p = seed["profile"]
        arrays = np.asarray([p["z"], p["Td"], p["Tu"]], dtype=float)
        return bool(
            seed["compatibility_key"] == compatibility_key(scenario)
            and seed["build"]
            == {
                k: identity.get(k)
                for k in ("core_version", "core_commit", "app_version", "libraries")
            }
            and seed["property_table_sha256"] == cache["sha256"]
            and seed["acceptance"] in ("accepted", "qualified")
            and seed["validity"]["solver_converged"] is True
            and seed["validity"]["fields_finite"] is True
            and seed["validity"]["domain_status"] in ("within", "qualified")
            and seed["profile_sha256"] == digest(p)
            and arrays.ndim == 2
            and arrays.shape[1] >= 2
            and np.all(np.isfinite(arrays))
            and arrays[0, 0] == 0
            and arrays[0, -1] == scenario.definition.well.bit_md_m
            and np.all(np.diff(arrays[0]) > 0)
        )
    except (KeyError, TypeError, ValueError):
        return False


def solve(
    scenario,
    geometry,
    fluid,
    settings,
    identity,
    cache,
    validate_thermal,
    seed=None,
    notify=lambda *_: None,
):
    import borethm
    from .adapter import CalculationError

    profile = numerical_profile(scenario)
    bounded = scenario.numerics.initialisation == "interactive-bounded-v1"
    accepted_seed = (
        seed if bounded and compatible_seed(seed, scenario, identity, cache) else None
    )
    attempts, targets = [], 0

    def attempt(strategy, role, options, initial=None, seed_identity=None):
        nonlocal targets
        if role == "target":
            targets += 1
        number = len(attempts) + 1
        event = dict(attempt=number, strategy=strategy, role=role)
        if role == "target":
            event["target_attempt"] = targets
        if seed_identity:
            event.update(
                seed_run_id=seed_identity.get("run_id"),
                seed_profile_sha256=seed_identity.get("profile_sha256"),
            )
        notify("attempt_started", event)
        started = time.perf_counter()
        record = {
            **event,
            "seed": seed_identity,
            "exposure": options.get("t_years")
            if role == "continuation_seed"
            else scenario.definition.operating.exposure.model_dump(),
        }
        result = None
        try:
            result = borethm.circulating_temperatures(
                m_dot=options.pop("_mass_rate"),
                geometry=geometry,
                fluid=fluid,
                **{**options, "init": initial},
            )
            sol = result["sol"]
            record.update(
                solver_status=int(sol.status), solver_message=str(sol.message)
            )
            validate_thermal(result, options)
            record["outcome"] = "validated"
        except CalculationError as error:
            record.update(
                outcome="rejected", failure_code=error.code, solver_message=str(error)
            )
            if error.code not in {"SOLVER_NOT_CONVERGED", "RESULT_NONFINITE"}:
                record["duration_s"] = time.perf_counter() - started
                attempts.append(record)
                notify(
                    "attempt_finished",
                    {
                        **event,
                        **{
                            k: record[k]
                            for k in (
                                "duration_s",
                                "outcome",
                                "solver_status",
                                "solver_message",
                            )
                            if k in record
                        },
                    },
                )
                error.diagnostics.update(
                    attempts=attempts,
                    solver_converged=bool(result and result["sol"].success),
                )
                raise
        record["duration_s"] = time.perf_counter() - started
        attempts.append(record)
        notify(
            "attempt_finished",
            {
                **event,
                **{
                    k: record[k]
                    for k in (
                        "duration_s",
                        "outcome",
                        "solver_status",
                        "solver_message",
                    )
                    if k in record
                },
            },
        )
        return result if record["outcome"] == "validated" else None

    from .adapter import mass_rate

    options = {**settings, "_mass_rate": mass_rate(scenario, fluid)}
    if accepted_seed:
        result = attempt(
            "compatible-accepted-profile-v1",
            "target",
            dict(options),
            accepted_seed["profile"],
            {k: accepted_seed[k] for k in ("run_id", "profile_sha256")},
        )
        if result:
            return result, attempts, accepted_seed
    result = attempt("deterministic-default-v1", "target", dict(options))
    if result:
        return result, attempts, accepted_seed
    if bounded:
        for years in profile["continuation"]["values"]:
            if targets >= profile["maximum_target_attempts"]:
                break
            continuation_settings = {**options, "exposure": None, "t_years": years}
            intermediate = attempt(
                f"uniform-exposure-{years:.12g}-years-v1",
                "continuation_seed",
                continuation_settings,
            )
            if intermediate is None:
                continue
            initial = {k: intermediate[k] for k in ("z", "Td", "Tu")}
            result = attempt(
                "uniform-exposure-continuation-v1",
                "target",
                dict(options),
                initial,
                {
                    "profile_sha256": digest(
                        {k: np.asarray(v).tolist() for k, v in initial.items()}
                    )
                },
            )
            if result:
                return result, attempts, accepted_seed
    raise CalculationError(
        "SOLVER_NOT_CONVERGED",
        "All bounded initialisation attempts failed. Inspect attempt diagnostics; try a supported operating condition or an explicitly different numerical policy.",
        {
            "attempts": attempts,
            "solver_converged": False,
            "initialisation_policy": profile,
        },
    )
