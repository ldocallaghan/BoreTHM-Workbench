"""Display units: factors, round trips, entry equivalence and hash stability."""

from fractions import Fraction
import json
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from borethm_workbench import units
from borethm_workbench.contracts import canonical_bytes, parse_scenario_json, physical_hash
from borethm_workbench.service import create_app

ROOT = Path(__file__).resolve().parents[1]
PRESETS = ROOT / "src/borethm_workbench/presets"


def test_defining_factors_are_exact():
    assert units.FOOT_M == 0.3048
    assert units.INCH_M == 0.0254
    assert units.POUND_KG == 0.45359237
    assert units.US_GALLON_L == 3.785411784
    assert units.PSI_PA == 6894.757293168
    assert units.PPG_KG_M3 == 119.826427


EXPECTED = {
    # quantity: (oilfield value, SI value) pairs that define the conversion.
    "length": [(1.0, 0.3048), (10000.0, 3048.0)],
    "diameter": [(1.0, 0.0254), (8.5, 0.2159)],
    "temperature": [(32.0, 0.0), (212.0, 100.0), (-40.0, -40.0)],
    "temperature_difference": [(9.0, 5.0)],
    "temperature_gradient": [(1.0, (5 / 9) / 30.48)],
    "pressure": [(1.0, 6894.757293168), (14.503773773, 100000.0)],
    "pressure_gradient": [(1.0, 6894.757293168 / 0.3048), (0.433, 0.433 * 6894.757293168 / 0.3048)],
    "density": [(1.0, 119.826427), (8.345, 8.345 * 119.826427)],
    "specific_gravity": [(1.0, 1000.0)],
    "volume_flow": [(1.0, 3.785411784e-3 / 60), (500.0, 500 * 3.785411784e-3 / 60)],
    "mass_flow": [(1.0, 0.45359237)],
    "conductivity": [(1.0, 1055.05585262 / 3600 / (0.3048 * 5 / 9))],
    "power": [(1.0, 550 * 0.3048 * 0.45359237 * 9.80665)],
    "velocity": [(1.0, 0.3048 / 60), (245.0, 245.0 * 0.3048 / 60)],
    "rate_of_penetration": [(1.0, 0.3048), (20.0, 6.096)],
    "minutes": [(6.33, 6.33)],
    "hours": [(17.5, 17.5)],
}

SI_DISPLAY = {
    "length": (1.0, 1.0),
    "diameter": (1.0, 0.001),
    "temperature": (25.0, 25.0),
    "temperature_difference": (1.0, 1.0),
    "temperature_gradient": (30.0, 0.03),
    "pressure": (1.0, 1e6),
    "pressure_gradient": (10.0, 10000.0),
    "density": (1000.0, 1000.0),
    "specific_gravity": (1.2, 1200.0),
    "volume_flow": (40.0, 0.04),
    "mass_flow": (30.0, 30.0),
    "conductivity": (2.5, 2.5),
    "power": (28.0, 28000.0),
    "velocity": (1.245, 1.245),
    "rate_of_penetration": (6.0, 6.0),
    "minutes": (15.51, 15.51),
    "hours": (24.0, 24.0),
}


def test_every_quantity_has_both_systems_and_a_test():
    assert set(units.QUANTITIES) == set(EXPECTED) == set(SI_DISPLAY)
    for name, (_, _, systems) in units.QUANTITIES.items():
        assert set(systems) == set(units.SYSTEMS), name


@pytest.mark.parametrize("quantity", sorted(EXPECTED))
def test_oilfield_factors(quantity):
    for shown, si in EXPECTED[quantity]:
        assert math.isclose(units.to_si(shown, quantity, "oilfield"), si, rel_tol=1e-11, abs_tol=1e-12)
        assert math.isclose(units.from_si(si, quantity, "oilfield"), shown, rel_tol=1e-11, abs_tol=1e-9)


@pytest.mark.parametrize("quantity", sorted(SI_DISPLAY))
def test_si_display_factors(quantity):
    shown, si = SI_DISPLAY[quantity]
    assert math.isclose(units.to_si(shown, quantity, "SI"), si, rel_tol=1e-15)
    assert math.isclose(units.from_si(si, quantity, "SI"), shown, rel_tol=1e-15)


@pytest.mark.parametrize("quantity", sorted(units.QUANTITIES))
@pytest.mark.parametrize("system", units.SYSTEMS)
def test_round_trip_to_twelve_significant_figures(quantity, system):
    for value in (0.0, 1e-4, 0.0254, 1.0, 3.5, 999.0, 3048.0, 2.5e6, 4.5e7, -12.5):
        back = units.to_si(units.from_si(value, quantity, system), quantity, system)
        assert back == units.round_significant(value) or math.isclose(back, value, rel_tol=5e-12, abs_tol=1e-15)


# Exact definitions as fractions: SI display value per oilfield unit, with
# the oilfield offset, independent of the module's floating-point factors.
FOOT = Fraction("0.3048")
PSI = Fraction("6894.757293168")
EXACT = {
    "length": (FOOT, 0, Fraction(1)),
    "diameter": (Fraction("0.0254"), 0, Fraction("0.001")),
    "temperature": (Fraction(5, 9), -32, Fraction(1)),
    "temperature_gradient": (Fraction(5, 9) / (100 * FOOT), 0, Fraction("0.001")),
    "pressure": (PSI, 0, Fraction(10**6)),
    "pressure_gradient": (PSI / FOOT, 0, Fraction(1000)),
    "density": (Fraction("119.826427"), 0, Fraction(1)),
    "volume_flow": (Fraction("3.785411784") / 1000 / 60, 0, Fraction("0.001")),
    "mass_flow": (Fraction("0.45359237"), 0, Fraction(1)),
    "power": (550 * FOOT * Fraction("0.45359237") * Fraction("9.80665"), 0, Fraction(1000)),
}


@pytest.mark.parametrize("quantity", sorted(EXACT))
def test_same_physical_entry_gives_the_same_si_value(quantity):
    factor, offset, si_display_factor = EXACT[quantity]
    for oilfield in ("1", "2.5", "40", "104", "212", "3200.75", "10000"):
        exact_si = (Fraction(oilfield) + offset) * factor
        # The same value typed in SI display units, to 12 significant figures.
        typed_si = float(f"{float(exact_si / si_display_factor):.11e}")
        a = units.to_si(float(oilfield), quantity, "oilfield")
        b = units.to_si(typed_si, quantity, "SI")
        assert f"{a:.11e}" == f"{b:.11e}", (quantity, oilfield, a, b)
        assert math.isclose(a, float(exact_si), rel_tol=1e-11, abs_tol=1e-12)


def test_units_contract_matches_module():
    data = (ROOT / "contracts/units.json").read_bytes()
    assert data == canonical_bytes(units.units_table())


def test_formatting_uses_display_symbols():
    assert units.fmt(3048.0, "length", "oilfield") == "10,000 ft"
    assert units.fmt(100.0, "temperature", "oilfield") == "212.0 °F"
    assert units.fmt(2e6, "pressure", "SI") == "2.00 MPa"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, start_jobs=False)
    with TestClient(app) as test_client:
        yield test_client
    app.state.store.close()


def test_unit_choice_is_a_stored_setting(client, tmp_path):
    assert client.get("/api/v1/settings").json() == {"units": "SI"}
    assert client.put("/api/v1/settings", json={"units": "oilfield"}).json() == {"units": "oilfield"}
    assert client.get("/api/v1/settings").json() == {"units": "oilfield"}
    assert json.loads((tmp_path / "settings.json").read_text()) == {"units": "oilfield"}
    assert client.put("/api/v1/settings", json={"units": "imperial"}).status_code == 422
    assert client.get("/api/v1/units").json() == units.units_table()


def test_scenario_hash_does_not_depend_on_the_unit_choice(client):
    raw = (PRESETS / "synthetic-water.json").read_bytes()
    scenario = parse_scenario_json(raw)
    expected = physical_hash(scenario)
    hashes = []
    for system in ("SI", "oilfield", "SI"):
        client.put("/api/v1/settings", json={"units": system})
        response = client.post("/api/v1/scenarios/validate", json=json.loads(raw))
        assert response.status_code == 200
        hashes.append(response.json()["physical_hash"])
        assert canonical_bytes(response.json()["resolved_scenario"]) == canonical_bytes(
            scenario.model_dump()
        )
    assert hashes == [expected] * 3


def test_a_value_that_rounds_to_zero_prints_without_a_sign():
    from borethm_workbench import svg

    # A change of -0.01 K is -0.018 F, shown to one decimal.
    assert units.fmt(-0.01, "temperature_difference", "oilfield") == "0.0 °F"
    assert units.fmt(-0.05, "temperature_difference", "oilfield") == "-0.1 °F"
    assert units.fmt(-1e-9, "pressure", "SI") == "0.00 MPa"
    assert svg._label(-1e-11) == "0" and svg._label(-0.25) == "-0.25"
    assert units.unsigned_zero("-0") == "0" and units.unsigned_zero("-0,000.00") == "0,000.00"
