"""Bounded portable bundle exchange; no archive member is extracted."""

import hashlib
import io
import json
from pathlib import PurePosixPath
import stat
import zipfile

from .contracts import (
    validate_completed_manifest,
    HistoryDataset,
    canonical_bytes,
    parse_scenario_json,
    digest,
    physical_hash,
)


def export_bundle(store, rid):
    run = store.get(rid)
    manifest = run["manifest"]
    if run["state"] != "completed":
        raise ValueError("Only completed immutable results can be bundled")
    if store.check_integrity(rid)["status"] != "verified":
        raise ValueError("ARTIFACT_INVALID: stored run failed integrity verification")
    directory = store.artifact_path(rid)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(directory / "manifest.json", "manifest.json")
        for artifact in manifest["artifacts"]:
            path = directory / artifact["filename"]
            data = path.read_bytes()
            if (
                len(data) != artifact["byte_count"]
                or hashlib.sha256(data).hexdigest() != artifact["sha256"]
            ):
                raise ValueError("ARTIFACT_INVALID: stored checksum mismatch")
            archive.writestr(artifact["filename"], data)
    return out.getvalue()


def inspect_bundle(data):
    if len(data) > 100 * 1024**2:
        raise ValueError("RESOURCE_LIMIT: compressed bundle exceeds 100 MiB")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        files = {}
        expanded = 0
        for member in archive.infolist():
            name = member.filename
            path = PurePosixPath(name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in name
                or ":" in name
                or name in files
                or member.is_dir()
            ):
                raise ValueError("ARTIFACT_INVALID: unsafe or duplicate archive path")
            if stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError("ARTIFACT_INVALID: symlinks are forbidden")
            expanded += member.file_size
            if expanded > 500 * 1024**2 or len(files) >= 100:
                raise ValueError("RESOURCE_LIMIT: expanded bundle limit exceeded")
            files[name] = archive.read(member)
        manifest = json.loads(files["manifest.json"])
        from . import __version__

        if manifest.get("build", {}).get("app_version") != __version__:
            raise ValueError(
                "ARTIFACT_INVALID: archives from other application versions are unsupported"
            )
        if manifest.get("build", {}).get("core_version") == "1.6.0":
            raise ValueError(
                'ARTIFACT_INVALID: BoreTHM 1.6.0 archives cannot be reproduced. Re-run the scenario with physics="v1.7.0" on the current release.'
            )
        validate_completed_manifest(manifest)
        if (
            manifest.get("schema_version") != "1.0"
            or manifest.get("job_status") != "completed"
        ):
            raise ValueError("ARTIFACT_INVALID: unsupported manifest schema/state")
        described = set()
        for artifact in manifest["artifacts"]:
            name = artifact["filename"]
            if name in described:
                raise ValueError("ARTIFACT_INVALID: duplicate descriptor")
            described.add(name)
            content = files.get(name)
            if (
                content is None
                or len(content) != artifact["byte_count"]
                or hashlib.sha256(content).hexdigest() != artifact["sha256"]
            ):
                raise ValueError(
                    "ARTIFACT_INVALID: missing/truncated artifact or checksum mismatch"
                )
        if described != set(files) - {"manifest.json"}:
            raise ValueError(
                "ARTIFACT_INVALID: archive entries are not fully described"
            )
        scenario = parse_scenario_json(files["scenario.json"])
        if (
            physical_hash(scenario) != manifest["physical_request_hash"]
            or digest(scenario.model_dump()) != manifest["resolved_scenario_hash"]
        ):
            raise ValueError("ARTIFACT_INVALID: scenario identity mismatch")
        if (
            manifest["physics"] != scenario.model.physics
            or manifest["physics_settings"].get("physics") != scenario.model.physics
        ):
            raise ValueError("ARTIFACT_INVALID: scenario and recorded physics differ")
        if digest(scenario.model_dump()) != digest(manifest["resolved_scenario"]):
            raise ValueError(
                "ARTIFACT_INVALID: manifest scenario differs from artifact"
            )
        if manifest.get("trajectory") or scenario.mode != "site_assessment":
            from .trajectories import validate_bundle_trajectory

            validate_bundle_trajectory(manifest, scenario, files)
        if scenario.mode in ("wall_history", "tool_history"):
            index = json.loads(files["history-index.json"])
            if index != manifest["history"]:
                raise ValueError(
                    "ARTIFACT_INVALID: history index differs from manifest"
                )
            expected = (
                [c.id for c in scenario.definition.columns]
                if scenario.mode == "wall_history"
                else ["tool"]
            )
            if [c["id"] for c in index["columns"]] != expected:
                raise ValueError(
                    "ARTIFACT_INVALID: missing or reordered history columns"
                )
            datasets = []
            for i, column in enumerate(index["columns"]):
                artifact = next(
                    (
                        a
                        for a in manifest["artifacts"]
                        if a["id"] == column["artifact_id"]
                    ),
                    None,
                )
                if artifact is None:
                    raise ValueError("ARTIFACT_INVALID: missing history artifact")
                dataset = HistoryDataset.model_validate_json(
                    files[artifact["filename"]]
                ).model_dump(exclude_none=True)
                if (
                    dataset["id"] != column["id"]
                    or len(dataset["time_s"]) != column["samples"]
                    or dataset["grid"]["sha256"] != column["grid_sha256"]
                ):
                    raise ValueError(
                        "ARTIFACT_INVALID: history dataset identity mismatch"
                    )
                if dataset["kind"] != (
                    "wall" if scenario.mode == "wall_history" else "tool"
                ):
                    raise ValueError("ARTIFACT_INVALID: wrong history kind")
                if scenario.mode == "wall_history" and any(
                    z != scenario.definition.columns[i].md_m for z in dataset["md_m"]
                ):
                    raise ValueError("ARTIFACT_INVALID: radial column depth mismatch")
                if scenario.mode == "tool_history" and any(
                    z < 0 or z > scenario.definition.bottom_md_m
                    for z in dataset["md_m"]
                ):
                    raise ValueError(
                        "ARTIFACT_INVALID: tool depth outside declared column"
                    )
                datasets.append(dataset)
            if scenario.mode == "wall_history":
                from .interventions import validate_bundle_checkpoints

                if bool(scenario.intervention) != bool(manifest.get("intervention")):
                    raise ValueError(
                        "ARTIFACT_INVALID: intervention capability mismatch"
                    )
                checkpoints = validate_bundle_checkpoints(
                    manifest, scenario, files, datasets
                )
            canonical_bytes(manifest)
            return {
                "kind": "result_bundle",
                "scenario": scenario.model_dump(mode="json"),
                "manifest": manifest,
                "datasets": datasets,
                "checkpoints": checkpoints if scenario.mode == "wall_history" else None,
                "source_sha256": hashlib.sha256(data).hexdigest(),
                "verified_artifacts": len(described),
            }
        profile = json.loads(files["profile.json"])
        if profile != manifest["profile"]:
            raise ValueError("ARTIFACT_INVALID: profile differs from manifest")
        values = [v for k, v in profile.items() if isinstance(v, list)]
        if not values or len({len(v) for v in values}) != 1:
            raise ValueError(
                "ARTIFACT_INVALID: parallel coordinates have inconsistent lengths"
            )
        canonical_bytes(manifest)
        md = profile["md_m"]
        expected_depth = (
            scenario.definition.well.bit_md_m
            if scenario.mode == "circulation_state"
            else manifest["assessment"]["z_m"]
        )
        if (
            not md
            or md[0] != 0
            or md[-1] != expected_depth
            or any(b <= a for a, b in zip(md, md[1:]))
        ):
            raise ValueError("ARTIFACT_INVALID: invalid MD coordinates")
        if manifest["build"]["core_version"].split(".")[0] != "1":
            raise ValueError("ARTIFACT_INVALID: incompatible engine identity")
        return {
            "kind": "result_bundle",
            "scenario": scenario.model_dump(mode="json"),
            "manifest": manifest,
            "source_sha256": hashlib.sha256(data).hexdigest(),
            "verified_artifacts": len(described),
        }
