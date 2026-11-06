import numpy as np
import pytest

from borethm_workbench import adapter
from borethm_workbench.contracts import Scenario


def test_direct_core_parity(scenario, physical_result):
    import borethm

    options = dict(
        m_dot=30.0,
        geometry=adapter.make_geometry(scenario),
        **adapter.make_settings(scenario),
    )
    direct = borethm.circulating_temperatures(**options)
    from borethm_workbench.contracts import numerical_profile

    if numerical_profile(scenario).get("accepted_profile_refinement"):
        direct = borethm.circulating_temperatures(
            **{**options, "init": {k: direct[k] for k in ("z", "Td", "Tu")}}
        )
    assert direct["success"]
    for key, original in (
        ("bore_temperature_C", "Td"),
        ("annulus_temperature_C", "Tu"),
    ):
        np.testing.assert_allclose(
            physical_result["profile"][key], direct[original], rtol=0, atol=0.001
        )
    hyd = borethm.hydraulics(
        30.0,
        direct["geometry"],
        result=direct,
        tfa=0.0007759,
        P_surface=1e6,
        surface=direct["surface"],
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
        assert abs(physical_result["summary"][key]["value"] - hyd[key]) <= max(
            1.0, 1e-6 * abs(hyd[key])
        )


def test_surface_flow_equivalence_water_and_mud(scenario):
    for definition in (
        {"kind": "iapws_water"},
        dict(
            kind="barite_mud",
            surface_density_kg_m3=1200.0,
            plastic_viscosity_Pa_s=0.02,
            viscosity_reference_temperature_C=20.0,
            yield_point_Pa=5.0,
            conserve_mass=True,
        ),
    ):
        data = scenario.model_dump()
        data["definition"]["fluid"] = definition
        data["definition"]["operating"]["flow"] = dict(
            kind="surface_volume",
            volume_rate_m3_s=0.03,
            temperature_C=20.0,
            pressure_absolute_Pa=1e5,
        )
        current = Scenario.model_validate(data)
        fluid = adapter.make_fluid(current.definition.fluid)
        rate = adapter.mass_rate(current, fluid)
        from borethm.model1_coupled import fluid_props

        rho = fluid_props(np.array([20.0]), np.array([1e5]), fluid)[0][0]
        assert rate == pytest.approx(0.03 * rho)
        equivalent = current.model_dump()
        equivalent["definition"]["operating"]["flow"] = dict(
            kind="mass", mass_rate_kg_s=rate
        )
        result_a = adapter.run_circulation(current)
        result_b = adapter.run_circulation(Scenario.model_validate(equivalent))
        np.testing.assert_allclose(
            result_a["profile"]["bore_temperature_C"],
            result_b["profile"]["bore_temperature_C"],
            atol=0.001,
        )


def test_exposure_floors_remain_distinct(scenario):
    from borethm.model1_coupled import exposure_seconds

    data = scenario.model_dump()
    data["definition"]["operating"]["exposure"] = {
        "kind": "legacy_uniform_years",
        "years": 0.0,
    }
    years = adapter.make_settings(Scenario.model_validate(data))
    data["definition"]["operating"]["exposure"] = {
        "kind": "uniform_seconds",
        "seconds": 0.0,
    }
    seconds = adapter.make_settings(Scenario.model_validate(data))
    z = np.array([0.0, 3000.0])
    assert np.all(exposure_seconds(z, years["t_years"], years["exposure"]) == 31536)
    assert np.all(exposure_seconds(z, seconds["t_years"], seconds["exposure"]) == 3600)


def test_failed_solver_is_rejected_even_with_passing_energy(scenario, monkeypatch):
    import borethm
    from types import SimpleNamespace

    monkeypatch.setattr(
        borethm,
        "circulating_temperatures",
        lambda **kw: {
            "sol": SimpleNamespace(
                success=False, status=1, message="Injected non-convergence"
            ),
            "energy_check": {"passed": True},
        },
    )
    with pytest.raises(adapter.CalculationError) as error:
        adapter.run_circulation(scenario)
    assert error.value.code == "SOLVER_NOT_CONVERGED"
    assert error.value.diagnostics["solver_converged"] is False


def test_guard_and_audit_coverage_persist(physical_result):
    assert physical_result["validity"]["solver_converged"]
    assert physical_result["acceptance"] == "qualified"
    codes = {w["code"] for w in physical_result["warnings"]}
    assert {"PROPERTY_GUARD_ACTIVE", "PROPERTY_AUDIT_INCOMPLETE"} <= codes
    assert physical_result["validity"]["assessment_status"] == "not_evaluated"


def test_signed_budget_and_wall_reference(physical_result):
    h = physical_result["hydraulics"]
    assert h["dp_buoyancy"] < 0
    assert h["spp"] == pytest.approx(
        sum(
            h[k]
            for k in ("dp_string", "dp_annulus", "dp_bit", "dp_buoyancy", "dp_surface")
        ),
        abs=1.0,
    )
    p = physical_result["profile"]
    assert p["annulus_wall_pressure_gauge_Pa"][0] == 900000.0
    assert p["property_pressure_absolute_Pa"][0] == 1e6
    assert (
        physical_result["pressure_reference"]["wall_basis"]
        != physical_result["pressure_reference"]["budget_basis"]
    )


def test_constant_property_column_matches_analytic(scenario):
    data = scenario.model_dump()
    data["definition"]["fluid"] = dict(
        kind="constant_properties",
        density_kg_m3=1000.0,
        heat_capacity_J_kg_K=4200.0,
        viscosity_Pa_s=0.001,
        conductivity_W_m_K=0.6,
        approximation_label="Analytic fixture",
    )
    result = adapter.run_circulation(Scenario.model_validate(data))
    p = result["profile"]
    expected = 1000 * 9.81 * np.array(p["md_m"])
    np.testing.assert_allclose(
        p["annulus_hydrostatic_pressure_difference_Pa"], expected, atol=1.0
    )


def test_energy_source_ledger_is_not_full_validation(physical_result):
    assert physical_result["validity"]["energy_status"] == "pass"
    assert physical_result["source_ledger"]["base_face_W"] == 28000.0
    assert physical_result["source_ledger"]["surface_W_in_hole"] == 0.0
    assert "not a complete" in physical_result["source_ledger"]["diagnostic_scope"]
