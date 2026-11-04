import copy
import json

import pytest
from pydantic import ValidationError

from borethm_workbench.adapter import CalculationError, make_geometry, validate
from borethm_workbench.contracts import Scenario, canonical_bytes, physical_hash


def test_metadata_and_order_do_not_change_physical_hash(scenario):
    original = physical_hash(scenario)
    scenario.title = "Different display title"
    scenario.metadata = {"notes": "hello", "display_units": "imperial"}
    assert physical_hash(scenario) == original
    assert (
        physical_hash(
            Scenario.model_validate(
                json.loads(json.dumps(scenario.model_dump(), sort_keys=True))
            )
        )
        == original
    )
    scenario.definition.operating.inlet_temperature_C += 1
    assert physical_hash(scenario) != original


def test_signed_zero_and_nonfinite():
    assert canonical_bytes({"x": -0.0}) == canonical_bytes({"x": 0.0})
    with pytest.raises(ValueError):
        canonical_bytes({"x": float("nan")})


def test_omitted_and_explicit_defaults_share_identity(scenario):
    data = scenario.model_dump()
    data["definition"]["well"]["string"][0]["pipe"].pop("thermal_joint_fraction")
    omitted = Scenario.model_validate(data)
    assert physical_hash(omitted) == physical_hash(scenario)


@pytest.mark.parametrize("invalid", [True, "30", float("inf"), -1, 0])
def test_flow_rejects_invalid_types(scenario, invalid):
    data = scenario.model_dump()
    data["definition"]["operating"]["flow"]["mass_rate_kg_s"] = invalid
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_unknown_or_unsupported_fields_rejected(scenario):
    data = scenario.model_dump()
    data["definition"]["heat"]["pressure_wave"] = True
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)
    data = scenario.model_dump()
    data["mode"] = "scheduled_run"
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


@pytest.mark.parametrize(
    "mutation", ["depth", "gap", "overlap", "zero_length", "layer", "joint"]
)
def test_geometry_is_not_silently_repaired(scenario, mutation):
    data = scenario.model_dump()
    well = data["definition"]["well"]
    if mutation == "depth":
        well["bit_md_m"] += 0.01
    elif mutation == "gap":
        well["hole"][0]["md_top_m"] = 0.001
    elif mutation == "overlap":
        well["hole"].append({**well["hole"][0], "id": "overlap"})
    elif mutation == "zero_length":
        well["string"][0]["length_m"] = 0
    elif mutation == "layer":
        well["string"][0]["pipe"]["layers"][0]["r_in_m"] = 0.07
    elif mutation == "joint":
        well["string"][0]["pipe"]["hydraulic_joint"] = dict(
            outer_diameter_m=0.25,
            bore_diameter_m=0.09,
            restriction_length_m=0.3,
            spacing_m=9.0,
            source="test",
        )
    with pytest.raises((ValidationError, CalculationError)):
        validate(Scenario.model_validate(data))


def test_unresolved_tool_qualification(scenario):
    from borethm_workbench.contracts import ToolLoss

    scenario.definition.well.string[0].tool_loss = ToolLoss(
        reference_pressure_Pa=0.0,
        reference_flow_m3_s=0.03,
        reference_density_kg_m3=1000.0,
        basis="additional",
        unresolved=True,
        source="Unknown",
    )
    warnings = validate(scenario)
    assert any(w["code"] == "INTERNAL_TOOL_LOSS_UNKNOWN" for w in warnings)
    make_geometry(scenario)


def test_declared_property_domain_rejected(scenario):
    scenario.definition.operating.inlet_temperature_C = 500.0
    with pytest.raises(CalculationError, match="outside"):
        validate(scenario)


def test_profile_contract_rejects_coordinate_mismatch_and_nonfinite(physical_result):
    from borethm_workbench.contracts import ProfileResult

    profile = copy.deepcopy(physical_result["profile"])
    ProfileResult.model_validate(profile)
    profile["annulus_temperature_C"].pop()
    with pytest.raises(ValidationError):
        ProfileResult.model_validate(profile)
    profile = copy.deepcopy(physical_result["profile"])
    profile["bore_temperature_C"][0] = float("nan")
    with pytest.raises(ValidationError):
        ProfileResult.model_validate(profile)
