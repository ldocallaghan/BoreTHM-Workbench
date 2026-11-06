"""Select the explicitly tested physics for scenario inputs and expected values."""

import json
from pathlib import Path

PHYSICS = "v1.8.0"
FIXTURES = Path(__file__).parent / "fixtures"


def preset_bytes(path):
    value = json.loads(Path(path).read_bytes())
    value["schema_version"] = "1.1"
    value["model"].update(supported_release="1.8.0", physics=PHYSICS)
    return json.dumps(value).encode("utf-8")


def fixture_path(name):
    return (
        FIXTURES / "previous-defaults" / name
        if PHYSICS == "v1.7.0"
        else FIXTURES / name
    )
