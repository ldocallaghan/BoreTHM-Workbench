"""Generate example result archives as release assets."""

import argparse
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient

from borethm_workbench.service import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    import time

    with (
        tempfile.TemporaryDirectory() as directory,
        TestClient(create_app(directory, deadline=240)) as client,
    ):
        available = client.get("/api/v1/capabilities").json()["modes"]
        names = [
            "synthetic-water",
            "constant-properties",
            "inclined-water",
            "newberry-assessment",
        ]
        if "wall_history" in available:
            names.extend(["open-hole-history", "scheduled-intervention"])
        if "tool_history" in available:
            names.append("tool-trip-history")
        for name in names:
            scenario = client.get("/api/v1/presets/" + name).json()
            response = client.post("/api/v1/runs", json={"scenario": scenario})
            response.raise_for_status()
            rid = response.json()["id"]
            for _ in range(3000):
                run = client.get("/api/v1/runs/" + rid).json()
                if run["state"] in {"completed", "failed", "cancelled", "timed_out"}:
                    break
                time.sleep(0.1)
            if run["state"] != "completed":
                raise RuntimeError(str(run))
            payload = client.get("/api/v1/runs/" + rid + "/export")
            payload.raise_for_status()
            (args.output / (name + "-run.zip")).write_bytes(payload.content)


if __name__ == "__main__":
    main()
