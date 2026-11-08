"""Non-convergence and property-domain errors for circulation runs."""

import json
from pathlib import Path

import pytest

from borethm_workbench.adapter import NOT_CONVERGED, CalculationError, run_circulation
from borethm_workbench.contracts import parse_scenario

PRESETS = Path(__file__).resolve().parents[1] / "src/borethm_workbench/presets"


def preset(name, physics):
    data = json.loads((PRESETS / f"{name}.json").read_bytes())
    data["model"]["physics"] = physics
    return data


def test_unsuccessful_model_result_is_a_non_convergence():
    scenario = parse_scenario(preset("high-temperature", "v1.8.0"))
    with pytest.raises(CalculationError) as caught:
        run_circulation(scenario)
    error = caught.value
    assert error.code == "SOLVER_NOT_CONVERGED"
    assert str(error) == NOT_CONVERGED == "The circulation calculation did not converge for these inputs."
    assert error.diagnostics["model_success"] is False
    # The unsuccessful profile is far outside the water-property table.
    assert error.diagnostics["temperature_range_C"][1] > 1000
    assert len(error.diagnostics["attempts"]) == 1


def test_same_preset_converges_without_friction_heating():
    data = preset("high-temperature", "v1.8.0")
    data["definition"]["heat"]["friction_heating"] = False
    result = run_circulation(parse_scenario(data))
    assert 200 < result["summary"]["bit_bore_delivery_temperature"]["value"] < 250


def test_formation_outside_the_table_is_rejected_before_solving(monkeypatch):
    import borethm

    def solve(*args, **kwargs):
        raise AssertionError("no solve expected")

    monkeypatch.setattr(borethm, "circulating_temperatures", solve)
    data = preset("synthetic-water", "v1.8.0")
    data["definition"]["formation"]["geotherm"]["points_tvd_m_temperature_C"][-1][1] = 520.0
    with pytest.raises(CalculationError) as caught:
        run_circulation(parse_scenario(data))
    assert caught.value.code == "PROPERTY_DOMAIN_OUTSIDE"
    assert "formation temperature at the bit" in str(caught.value)
