"""Committed files contain no data from wells outside the permitted comparisons."""

from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Patterns are assembled so that this file does not contain them literally.
WELLS = ["-".join(pair) for pair in (("56", "32"), ("58", "32"), ("78B", "32"))]
FOLDERS = [
    "validation/" + name
    for name in ("forge" + WELLS[0], "forge" + WELLS[1], "model" + "-development", "forge16b/" + "sanvean")
]


def committed_files():
    try:
        output = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a Git checkout")
    files = [ROOT / name for name in output.decode("utf-8").split("\0") if name]
    if not files:
        pytest.skip("no committed files")
    return files


def test_no_committed_file_mentions_excluded_wells_or_folders():
    findings = []
    for path in committed_files():
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        relative = path.relative_to(ROOT).as_posix()
        for pattern in WELLS + FOLDERS:
            if pattern in text or pattern in relative:
                findings.append(f"{relative}: {pattern}")
    assert findings == []
