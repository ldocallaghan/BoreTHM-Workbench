"""Survey reconstruction and display coordinates; core TVD remains authoritative."""

import numpy as np

from .contracts import TrajectoryRequest, digest

DISPLAY_TRANSFORMS = {
    "legacy-y-up-v1": "(E-E0, -(TVD-TVD0), N-N0); X east, Y up, Z north",
    "enu-y-up-v2": "(E-E0, -(TVD-TVD0), -(N-N0)); X east, Y up, north -Z",
}


def core_survey(survey, length):
    from borethm.well_geometry import Survey

    if survey.kind == "vertical":
        return Survey.vertical()
    if survey.kind == "md_tvd":
        values = np.asarray(survey.points_md_m_tvd_m)
        return Survey(values[:, 0], values[:, 1])
    return Survey.from_stations(
        [s.md_m for s in survey.stations],
        [s.inclination_deg for s in survey.stations],
        [
            s.azimuth_deg % 360 + survey.north_correction_deg % 360
            for s in survey.stations
        ],
        tvd0=survey.origin_tvd_m,
    )


def station_coordinates(survey, length):
    if survey.kind == "vertical":
        from borethm.well_geometry import Survey

        return Survey([0.0, length], [0.0, length]), np.zeros(2), np.zeros(2)
    core = core_survey(survey, length)
    if survey.kind == "md_tvd":
        return core, None, None
    md = core.md
    inc = np.radians([s.inclination_deg for s in survey.stations])
    az = np.radians(
        [
            s.azimuth_deg % 360 + survey.north_correction_deg % 360
            for s in survey.stations
        ]
    )
    i, j, a, b = inc[:-1], inc[1:], az[:-1], az[1:]
    dogleg = np.arccos(
        np.clip(np.cos(j - i) - np.sin(i) * np.sin(j) * (1 - np.cos(b - a)), -1, 1)
    )
    ratio = np.ones_like(dogleg)
    big = dogleg > 1.0e-9
    ratio[big] = 2 / dogleg[big] * np.tan(dogleg[big] / 2)
    half = np.diff(md) / 2 * ratio
    east = (
        survey.origin_easting_m
        + np.r_[0.0, np.cumsum(half * (np.sin(i) * np.sin(a) + np.sin(j) * np.sin(b)))]
    )
    north = (
        survey.origin_northing_m
        + np.r_[0.0, np.cumsum(half * (np.sin(i) * np.cos(a) + np.sin(j) * np.cos(b)))]
    )
    return core, east, north


def reconstruct(request):
    request = (
        TrajectoryRequest.model_validate(request)
        if isinstance(request, dict)
        else request
    )
    survey = request.survey
    core, east, north = station_coordinates(survey, request.bit_md_m)
    md = np.unique(np.r_[core.md, request.sample_md_m])
    tvd = core.tvd(md)
    supported = bool(
        abs(core.tvd_at[0]) < 1.0e-9
        and np.all(np.diff(core.tvd_at) >= -1.0e-9)
        and (
            survey.kind != "stations"
            or all(s.inclination_deg <= 90 for s in survey.stations)
        )
    )
    positioned = east is not None
    result = dict(
        schema_version="trajectory-1",
        survey=survey.model_dump(),
        bit_md_m=request.bit_md_m,
        coordinate_convention="Easting, northing, TVD positive down; metres",
        display_transform_version="enu-y-up-v2",
        renderer_transform=DISPLAY_TRANSFORMS["enu-y-up-v2"],
        station_md_m=core.md.tolist(),
        station_tvd_m=core.tvd_at.tolist(),
        station_easting_m=None if east is None else east.tolist(),
        station_northing_m=None if north is None else north.tolist(),
        md_m=md.tolist(),
        tvd_m=tvd.tolist(),
        easting_m=None if east is None else np.interp(md, core.md, east).tolist(),
        northing_m=None if north is None else np.interp(md, core.md, north).tolist(),
        fully_positioned=positioned,
        calculation_supported=supported,
        stability_supported=survey.kind == "vertical",
        interpolation="Supplied MD/TVD stations; core-linear TVD, no inferred horizontal coordinates. MD is authoritative."
        if survey.kind == "md_tvd"
        else "Declared vertical coordinates; MD equals TVD."
        if survey.kind == "vertical"
        else "Station minimum-curvature increments; chordal XYZ/linear core TVD between stations. MD is authoritative.",
        qualification="MD/TVD only: no unique easting/northing; use the labelled 2D MD section"
        if not positioned
        else "Vertical declared trajectory"
        if survey.kind == "vertical"
        else "Retained survey stations; no smoothing changes numerical TVD",
        applicability="Circulation thermal/hydraulics only; no deviated stability conclusion"
        if supported
        else "Display geometry only: upgoing TVD or nonzero surface TVD is unsupported for calculations",
    )
    result["coordinate_sha256"] = digest(
        {
            k: result[k]
            for k in (
                "station_md_m",
                "station_tvd_m",
                "station_easting_m",
                "station_northing_m",
                "md_m",
                "tvd_m",
                "easting_m",
                "northing_m",
            )
        }
    )
    return result


def geotherm_breaks(survey, length, tvd_knots):
    """Find every chord crossing, retaining both ends of horizontal intervals."""
    if survey.kind == "vertical":
        return [float(z) for z in tvd_knots if 0 < z < length]
    core = core_survey(survey, length)
    result = list(core.md[1:-1])
    for depth in tvd_knots:
        for a, b, x, y in zip(
            core.md[:-1], core.md[1:], core.tvd_at[:-1], core.tvd_at[1:]
        ):
            if x == y:
                if depth == x:
                    result.extend((a, b))
            elif min(x, y) <= depth <= max(x, y):
                result.append(a + (b - a) * (depth - x) / (y - x))
    return [float(v) for v in np.unique(result) if 0 < v < length]


def manifest_trajectory(scenario, md):
    return reconstruct(
        TrajectoryRequest(
            bit_md_m=scenario.definition.well.bit_md_m,
            survey=scenario.definition.well.survey,
            sample_md_m=np.asarray(md).tolist(),
        )
    )


def history_trajectory(scenario):
    definition = scenario.definition
    if definition.trajectory:
        result = reconstruct(definition.trajectory)
    else:
        from .contracts import Vertical

        length = (
            definition.bottom_md_m
            if scenario.mode == "tool_history"
            else max(c.md_m for c in definition.columns)
        )
        result = reconstruct(
            TrajectoryRequest(
                bit_md_m=max(length, 1.0), survey=Vertical(kind="vertical")
            )
        )
        result["qualification"] = (
            "Explicit display assumption: vertical schematic; no measured trajectory supplied"
        )
    result["applicability"] = (
        "History location display only; temperatures use prescribed MD-indexed boundaries"
    )
    return result


def validate_bundle_trajectory(manifest, scenario, files):
    import json

    trajectory = manifest.get("trajectory")
    required = (
        scenario.mode == "circulation_state"
        and scenario.definition.well.survey.kind != "vertical"
    ) or (
        scenario.mode in ("wall_history", "tool_history")
        and scenario.definition.trajectory is not None
    )
    if trajectory is None:
        if required:
            raise ValueError("ARTIFACT_INVALID: surveyed result lacks trajectory")
        return
    if (
        "trajectory.json" not in files
        or json.loads(files["trajectory.json"]) != trajectory
    ):
        raise ValueError("ARTIFACT_INVALID: trajectory differs from artifact")
    expected = (
        manifest_trajectory(scenario, manifest["profile"]["md_m"])
        if scenario.mode == "circulation_state"
        else history_trajectory(scenario)
    )
    # Numeric coordinates/policy are authoritative; explanatory wording can
    # improve between app builds without invalidating an older archive.
    transform_version = trajectory.get("display_transform_version", "legacy-y-up-v1")
    if (
        transform_version not in DISPLAY_TRANSFORMS
        or trajectory.get("renderer_transform") != DISPLAY_TRANSFORMS[transform_version]
    ):
        raise ValueError(
            "ARTIFACT_INVALID: unknown or inconsistent display transform version"
        )
    keys = (
        "schema_version",
        "survey",
        "bit_md_m",
        "coordinate_convention",
        "station_md_m",
        "station_tvd_m",
        "station_easting_m",
        "station_northing_m",
        "md_m",
        "tvd_m",
        "easting_m",
        "northing_m",
        "fully_positioned",
        "calculation_supported",
        "stability_supported",
        "coordinate_sha256",
    )
    if any(trajectory.get(key) != expected[key] for key in keys):
        raise ValueError(
            "ARTIFACT_INVALID: trajectory differs from declared survey/core interpolation"
        )
    if scenario.mode == "circulation_state":
        profile = manifest["profile"]
        tvd = core_survey(
            scenario.definition.well.survey, scenario.definition.well.bit_md_m
        ).tvd(profile["md_m"])
        if not np.array_equal(tvd, profile["tvd_m"]):
            raise ValueError("ARTIFACT_INVALID: profile TVD differs from trajectory")
