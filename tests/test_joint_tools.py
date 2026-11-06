"""Declared joint transitions and named tools use the selected model policy."""

import pytest
from pydantic import ValidationError

from borethm_workbench.adapter import make_geometry, physics_record
from borethm_workbench.contracts import Joint, ToolLoss


@pytest.mark.parametrize("kind", ["ordinary", "insulated", "heavy-weight", "bha"])
@pytest.mark.parametrize("physics", ["v1.8.0", "v1.7.0"])
def test_joint_default_is_resolved_by_kind(scenario, kind, physics):
    scenario.model.physics = physics
    scenario.definition.well.string[0].pipe.hydraulic_joint = Joint(
        outer_diameter_m=0.14,
        bore_diameter_m=0.09,
        restriction_length_m=0.2,
        spacing_m=9.5,
        kind=kind,
        source="Synthetic geometry",
    )
    geometry = make_geometry(scenario)
    joint = physics_record(scenario, geometry=geometry)["joints"][0]
    assert joint["transition"] == (
        "tapered" if kind == "ordinary" and physics == "v1.8.0" else "abrupt"
    )
    if joint["transition"] == "tapered":
        assert joint["taper_angle_deg"] == 11.5


def test_explicit_taper_requires_angle():
    data = dict(
        outer_diameter_m=0.14,
        bore_diameter_m=0.09,
        restriction_length_m=0.2,
        spacing_m=9.5,
        source="Synthetic",
    )
    with pytest.raises(ValidationError, match="included angle"):
        Joint(**data, transition="tapered")
    assert (
        Joint(**data, transition="tapered", taper_angle_deg=20.0).taper_angle_deg
        == 20.0
    )


@pytest.mark.parametrize(
    "tool_class",
    ["mwd_pulser", "rotary_steerable", "mud_motor", "filter_sub", "float_sub"],
)
@pytest.mark.parametrize("physics", ["v1.8.0", "v1.7.0"])
def test_generic_allowance_and_supplied_override(scenario, tool_class, physics):
    scenario.model.physics = physics
    segment = scenario.definition.well.string[-1]
    segment.tool_class = tool_class
    segment.collar_in = 6.75
    geometry = make_geometry(scenario)
    tool = geometry.string[-1].tools[0]
    assert (tool.resolved(physics) is None) == (physics == "v1.7.0")
    segment.tool_loss = ToolLoss(
        reference_pressure_Pa=12345.0,
        reference_flow_m3_s=0.03,
        reference_density_kg_m3=1000.0,
        basis="additional",
        source="Synthetic supplied figure",
    )
    geometry = make_geometry(scenario)
    assert geometry.string[-1].tools == ()
    assert geometry.string[-1].tool_loss.dp_ref == 12345.0
