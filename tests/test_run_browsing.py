"""Filter saved jobs before pagination so results remain discoverable."""

from fastapi.testclient import TestClient
from borethm_workbench.service import create_app
from borethm_workbench.contracts import parse_scenario
from pathlib import Path
import json
import copy


def test_older_circulation_runs_survive_newer_jobs_in_other_modes(
    tmp_path, scenario, physical_result
):
    root = Path(__file__).resolve().parents[1]
    history = parse_scenario(
        json.loads(
            (root / "src/borethm_workbench/presets/open-hole-history.json").read_bytes()
        )
    )
    with TestClient(create_app(tmp_path, start_jobs=False)) as client:
        store = client.app.state.store
        first = None
        for _ in range(20):
            run, _ = store.submit(scenario, None)
            assert store.claim() == run["id"]
            store.stage(run["id"], "running", {})
            store.stage(run["id"], "postprocessing", {})
            from borethm_workbench.worker import publish

            publish(store, run["id"], copy.deepcopy(physical_result))
            if first is None:
                first = run["id"]
        for _ in range(100):
            run, _ = store.submit(history, None)
            store.cancel(run["id"])
            store.finish(run["id"], "cancelled", {})
        seen = []
        for offset in (0, 10):
            response = client.get(
                "/api/v1/runs",
                params={"mode": "circulation_state", "limit": 10, "offset": offset},
            )
            assert response.status_code == 200, response.text
            page = response.json()
            seen.extend(r["id"] for r in page["items"])
            assert all(r["mode"] == "circulation_state" for r in page["items"])
            assert page["has_more"] == (offset == 0)
        assert len(set(seen)) == 20 and first in seen
        assert client.get("/api/v1/runs/" + first).json()["id"] == first
        assert len(store.list(100)) == 100
