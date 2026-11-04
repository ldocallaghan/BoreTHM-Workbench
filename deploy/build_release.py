"""Build the browser assets, dependency licence file and Python distributions."""

import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("Node.js and npm are needed to build the browser assets")
    subprocess.run([npm, "run", "build"], cwd=ROOT / "frontend", check=True)
    modules = ROOT / "frontend/node_modules"
    pending = list(
        json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))[
            "dependencies"
        ]
    )
    seen = set()
    texts = []
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        folder = modules / name
        package = json.loads((folder / "package.json").read_text(encoding="utf-8"))
        pending.extend(package.get("dependencies", {}))
        licences = sorted(
            p
            for p in folder.iterdir()
            if p.is_file()
            and p.name.lower().startswith(("license", "licence", "copying"))
        )
        if not licences:
            raise RuntimeError("Missing licence text for " + name)
        texts.append(
            name
            + " "
            + package["version"]
            + "\n"
            + "\n".join(p.read_text(encoding="utf-8") for p in licences)
        )
    target = ROOT / "src/borethm_workbench/_distribution"
    target.mkdir(exist_ok=True)
    (target / "THIRD-PARTY-LICENCES.txt").write_text(
        "\n\n".join(texts), encoding="utf-8", newline="\n"
    )
    shutil.copyfile(ROOT / "requirements-lock.txt", target / "requirements-lock.txt")
    subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation"], cwd=ROOT, check=True
    )


if __name__ == "__main__":
    main()
