"""Record installed versions of dependencies named in the installation lock."""

from importlib import metadata
from pathlib import Path
import re


def dependency_versions():
    packaged = Path(__file__).parent / "_distribution/requirements-lock.txt"
    checkout = Path(__file__).resolve().parents[2] / "requirements-lock.txt"
    lock = packaged if packaged.is_file() else checkout
    names = set(
        re.findall(r"^([A-Za-z0-9_.-]+)==", lock.read_text(encoding="utf-8"), re.M)
    )
    versions = {}
    for name in sorted(names):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return versions
