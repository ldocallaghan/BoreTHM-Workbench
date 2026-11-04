"""Supervise independently killable solves outside the API event loop."""

from dataclasses import asdict
import json
import os
import subprocess
import threading
import time

from .contracts import digest
from .operations import ResourceLimit
from .processes import python_command, python_environment
from .store import TERMINAL


class Coordinator:
    def __init__(self, store, deadline=30):
        self.store = store
        self.deadline = deadline
        self.stop_event = threading.Event()
        self.thread = None
        self.error = None
        self.process = None
        self.active_run = None
        self.lock_file = None

    def start(self):
        # OS lock releases after a crash. Recovery runs only after ownership.
        self.lock_file = open(self.store.root / "coordinator.lock", "a+b")
        self.lock_file.seek(0)
        self.lock_file.write(b"0")
        self.lock_file.flush()
        self.lock_file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.store.keep_open()
            self.store.recover()
        except BaseException:
            self.store.close()
            self.lock_file.close()
            self.lock_file = None
            raise
        self.thread = threading.Thread(
            target=self.loop, name="borethm-job-supervisor", daemon=True
        )
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                raise RuntimeError(
                    "Job supervisor did not stop; coordinator lock retained"
                )
        if self.lock_file:
            self.store.close()
            self.lock_file.close()
            self.lock_file = None

    @staticmethod
    def stop_process(process):
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                # On Windows TerminateProcess can race the worker's own exit.
                # A process that has already exited needs no further signal.
                if process.poll() is None:
                    raise
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    if process.poll() is None:
                        raise
                process.wait(timeout=3)

    def loop(self):
        try:
            self._loop()
        except BaseException as error:
            self.error = f"{type(error).__name__}: {error}"
            termination_error = None
            if self.process:
                try:
                    self.stop_process(self.process)
                except OSError as stop_error:
                    termination_error = str(stop_error)
            if self.active_run:
                try:
                    self.failure(
                        self.active_run,
                        "failed",
                        "WORKER_FAILED",
                        "Job supervisor failed",
                        {
                            "supervisor_error": self.error[:2000],
                            "termination_error": termination_error,
                        },
                    )
                    self.store.clear_pending(self.active_run)
                except Exception:
                    # Readiness remains false if metadata cannot be written.
                    pass

    def _worker_args(self, rid):
        return python_command(
            "-m",
            "borethm_workbench.worker",
            "--root",
            str(self.store.root),
            "--run",
            rid,
            "--policy",
            json.dumps(asdict(self.store.policy), separators=(",", ":")),
        )

    def _loop(self):
        last_maintenance = time.monotonic()
        while not self.stop_event.is_set():
            # Pagination and recency must never hide queued cancellation.
            for rid in self.store.pending_cancellations():
                self.failure(
                    rid, "cancelled", "USER_CANCELLED", "Cancelled before solve"
                )
            if time.monotonic() - last_maintenance >= 60:
                self.store.prune_events()
                last_maintenance = time.monotonic()
            rid = self.store.claim()
            if not rid:
                self.stop_event.wait(0.1)
                continue
            self.active_run = rid
            self._run_job(rid)
            self.store.clear_pending(rid)
            self.process = None
            self.active_run = None

    def _run_job(self, rid):
        if self.stop_event.is_set():
            self.failure(
                rid, "failed", "WORKER_FAILED", "Service stopped before worker launch"
            )
            return
        try:
            self.store.check_storage()
        except ResourceLimit as error:
            self.failure(rid, "failed", error.code, str(error))
            return
        started = time.monotonic()
        claimed = self.store.get(rid)
        frozen = self.store.revision(
            claimed["scenario_id"], claimed["scenario_revision"]
        )
        deadline = (
            1800
            if frozen["mode"] == "site_assessment"
            else 300
            if frozen["mode"] in ("wall_history", "tool_history")
            else self.deadline
        )
        environment = python_environment()
        for key in (
            "OPENBLAS_NUM_THREADS",
            "OMP_NUM_THREADS",
            "MKL_NUM_THREADS",
            "NUMEXPR_NUM_THREADS",
        ):
            environment[key] = "1"
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self.process = subprocess.Popen(
                self._worker_args(rid),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=flags,
            )
        except OSError as error:
            self.failure(
                rid,
                "failed",
                "WORKER_FAILED",
                "Could not start calculation worker",
                {"launch_error": str(error)[:2000]},
            )
            return
        stderr_tail = bytearray()
        process = self.process

        def capture_stderr():
            while chunk := process.stderr.read(4096):
                stderr_tail.extend(chunk)
                del stderr_tail[:-8192]

        reader = threading.Thread(
            target=capture_stderr, name="borethm-worker-stderr", daemon=True
        )
        reader.start()
        while process.poll() is None:
            run = self.store.get(rid)
            cancel = run["state"] == "cancel_requested"
            timeout = time.monotonic() - started > deadline
            if cancel or timeout or self.stop_event.is_set():
                self.stop_process(process)
                state = "cancelled" if cancel else "timed_out" if timeout else "failed"
                code = (
                    "USER_CANCELLED"
                    if cancel
                    else "NUMERICAL_TIMEOUT"
                    if timeout
                    else "WORKER_FAILED"
                )
                self.failure(
                    rid,
                    state,
                    code,
                    "Worker stopped: " + state,
                    {"effective_deadline_s": deadline},
                )
                break
            for queued_cancel in self.store.pending_cancellations():
                if queued_cancel != rid:
                    self.failure(
                        queued_cancel,
                        "cancelled",
                        "USER_CANCELLED",
                        "Cancelled before solve",
                    )
            self.stop_event.wait(0.05)
        process.wait()
        reader.join(timeout=2)
        if not reader.is_alive():
            process.stderr.close()
        if self.store.get(rid)["state"] not in TERMINAL:
            self.failure(
                rid,
                "failed",
                "WORKER_FAILED",
                f"Worker exited without publishing (exit {process.returncode})",
                {
                    "exit_code": process.returncode,
                    "stderr_tail": bytes(stderr_tail).decode("utf-8", errors="replace"),
                },
            )

    def failure(self, rid, state, code, message, diagnostics=None):
        run = self.store.get(rid)
        scenario = self.store.revision(run["scenario_id"], run["scenario_revision"])
        self.store.finish(
            rid,
            state,
            {
                "schema_version": "1.0",
                "acceptance": "rejected",
                "resolved_scenario": scenario,
                "resolved_scenario_hash": digest(scenario),
                "operational_policy": self.store.policy.as_dict(),
                "diagnostics": diagnostics or {},
                "failure": {"code": code, "message": message},
            },
        )
