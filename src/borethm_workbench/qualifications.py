"""Plain sentences for result qualifications.

Each qualification keeps its code for the details views. The results page and
the report show the sentence. Hydraulic qualifications carry the model's flag
name as their message; those flags are described here too.
"""

FLAGS = {
    "hydraulic_geometry_blend": "Pressure losses are blended over a short distance where the pipe or hole size changes.",
    "joint_local_annular_or_Bingham_approximation": "Pressure losses at pipe joints in the annulus, or with mud, use an approximation.",
    "joint_local_outside_Newtonian_Re_domain": "Flow at the pipe joints is outside the range the joint loss formula was derived for.",
    "joint_taper_no_stated_Reynolds_range": "The loss formula for tapered joints states no range of flow conditions.",
    "local_change_annular_or_Bingham_approximation": "Pressure losses at changes of size in the annulus, or with mud, use an approximation.",
    "local_change_outside_Newtonian_Re_domain": "Flow at a change of size is outside the range its loss formula was derived for.",
    "supplied_tool_loss_treated_as_dissipation": "A supplied tool pressure loss is counted as heat added to the fluid.",
    "internal_tool_loss_unknown": "A tool's internal pressure loss is not known and is left out of the standpipe pressure.",
    "tool_flow_outside_published_range": "The flow through a tool is outside the range of its published pressure-loss figure.",
    "tool_temperature_outside_published_rating": "The fluid at a tool is hotter than the highest temperature of its published pressure-loss figure.",
    "tool_allowance_supplied_figure": "A tool uses the pressure-loss figure supplied for it.",
    "tool_allowance_generic": "A named tool uses the generic pressure-loss allowance for its class; enter the maker's figure where known.",
}

CODES = {
    "ASSESSMENT_MODEL_QUALIFICATIONS": "The verdict carries the model's own assumptions, evidence limits and equipment qualifications.",
    "PROPERTY_AUDIT_INCOMPLETE": "Water properties were checked for the published profile; intermediate states inside the calculation were not all checked.",
    "PROPERTY_GUARD_ACTIVE": "Where the pressure is below 5 MPa absolute, the hydraulic calculation takes water properties at 5 MPa.",
    "ENERGY_DIAGNOSTIC_FLAGGED": "The model's balance of friction heating against pump energy is outside its tolerance.",
    "ENERGY_DIAGNOSTIC_UNAVAILABLE": "The model's balance of friction heating against pump energy is not available.",
    "OPTIONAL_DIAGNOSTIC_UNAVAILABLE": "Some optional diagnostic values were not finite and are left out.",
    "PROVENANCE_MISSING": "The scenario does not state the source of its inputs.",
    "INCLINED_THERMAL_HYDRAULICS": "The well is inclined: friction follows measured depth and the fluid column follows vertical depth. Stability and programme checks are not available for inclined wells.",
    "SURVEY_INCOMPLETE": "The survey gives measured and vertical depth only, so the well has no single 3D path.",
    "MUD_FORMULATION_QUALIFIED": "The mud is modelled as water weighted with barite, with a Bingham plastic rheology; the model's field-validation limits apply.",
    "INTERNAL_TOOL_LOSS_UNKNOWN": FLAGS["internal_tool_loss_unknown"],
}


def _sentence(text):
    text = text.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "?", "!")) else text + "."


def _flag(flag):
    if flag in FLAGS:
        return FLAGS[flag]
    parts = flag.split(":")
    if parts[0] == "tool_default_zero_no_source" and len(parts) == 3:
        return f"No published pressure-loss figure exists for the {parts[1].replace('_', ' ')} in the {parts[2]} in size band, so its allowance is zero."
    if parts[0] == "tool_default_single_source" and len(parts) == 3:
        return f"The generic pressure-loss allowance for the {parts[1].replace('_', ' ')} in the {parts[2]} in size band rests on one published source."
    return None


def plain(item):
    """A plain sentence for one qualification {code, message}."""
    code, message = item.get("code", ""), str(item.get("message", ""))
    if code in ("HYDRAULIC_QUALIFICATION", "TOOL_RATING_EXCEEDED", "INTERNAL_TOOL_LOSS_UNKNOWN"):
        described = _flag(message)
        if described:
            return described
    if code in CODES:
        return CODES[code]
    if code == "SITE_INPUT_QUALIFICATION":
        return "Site input check, in the model's wording: " + _sentence(message)[0].lower() + _sentence(message)[1:]
    if code == "CONSTANT_PROPERTY_APPROXIMATION":
        return "The fluid uses constant properties: " + _sentence(message)
    if " " in message:
        return _sentence(message)
    return _sentence(message.replace("_", " ")) if message else _sentence(code.replace("_", " ").lower())


def with_sentences(items):
    return [{**item, "plain": plain(item)} for item in items]
