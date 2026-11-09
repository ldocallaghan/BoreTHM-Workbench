"""SQLite revisions/jobs/events and atomic artifact publication (/03)."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import re
import shutil
import uuid

from .contracts import (
    validate_completed_manifest,
    JobEvent,
    canonical_bytes,
    digest,
    physical_hash,
)
from .operations import ArtifactInvalid, OperationalPolicy, ResourceLimit

TERMINAL = {"completed", "failed", "cancelled", "timed_out"}
ACTIVE = {"preparing", "running", "postprocessing"}


def now():
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix):
    return prefix + "_" + uuid.uuid4().hex


class Conflict(Exception):
    def __init__(self, message, code="SCENARIO_REVISION_CONFLICT"):
        super().__init__(message)
        self.code = code


class Store:
    def __init__(self, root, policy=None):
        self.policy = policy or OperationalPolicy()
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "metadata.sqlite3"
        self.anchor = None
        (self.root / "artifacts").mkdir(exist_ok=True)
        with self.connection() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS scenarios(id TEXT PRIMARY KEY, current_revision INTEGER, created_at TEXT);
                CREATE TABLE IF NOT EXISTS revisions(scenario_id TEXT, revision INTEGER, content TEXT, physical_hash TEXT, created_at TEXT,
                    PRIMARY KEY(scenario_id,revision), FOREIGN KEY(scenario_id) REFERENCES scenarios(id));
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, scenario_id TEXT, revision INTEGER, state TEXT,
                    created_at TEXT, updated_at TEXT, idempotency_key TEXT UNIQUE, request_hash TEXT,
                    failure_kind TEXT, manifest TEXT);
                CREATE TABLE IF NOT EXISTS events(run_id TEXT, sequence INTEGER, timestamp TEXT, type TEXT, payload TEXT,
                    PRIMARY KEY(run_id,sequence));
                CREATE TABLE IF NOT EXISTS event_cursors(run_id TEXT PRIMARY KEY, last_sequence INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS artifact_integrity(run_id TEXT PRIMARY KEY, checked_at TEXT, status TEXT, details TEXT);
                CREATE INDEX IF NOT EXISTS runs_state_created ON runs(state,created_at);
                CREATE TABLE IF NOT EXISTS sweeps(id TEXT PRIMARY KEY,created_at TEXT,request_hash TEXT,
                    idempotency_key TEXT UNIQUE,definition TEXT);
                CREATE TABLE IF NOT EXISTS sweep_cases(sweep_id TEXT,case_index INTEGER,run_id TEXT UNIQUE,coordinates TEXT,
                    PRIMARY KEY(sweep_id,case_index),FOREIGN KEY(sweep_id) REFERENCES sweeps(id),FOREIGN KEY(run_id) REFERENCES runs(id));
                CREATE TABLE IF NOT EXISTS studies(id TEXT PRIMARY KEY,created_at TEXT,request_hash TEXT,
                    idempotency_key TEXT UNIQUE,definition TEXT);
                CREATE TABLE IF NOT EXISTS study_cases(study_id TEXT,case_index INTEGER,run_id TEXT UNIQUE,coordinates TEXT,
                    PRIMARY KEY(study_id,case_index),FOREIGN KEY(study_id) REFERENCES studies(id),FOREIGN KEY(run_id) REFERENCES runs(id));
            """)
            # Existing databases keep their revisions/results and event identities.
            columns = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
            if "artifact_bytes" not in columns:
                conn.execute(
                    "ALTER TABLE runs ADD COLUMN artifact_bytes INTEGER NOT NULL DEFAULT 0"
                )
                conn.execute("""UPDATE runs SET artifact_bytes=length(CAST(manifest AS BLOB)) +
                    COALESCE((SELECT SUM(json_extract(value,'$.byte_count')) FROM json_each(runs.manifest,'$.artifacts')),0)
                    WHERE state='completed' AND manifest IS NOT NULL""")
            conn.execute("""INSERT INTO event_cursors(run_id,last_sequence)
                SELECT run_id,MAX(sequence) FROM events GROUP BY run_id
                ON CONFLICT(run_id) DO UPDATE SET last_sequence=MAX(last_sequence,excluded.last_sequence)""")

    def keep_open(self):
        # Keep WAL/SHM ownership in the service while independently killable
        # workers open short transactions. A killed last connection on Windows
        # must not race WAL/SHM teardown against the next metadata connection.
        if self.anchor is None:
            self.anchor = sqlite3.connect(
                self.db, timeout=10, check_same_thread=False, isolation_level=None
            )
            self.anchor.execute("PRAGMA journal_mode=WAL")

    def close(self):
        if self.anchor is not None:
            self.anchor.close()
            self.anchor = None

    @contextmanager
    def connection(self, write=True):
        conn = sqlite3.connect(self.db, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def event(self, conn, run, kind, payload=None):
        conn.execute(
            "INSERT INTO event_cursors VALUES (?,0) ON CONFLICT(run_id) DO NOTHING",
            (run["id"],),
        )
        conn.execute(
            "UPDATE event_cursors SET last_sequence=last_sequence+1 WHERE run_id=?",
            (run["id"],),
        )
        sequence = conn.execute(
            "SELECT last_sequence FROM event_cursors WHERE run_id=?", (run["id"],)
        ).fetchone()[0]
        event = JobEvent(
            sequence=sequence,
            timestamp=now(),
            run_id=run["id"],
            scenario_id=run["scenario_id"],
            scenario_revision=run["revision"],
            type=kind,
            **(payload or {}),
        )
        envelope = event.model_dump(exclude_none=True)
        conn.execute(
            "INSERT INTO events VALUES (?,?,?,?,?)",
            (
                run["id"],
                sequence,
                event.timestamp,
                kind,
                canonical_bytes(envelope).decode(),
            ),
        )
        conn.execute(
            "DELETE FROM events WHERE run_id=? AND sequence<=?",
            (run["id"], sequence - self.policy.retained_events_per_job),
        )

    def create_scenario(self, scenario):
        sid = new_id("scenario")
        with self.connection() as conn:
            conn.execute("INSERT INTO scenarios VALUES (?,1,?)", (sid, now()))
            conn.execute(
                "INSERT INTO revisions VALUES (?,?,?,?,?)",
                (
                    sid,
                    1,
                    canonical_bytes(scenario.model_dump()).decode(),
                    physical_hash(scenario),
                    now(),
                ),
            )
        return {"id": sid, "current_revision": 1}

    def append_revision(self, sid, scenario, expected):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM scenarios WHERE id=?", (sid,)).fetchone()
            if row is None:
                raise KeyError(sid)
            if str(row["current_revision"]) != expected:
                raise Conflict(
                    "SCENARIO_REVISION_CONFLICT: reload or save a separate scenario"
                )
            revision = row["current_revision"] + 1
            conn.execute(
                "INSERT INTO revisions VALUES (?,?,?,?,?)",
                (
                    sid,
                    revision,
                    canonical_bytes(scenario.model_dump()).decode(),
                    physical_hash(scenario),
                    now(),
                ),
            )
            conn.execute(
                "UPDATE scenarios SET current_revision=? WHERE id=?", (revision, sid)
            )
        return {"id": sid, "current_revision": revision}

    def get_scenario(self, sid):
        with self.connection(write=False) as conn:
            row = conn.execute("SELECT * FROM scenarios WHERE id=?", (sid,)).fetchone()
            if not row:
                raise KeyError(sid)
            revisions = conn.execute(
                "SELECT revision, physical_hash, created_at FROM revisions WHERE scenario_id=? ORDER BY revision",
                (sid,),
            ).fetchall()
            return {**dict(row), "revisions": [dict(r) for r in revisions]}

    def revision(self, sid, revision):
        with self.connection(write=False) as conn:
            row = conn.execute(
                "SELECT content FROM revisions WHERE scenario_id=? AND revision=?",
                (sid, revision),
            ).fetchone()
        if not row:
            raise KeyError(sid)
        return json.loads(row[0])

    def submit(self, scenario, idempotency_key, sid=None, revision=None, max_queue=20):
        request_hash = digest(
            {
                "scenario": scenario.model_dump(),
                "scenario_id": sid,
                "revision": revision,
            }
        )
        with self.connection() as conn:
            if idempotency_key:
                existing = conn.execute(
                    "SELECT * FROM runs WHERE idempotency_key=?", (idempotency_key,)
                ).fetchone()
                if existing:
                    if existing["request_hash"] != request_hash:
                        raise Conflict(
                            "Idempotency key was already used for different content",
                            "IDEMPOTENCY_CONFLICT",
                        )
                    return self.row(existing), False
            count = conn.execute(
                "SELECT COUNT(*) FROM runs WHERE state NOT IN ('completed','failed','cancelled','timed_out')"
            ).fetchone()[0]
            if count >= max_queue:
                raise OverflowError("Local queue has reached its 20-job limit")
            if sid is None:
                sid, revision = new_id("scenario"), 1
                conn.execute("INSERT INTO scenarios VALUES (?,1,?)", (sid, now()))
                conn.execute(
                    "INSERT INTO revisions VALUES (?,?,?,?,?)",
                    (
                        sid,
                        revision,
                        canonical_bytes(scenario.model_dump()).decode(),
                        physical_hash(scenario),
                        now(),
                    ),
                )
            rid = new_id("run")
            stamp = now()
            conn.execute(
                "INSERT INTO runs(id,scenario_id,revision,state,created_at,updated_at,idempotency_key,request_hash,failure_kind,manifest) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    rid,
                    sid,
                    revision,
                    "queued",
                    stamp,
                    stamp,
                    idempotency_key,
                    request_hash,
                    None,
                    None,
                ),
            )
            run = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
            self.event(conn, run, "queued")
            return self.row(run), True

    @staticmethod
    def row(row):
        out = dict(row)
        out["scenario_revision"] = out.pop("revision")
        out.pop("request_hash", None)
        out.pop("idempotency_key", None)
        out.pop("artifact_bytes", None)
        out["manifest"] = json.loads(out["manifest"]) if out["manifest"] else None
        return out

    def get(self, rid):
        with self.connection(write=False) as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        if not row:
            raise KeyError(rid)
        return self.row(row)

    def list(self, limit=25, offset=0, mode=None):
        with self.connection(write=False) as conn:
            condition = (
                " WHERE json_extract(revisions.content,'$.mode')=?" if mode else ""
            )
            parameters = (mode, limit, offset) if mode else (limit, offset)
            rows = conn.execute(
                "SELECT runs.*, json_extract(revisions.content,'$.mode') AS mode FROM runs JOIN revisions ON runs.scenario_id=revisions.scenario_id AND runs.revision=revisions.revision"
                + condition
                + " ORDER BY runs.created_at DESC,runs.id DESC LIMIT ? OFFSET ?",
                parameters,
            ).fetchall()
        results = []
        for r in rows:
            item = self.row(r)
            if item["manifest"]:
                item["manifest"] = {
                    k: item["manifest"].get(k)
                    for k in (
                        "mode",
                        "acceptance",
                        "title",
                        "summary",
                        "warnings",
                        "validity",
                    )
                }
            results.append(item)
        return results

    def claim(self):
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE state='queued' ORDER BY created_at, rowid LIMIT 1"
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE runs SET state='preparing',updated_at=? WHERE id=?",
                (now(), row["id"]),
            )
            self.event(
                conn,
                row,
                "stage_started",
                {"stage": "preparing", "name": "Verify engine and frozen scenario"},
            )
            return row["id"]

    def stage(self, rid, state, payload):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
            if row["state"] in TERMINAL or row["state"] == "cancel_requested":
                raise InterruptedError("Cancellation accepted")
            allowed = {"preparing": "running", "running": "postprocessing"}
            if allowed.get(row["state"]) != state:
                raise RuntimeError(
                    f"Invalid stage transition {row['state']} -> {state}"
                )
            conn.execute(
                "UPDATE runs SET state=?,updated_at=? WHERE id=?", (state, now(), rid)
            )
            self.event(conn, row, "stage_started", {"stage": state, **payload})

    def notify(self, rid, kind, payload):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
            if row["state"] in TERMINAL or row["state"] == "cancel_requested":
                raise InterruptedError("Cancellation accepted")
            self.event(conn, row, kind, payload)

    def find_seed(self, scenario, identity, cache):
        from .initialisation import compatibility_key, compatible_seed, seed_snapshot

        if scenario.numerics.initialisation != "interactive-bounded-v1":
            return None
        key = compatibility_key(scenario)
        with self.connection(write=False) as conn:
            rows = conn.execute(
                """SELECT id,manifest FROM runs WHERE state='completed'
                AND json_extract(manifest,'$.seed_compatibility_key')=?
                AND json_extract(manifest,'$.build.core_version')=?
                AND json_extract(manifest,'$.build.app_version')=?
                AND json_extract(manifest,'$.property_table.sha256')=?
                ORDER BY created_at DESC LIMIT 20""",
                (
                    key,
                    identity["core_version"],
                    identity["app_version"],
                    cache["sha256"],
                ),
            ).fetchall()
        for row in rows:
            try:
                snapshot = seed_snapshot(json.loads(row["manifest"]))
                if (
                    compatible_seed(snapshot, scenario, identity, cache)
                    and self.check_integrity(row["id"])["status"] == "verified"
                ):
                    return snapshot
            except (KeyError, TypeError, ValueError):
                # Older/incomplete metadata cannot suppress another valid seed.
                continue
        return None

    def cancel(self, rid):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
            if not row:
                raise KeyError(rid)
            if row["state"] not in TERMINAL and row["state"] != "cancel_requested":
                conn.execute(
                    "UPDATE runs SET state='cancel_requested',updated_at=? WHERE id=?",
                    (now(), rid),
                )
                self.event(conn, row, "cancel_requested")
        return self.get(rid)

    def finish(self, rid, state, manifest, staged_dir=None):
        """The state transaction chooses the winner of cancellation/publication."""
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
            if row["state"] in TERMINAL:
                return False
            if row["state"] == "cancel_requested":
                state = "cancelled"
                manifest = {
                    "schema_version": "1.0",
                    "acceptance": "rejected",
                    "failure": {
                        "code": "USER_CANCELLED",
                        "message": "Calculation cancelled before publication",
                    },
                }
            if state != "completed":
                from .adapter import cache_status, engine_identity
                from .contracts import parse_scenario

                frozen = json.loads(
                    conn.execute(
                        "SELECT content FROM revisions WHERE scenario_id=? AND revision=?",
                        (row["scenario_id"], row["revision"]),
                    ).fetchone()[0]
                )
                parsed = parse_scenario(frozen)
                manifest.setdefault("schema_version", "1.0")
                manifest.setdefault("mode", frozen["mode"])
                manifest.setdefault("title", frozen["title"])
                manifest.setdefault("resolved_scenario", frozen)
                manifest.setdefault("physics", parsed.model.physics)
                manifest.setdefault("physical_request_hash", physical_hash(parsed))
                manifest.setdefault("resolved_scenario_hash", digest(frozen))
                if "build" not in manifest:
                    manifest["build"] = engine_identity()
                if "property_table" not in manifest:
                    manifest["property_table"] = cache_status()
                manifest.setdefault("numerical_profile", frozen["numerics"])
                manifest.setdefault(
                    "attempts", manifest.get("diagnostics", {}).get("attempts", [])
                )
                manifest.setdefault("artifacts", [])
                manifest.setdefault("operational_policy", self.policy.as_dict())
            if state == "completed" and row["state"] != "postprocessing":
                raise RuntimeError("Only postprocessed results can complete")
            manifest.update(
                run_id=rid,
                scenario_id=row["scenario_id"],
                scenario_revision=row["revision"],
                job_status=state,
                created_at=row["created_at"],
                completed_at=now(),
            )
            if state == "completed":
                validate_completed_manifest(manifest)
                expected = self.artifact_path(rid, pending=True)
                if Path(staged_dir).resolve() != expected:
                    raise ValueError(
                        "Publication requires this run's application-owned pending directory"
                    )
                errors = self.artifact_errors(expected, manifest)
                if errors:
                    raise ArtifactInvalid(
                        "Staged artifacts failed checksum verification: "
                        + "; ".join(errors)
                    )
                final_dir = self.artifact_path(rid)
                encoded_manifest = canonical_bytes(manifest)
                artifact_bytes = sum(
                    a["byte_count"] for a in manifest["artifacts"]
                ) + len(encoded_manifest)
                self.check_storage(artifact_bytes, conn=conn)
                (expected / "manifest.json").write_bytes(encoded_manifest)
                os.replace(staged_dir, final_dir)
                conn.execute(
                    "UPDATE runs SET artifact_bytes=? WHERE id=?", (artifact_bytes, rid)
                )
            code = manifest.get("failure", {}).get("code")
            kind = {
                "SOLVER_NOT_CONVERGED": "numerical_failed",
                "RESULT_NONFINITE": "numerical_failed",
                "PROPERTY_DOMAIN_OUTSIDE": "domain_rejected",
            }.get(code, code.lower() if code else None)
            conn.execute(
                "UPDATE runs SET state=?,updated_at=?,failure_kind=?,manifest=? WHERE id=?",
                (state, now(), kind, canonical_bytes(manifest).decode(), rid),
            )
            if state == "completed":
                for item in manifest["warnings"]:
                    self.event(conn, row, "warning", {"warning": item})
                for descriptor in manifest["artifacts"]:
                    self.event(
                        conn, row, "artifact_available", {"artifact": descriptor}
                    )
                for checkpoint in manifest.get("intervention", {}).get(
                    "checkpoints", []
                ):
                    self.event(
                        conn,
                        row,
                        "checkpoint_available",
                        {
                            "checkpoint_id": checkpoint["id"],
                            "time_s": checkpoint["time_s"],
                        },
                    )
            self.event(
                conn,
                row,
                state,
                {
                    "acceptance": manifest.get("acceptance"),
                    "failure": manifest.get("failure"),
                },
            )
        return state == "completed"

    def events(self, rid, after):
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(seconds=self.policy.event_retention_seconds)
        ).isoformat()
        with self.connection(write=False) as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE run_id=? AND sequence>? AND timestamp>=? ORDER BY sequence LIMIT 100",
                (rid, after, cutoff),
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def event_window(self, rid, after):
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(seconds=self.policy.event_retention_seconds)
        ).isoformat()
        with self.connection(write=False) as conn:
            latest = conn.execute(
                "SELECT last_sequence FROM event_cursors WHERE run_id=?", (rid,)
            ).fetchone()
            latest = latest[0] if latest else 0
            first = conn.execute(
                "SELECT MIN(sequence) FROM events WHERE run_id=? AND timestamp>=?",
                (rid, cutoff),
            ).fetchone()[0]
        reason = (
            "cursor_ahead"
            if after > latest
            else "events_expired"
            if after < (first if first is not None else latest + 1) - 1
            else None
        )
        return {"latest": latest, "first_retained": first, "reset_reason": reason}

    def pending_cancellations(self):
        with self.connection(write=False) as conn:
            return [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM runs WHERE state='cancel_requested'"
                )
            ]

    def prune_events(self):
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(seconds=self.policy.event_retention_seconds)
        ).isoformat()
        with self.connection() as conn:
            conn.execute("DELETE FROM events WHERE timestamp<?", (cutoff,))

    def artifact_path(self, rid, pending=False):
        if not re.fullmatch(r"run_[0-9a-f]{32}", rid):
            raise ValueError("Invalid application run ID")
        base = self.root / "artifacts"
        path = base / (rid + (".pending" if pending else ""))
        if (
            base.is_symlink()
            or path.is_symlink()
            or path.resolve().parent != base.resolve()
        ):
            raise ValueError(
                "Artifact path must stay within the application artifact directory"
            )
        return path.resolve()

    def clear_pending(self, rid):
        path = self.artifact_path(rid, pending=True)
        if path.exists():
            shutil.rmtree(path)

    def storage_status(self):
        with self.connection(write=False) as conn:
            used = conn.execute(
                "SELECT COALESCE(SUM(artifact_bytes),0) FROM runs"
            ).fetchone()[0]
        return {
            "committed_artifact_bytes": used,
            "free_disk_bytes": shutil.disk_usage(self.root).free,
            "retention": "Committed runs and artifacts are retained; only abandoned pending directories are cleaned",
        }

    def metrics(self):
        with self.connection(write=False) as conn:
            states = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT state,COUNT(*) FROM runs GROUP BY state"
                )
            }
            acceptance = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT json_extract(manifest,'$.acceptance'),COUNT(*) FROM runs WHERE manifest IS NOT NULL GROUP BY json_extract(manifest,'$.acceptance')"
                )
            }
            failures = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT failure_kind,COUNT(*) FROM runs WHERE failure_kind IS NOT NULL GROUP BY failure_kind"
                )
            }
        return {
            "states": states,
            "acceptance": acceptance,
            "failure_kinds": failures,
            "storage": self.storage_status(),
        }

    def check_storage(self, additional_bytes=0, *, conn=None, require_free=True):
        if conn is None:
            with self.connection(write=False) as read:
                used = read.execute(
                    "SELECT COALESCE(SUM(artifact_bytes),0) FROM runs"
                ).fetchone()[0]
        else:
            used = conn.execute(
                "SELECT COALESCE(SUM(artifact_bytes),0) FROM runs"
            ).fetchone()[0]
        if additional_bytes > self.policy.max_run_artifact_bytes:
            raise ResourceLimit(
                "Run artifacts exceed the configured per-run byte limit"
            )
        if used + additional_bytes > self.policy.max_artifact_storage_bytes:
            raise ResourceLimit(
                "Committed artifacts have reached the configured storage budget; retained results were preserved"
            )
        if (
            require_free
            and shutil.disk_usage(self.root).free
            < self.policy.minimum_free_disk_bytes + additional_bytes
        ):
            raise ResourceLimit(
                "Insufficient free disk space above the configured reserve; retained results were preserved"
            )

    @staticmethod
    def artifact_errors(directory, manifest):
        errors, names = [], {"manifest.json"}
        for descriptor in manifest["artifacts"]:
            name = descriptor["filename"]
            if (
                name in names
                or Path(name).name != name
                or "/" in name
                or "\\" in name
                or ":" in name
            ):
                errors.append("Invalid or duplicate artifact descriptor path")
                continue
            names.add(name)
            artifact = directory / name
            try:
                if (
                    artifact.is_symlink()
                    or not artifact.is_file()
                    or artifact.stat().st_size != descriptor["byte_count"]
                ):
                    errors.append(f"Missing or truncated artifact: {descriptor['id']}")
                    continue
                with artifact.open("rb") as handle:
                    checksum = hashlib.file_digest(handle, "sha256").hexdigest()
                if checksum != descriptor["sha256"]:
                    errors.append(f"Artifact checksum mismatch: {descriptor['id']}")
            except OSError as error:
                errors.append(str(error))
        return errors

    def check_integrity(self, rid):
        run = self.get(rid)
        errors = []
        if run["state"] != "completed":
            return {
                "run_id": rid,
                "status": "not_applicable",
                "checked_at": now(),
                "errors": [],
            }
        manifest = run["manifest"]
        try:
            directory = self.artifact_path(rid)
            path = directory / "manifest.json"
            if (
                not path.is_file()
                or path.is_symlink()
                or path.read_bytes() != canonical_bytes(manifest)
            ):
                errors.append(
                    "Manifest file is missing or does not match committed metadata"
                )
            errors.extend(self.artifact_errors(directory, manifest))
        except (OSError, ValueError, KeyError) as error:
            errors.append(str(error))
        record = {
            "run_id": rid,
            "status": "invalid" if errors else "verified",
            "checked_at": now(),
            "errors": errors,
        }
        with self.connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO artifact_integrity VALUES (?,?,?,?)",
                (
                    rid,
                    record["checked_at"],
                    record["status"],
                    canonical_bytes(errors).decode(),
                ),
            )
        return record

    def recover(self):
        with self.connection(write=False) as conn:
            ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM runs WHERE state IN ('preparing','running','postprocessing','cancel_requested')"
                ).fetchall()
            ]
        for rid in ids:
            self.finish(
                rid,
                "failed",
                {
                    "schema_version": "1.0",
                    "acceptance": "rejected",
                    "failure": {
                        "code": "WORKER_FAILED",
                        "message": "Service interrupted; no resumable checkpoint",
                    },
                },
            )
        with self.connection(write=False) as conn:
            states = {r[0]: r[1] for r in conn.execute("SELECT id,state FROM runs")}
        quarantine = self.root / "recovery-orphans"
        for path in (self.root / "artifacts").iterdir():
            match = re.fullmatch(r"(run_[0-9a-f]{32})(\.pending)?", path.name)
            if not match or path.is_symlink():
                continue
            rid, pending = match.groups()
            if pending and rid in states and states[rid] in TERMINAL:
                self.clear_pending(rid)
            elif not pending and states.get(rid) != "completed":
                # A rename completed but its SQLite transaction did not commit.
                # Preserve the orphan for inspection instead of exposing/deleting it.
                quarantine.mkdir(exist_ok=True)
                if quarantine.is_symlink() or quarantine.resolve().parent != self.root:
                    raise ValueError(
                        "Recovery quarantine must remain in the application directory"
                    )
                target = quarantine / (rid + "_" + uuid.uuid4().hex)
                os.replace(self.artifact_path(rid), target)
        for rid, state in states.items():
            if state == "completed":
                self.check_integrity(rid)
        self.prune_events()
