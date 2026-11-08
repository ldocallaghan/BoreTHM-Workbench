"""Application settings stored in the data directory.

Settings are user preferences. They are not part of any scenario, archive or
hash.
"""

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

FILENAME = "settings.json"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    units: Literal["SI", "oilfield"] = "SI"


def load_settings(root):
    path = Path(root) / FILENAME
    try:
        return Settings.model_validate_json(path.read_bytes())
    except (OSError, ValueError):
        return Settings()


def save_settings(root, settings: Settings):
    path = Path(root) / FILENAME
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(settings.model_dump(), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return settings
