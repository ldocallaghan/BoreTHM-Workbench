"""All presets use one selected physics policy throughout a run."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from borethm_workbench.adapter import CalculationError, run_circulation
from borethm_workbench.assessment import run_assessment
from borethm_workbench.contracts import parse_scenario
from borethm_workbench.histories import run_history

PRESETS = Path(__file__).resolve().parents[1] / "src/borethm_workbench/presets"


@pytest.mark.parametrize("path", sorted(PRESETS.glob("*.json")), ids=lambda p: p.stem)
def test_preset_calls_resolve_consistently(path, monkeypatch):
    from borethm import hydraulic_losses
    from borethm.physics import resolve
    import scenario_inputs

    scenario = parse_scenario(json.loads(path.read_bytes()))
    scenario.model.physics = scenario_inputs.PHYSICS
    expected = resolve(scenario.model.physics)
    calls = []
    original = hydraulic_losses.LossModel.__init__

    def observed(self, *args, **kwargs):
        original(self, *args, **kwargs)
        calls.append(
            dict(
                physics=self.physics,
                friction=self.friction,
                roughness=self.roughness,
                regime=self.regime,
                band=self.band,
                joints=[j for j in self.joints if j is not None],
                treatments=[
                    (
                        json.dumps(
                            asdict(segment.pipe.hydraulic_joint), sort_keys=True
                        ),
                        asdict(joint),
                    )
                    for segment, joint in zip(self.geometry.string, self.joints)
                    if joint is not None
                ],
            )
        )

    monkeypatch.setattr(hydraulic_losses.LossModel, "__init__", observed)
    operation = (
        run_assessment
        if scenario.mode == "site_assessment"
        else run_history
        if scenario.mode in ("wall_history", "tool_history")
        else run_circulation
    )
    try:
        result = operation(scenario)
    except CalculationError as error:
        # Under current physics the model reports no success for this preset.
        assert path.stem == "high-temperature"
        assert scenario.model.physics == "v1.8.0"
        assert error.code == "SOLVER_NOT_CONVERGED"
        assert str(error) == "The circulation calculation did not converge for these inputs."
    else:
        assert result["physics"] == scenario.model.physics
        for key, value in expected.items():
            assert result["physics_settings"][key] == value
    treatments = {}
    for call in calls:
        assert call["physics"] == scenario.model.physics
        assert call["friction"] == expected["friction"]
        assert call["roughness"] == expected["roughness"]
        assert call["regime"] == (
            "herschel-bulkley"
            if scenario.model.physics == "v1.8.0"
            else "apparent-viscosity"
        )
        assert call["band"] == (
            "erge" if scenario.model.physics == "v1.8.0" else "founargiotakis"
        )
        for declared, resolved in call["treatments"]:
            assert treatments.setdefault(declared, resolved) == resolved
        for joint in call["joints"]:
            assert joint.transition in ("abrupt", "gradual", "tapered")
            if joint.transition == "tapered":
                assert joint.taper_angle_deg is not None
