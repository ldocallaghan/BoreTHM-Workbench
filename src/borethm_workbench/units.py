"""Display units for entry and display.

Scenarios, archives, contracts and the model hold SI values. This module is
the single source of the display-unit table and its conversion factors. The
frontend reads the same table from ``contracts/units.json``, which the
``contracts`` command writes from ``units_table()``.

A display value is ``si_value / factor - offset``; an SI value is
``(display_value + offset) * factor``. Only temperature uses an offset.
"""

import math

SYSTEMS = ("SI", "oilfield")

# Exact defining factors.
FOOT_M = 0.3048
INCH_M = 0.0254
POUND_KG = 0.45359237
US_GALLON_L = 3.785411784
PSI_PA = 6894.757293168
PPG_KG_M3 = 119.826427
# Specific gravity uses the model's reference: 1,000 kg/m3 per unit SG.
SG_KG_M3 = 1000.0
# British thermal unit (International Table) in joules, used for conductivity.
BTU_J = 1055.05585262
HOUR_S = 3600.0
# Mechanical horsepower: 550 ft lbf/s, with standard gravity 9.80665 m/s2.
HORSEPOWER_W = 550.0 * FOOT_M * POUND_KG * 9.80665

# quantity: label, SI internal unit, then {system: (symbol, factor, offset)}.
QUANTITIES = {
    "length": (
        "Depth, length",
        "m",
        {"SI": ("m", 1.0, 0.0), "oilfield": ("ft", FOOT_M, 0.0)},
    ),
    "diameter": (
        "Pipe and hole diameter",
        "m",
        {"SI": ("mm", 0.001, 0.0), "oilfield": ("in", INCH_M, 0.0)},
    ),
    "temperature": (
        "Temperature",
        "°C",
        {"SI": ("°C", 1.0, 0.0), "oilfield": ("°F", 5.0 / 9.0, -32.0)},
    ),
    "temperature_difference": (
        "Temperature difference",
        "K",
        {"SI": ("°C", 1.0, 0.0), "oilfield": ("°F", 5.0 / 9.0, 0.0)},
    ),
    "temperature_gradient": (
        "Temperature gradient",
        "K/m",
        {
            "SI": ("K/km", 0.001, 0.0),
            "oilfield": ("°F/100 ft", (5.0 / 9.0) / (100.0 * FOOT_M), 0.0),
        },
    ),
    "pressure": (
        "Pressure, stress",
        "Pa",
        {"SI": ("MPa", 1e6, 0.0), "oilfield": ("psi", PSI_PA, 0.0)},
    ),
    "pressure_gradient": (
        "Pressure or stress gradient",
        "Pa/m",
        {"SI": ("kPa/m", 1000.0, 0.0), "oilfield": ("psi/ft", PSI_PA / FOOT_M, 0.0)},
    ),
    "density": (
        "Fluid density",
        "kg/m³",
        {"SI": ("kg/m³", 1.0, 0.0), "oilfield": ("ppg", PPG_KG_M3, 0.0)},
    ),
    "specific_gravity": (
        "Specific gravity",
        "kg/m³",
        {"SI": ("SG", SG_KG_M3, 0.0), "oilfield": ("SG", SG_KG_M3, 0.0)},
    ),
    "volume_flow": (
        "Volume flow",
        "m³/s",
        {"SI": ("L/s", 0.001, 0.0), "oilfield": ("gpm", US_GALLON_L * 0.001 / 60.0, 0.0)},
    ),
    "mass_flow": (
        "Mass flow",
        "kg/s",
        {"SI": ("kg/s", 1.0, 0.0), "oilfield": ("lb/s", POUND_KG, 0.0)},
    ),
    "conductivity": (
        "Thermal conductivity",
        "W/(m K)",
        {
            "SI": ("W/(m K)", 1.0, 0.0),
            "oilfield": ("Btu/(h ft °F)", BTU_J / HOUR_S / (FOOT_M * 5.0 / 9.0), 0.0),
        },
    ),
    "velocity": (
        "Velocity",
        "m/s",
        {"SI": ("m/s", 1.0, 0.0), "oilfield": ("ft/min", FOOT_M / 60.0, 0.0)},
    ),
    "rate_of_penetration": (
        "Rate of penetration",
        "m/h",
        {"SI": ("m/h", 1.0, 0.0), "oilfield": ("ft/h", FOOT_M, 0.0)},
    ),
    "minutes": (
        "Duration in minutes",
        "min",
        {"SI": ("min", 1.0, 0.0), "oilfield": ("min", 1.0, 0.0)},
    ),
    "hours": (
        "Duration in hours",
        "h",
        {"SI": ("h", 1.0, 0.0), "oilfield": ("h", 1.0, 0.0)},
    ),
    "power": (
        "Power",
        "W",
        {"SI": ("kW", 1000.0, 0.0), "oilfield": ("hp", HORSEPOWER_W, 0.0)},
    ),
}

# Significant figures kept when a display entry becomes an SI value. Entry in
# either system of the same physical value then gives the same SI number.
ENTRY_SIGNIFICANT_FIGURES = 12


def round_significant(value, figures=ENTRY_SIGNIFICANT_FIGURES):
    """Round a finite float to the given significant figures."""
    if value == 0 or not math.isfinite(value):
        return value
    return float(f"{value:.{figures - 1}e}")


def _entry(quantity, system):
    if system not in SYSTEMS:
        raise ValueError(f"unknown unit system {system!r}")
    return QUANTITIES[quantity][2][system]


def symbol(quantity, system):
    return _entry(quantity, system)[0]


def from_si(value, quantity, system):
    """Convert an SI value to the display unit of a system."""
    _, factor, offset = _entry(quantity, system)
    return value / factor - offset


def to_si(value, quantity, system):
    """Convert an entered display value to SI, to 12 significant figures."""
    _, factor, offset = _entry(quantity, system)
    if factor == 1.0 and offset == 0.0:
        return value
    return round_significant((value + offset) * factor)


def units_table():
    """The table served to the frontend and written to contracts/units.json."""
    return {
        "systems": list(SYSTEMS),
        "entry_significant_figures": ENTRY_SIGNIFICANT_FIGURES,
        "quantities": {
            name: {
                "label": label,
                "internal": internal,
                "units": {
                    system: {
                        "symbol": s,
                        "factor": f,
                        "offset": o,
                        "decimals": DEFAULT_DECIMALS.get((name, system), 2),
                    }
                    for system, (s, f, o) in systems.items()
                },
            }
            for name, (label, internal, systems) in QUANTITIES.items()
        },
    }


def fmt(value, quantity, system, decimals=None):
    """Format an SI value in a system's display unit with its symbol."""
    if value is None or not math.isfinite(value):
        return "unavailable"
    shown = from_si(value, quantity, system)
    if decimals is None:
        decimals = DEFAULT_DECIMALS.get((quantity, system), 2)
    return f"{shown:,.{decimals}f} {symbol(quantity, system)}"


DEFAULT_DECIMALS = {
    ("length", "SI"): 0,
    ("length", "oilfield"): 0,
    ("diameter", "SI"): 1,
    ("diameter", "oilfield"): 3,
    ("temperature", "SI"): 1,
    ("temperature", "oilfield"): 1,
    ("temperature_difference", "SI"): 1,
    ("temperature_difference", "oilfield"): 1,
    ("temperature_gradient", "SI"): 1,
    ("temperature_gradient", "oilfield"): 2,
    ("pressure", "SI"): 2,
    ("pressure", "oilfield"): 0,
    ("pressure_gradient", "SI"): 2,
    ("pressure_gradient", "oilfield"): 3,
    ("density", "SI"): 0,
    ("density", "oilfield"): 2,
    ("specific_gravity", "SI"): 3,
    ("specific_gravity", "oilfield"): 3,
    ("volume_flow", "SI"): 1,
    ("volume_flow", "oilfield"): 0,
    ("mass_flow", "SI"): 1,
    ("mass_flow", "oilfield"): 1,
    ("conductivity", "SI"): 2,
    ("conductivity", "oilfield"): 3,
    ("power", "SI"): 1,
    ("power", "oilfield"): 1,
    ("velocity", "SI"): 3,
    ("velocity", "oilfield"): 1,
    ("rate_of_penetration", "SI"): 2,
    ("rate_of_penetration", "oilfield"): 1,
    ("minutes", "SI"): 2,
    ("minutes", "oilfield"): 2,
    ("hours", "SI"): 2,
    ("hours", "oilfield"): 2,
}
