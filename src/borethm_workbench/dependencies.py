"""Record the property-table dependency of each calculation mode."""


def uses_property_table(scenario):
    if scenario.mode in ("wall_history", "tool_history"):
        return False
    if scenario.mode == "circulation_state":
        return scenario.definition.fluid.kind != "constant_properties"
    return True


def property_identity(scenario):
    if not uses_property_table(scenario):
        return {
            "usage": "unused",
            "required": False,
            "ready": True,
            "sha256": None,
            "reason": "Prescribed thermal properties; no water-table lookups in this mode",
        }
    from .adapter import cache_status

    return {"usage": "required", "required": True, **cache_status()}
