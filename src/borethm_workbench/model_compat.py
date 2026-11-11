"""BoreTHM names that the 1.8.0 release does not export.

With a BoreTHM that provides the operations interface, each name here is
the package's public name. With BoreTHM 1.8.0, the fallback, it is taken
from the internal module that holds the same object. This is the only
module of the application that imports BoreTHM's internal modules.

This module is removed when the application requires the BoreTHM release
with the operations interface; its users then import the names from
borethm.
"""

# Public name -> (internal module, attribute) in BoreTHM 1.8.0.
_FALLBACK = {
    "LossModel": ("hydraulic_losses", "LossModel"),
    "Mud": ("fluids", "Mud"),
    "fluid_props": ("model1_coupled", "fluid_props"),
    "exposure_seconds": ("model1_coupled", "exposure_seconds"),
    "hydrostatic_pressure": ("model1_coupled", "P_of_z"),
    "resolve_physics": ("physics", "resolve"),
    "pressure_column": ("pressure", "column"),
    "WallGrid": ("wall_thermal", "Grid"),
    "wall_grid_radius": ("wall_thermal", "r_max_for"),
    "tool_temperatures": ("tool_thermal", "run"),
    "ToolSegment": ("tool_thermal", "Segment"),
    "ToolHistory": ("tool_thermal", "History"),
    "tool_loss_defaults": ("tool_losses", "defaults"),
    "release_reference_row": ("studies.release_v134", "reference_row"),
    "site_text": ("site_files", "site_text"),
}


def _fallback_named_pipes():
    from borethm import well_geometry as wg

    pipes = (wg.TK_DRAKON, wg.INTERNALLY_COATED, wg.EXTERNALLY_COATED, wg.CONVENTIONAL,
             wg.DUAL_WALL, wg.VACUUM_INSULATED)
    return {pipe.name: pipe for pipe in pipes}


def _fallback_data_bases():
    from borethm.site_evaluation import BASES

    return BASES


def _fallback_water_table_info():
    from borethm import water_table as wt

    return dict(path=wt._CACHE, exists=wt._CACHE.is_file(), temperatures_C=wt.T_GRID.copy(),
                pressures_Pa=wt.P_GRID.copy())


def _fallback_prepare_water_table():
    from borethm import water_table as wt

    wt._interpolators()
    return wt._CACHE


_FALLBACK_FUNCTIONS = {
    "named_pipes": _fallback_named_pipes,
    "data_bases": _fallback_data_bases,
    "water_table_info": _fallback_water_table_info,
    "prepare_water_table": _fallback_prepare_water_table,
}

NAMES = tuple(_FALLBACK) + tuple(_FALLBACK_FUNCTIONS)


def __getattr__(name):
    import borethm

    if name in getattr(borethm, "__all__", ()):
        return getattr(borethm, name)
    if name in _FALLBACK:
        from importlib import import_module

        module, attribute = _FALLBACK[name]
        return getattr(import_module("borethm." + module), attribute)
    if name in _FALLBACK_FUNCTIONS:
        return _FALLBACK_FUNCTIONS[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
