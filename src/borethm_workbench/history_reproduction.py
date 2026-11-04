"""Physical units and numerical replay rules for saved thermal histories."""

import math

ENERGY_FIELDS = {
    "change_J_m",
    "energy_far_J_m",
    "energy_in_J_m",
    "energy_residual_J_m",
    "energy_tolerance_J_m",
    "far_J_m",
    "rock_change_J_m",
    "rock_residual_J_m",
    "static_node_plus_wall_residual_J_m",
    "stored_J_m",
    "tolerance_J_m",
    "wall_J_m",
}
TEMPERATURE_FIELDS = {
    "fluid_node_temperature_C",
    "fluid_temperature_C",
    "internal_fluid_temperature_C",
    "steel_temperature_C",
    "last_circulating_temperature_C",
    "maximum_wall_temperature_C",
    "minimum_wall_temperature_C",
    "reference_temperature_C",
    "rock_temperature_C",
    "wall_temperature_C",
    "peak_internal_fluid_C",
    "peak_steel_C",
}
TIME_FIELDS = {
    "duration_s",
    "elapsed_event_s",
    "initial_step_s",
    "source_timestep_bracket_s",
    "time_origin_s",
    "time_s",
    "start_s",
    "end_s",
    "right_sample_time_s",
    "peak_internal_fluid_time_s",
    "time_above_s",
    "staging_step_s",
    "time_end_s",
    "horizon_s",
    "maximum_time_s",
}
COORDINATE_FIELDS = {
    "bit_md_m",
    "easting_m",
    "md_m",
    "northing_m",
    "radius_m",
    "station_easting_m",
    "station_md_m",
    "station_northing_m",
    "station_tvd_m",
    "tvd_m",
    "inner_perimeter_m",
    "outer_perimeter_m",
    "inner_diameter_m",
}
COUNT_FIELDS = {
    "index",
    "left_index",
    "right_index",
    "next_event_index",
    "samples",
    "state_index",
    "accepted_steps",
    "radial_nodes",
}
EXACT_INPUTS = {
    "step_growth": "1",
    "film_W_m2_K": "W/(m² K)",
    "fluid_capacity_J_m_K": "J/(m K)",
    "steel_capacity_J_m_K": "J/(m K)",
}


def field_rule(path):
    fields = [
        part
        for part in path.split("/")
        if part and not part.replace(".", "", 1).isdigit()
    ]
    name = fields[-1]
    if name in ENERGY_FIELDS:
        return "J/m", 1.0, 1.0e-6
    if name in TEMPERATURE_FIELDS:
        return "°C", 0.001, 0.0
    if name in TIME_FIELDS:
        return "s", 1.0e-8, 1.0e-9
    if name in COORDINATE_FIELDS:
        return "m", 0.0, 0.0
    if name == "wall_flux_W_m":
        return "W/m", 0.001, 1.0e-9
    if name in COUNT_FIELDS:
        return "count", 0.0, 0.0
    if name in EXACT_INPUTS:
        return EXACT_INPUTS[name], 0.0, 0.0
    raise ValueError("No numerical replay rule for " + path)


def numeric_difference(saved, recalculated, path):
    try:
        unit, absolute, relative = field_rule(path)
    except ValueError as error:
        return {
            "field": path,
            "reason": str(error),
            "saved": saved,
            "recalculated": recalculated,
            "difference": None,
            "tolerance": None,
            "unit": "undeclared",
        }
    tolerance = max(absolute, relative * abs(saved))
    delta = abs(saved - recalculated)
    if math.isfinite(delta) and delta <= tolerance:
        return None
    return {
        "field": path,
        "saved": saved if math.isfinite(saved) else None,
        "recalculated": recalculated if math.isfinite(recalculated) else None,
        "difference": delta if math.isfinite(delta) else None,
        "tolerance": tolerance,
        "unit": unit,
    }
