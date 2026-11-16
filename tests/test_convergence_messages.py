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


def superhot(physics, flow):
    """A vertical water well with 500 °C rock at the bit and a 20 °C inlet."""
    data = preset("synthetic-water", physics)
    data["definition"]["formation"]["geotherm"]["points_tvd_m_temperature_C"][-1][1] = 500.0
    data["definition"]["operating"]["flow"] = {"kind": "mass", "mass_rate_kg_s": flow}
    data["definition"]["operating"]["inlet_temperature_C"] = 20.0
    return parse_scenario(data)


@pytest.mark.parametrize("physics", ["v1.8.0", "v1.7.0"])
def test_superhot_formation_with_cold_circulation_reaches_the_solver(physics, monkeypatch):
    import borethm

    calls = []
    original = borethm.circulating_temperatures

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(borethm, "circulating_temperatures", counted)
    result = run_circulation(superhot(physics, 30.0))
    profile = result["profile"]
    assert calls
    assert profile["formation_temperature_C"][-1] == pytest.approx(500.0)
    fluid = profile["bore_temperature_C"] + profile["annulus_temperature_C"]
    assert max(fluid) < 479
    assert 230 < result["summary"]["bit_bore_delivery_temperature"]["value"] < 255


@pytest.mark.parametrize("physics", ["v1.8.0", "v1.7.0"])
def test_a_model_result_without_success_is_reported_as_non_convergence(physics, monkeypatch):
    """The model's own success flag decides, whatever the solver's convergence boundary."""
    import borethm

    calls = []
    original = borethm.circulating_temperatures

    def unsuccessful(*args, **kwargs):
        calls.append(1)
        return {**original(*args, **kwargs), "success": False}

    monkeypatch.setattr(borethm, "circulating_temperatures", unsuccessful)
    with pytest.raises(CalculationError) as caught:
        run_circulation(parse_scenario(preset("synthetic-water", physics)))
    error = caught.value
    assert calls == [1]
    assert error.code == "SOLVER_NOT_CONVERGED"
    assert str(error) == NOT_CONVERGED
    assert error.diagnostics["model_success"] is False
    [attempt] = error.diagnostics["attempts"]
    assert attempt["outcome"] == "rejected"
    assert attempt["failure_code"] == "SOLVER_NOT_CONVERGED"
