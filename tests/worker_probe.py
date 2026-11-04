"""Test-only process-lifecycle probe; never emits a scientific result."""

import argparse
import os
from pathlib import Path
import sys
import time

from borethm_workbench.store import Store

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    root = Path(args.root)
    store = Store(root)
    store.stage(args.run, "running", {"name": "Lifecycle probe; no physical output"})
    (root / (args.run + ".pid")).write_text(str(os.getpid()))
    end = time.monotonic() + 15
    while time.monotonic() < end and not (root / (args.run + ".release")).exists():
        time.sleep(0.02)
    sys.stderr.write("x" * 100000 + "\nprobe exited intentionally\n")
    raise SystemExit(17)
