"""Describe numerical property arrays independently of their archive encoding."""

import hashlib
from importlib import metadata
import platform

import numpy as np


def fingerprints(path):
    with np.load(path, allow_pickle=False) as table:
        return {
            key: {
                "shape": list(table[key].shape),
                "dtype": "little-endian float64",
                "sha256": hashlib.sha256(
                    np.asarray(table[key], dtype="<f8", order="C").tobytes()
                ).hexdigest(),
            }
            for key in ("T", "P", "rho", "cp", "mu", "k")
        }


def cache_report():
    from .adapter import cache_status

    status = cache_status()
    return {
        "protocol": "property-array-report-v1",
        "status": status,
        "arrays": status.get("arrays"),
        "generator": {
            "borethm": metadata.version("borethm"),
            "python": platform.python_version(),
            "libraries": {n: metadata.version(n) for n in ("numpy", "scipy", "iapws")},
        },
        "policy": "Readiness checks array dimensions and finite positive values. Reproduction reports array fingerprint differences and applies the declared numerical tolerances.",
    }
