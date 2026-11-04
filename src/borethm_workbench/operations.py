"""Versioned local service protections; these are not model-validity ranges."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class OperationalPolicy:
    retained_events_per_job: int = 2000
    event_retention_seconds: int = 7 * 24 * 3600
    max_run_artifact_bytes: int = 250 * 1024**2
    max_artifact_storage_bytes: int = 10 * 1024**3
    minimum_free_disk_bytes: int = 256 * 1024**2

    def __post_init__(self):
        for name, value in asdict(self).items():
            if type(value) is not int or value < (
                0 if name == "minimum_free_disk_bytes" else 1
            ):
                raise ValueError(
                    f"{name} must be a {'nonnegative' if name == 'minimum_free_disk_bytes' else 'positive'} integer"
                )

    def as_dict(self):
        return {"version": "local-resources-v1", **asdict(self)}


class ResourceLimit(Exception):
    code = "RESOURCE_LIMIT"


class ArtifactInvalid(ValueError):
    code = "ARTIFACT_INVALID"
