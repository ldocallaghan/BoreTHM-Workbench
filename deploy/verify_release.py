"""Check an installed wheel from a directory outside the checkout."""

import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import borethm_workbench

    package = Path(borethm_workbench.__file__).parent.resolve()
    if "site-packages" not in package.parts:
        raise RuntimeError("Install the built wheel in a clean environment first")
    if not (package / "_distribution/THIRD-PARTY-LICENCES.txt").is_file():
        raise RuntimeError("Frontend licence file is missing")
    if not (package / "static/index.html").is_file():
        raise RuntimeError("Browser bundle is missing")
    args.data_dir.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-I", "-m", "borethm_workbench.cli"]
    subprocess.run(command + ["prepare", "--data-dir", str(args.data_dir)], check=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    started = time.perf_counter()
    server = subprocess.Popen(
        command + ["serve", "--data-dir", str(args.data_dir), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
            for _ in range(100):
                try:
                    if client.get("/api/v1/health/ready").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.2)
            else:
                raise RuntimeError("Installed service did not become ready")
            assert client.get("/").status_code == 200
            capabilities = client.get("/api/v1/capabilities").json()
            scenario = client.get("/api/v1/presets/constant-properties").json()
            response = client.post("/api/v1/runs", json={"scenario": scenario})
            response.raise_for_status()
            rid = response.json()["id"]
            for _ in range(300):
                run = client.get("/api/v1/runs/" + rid).json()
                if run["state"] in {"completed", "failed", "timed_out", "cancelled"}:
                    break
                time.sleep(0.1)
            assert run["state"] == "completed", run
            bundle = args.data_dir / "result.zip"
            bundle.write_bytes(client.get("/api/v1/runs/" + rid + "/export").content)
            replay = subprocess.run(
                command + ["reproduce", str(bundle), "--data-dir", str(args.data_dir)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert replay.returncode == 0, replay.stdout + replay.stderr
            report = {
                "passed": True,
                "app_version": borethm_workbench.__version__,
                "capabilities": capabilities["engine"]["history_capabilities"],
                "reproduction": json.loads(replay.stdout),
                "duration_s": time.perf_counter() - started,
            }
            args.output.write_text(
                json.dumps(report, indent=2), encoding="utf-8", newline="\n"
            )
    finally:
        server.terminate()
        server.wait(timeout=15)


if __name__ == "__main__":
    main()
