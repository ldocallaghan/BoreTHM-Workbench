import json
from scenario_inputs import fixture_path
from pathlib import Path

import numpy as np
import pytest

from borethm_workbench.adapter import run_circulation
from borethm_workbench.contracts import Scenario

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "name", ["reference-assembly", "weighted-mud", "constant-properties"]
)
def test_frozen_physical_cohort(name):
    pin = json.loads(fixture_path("physical-cohort.json").read_bytes())["cases"][name]
    scenario = Scenario.model_validate(pin["scenario"])
    result = run_circulation(scenario)
    if scenario.schema_version == pin["scenario"]["schema_version"]:
        assert result["physical_request_hash"] == pin["physical_request_hash"]
    for key, scalar in pin["summary"].items():
        tolerance = (
            max(1.0, abs(scalar["value"]) * 1e-6)
            if scalar["unit"] in ("Pa difference", "W")
            else 0.001
        )
        assert abs(result["summary"][key]["value"] - scalar["value"]) <= tolerance
    assert (
        result["validity"]["solver_converged"] and result["validity"]["fields_finite"]
    )
    assert set(pin["warnings"]) <= {w["code"] for w in result["warnings"]}


def test_reference_adapter_matches_independent_programme_construction():
    import borethm
    from borethm.programme import HydraulicProgramme
    from borethm import well_geometry as wg

    pin = json.loads(fixture_path("physical-cohort.json").read_bytes())["cases"][
        "reference-assembly"
    ]
    scenario = Scenario.model_validate(pin["scenario"])
    programme = HydraulicProgramme.reference_programme()
    from borethm.physics import using

    with using(scenario.model.physics):
        geometry = programme.geometry(3000.0, wg.CONVENTIONAL)
    options = dict(
        m_dot=30.0,
        T_inj=40.0,
        G=0.035,
        T_surf=15.0,
        target_rock_T=120.0,
        Q_face=28000.0,
        t_years=0.01,
        k_rock=2.5,
        n_nodes=160,
        verbose=False,
        geotherm=borethm.layered_geotherm(((0.0, 15.0), (3000.0, 120.0)), 0.035),
        target_depth=None,
        geometry=geometry,
        exposure=None,
        fluid=None,
        rhocp_rock=2.7e6,
        friction_heat=True,
        tfa=0.0007759,
        film_mult=1.0,
        breaks=(),
        init=None,
        P_surface=1e6,
        physics=scenario.model.physics,
        surface=programme.surface,
        transitions="abrupt",
        rock_response="cylinder",
        annulus_heat=None,
    )
    direct = borethm.circulating_temperatures(**options)
    from borethm_workbench.contracts import numerical_profile

    if numerical_profile(scenario).get("accepted_profile_refinement"):
        direct = borethm.circulating_temperatures(
            **{**options, "init": {k: direct[k] for k in ("z", "Td", "Tu")}}
        )
    adapted = run_circulation(scenario)
    assert direct["success"]
    md = np.asarray(adapted["profile"]["md_m"])
    for index, key in enumerate(("bore_temperature_C", "annulus_temperature_C")):
        np.testing.assert_allclose(
            adapted["profile"][key], direct["sol"].sol(md)[index], atol=0.001, rtol=0
        )
    hyd = borethm.hydraulics(
        30.0,
        geometry,
        result=direct,
        fluid=None,
        tfa=0.0007759,
        P_surface=1e6,
        surface=programme.surface,
        transitions="abrupt",
        physics=scenario.model.physics,
    )
    for key in (
        "spp",
        "dp_string",
        "dp_annulus",
        "dp_bit",
        "dp_buoyancy",
        "dp_surface",
    ):
        assert abs(hyd[key] - adapted["summary"][key]["value"]) <= max(
            1.0, abs(hyd[key]) * 1e-6
        )


def test_exact_geometry_geotherm_and_heat_knots_survive_sampling():
    pin = json.loads(fixture_path("physical-cohort.json").read_bytes())["cases"][
        "reference-assembly"
    ]
    scenario = Scenario.model_validate(pin["scenario"])
    from borethm_workbench.contracts import AnnulusSource

    scenario.definition.heat.annulus_source = AnnulusSource(
        kind="prescribed_heating",
        interpolation="linear",
        source="Synthetic knot-preservation fixture",
        points_md_m_W_m=[(0.0, 2.0), (1234.5, 4.0), (3000.0, 1.0)],
    )
    scenario.definition.formation.geotherm.points_tvd_m_temperature_C = [
        (0.0, 15.0),
        (1637.25, 70.0),
        (3000.0, 120.0),
    ]
    result = run_circulation(scenario)
    md = result["profile"]["md_m"]
    ends = np.cumsum([s.length_m for s in scenario.definition.well.string])
    for knot in [
        *ends,
        *[h.md_top_m for h in scenario.definition.well.hole],
        1234.5,
        1637.25,
    ]:
        assert float(knot) in md
    assert "blended" in result["sampling"]["hydraulic_local_losses"]
    assert result["source_ledger"]["surface_W_in_hole"] == 0


def test_audit_reports_each_property_path_without_claiming_trial_coverage(scenario):
    result = run_circulation(scenario)
    audit = result["property_audit"]
    assert not audit["internal_trial_lookups_instrumented"]
    assert not audit["wall_column_intermediate_passes_instrumented"]
    assert audit["table_temperature_C"] == [1.0, 479.0]
    hydraulic = audit["paths"]["hydraulic_liquid_properties"]
    assert (
        1e6 < hydraulic["raw_pressure_absolute_Pa"][0] < 1.01e6
    )  # first interior quadrature point
    assert hydraulic["effective_guarded_pressure_absolute_Pa"][0] == 5e6
