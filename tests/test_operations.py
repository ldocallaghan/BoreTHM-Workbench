"""Failure injection and real subprocess coverage for local operational policy."""

import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from fastapi.testclient import TestClient
import pytest

from borethm_workbench.contracts import ErrorEnvelope, JobEvent
from borethm_workbench.jobs import Coordinator
from borethm_workbench.operations import (
    ArtifactInvalid,
    OperationalPolicy,
    ResourceLimit,
)
from borethm_workbench.service import create_app
from borethm_workbench.store import Store, TERMINAL
from borethm_workbench.worker import publish


def wait_for(store, rid, predicate, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        result = store.get(rid)
        if predicate(result):
            return result
        time.sleep(0.02)
    raise AssertionError(f"Expected job condition; last record: {result}")


def reject(store, rid):
    store.finish(
        rid,
        "failed",
        {
            "acceptance": "rejected",
            "failure": {"code": "WORKER_FAILED", "message": "Injected test failure"},
        },
    )


def complete(store, scenario, physical_result):
    run, _ = store.submit(scenario, None)
    rid = store.claim()
    assert rid == run["id"]
    store.stage(rid, "running", {})
    store.stage(rid, "postprocessing", {})
    publish(store, rid, copy.deepcopy(physical_result))
    return rid


def probe_args(coordinator):
    return lambda rid: [
        sys.executable,
        str(Path(__file__).with_name("worker_probe.py")),
        "--root",
        str(coordinator.store.root),
        "--run",
        rid,
    ]


def test_legacy_database_migration_and_monotonic_retention(tmp_path, scenario):
    # Preserve the previous schema's content and payload representation.
    store = Store(tmp_path)
    run, _ = store.submit(scenario, "legacy")
    rid = run["id"]
    with sqlite3.connect(store.db) as conn:
        conn.execute("ALTER TABLE runs DROP COLUMN artifact_bytes")
        conn.execute("DROP TABLE event_cursors")
        conn.execute("DROP TABLE artifact_integrity")
        conn.execute(
            "UPDATE events SET payload=?",
            (
                json.dumps(
                    dict(
                        run_id=rid, scenario_id=run["scenario_id"], scenario_revision=1
                    )
                ),
            ),
        )
    migrated = Store(
        tmp_path, policy=replace(OperationalPolicy(), retained_events_per_job=2)
    )
    assert migrated.get(rid) == run
    assert migrated.revision(run["scenario_id"], 1) == scenario.model_dump(mode="json")
    assert migrated.submit(scenario, "legacy")[0]["id"] == rid
    with migrated.connection() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        for _ in range(4):
            migrated.event(
                conn, row, "progress", {"completed_work": 1, "total_work": 2}
            )
    assert [e["sequence"] for e in migrated.events(rid, 0)] == [4, 5]
    assert migrated.event_window(rid, 0)["reset_reason"] == "events_expired"
    expired = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    with migrated.connection() as conn:
        conn.execute("UPDATE events SET timestamp=?", (expired,))
    migrated.prune_events()
    assert migrated.events(rid, 0) == []
    Store(tmp_path).cancel(rid)
    assert migrated.events(rid, 0)[0]["sequence"] == 6


def test_terminal_sse_drains_all_batches_and_legacy_events(tmp_path, scenario):
    app = create_app(tmp_path, start_jobs=False)
    store = app.state.store
    run, _ = store.submit(scenario, None)
    rid = run["id"]
    with store.connection() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        for index in range(220):
            store.event(
                conn, row, "progress", {"completed_work": index, "total_work": 220}
            )
        conn.execute(
            "UPDATE events SET payload=? WHERE run_id=? AND sequence=1",
            (
                json.dumps(
                    dict(
                        run_id=rid, scenario_id=run["scenario_id"], scenario_revision=1
                    )
                ),
                rid,
            ),
        )
    reject(store, rid)
    with TestClient(app) as client:
        result = client.get(f"/api/v1/runs/{rid}/events")
        envelopes = [
            JobEvent.model_validate_json(line[6:])
            for line in result.text.splitlines()
            if line.startswith("data: ")
        ]
        assert [e.sequence for e in envelopes] == list(range(1, 223))
        assert envelopes[-1].type == "failed"
        replay = client.get(
            f"/api/v1/runs/{rid}/events", headers={"Last-Event-ID": "220"}
        )
        assert [
            int(line[4:])
            for line in replay.text.splitlines()
            if line.startswith("id: ")
        ] == [221, 222]


@pytest.mark.parametrize(
    "cursor,reason", [("0", "events_expired"), ("999", "cursor_ahead")]
)
def test_sse_reset_fetches_authoritative_status(tmp_path, scenario, cursor, reason):
    app = create_app(
        tmp_path,
        start_jobs=False,
        policy=replace(OperationalPolicy(), retained_events_per_job=2),
    )
    store = app.state.store
    run, _ = store.submit(scenario, None)
    store.claim()
    reject(store, run["id"])
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/runs/{run['id']}/events", headers={"Last-Event-ID": cursor}
        )
        data = [
            JobEvent.model_validate_json(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert len(data) == 1 and data[0].type == "reset" and data[0].reason == reason
        assert client.get(data[0].status_route).json()["state"] == "failed"


def test_error_contract_and_published_event_schema(tmp_path, scenario):
    with TestClient(create_app(tmp_path, start_jobs=False)) as client:
        responses = [
            client.get("/api/v1/runs/missing"),
            client.post("/api/v1/scenarios/validate", json={}),
            client.post(
                "/api/v1/scenarios/validate",
                json=scenario.model_dump(),
                headers={"Origin": "https://evil.example"},
            ),
            client.post("/api/v1/imports", content=b"x" * (5 * 1024**2 + 1)),
        ]
        assert [r.status_code for r in responses] == [404, 422, 403, 413]
        assert [
            ErrorEnvelope.model_validate(r.json()).detail[0].code for r in responses
        ] == [
            "RESOURCE_NOT_FOUND",
            "SCHEMA_INVALID",
            "ORIGIN_REJECTED",
            "RESOURCE_LIMIT",
        ]
        run = client.post(
            "/api/v1/runs",
            json={"scenario": scenario.model_dump()},
            headers={"Idempotency-Key": "reuse"},
        ).json()
        scenario.title += " changed"
        response = client.post(
            "/api/v1/runs",
            json={"scenario": scenario.model_dump()},
            headers={"Idempotency-Key": "reuse"},
        )
        assert (
            ErrorEnvelope.model_validate(response.json()).detail[0].code
            == "IDEMPOTENCY_CONFLICT"
        )
        assert (
            client.get(
                f"/api/v1/runs/{run['id']}/events", headers={"Last-Event-ID": "-1"}
            ).status_code
            == 422
        )
        schemas = client.get("/openapi.json").json()["components"]["schemas"]
        assert "JobEvent" in schemas and "ErrorEnvelope" in schemas
        assert (
            client.get("/api/v1/schemas/job-event.json").json()
            == JobEvent.model_json_schema()
        )
        assert (
            client.get("/api/v1/capabilities").json()["operational_policy"]["version"]
            == "local-resources-v1"
        )


def test_recovery_preserves_results_and_quarantines_uncommitted_directory(
    tmp_path, scenario, physical_result
):
    store = Store(tmp_path)
    rid = complete(store, scenario, physical_result)
    directory = store.artifact_path(rid)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    interrupted, _ = store.submit(scenario, None)
    store.claim()
    pending = store.artifact_path(interrupted["id"], pending=True)
    pending.mkdir()
    (pending / "partial").write_bytes(b"partial output")
    orphan = store.artifact_path(interrupted["id"])
    orphan.mkdir()
    (orphan / "manifest.json").write_bytes(b"uncommitted output")
    queued, _ = store.submit(scenario, None)
    store.recover()
    assert store.get(interrupted["id"])["state"] == "failed"
    assert store.get(queued["id"])["state"] == "queued"
    assert not pending.exists() and not orphan.exists()
    assert (
        list((tmp_path / "recovery-orphans").glob("*/manifest.json"))[0].read_bytes()
        == b"uncommitted output"
    )
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
    assert store.check_integrity(rid)["status"] == "verified"
    (directory / "profile.json").write_bytes(b"{}")
    store.recover()
    audit = store.check_integrity(rid)
    assert audit["status"] == "invalid" and audit["errors"]
    assert (
        store.get(rid)["state"] == "completed"
    )  # Audit cannot rewrite scientific history.
    with TestClient(create_app(tmp_path, start_jobs=False)) as client:
        assert client.get(f"/api/v1/runs/{rid}/integrity").json()["status"] == "invalid"
        result = client.get(f"/api/v1/runs/{rid}/artifacts/artifact_1")
        assert result.status_code == 409
        assert (
            ErrorEnvelope.model_validate(result.json()).detail[0].code
            == "ARTIFACT_INVALID"
        )
        result = client.get(f"/api/v1/runs/{rid}/export")
        assert result.status_code == 409
        assert (
            ErrorEnvelope.model_validate(result.json()).detail[0].code
            == "ARTIFACT_INVALID"
        )


def test_publication_rename_before_commit_is_recovered(
    tmp_path, scenario, physical_result, monkeypatch
):
    store = Store(tmp_path)
    run, _ = store.submit(scenario, None)
    rid = store.claim()
    store.stage(rid, "running", {})
    store.stage(rid, "postprocessing", {})
    original = store.event

    def fail_terminal(conn, row, kind, payload=None):
        if kind == "completed":
            raise OSError("Injected metadata commit failure after atomic rename")
        return original(conn, row, kind, payload)

    monkeypatch.setattr(store, "event", fail_terminal)
    with pytest.raises(OSError):
        publish(store, rid, copy.deepcopy(physical_result))
    assert store.get(rid)["state"] == "postprocessing"
    assert store.artifact_path(rid).is_dir()
    Store(tmp_path).recover()
    assert store.get(rid)["state"] == "failed"
    assert not store.artifact_path(rid).exists()
    assert len(list((tmp_path / "recovery-orphans").iterdir())) == 1


def test_truncated_staged_artifact_cannot_be_published(
    tmp_path, scenario, physical_result, monkeypatch
):
    store = Store(tmp_path)
    run, _ = store.submit(scenario, None)
    rid = store.claim()
    store.stage(rid, "running", {})
    store.stage(rid, "postprocessing", {})
    original = store.finish

    def corrupt_then_finish(run_id, state, manifest, staged_dir=None):
        (staged_dir / "profile.json").write_bytes(b"{}")
        return original(run_id, state, manifest, staged_dir)

    monkeypatch.setattr(store, "finish", corrupt_then_finish)
    with pytest.raises(ArtifactInvalid, match="checksum verification"):
        publish(store, rid, copy.deepcopy(physical_result))
    assert store.get(rid)["state"] == "postprocessing"
    assert not store.artifact_path(rid).exists()
    with TestClient(create_app(tmp_path, start_jobs=False)) as client:
        assert client.get(f"/api/v1/runs/{rid}/artifacts/artifact_1").status_code == 404
    Store(tmp_path).recover()
    assert store.get(rid)["state"] == "failed"
    assert not store.artifact_path(rid, pending=True).exists()


def test_cancel_old_job_outside_history_page(tmp_path, scenario, monkeypatch):
    store = Store(tmp_path)
    blocking, _ = store.submit(scenario, None)
    coordinator = Coordinator(store)
    monkeypatch.setattr(coordinator, "_worker_args", probe_args(coordinator))
    coordinator.start()
    try:
        wait_for(store, blocking["id"], lambda r: r["state"] == "running")
        oldest, _ = store.submit(scenario, None)
        store.cancel(oldest["id"])
        # Terminal history rows only; no fabricated accepted calculation output.
        with store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE id=?", (oldest["id"],)
            ).fetchone()
            for index in range(105):
                conn.execute(
                    "INSERT INTO runs(id,scenario_id,revision,state,created_at,updated_at,request_hash) VALUES (?,?,?,?,?,?,?)",
                    (
                        f"run_{index:032x}",
                        row["scenario_id"],
                        1,
                        "failed",
                        "9999",
                        "9999",
                        row["request_hash"],
                    ),
                )
        assert oldest["id"] not in {r["id"] for r in store.list(100)}
        result = wait_for(store, oldest["id"], lambda r: r["state"] in TERMINAL)
        assert result["state"] == "cancelled"
        assert store.get(blocking["id"])["state"] == "running"
        assert coordinator.error is None
    finally:
        coordinator.close()


def test_active_cancel_stops_process_and_next_job_runs(tmp_path, scenario, monkeypatch):
    store = Store(tmp_path)
    first, _ = store.submit(scenario, None)
    second, _ = store.submit(scenario, None)
    coordinator = Coordinator(store)
    monkeypatch.setattr(coordinator, "_worker_args", probe_args(coordinator))
    coordinator.start()
    try:
        wait_for(store, first["id"], lambda r: r["state"] == "running")
        started = time.monotonic()
        store.cancel(first["id"])
        assert time.monotonic() - started < 1
        assert (
            wait_for(store, first["id"], lambda r: r["state"] in TERMINAL)["state"]
            == "cancelled"
        )
        assert time.monotonic() - started < 5
        wait_for(store, second["id"], lambda r: r["state"] == "running")
        (tmp_path / (second["id"] + ".release")).touch()
        result = wait_for(store, second["id"], lambda r: r["state"] in TERMINAL)
        assert (
            result["state"] == "failed"
            and result["manifest"]["diagnostics"]["exit_code"] == 17
        )
        assert (
            result["manifest"]["diagnostics"]["stderr_tail"]
            .replace("\r\n", "\n")
            .endswith("probe exited intentionally\n")
        )
        assert len(result["manifest"]["diagnostics"]["stderr_tail"]) <= 8192
        assert coordinator.error is None
        assert not store.artifact_path(first["id"]).exists()
    finally:
        coordinator.close()


def test_launch_failure_does_not_strand_or_block_queue(tmp_path, scenario, monkeypatch):
    store = Store(tmp_path)
    first, _ = store.submit(scenario, None)
    second, _ = store.submit(scenario, None)
    coordinator = Coordinator(store)
    monkeypatch.setattr(coordinator, "_worker_args", probe_args(coordinator))
    original = subprocess.Popen
    launched = []

    def launch(*args, **kwargs):
        launched.append(True)
        if len(launched) == 1:
            raise OSError("Injected process launch failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", launch)
    coordinator.start()
    try:
        result = wait_for(store, first["id"], lambda r: r["state"] in TERMINAL)
        assert (
            result["state"] == "failed"
            and "launch_error" in result["manifest"]["diagnostics"]
        )
        wait_for(store, second["id"], lambda r: r["state"] == "running")
        assert coordinator.error is None
    finally:
        coordinator.close()
    assert store.get(second["id"])["state"] == "failed"
    assert coordinator.process is None and coordinator.lock_file is None


def test_supervisor_exception_stops_worker_and_marks_unready(
    tmp_path, scenario, monkeypatch
):
    store = Store(tmp_path)
    run, _ = store.submit(scenario, None)
    coordinator = Coordinator(store)
    process = []

    def crash(rid):
        coordinator.process = subprocess.Popen(
            probe_args(coordinator)(rid),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        process.append(coordinator.process)
        raise RuntimeError("Injected supervisor error")

    monkeypatch.setattr(coordinator, "_run_job", crash)
    coordinator.start()
    try:
        assert (
            wait_for(store, run["id"], lambda r: r["state"] in TERMINAL)["state"]
            == "failed"
        )
        coordinator.thread.join(timeout=5)
        assert process[0].poll() is not None
        assert "Injected supervisor error" in coordinator.error
    finally:
        coordinator.close()


def test_storage_limits_preserve_prior_artifacts(tmp_path, scenario, physical_result):
    store = Store(tmp_path)
    rid = complete(store, scenario, physical_result)
    before = {p.name: p.read_bytes() for p in store.artifact_path(rid).iterdir()}
    actual = sum(len(b) for b in before.values())
    assert store.storage_status()["committed_artifact_bytes"] == actual
    policy = replace(OperationalPolicy(), max_artifact_storage_bytes=actual + 1)
    limited = Store(tmp_path, policy=policy)
    run, _ = limited.submit(scenario, None)
    new_rid = limited.claim()
    limited.stage(new_rid, "running", {})
    limited.stage(new_rid, "postprocessing", {})
    with pytest.raises(ResourceLimit):
        publish(limited, new_rid, copy.deepcopy(physical_result))
    assert not limited.artifact_path(new_rid, pending=True).exists()
    assert {
        p.name: p.read_bytes() for p in store.artifact_path(rid).iterdir()
    } == before
    assert limited.check_integrity(rid)["status"] == "verified"
    per_run = Store(
        tmp_path, policy=replace(OperationalPolicy(), max_run_artifact_bytes=100)
    )
    with pytest.raises(ResourceLimit, match="per-run"):
        per_run.check_storage(101)


def test_free_disk_preflight_rejects_without_worker(tmp_path, scenario, monkeypatch):
    store = Store(tmp_path)
    run, _ = store.submit(scenario, None)
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(
        "borethm_workbench.store.shutil.disk_usage", lambda root: usage(100, 100, 0)
    )
    coordinator = Coordinator(store)
    monkeypatch.setattr(
        coordinator,
        "_worker_args",
        lambda rid: pytest.fail("Worker must not launch below disk reserve"),
    )
    coordinator.start()
    try:
        result = wait_for(store, run["id"], lambda r: r["state"] in TERMINAL)
        assert (
            result["state"] == "failed"
            and result["manifest"]["failure"]["code"] == "RESOURCE_LIMIT"
        )
        assert result["manifest"]["operational_policy"] == store.policy.as_dict()
        assert coordinator.error is None
    finally:
        coordinator.close()


def test_resource_policy_reaches_real_calculation_worker(tmp_path, scenario):
    policy = replace(OperationalPolicy(), max_run_artifact_bytes=100)
    store = Store(tmp_path, policy=policy)
    run, _ = store.submit(scenario, None)
    coordinator = Coordinator(store)
    coordinator.start()
    try:
        result = wait_for(
            store, run["id"], lambda r: r["state"] in TERMINAL, seconds=20
        )
        assert result["manifest"]["failure"]["code"] == "RESOURCE_LIMIT"
        assert result["manifest"]["operational_policy"] == policy.as_dict()
        assert not store.artifact_path(run["id"]).exists()
        assert not store.artifact_path(run["id"], pending=True).exists()
        assert coordinator.error is None
    finally:
        coordinator.close()


def test_single_coordinator_owns_recovery_and_execution(tmp_path):
    first = Coordinator(Store(tmp_path))
    second = Coordinator(Store(tmp_path))
    first.start()
    try:
        with pytest.raises(OSError):
            second.start()
        assert second.lock_file is None
        assert first.thread.is_alive() and first.error is None
    finally:
        first.close()
    # A clean shutdown releases ownership for a fresh service.
    second.start()
    second.close()


def test_terminal_failure_retains_supplied_provenance_without_rechecking(
    tmp_path, scenario, monkeypatch
):
    store = Store(tmp_path)
    run, _ = store.submit(scenario, None)

    def unexpected():
        raise AssertionError(
            "Frozen supplied provenance must not trigger a second identity/cache probe"
        )

    monkeypatch.setattr("borethm_workbench.adapter.engine_identity", unexpected)
    monkeypatch.setattr("borethm_workbench.adapter.cache_status", unexpected)
    store.finish(
        run["id"],
        "failed",
        {
            "build": {"snapshot": "build"},
            "property_table": {"snapshot": "cache"},
            "failure": {"code": "WORKER_FAILED", "message": "test failure"},
        },
    )
    manifest = store.get(run["id"])["manifest"]
    assert manifest["build"] == {"snapshot": "build"} and manifest[
        "property_table"
    ] == {"snapshot": "cache"}


def test_termination_racing_worker_exit_is_harmless():
    class Exiting:
        calls = 0

        def poll(self):
            self.calls += 1
            return None if self.calls == 1 else 0

        def terminate(self):
            raise PermissionError("Worker exited just before TerminateProcess")

        def wait(self, timeout):
            return 0

    Coordinator.stop_process(Exiting())


def test_actual_interpreter_pid_and_environment_are_preserved():
    from borethm_workbench.processes import python_command, python_environment

    process = subprocess.Popen(
        python_command(
            "-I",
            "-c",
            "import os,sys,json,borethm\nprint(json.dumps(dict(pid=os.getpid(),prefix=sys.prefix,core=borethm.__version__)))",
        ),
        env=python_environment(),
        stdout=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    output, _ = process.communicate(timeout=15)
    assert process.returncode == 0
    observed = json.loads(output)
    assert observed["pid"] == process.pid
    assert Path(observed["prefix"]).resolve() == Path(sys.prefix).resolve()
    from importlib.metadata import version

    assert observed["core"] == version("borethm")


def test_repeated_real_bvp_cancellation_does_not_stop_coordinator(tmp_path):
    from borethm_workbench.contracts import Scenario

    fixture = Path(__file__).parent / "fixtures/hot-short-exposure.json"
    scenario = Scenario.model_validate_json(fixture.read_bytes())
    store = Store(tmp_path)
    coordinator = Coordinator(store)
    coordinator.start()
    try:
        for _ in range(12):
            job, _ = store.submit(scenario, None)
            wait_for(store, job["id"], lambda r: r["state"] == "running")
            began = time.monotonic()
            store.cancel(job["id"])
            try:
                terminal = wait_for(
                    store, job["id"], lambda r: r["state"] in TERMINAL, seconds=5
                )
            except AssertionError as error:
                import traceback

                frame = sys._current_frames().get(coordinator.thread.ident)
                stack = (
                    "".join(traceback.format_stack(frame))
                    if frame
                    else "No supervisor frame"
                )
                raise AssertionError(
                    f"{error}\nSupervisor error: {coordinator.error}\nActive: {coordinator.active_run}\nProcess: {coordinator.process.poll() if coordinator.process else None}\n{stack}"
                ) from error
            assert terminal["state"] == "cancelled" and time.monotonic() - began < 5
            assert coordinator.error is None
    finally:
        coordinator.close()


@pytest.mark.parametrize(
    "values",
    [
        {"retained_events_per_job": 0},
        {"max_run_artifact_bytes": -1},
        {"minimum_free_disk_bytes": -1},
        {"event_retention_seconds": True},
    ],
)
def test_operational_policy_rejects_invalid_configuration(values):
    with pytest.raises(ValueError):
        OperationalPolicy(**values)
