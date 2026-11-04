from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

Positive = Annotated[float, Field(gt=0, allow_inf_nan=False, strict=True)]
Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]
Number = Annotated[float, Field(allow_inf_nan=False, strict=True)]
Identifier = Annotated[str, Field(min_length=1, max_length=200)]


class Data(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, validate_default=True
    )


class Model(Data):
    core_family: Literal["borethm"] = "borethm"
    supported_release: Literal["1.6.0"] = "1.6.0"
    hydraulics_model: Literal["v1.6.0"] = "v1.6.0"
    rock_response: Literal["cylinder", "logarithmic"] = "cylinder"


class Layer(Data):
    r_in_m: Positive
    r_out_m: Positive
    conductivity_W_m_K: Positive

    @model_validator(mode="after")
    def ordered(self):
        if self.r_out_m <= self.r_in_m:
            raise ValueError("GEOMETRY_INVALID: outer radius must exceed inner radius")
        return self


class Joint(Data):
    outer_diameter_m: Positive
    bore_diameter_m: Positive
    restriction_length_m: Nonnegative
    spacing_m: Positive
    transition: Literal["abrupt", "gradual"] = "abrupt"
    source: str


class LocalLoss(Data):
    bore_coefficient: Nonnegative = 0
    annulus_coefficient: Nonnegative = 0
    bore_reference_area_m2: Positive | None = None
    annulus_reference_area_m2: Positive | None = None
    source: str


class ToolLoss(Data):
    reference_pressure_Pa: Nonnegative
    reference_flow_m3_s: Positive
    reference_density_kg_m3: Positive
    basis: Literal["additional", "total_internal"]
    span_segments: Annotated[int, Field(ge=1, strict=True)] = 1
    include_boundary_changes: bool = True
    unresolved: bool = False
    flow_range_m3_s: tuple[Nonnegative, Positive] | None = None
    temperature_max_C: Number | None = None
    source: str


def concentric(layers):
    for left, right in zip(layers, layers[1:]):
        if not math.isclose(left.r_out_m, right.r_in_m, abs_tol=1e-10, rel_tol=0):
            raise ValueError(
                "GEOMETRY_INVALID: thermal layers must be concentric and contiguous"
            )


class Pipe(Data):
    name: Identifier
    layers: Annotated[list[Layer], Field(min_length=1, max_length=20)]
    thermal_joint_fraction: Annotated[float, Field(ge=0, le=1, strict=True)] = 0
    hydraulic_joint: Joint | None = None
    source: str = ""

    @model_validator(mode="after")
    def geometry(self):
        concentric(self.layers)
        return self


class Segment(Data):
    id: Identifier
    length_m: Positive
    pipe: Pipe
    local_loss: LocalLoss | None = None
    tool_loss: ToolLoss | None = None


class Hole(Data):
    id: Identifier
    md_top_m: Nonnegative
    md_bottom_m: Positive
    r_wall_m: Positive
    layers: Annotated[list[Layer], Field(max_length=20)] = []

    @model_validator(mode="after")
    def geometry(self):
        if self.md_bottom_m <= self.md_top_m:
            raise ValueError(
                "GEOMETRY_INVALID: hole interval must have positive length"
            )
        concentric(self.layers)
        if self.layers and not math.isclose(
            self.layers[0].r_in_m, self.r_wall_m, abs_tol=1e-10
        ):
            raise ValueError(
                "GEOMETRY_INVALID: first hole layer must start at wall radius"
            )
        return self


class Vertical(Data):
    kind: Literal["vertical"]


class SurveyStation(Data):
    md_m: Nonnegative
    inclination_deg: Annotated[float, Field(ge=0, le=180, strict=True)]
    azimuth_deg: Number


class StationSurvey(Data):
    kind: Literal["stations"]
    stations: Annotated[list[SurveyStation], Field(min_length=2, max_length=1000)]
    north_reference: Literal["true", "grid", "magnetic"]
    north_reference_source: Annotated[str, Field(min_length=1, max_length=2000)]
    north_correction_deg: Number
    origin_easting_m: Number = 0.0
    origin_northing_m: Number = 0.0
    origin_tvd_m: Nonnegative = 0.0

    @model_validator(mode="after")
    def ordered(self):
        if self.stations[0].md_m != 0 or any(
            b.md_m <= a.md_m for a, b in zip(self.stations, self.stations[1:])
        ):
            raise ValueError(
                "SURVEY_INCOMPLETE: stations must increase strictly from surface MD zero"
            )
        # An exact reversal has no unique finite minimum-curvature arc.
        for a, b in zip(self.stations, self.stations[1:]):
            i, j = math.radians(a.inclination_deg), math.radians(b.inclination_deg)
            cosine = math.cos(j - i) - math.sin(i) * math.sin(j) * (
                1 - math.cos(math.radians(b.azimuth_deg % 360 - a.azimuth_deg % 360))
            )
            if cosine <= -1 + 1.0e-10:
                raise ValueError(
                    "SURVEY_INCOMPLETE: antipodal station directions have no unique minimum-curvature path"
                )
        return self


class MDTVDSurvey(Data):
    kind: Literal["md_tvd"]
    points_md_m_tvd_m: Annotated[
        list[tuple[Nonnegative, Number]], Field(min_length=2, max_length=1000)
    ]
    source: Annotated[str, Field(min_length=1, max_length=2000)]

    @model_validator(mode="after")
    def ordered(self):
        points = self.points_md_m_tvd_m
        if points[0][0] != 0 or any(b[0] <= a[0] for a, b in zip(points, points[1:])):
            raise ValueError(
                "SURVEY_INCOMPLETE: MD/TVD stations must increase from surface MD zero"
            )
        if any(
            abs(b[1] - a[1]) > b[0] - a[0] + 1.0e-8 for a, b in zip(points, points[1:])
        ):
            raise ValueError(
                "SURVEY_INCOMPLETE: absolute TVD increment cannot exceed MD increment"
            )
        return self


SurveyInput = Annotated[
    Vertical | StationSurvey | MDTVDSurvey, Field(discriminator="kind")
]


def survey_coverage(survey, length):
    end = (
        survey.stations[-1].md_m
        if survey.kind == "stations"
        else survey.points_md_m_tvd_m[-1][0]
        if survey.kind == "md_tvd"
        else length
    )
    if end != length:
        raise ValueError(
            "SURVEY_INCOMPLETE: survey must cover exactly surface to bit MD; no implicit extrapolation"
        )


class TrajectoryRequest(Data):
    bit_md_m: Positive
    survey: SurveyInput
    sample_md_m: Annotated[list[Nonnegative], Field(max_length=5000)] = []

    @model_validator(mode="after")
    def covered(self):
        survey_coverage(self.survey, self.bit_md_m)
        if any(z > self.bit_md_m for z in self.sample_md_m):
            raise ValueError("SURVEY_INCOMPLETE: samples exceed the bit")
        return self


class Well(Data):
    bit_md_m: Positive
    survey: SurveyInput
    string: Annotated[list[Segment], Field(min_length=1, max_length=500)]
    hole: Annotated[list[Hole], Field(min_length=1, max_length=500)]

    @model_validator(mode="after")
    def geometry(self):
        survey_coverage(self.survey, self.bit_md_m)
        tol = max(1e-6, 1e-10 * self.bit_md_m)
        if abs(sum(s.length_m for s in self.string) - self.bit_md_m) > tol:
            raise ValueError("GEOMETRY_INVALID: bit MD must equal total string length")
        for collection in (self.string, self.hole):
            if len({x.id for x in collection}) != len(collection):
                raise ValueError("GEOMETRY_INVALID: component IDs must be unique")
        end = 0
        for hole in self.hole:
            if abs(hole.md_top_m - end) > tol:
                raise ValueError(
                    "GEOMETRY_INVALID: hole intervals must be contiguous from surface"
                )
            end = hole.md_bottom_m
        if abs(end - self.bit_md_m) > tol:
            raise ValueError(
                "GEOMETRY_INVALID: hole must cover exactly the complete string"
            )
        return self


class Geotherm(Data):
    kind: Literal["points"]
    points_tvd_m_temperature_C: Annotated[
        list[tuple[Nonnegative, Number]], Field(min_length=2, max_length=10000)
    ]
    gradient_below_K_m: Number

    @model_validator(mode="after")
    def ordered(self):
        points = self.points_tvd_m_temperature_C
        if points[0][0] != 0 or any(b[0] <= a[0] for a, b in zip(points, points[1:])):
            raise ValueError("geotherm depths must increase strictly from zero")
        return self


class Formation(Data):
    geotherm: Geotherm
    conductivity_W_m_K: Positive
    volumetric_heat_capacity_J_m3_K: Positive


class Water(Data):
    kind: Literal["iapws_water"]


class Mud(Data):
    kind: Literal["barite_mud"]
    surface_density_kg_m3: Annotated[float, Field(ge=1000, lt=4200, strict=True)]
    plastic_viscosity_Pa_s: Positive
    viscosity_reference_temperature_C: Number
    yield_point_Pa: Nonnegative
    conserve_mass: bool


class Constant(Data):
    kind: Literal["constant_properties"]
    density_kg_m3: Positive
    heat_capacity_J_kg_K: Positive
    viscosity_Pa_s: Positive
    conductivity_W_m_K: Positive
    approximation_label: Identifier


Fluid = Annotated[Water | Mud | Constant, Field(discriminator="kind")]


class MassFlow(Data):
    kind: Literal["mass"]
    mass_rate_kg_s: Positive


class VolumeFlow(Data):
    kind: Literal["surface_volume"]
    volume_rate_m3_s: Positive
    temperature_C: Number
    pressure_absolute_Pa: Positive


class SurfacePressure(Data):
    kind: Literal["prescribed_absolute"]
    pressure_absolute_Pa: Positive


class Years(Data):
    kind: Literal["legacy_uniform_years"]
    years: Nonnegative


class Seconds(Data):
    kind: Literal["uniform_seconds"]
    seconds: Nonnegative


class DepthExposure(Data):
    kind: Literal["depth_seconds"]
    points_md_m_seconds: Annotated[
        list[tuple[Nonnegative, Nonnegative]], Field(min_length=2, max_length=10000)
    ]
    interpolation: Literal["linear"]


class Operating(Data):
    flow: Annotated[MassFlow | VolumeFlow, Field(discriminator="kind")]
    inlet_temperature_C: Number
    surface_pressure: SurfacePressure
    exposure: Annotated[Years | Seconds | DepthExposure, Field(discriminator="kind")]


class AnnulusSource(Data):
    kind: Literal["prescribed_heating"]
    points_md_m_W_m: Annotated[
        list[tuple[Nonnegative, Nonnegative]], Field(min_length=2, max_length=10000)
    ]
    interpolation: Literal["linear"]
    source: str


class Heat(Data):
    friction_heating: bool
    face_heat_W: Nonnegative
    annulus_source: AnnulusSource | None = None
    rotary_partition: None = None


class NoSurfaceLoss(Data):
    kind: Literal["none"]


class APISurfaceLoss(Data):
    kind: Literal["api_case"]
    case: Annotated[int, Field(ge=1, le=5, strict=True)]


class CoefficientSurfaceLoss(Data):
    kind: Literal["coefficient"]
    coefficient: Nonnegative
    unit_convention: Literal["psi_ppg_US_gpm"]
    source: str


class Hydraulics(Data):
    nozzle_area_m2: Positive
    transitions: Literal["abrupt", "gradual"]
    surface_loss: Annotated[
        NoSurfaceLoss | APISurfaceLoss | CoefficientSurfaceLoss,
        Field(discriminator="kind"),
    ]


class Circulation(Data):
    well: Well
    formation: Formation
    fluid: Fluid
    operating: Operating
    heat: Heat
    hydraulics: Hydraulics

    @model_validator(mode="after")
    def coverage(self):
        tables = []
        if isinstance(self.operating.exposure, DepthExposure):
            tables.append(self.operating.exposure.points_md_m_seconds)
        if self.heat.annulus_source:
            tables.append(self.heat.annulus_source.points_md_m_W_m)
        for points in tables:
            if points[0][0] != 0 or points[-1][0] != self.well.bit_md_m:
                raise ValueError("depth profile must cover surface through bit exactly")
            if any(b[0] <= a[0] for a, b in zip(points, points[1:])):
                raise ValueError("profile depths must increase strictly")
        return self


class Numerics(Data):
    profile: Literal["reference-v1"] = "reference-v1"
    initialisation: Literal["deterministic-default-v1", "interactive-bounded-v1"] = (
        "deterministic-default-v1"
    )


NUMERICAL_PROFILE = {
    "name": "reference-v1",
    "initial_nodes": 160,
    "bvp_tolerance": 1e-4,
    "bvp_maximum_nodes": 20000,
    "returned_samples": 400,
    "display_samples": 400,
    "film_multiplier": 1.0,
    "maximum_target_attempts": 1,
    "initialisation": "deterministic-default-v1",
    "continuation": "disabled in the deterministic profile",
}


def numerical_profile(scenario):
    if scenario.numerics.initialisation == "deterministic-default-v1":
        return dict(NUMERICAL_PROFILE)
    return {
        **NUMERICAL_PROFILE,
        "initialisation": "interactive-bounded-v1",
        "maximum_target_attempts": 3,
        "maximum_continuation_solves": 2,
        "continuation": {
            "parameter": "uniform_exposure_years",
            "values": [1 / 365, 1 / 52],
        },
        "seed_compatibility": "identical-nonoperating-inputs-and-execution-build-v1",
    }


class Provenance(Data):
    path: str
    basis: str
    source: str


class Scenario(Data):
    schema_version: Literal["1.0"]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    mode: Literal["circulation_state"]
    model: Model
    definition: Circulation
    numerics: Numerics
    provenance: Annotated[list[Provenance], Field(max_length=1000)] = []
    metadata: dict = {}

    @model_validator(mode="after")
    def bounded_metadata(self):
        if len(canonical_bytes(self.metadata)) > 16384:
            raise ValueError("metadata exceeds 16 KiB")
        return self


class SiteSource(Data):
    kind: Literal["toml"]
    content: Annotated[str, Field(min_length=1, max_length=4 * 1024 * 1024)]
    source: str


class AssessmentOptions(Data):
    calculation_mode: Literal["v1.3.4"] = "v1.3.4"
    thermal: Literal[True] = True
    coupling: Literal["evaluator_default", "coupled", "uncoupled"] = "evaluator_default"


class AssessmentDefinition(Data):
    site: SiteSource
    options: AssessmentOptions


class AssessmentNumerics(Data):
    profile: Literal["assessment-reference-v1"] = "assessment-reference-v1"
    initialisation: Literal["core-evaluator-policy-v160"] = "core-evaluator-policy-v160"


class AssessmentScenario(Data):
    schema_version: Literal["1.0"]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    mode: Literal["site_assessment"]
    model: Model
    definition: AssessmentDefinition
    numerics: AssessmentNumerics
    provenance: Annotated[list[Provenance], Field(max_length=1000)] = []
    metadata: dict = {}

    @model_validator(mode="after")
    def bounded_metadata(self):
        if len(canonical_bytes(self.metadata)) > 16384:
            raise ValueError("metadata exceeds 16 KiB")
        if self.model.rock_response != "cylinder":
            raise ValueError(
                "CAPABILITY_UNSUPPORTED: assessment uses the v1.6.0 evaluator rock-response policy"
            )
        return self


Duration = Annotated[
    float, Field(gt=0, le=30 * 86400, allow_inf_nan=False, strict=True)
]


class CirculatingState(Data):
    kind: Literal["circulating"]
    id: Identifier
    label: Identifier
    duration_s: Duration
    fluid_temperature_C: Number
    film_W_m2_K: Positive


class StaticState(Data):
    kind: Literal["static"]
    id: Identifier
    label: Identifier
    duration_s: Duration
    film_W_m2_K: Positive
    fluid_capacity_J_m_K: Positive
    initial_fluid_temperature_C: Number | None = None


class FixedState(Data):
    kind: Literal["fixed"]
    id: Identifier
    label: Identifier
    duration_s: Duration
    wall_temperature_C: Number


class FluxState(Data):
    kind: Literal["flux"]
    id: Identifier
    label: Identifier
    duration_s: Duration
    flux_into_rock_W_m: Number


WallState = Annotated[
    CirculatingState | StaticState | FixedState | FluxState, Field(discriminator="kind")
]


class RadialColumn(Data):
    id: Identifier
    md_m: Nonnegative
    boundary: Literal["open_hole"] = "open_hole"
    wall_radius_m: Positive
    conductivity_W_m_K: Positive
    volumetric_heat_capacity_J_m3_K: Positive
    far_field_temperature_C: Number
    initial_rock_temperature_C: Number
    initial_profile_C: list[Number] | None = None
    outer_radius_m: Positive | None = None
    # Local prescribed boundaries may differ; event durations/kinds are shared.
    boundary_overrides: dict[str, dict[str, Number]] = {}


class WallHistoryDefinition(Data):
    columns: Annotated[list[RadialColumn], Field(min_length=1, max_length=20)]
    states: Annotated[list[WallState], Field(min_length=1, max_length=32)]
    boundary_source: Annotated[str, Field(min_length=1, max_length=2000)]
    trajectory: TrajectoryRequest | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class HistoryNumerics(Data):
    profile: Literal["history-passive-v1"] = "history-passive-v1"
    radial_nodes: Annotated[int, Field(ge=32, le=512, strict=True)] = 128
    initial_step_s: Annotated[float, Field(gt=0, le=3600, strict=True)] = 1.0
    step_growth: Annotated[float, Field(ge=1, le=2, strict=True)] = 1.08
    tool_step_s: Annotated[float, Field(ge=0.1, le=300, strict=True)] = 30.0


class InterventionPolicy(Data):
    profile: Literal["prescribed-wall-events-v1"] = "prescribed-wall-events-v1"
    fluid_family: Literal["prescribed_water"] = "prescribed_water"
    maximum_time_s: Duration
    stop_after_event_id: Identifier | None = None
    continuation: dict | None = None


class WallHistoryScenario(Data):
    schema_version: Literal["1.0"]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    mode: Literal["wall_history"]
    model: Model
    definition: WallHistoryDefinition
    numerics: HistoryNumerics
    intervention: InterventionPolicy | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    provenance: Annotated[list[Provenance], Field(max_length=1000)] = []
    metadata: dict = {}

    @model_validator(mode="after")
    def history_valid(self):
        if len(canonical_bytes(self.metadata)) > 16384:
            raise ValueError("metadata exceeds 16 KiB")
        states = self.definition.states
        if self.definition.trajectory and any(
            c.md_m > self.definition.trajectory.bit_md_m
            for c in self.definition.columns
        ):
            raise ValueError(
                "SURVEY_INCOMPLETE: radial column outside declared trajectory"
            )
        if len({s.id for s in states}) != len(states) or len(
            {c.id for c in self.definition.columns}
        ) != len(self.definition.columns):
            raise ValueError("HISTORY_INVALID: duplicate state/column identity")
        if sum(s.duration_s for s in states) > 30 * 86400:
            raise ValueError("RESOURCE_LIMIT: total history duration exceeds 30 days")
        for column in self.definition.columns:
            if (
                column.outer_radius_m is not None
                and column.outer_radius_m <= column.wall_radius_m
            ):
                raise ValueError(
                    "GEOMETRY_INVALID: radial domain must extend beyond the wall"
                )
            if (
                column.initial_profile_C is not None
                and len(column.initial_profile_C) != self.numerics.radial_nodes
            ):
                raise ValueError(
                    "HISTORY_INVALID: initial field must match the declared radial grid"
                )
            if column.boundary_overrides.keys() - {s.id for s in states}:
                raise ValueError("HISTORY_INVALID: unknown state override")
            previous_fluid = None
            for state in states:
                updates = column.boundary_overrides.get(state.id, {})
                permitted = (
                    {"fluid_temperature_C", "film_W_m2_K"}
                    if state.kind == "circulating"
                    else {
                        "film_W_m2_K",
                        "fluid_capacity_J_m_K",
                        "initial_fluid_temperature_C",
                    }
                    if state.kind == "static"
                    else {"wall_temperature_C"}
                    if state.kind == "fixed"
                    else {"flux_into_rock_W_m"}
                )
                if updates.keys() - permitted:
                    raise ValueError(
                        "HISTORY_INVALID: override cannot change state identity, duration or unsupported parameters"
                    )
                effective = type(state).model_validate(
                    {**state.model_dump(), **updates}
                )
                if (
                    state.kind == "static"
                    and effective.initial_fluid_temperature_C is None
                    and previous_fluid is None
                ):
                    raise ValueError(
                        "HISTORY_INVALID: static node requires an explicit initial fluid temperature or earlier circulation"
                    )
                if state.kind == "circulating":
                    previous_fluid = effective.fluid_temperature_C
                elif (
                    self.intervention
                    and state.kind == "static"
                    and effective.initial_fluid_temperature_C is not None
                ):
                    previous_fluid = effective.initial_fluid_temperature_C
        if self.intervention:
            from .interventions import validate_intervention

            validate_intervention(self)
        return self


class ToolLump(Data):
    steel_capacity_J_m_K: Positive
    fluid_capacity_J_m_K: Positive
    inner_perimeter_m: Positive
    outer_perimeter_m: Positive
    inner_diameter_m: Positive

    @model_validator(mode="after")
    def cylindrical_geometry(self):
        if self.outer_perimeter_m <= self.inner_perimeter_m or not math.isclose(
            self.inner_perimeter_m, math.pi * self.inner_diameter_m, rel_tol=1.0e-8
        ):
            raise ValueError(
                "GEOMETRY_INVALID: tool perimeters must describe a hollow cylindrical lump with the declared inner diameter"
            )
        return self


class ToolStageBoundary(Data):
    fluid_temperature_C: Number
    inside_film_W_m2_K: Positive
    outside_film_W_m2_K: Positive


class ToolStop(Data):
    kind: Literal["static", "circulating"]
    label: Identifier
    duration_s: Duration
    outside_temperature_C: Number
    inside_film_W_m2_K: Positive
    outside_film_W_m2_K: Positive
    flowing_temperature_C: Number | None = None

    @model_validator(mode="after")
    def flowing(self):
        if (self.kind == "circulating") != (self.flowing_temperature_C is not None):
            raise ValueError(
                "HISTORY_INVALID: flowing temperature is required only for circulation"
            )
        return self


class ToolHistoryDefinition(Data):
    lump: ToolLump
    column_md_m_temperature_C: Annotated[
        list[tuple[Nonnegative, Number]], Field(min_length=2, max_length=2000)
    ]
    bottom_md_m: Positive
    speed_m_s: Positive
    initial_steel_temperature_C: Number
    initial_fluid_temperature_C: Number
    static_inside_film_W_m2_K: Positive
    static_outside_film_W_m2_K: Positive
    stage_depths_m: Annotated[list[Positive], Field(max_length=32)] = []
    stage_duration_s: Nonnegative = 0.0
    staging_boundary: ToolStageBoundary | None = None
    stops: Annotated[list[ToolStop], Field(max_length=32)] = []
    threshold_temperatures_C: Annotated[list[Number], Field(max_length=20)] = [
        125.0,
        150.0,
    ]
    boundary_source: Annotated[str, Field(min_length=1, max_length=2000)]
    trajectory: TrajectoryRequest | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class ToolHistoryScenario(Data):
    schema_version: Literal["1.0"]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    mode: Literal["tool_history"]
    model: Model
    definition: ToolHistoryDefinition
    numerics: HistoryNumerics
    provenance: Annotated[list[Provenance], Field(max_length=1000)] = []
    metadata: dict = {}

    @model_validator(mode="after")
    def tool_valid(self):
        d = self.definition
        if d.trajectory and d.trajectory.bit_md_m != d.bottom_md_m:
            raise ValueError(
                "SURVEY_INCOMPLETE: tool trajectory must cover the declared bottom MD"
            )
        points = d.column_md_m_temperature_C
        if (
            points[0][0] != 0
            or points[-1][0] != d.bottom_md_m
            or any(b[0] <= a[0] for a, b in zip(points, points[1:]))
        ):
            raise ValueError(
                "HISTORY_INVALID: prescribed column must increase from surface to bottom without extrapolation"
            )
        if any(a >= b for a, b in zip(d.stage_depths_m, d.stage_depths_m[1:])) or any(
            z >= d.bottom_md_m for z in d.stage_depths_m
        ):
            raise ValueError(
                "HISTORY_INVALID: stages must be ordered unique interior depths"
            )
        if (
            d.stage_depths_m and not (d.staging_boundary and d.stage_duration_s > 0)
        ) or (
            not d.stage_depths_m
            and (d.staging_boundary is not None or d.stage_duration_s != 0)
        ):
            raise ValueError(
                "HISTORY_INVALID: stage depths require duration and a complete circulation boundary"
            )
        if (
            d.bottom_md_m / d.speed_m_s
            + len(d.stage_depths_m) * d.stage_duration_s
            + sum(s.duration_s for s in d.stops)
            > 30 * 86400
        ):
            raise ValueError("RESOURCE_LIMIT: total tool duration exceeds 30 days")
        if len(canonical_bytes(self.metadata)) > 16384:
            raise ValueError("metadata exceeds 16 KiB")
        return self


AnyScenario = Annotated[
    Scenario | AssessmentScenario | WallHistoryScenario | ToolHistoryScenario,
    Field(discriminator="mode"),
]
SCENARIO_ADAPTER = TypeAdapter(AnyScenario)


def parse_scenario(value):
    return SCENARIO_ADAPTER.validate_python(value)


def parse_scenario_json(value):
    return SCENARIO_ADAPTER.validate_json(value)


def normalise(value):
    if isinstance(value, dict):
        return {key: normalise(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalise(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite JSON number")
        return 0.0 if value == 0 else value
    return value


def canonical_bytes(value):
    return json.dumps(
        normalise(value),
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def physical_hash(scenario: Scenario):
    return digest(scenario.model_dump(exclude={"title", "metadata", "provenance"}))


class Submit(Data):
    scenario: AnyScenario


class RevisionSubmit(Data):
    scenario_id: str
    revision: Annotated[int, Field(ge=1, strict=True)]


RunRequest = Submit | RevisionSubmit


class Comparison(Data):
    baseline_run_id: str
    variant_run_id: str
    alignment: Literal["common_md"] = "common_md"


class HistoryComparison(Data):
    baseline_run_id: str
    variant_run_id: str
    baseline_dataset_id: Identifier
    variant_dataset_id: Identifier


class InterventionBranch(Data):
    checkpoint_id: Identifier
    action: Literal["resume", "branch"]
    future_states: (
        Annotated[list[WallState], Field(min_length=1, max_length=32)] | None
    ) = None
    title: Annotated[str, Field(min_length=1, max_length=200)] | None = None


class SweepAxis(Data):
    parameter: Literal[
        "mass_flow_kg_s",
        "inlet_temperature_C",
        "wellhead_pressure_absolute_Pa",
        "exposure_years",
    ]
    values: Annotated[list[Number], Field(min_length=1, max_length=25)]

    @model_validator(mode="after")
    def unique_values(self):
        if len(set(self.values)) != len(self.values):
            raise ValueError("SWEEP_INVALID: duplicate axis values")
        return self


class SweepRequest(Data):
    title: Annotated[str, Field(min_length=1, max_length=200)] = "Parameter sweep"
    scenario: Scenario
    axes: Annotated[list[SweepAxis], Field(min_length=1, max_length=2)]

    @model_validator(mode="after")
    def bounded_cases(self):
        if len({a.parameter for a in self.axes}) != len(self.axes):
            raise ValueError("SWEEP_INVALID: axes must use distinct parameters")
        if math.prod(len(a.values) for a in self.axes) > 25:
            raise ValueError("RESOURCE_LIMIT: at most 25 interactive sweep cases")
        return self

    alignment: Literal["common_time_same_events"] = "common_time_same_events"


class ScalarResult(Data):
    value: Number
    unit: str
    basis: str
    validity: Literal["accepted", "qualified"]


class ProfileResult(Data):
    md_m: list[Number]
    tvd_m: list[Number]
    bore_temperature_C: list[Number]
    annulus_temperature_C: list[Number]
    formation_temperature_C: list[Number]
    annulus_wall_pressure_gauge_Pa: list[Number]
    annulus_hydrostatic_pressure_difference_Pa: list[Number]
    annulus_friction_Pa: list[Number]
    property_pressure_absolute_Pa: list[Number]
    component_id: list[str]
    hole_interval_id: list[str]

    @model_validator(mode="after")
    def coordinate_identity(self):
        arrays = self.model_dump().values()
        if not self.md_m or len({len(x) for x in arrays}) != 1:
            raise ValueError(
                "ARTIFACT_INVALID: all profile arrays must share a nonempty coordinate"
            )
        if self.md_m[0] != 0 or any(b <= a for a, b in zip(self.md_m, self.md_m[1:])):
            raise ValueError("ARTIFACT_INVALID: MD must increase strictly from zero")
        return self


class ArtifactDescriptor(Data):
    id: str
    filename: str
    type: str
    schema_version: Literal["1.0"]
    byte_count: Annotated[int, Field(ge=0)]
    sha256: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    media_type: str
    compression: Literal["none"]
    retrieval_route: str


class ValidityResult(Data):
    solver_converged: Literal[True]
    fields_finite: Literal[True]
    domain_status: Literal["within", "qualified"]
    energy_status: Literal["pass", "flagged", "unavailable", "not_applicable"]
    assessment_status: str
    boundary_residuals: dict | None = None


class CompletedRunManifest(BaseModel):
    """Required scientific contract; new version-compatible diagnostics allowed."""

    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    schema_version: Literal["1.0"]
    mode: Literal["circulation_state", "site_assessment"]
    run_id: str
    scenario_id: str
    scenario_revision: int
    job_status: Literal["completed"]
    title: str
    acceptance: Literal["accepted", "qualified"]
    acceptance_policy: str
    physical_request_hash: str
    resolved_scenario_hash: str
    resolved_scenario: AnyScenario
    build: dict
    property_table: dict
    numerical_profile: dict
    numerical_profile_hash: str
    execution_identity: str
    validity: ValidityResult
    time: dict
    pressure_reference: dict
    summary: dict[str, ScalarResult]
    profile: ProfileResult
    warnings: list[dict]
    attempts: list[dict]
    artifacts: list[ArtifactDescriptor]
    limitations: list[str]
    wall_clock_duration_s: Nonnegative
    created_at: str
    completed_at: str


class HistoryDataset(Data):
    schema_version: Literal["history-data-1"] = "history-data-1"
    kind: Literal["wall", "tool"]
    id: Identifier
    time_s: list[Number]
    time_origin_s: Nonnegative = Field(
        default=0.0, exclude_if=lambda value: value == 0.0
    )
    state_index: list[int]
    state_id: list[str]
    state_label: list[str]
    event_side: list[Literal["left", "right", "interior"]]
    sample_kind: list[Literal["initial", "event_boundary", "accepted_step"]]
    grid: dict
    units: dict[str, str]
    md_m: list[Number]
    wall_temperature_C: list[Number] | None = None
    fluid_node_temperature_C: list[Number | None] | None = None
    wall_flux_W_m: list[Number | None] | None = None
    energy_in_J_m: list[Number] | None = None
    energy_far_J_m: list[Number] | None = None
    stored_J_m: list[Number] | None = None
    rock_temperature_C: list[list[Number]] | None = None
    steel_temperature_C: list[Number] | None = None
    internal_fluid_temperature_C: list[Number] | None = None
    events: list[dict]
    statistics: dict
    snapshots: list[dict]

    @model_validator(mode="after")
    def history_coordinates(self):
        n = len(self.time_s)
        arrays = [
            v
            for k, v in self.model_dump().items()
            if isinstance(v, list) and k not in ("events",)
        ]
        if (
            n < 2
            or any(len(v) != n for v in arrays)
            or self.time_s[0] != self.time_origin_s
            or any(b < a for a, b in zip(self.time_s, self.time_s[1:]))
        ):
            raise ValueError("ARTIFACT_INVALID: inconsistent history coordinates")
        if self.kind == "wall":
            required = (
                self.wall_temperature_C,
                self.fluid_node_temperature_C,
                self.wall_flux_W_m,
                self.energy_in_J_m,
                self.energy_far_J_m,
                self.stored_J_m,
                self.rock_temperature_C,
            )
            if any(v is None for v in required):
                raise ValueError("ARTIFACT_INVALID: wall history fields missing")
            r = self.grid.get("radius_m", [])
            if (
                len(r) < 2
                or any(b <= a for a, b in zip(r, r[1:]))
                or any(len(row) != len(r) for row in self.rock_temperature_C)
            ):
                raise ValueError("ARTIFACT_INVALID: radial grid/field mismatch")
            if self.grid.get("sha256") != digest({"radius_m": r}):
                raise ValueError("ARTIFACT_INVALID: radial grid identity mismatch")
        elif (
            self.steel_temperature_C is None
            or self.internal_fluid_temperature_C is None
        ):
            raise ValueError("ARTIFACT_INVALID: tool temperatures missing")
        elif self.grid.get("sha256") != digest(
            ToolLump.model_validate(self.grid.get("lump", {})).model_dump()
        ):
            raise ValueError("ARTIFACT_INVALID: tool grid identity mismatch")
        if (
            any(z < 0 for z in self.md_m)
            or any(i < 0 for i in self.state_index)
            or self.state_index[0] != 0
        ):
            raise ValueError("ARTIFACT_INVALID: invalid depth/state coordinate")
        for event in self.events:
            right, left = event.get("right_index"), event.get("left_index")
            if (
                not isinstance(right, int)
                or isinstance(right, bool)
                or not 0 <= right < n
                or self.state_id[right] != event.get("state_id")
                or self.state_index[right] != event.get("state_index")
                or not isinstance(event.get("time_s"), (int, float))
                or not 0 <= event["time_s"] <= self.time_s[right]
                or (
                    left is not None
                    and (
                        not isinstance(left, int)
                        or not 0 <= left < right
                        or self.time_s[left] > event["time_s"] + 1.0e-8
                    )
                )
            ):
                raise ValueError("ARTIFACT_INVALID: event identity/coordinate mismatch")
        for i, snapshot in enumerate(self.snapshots):
            field = (
                self.rock_temperature_C[i]
                if self.kind == "wall"
                else [self.steel_temperature_C[i], self.internal_fluid_temperature_C[i]]
            )
            expected_bracket = [
                self.time_s[i - 1]
                if i and self.sample_kind[i] == "accepted_step"
                else self.time_s[i],
                self.time_s[i],
            ]
            if (
                snapshot.get("index") != i
                or snapshot.get("time_s") != self.time_s[i]
                or snapshot.get("state_id") != self.state_id[i]
                or snapshot.get("event_side") != self.event_side[i]
                or snapshot.get("sample_kind") != self.sample_kind[i]
                or snapshot.get("grid_sha256") != self.grid.get("sha256")
                or snapshot.get("field_sha256") != digest(field)
                or snapshot.get("source_timestep_bracket_s") != expected_bracket
            ):
                raise ValueError(
                    "ARTIFACT_INVALID: snapshot identity/checksum mismatch"
                )
        for i, (a, b) in enumerate(zip(self.time_s, self.time_s[1:])):
            if a == b and not (
                self.event_side[i] == "left"
                and self.event_side[i + 1] == "right"
                and self.state_index[i] != self.state_index[i + 1]
            ):
                raise ValueError(
                    "ARTIFACT_INVALID: duplicate physical time requires explicit boundary sides"
                )
        return self


class HistoryRunManifest(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    schema_version: Literal["1.0"]
    mode: Literal["wall_history", "tool_history"]
    run_id: str
    scenario_id: str
    scenario_revision: int
    job_status: Literal["completed"]
    title: str
    acceptance: Literal["qualified"]
    acceptance_policy: str
    physical_request_hash: str
    resolved_scenario_hash: str
    resolved_scenario: WallHistoryScenario | ToolHistoryScenario
    build: dict
    property_table: dict
    numerical_profile: dict
    numerical_profile_hash: str
    execution_identity: str
    validity: ValidityResult
    time: dict
    pressure_reference: dict
    summary: dict[str, ScalarResult]
    history: dict
    warnings: list[dict]
    attempts: list[dict]
    artifacts: list[ArtifactDescriptor]
    limitations: list[str]
    wall_clock_duration_s: Nonnegative
    created_at: str
    completed_at: str


def validate_completed_manifest(value):
    return (
        HistoryRunManifest
        if value.get("mode") in ("wall_history", "tool_history")
        else CompletedRunManifest
    ).model_validate(value)


class FailedRunManifest(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    scenario_id: str
    scenario_revision: int
    job_status: Literal["failed", "cancelled", "timed_out"]
    acceptance: Literal["rejected"]
    failure: dict[str, str]
    created_at: str
    completed_at: str


class RunEnvelope(Data):
    id: str
    scenario_id: str
    scenario_revision: int
    state: Literal[
        "queued",
        "preparing",
        "running",
        "postprocessing",
        "completed",
        "cancel_requested",
        "cancelled",
        "failed",
        "timed_out",
    ]
    created_at: str
    updated_at: str
    failure_kind: str | None
    manifest: CompletedRunManifest | HistoryRunManifest | FailedRunManifest | None


class ErrorRecord(Data):
    code: str
    severity: Literal["error"] = "error"
    path: str = "/"
    message: str
    related_fields: list[str] = []
    evidence: dict = {}


class ErrorEnvelope(Data):
    detail: list[ErrorRecord]


class JobFailure(Data):
    code: str
    message: str


class Qualification(Data):
    code: str
    severity: Literal["warning"] = "warning"
    path: str = "/definition"
    message: str
    evidence: dict = {}


class JobEvent(Data):
    schema_version: Literal["1.0"] = "1.0"
    sequence: Annotated[int, Field(ge=0)]
    timestamp: str
    run_id: str
    scenario_id: str
    scenario_revision: Annotated[int, Field(ge=1)]
    type: Literal[
        "queued",
        "stage_started",
        "attempt_started",
        "attempt_finished",
        "progress",
        "warning",
        "artifact_available",
        "checkpoint_available",
        "cancel_requested",
        "completed",
        "failed",
        "cancelled",
        "timed_out",
        "reset",
    ]
    stage: Literal["preparing", "running", "postprocessing"] | None = None
    name: str | None = None
    attempt: Annotated[int, Field(ge=1)] | None = None
    acceptance: Literal["accepted", "qualified", "rejected"] | None = None
    failure: JobFailure | None = None
    completed_work: Annotated[int, Field(ge=0)] | None = None
    total_work: Annotated[int, Field(ge=0)] | None = None
    reason: Literal["events_expired", "cursor_ahead"] | None = None
    status_route: str | None = None
    strategy: str | None = None
    role: Literal["target", "continuation_seed"] | None = None
    target_attempt: Annotated[int, Field(ge=1, le=3)] | None = None
    duration_s: Nonnegative | None = None
    outcome: Literal["validated", "rejected"] | None = None
    solver_status: int | None = None
    solver_message: str | None = None
    seed_run_id: str | None = None
    seed_profile_sha256: str | None = None
    warning: Qualification | None = None
    artifact: ArtifactDescriptor | None = None
    process_id: Annotated[int, Field(ge=1)] | None = None
    checkpoint_id: str | None = None
    time_s: Nonnegative | None = None
