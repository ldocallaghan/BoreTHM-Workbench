import json
from pathlib import Path


from borethm_workbench.contracts import parse_scenario

ROOT = Path(__file__).resolve().parents[1]
PRESETS = ROOT / "src/borethm_workbench/presets"


def preset(name):
    return json.loads((PRESETS / (name + ".json")).read_bytes())


def test_new_export_transform_metadata_and_legacy_archive_inspection():
    from borethm_workbench.trajectories import (
        reconstruct,
        DISPLAY_TRANSFORMS,
        manifest_trajectory,
        validate_bundle_trajectory,
    )

    s = parse_scenario(preset("inclined-water"))
    current = reconstruct(
        {
            "bit_md_m": s.definition.well.bit_md_m,
            "survey": s.definition.well.survey.model_dump(),
        }
    )
    assert current["display_transform_version"] == "enu-y-up-v2"
    assert current["renderer_transform"] == DISPLAY_TRANSFORMS["enu-y-up-v2"]
    # Construct a synthetic legacy-format record. No historical research record is rewritten.
    legacy = manifest_trajectory(s, [0.0, s.definition.well.bit_md_m])
    legacy.pop("display_transform_version")
    legacy["renderer_transform"] = DISPLAY_TRANSFORMS["legacy-y-up-v1"]
    from borethm_workbench.contracts import canonical_bytes

    profile = {"md_m": legacy["md_m"], "tvd_m": legacy["tvd_m"]}
    validate_bundle_trajectory(
        {"trajectory": legacy, "profile": profile},
        s,
        {"trajectory.json": canonical_bytes(legacy)},
    )
