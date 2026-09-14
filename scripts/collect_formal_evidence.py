#!/usr/bin/env python3
"""Collect only the compact, auditable formal-evidence files.

The source experiment trees remain local and ignored.  This collector uses an
explicit logical-source map, copies no raw data, refuses ambiguous or missing
inputs, and never overwrites an existing destination.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ARTIFACTS = (
    "manifest.json",
    "summary.csv",
    "correction_decisions.csv",
    "target_transfer.csv",
    "pfa_summary.csv",
    "servo_estimates.csv",
    "servo_decisions.csv",
    "information_matched_pairwise_aggregate.csv",
)
SOURCE_KEYS = (
    "geometry",
    "geometry_nuisance",
    "e2e",
    "deadband",
    "target_transfer",
    "pfa",
    "servo",
    "information_matched",
)
DEFAULT_SOURCE_RELATIVE = {
    "geometry": "outputs/unknown_system_error_geometry_matrix_extended_20260914_formal_v2",
    "geometry_nuisance": "outputs/unknown_system_error_geometry_nuisance_sweep_20260914_formal_v1",
    "e2e": "outputs/unknown_system_error_end_to_end_20260914_formal_v4",
    "deadband": "outputs/unknown_system_error_geometry_deadband_audit_20260914_v2",
    "target_transfer": "outputs/unknown_system_error_target_transfer_audit_20260914_v2",
    "pfa": "outputs/unknown_system_error_pfa_audit_20260914_v8",
    "servo": "outputs/unknown_system_error_servo_pilot_20260914_v3",
    "information_matched": "outputs/unknown_system_error_information_matched_baseline_20260914_v1",
}
RAW_SUFFIXES = {".bin", ".npy", ".npz", ".log", ".png", ".jpg", ".jpeg"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_value(repository_root: Path, *args: str) -> str:
    commands = [
        ["git", "-C", str(repository_root), *args],
        [
            "git",
            "--git-dir",
            str(repository_root / ".git-real"),
            "--work-tree",
            str(repository_root),
            *args,
        ],
    ]
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            continue
        if completed.returncode == 0:
            return completed.stdout.strip()
    return "unavailable"


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if path.suffix.lower() != ".csv":
        raise ValueError(f"formal evidence source must be CSV: {path}")
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        fieldnames = list(reader.fieldnames or [])
        if not fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        rows = [dict(row) for row in reader]
    if not rows:
        raise ValueError(f"CSV has no data rows: {path}")
    return fieldnames, rows


def _write_csv(
    path: Path,
    fieldnames: Sequence[str],
    rows: Iterable[Mapping[str, object]],
) -> None:
    materialized = list(rows)
    if not fieldnames or not materialized:
        raise ValueError(f"cannot write empty formal CSV: {path}")
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=list(fieldnames),
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(materialized)


def _validate_source_roots(
    repository_root: Path,
    source_roots: Mapping[str, Path] | None,
) -> dict[str, Path]:
    if source_roots is None:
        resolved = {
            key: repository_root / relative
            for key, relative in DEFAULT_SOURCE_RELATIVE.items()
        }
    else:
        unknown = set(source_roots) - set(SOURCE_KEYS)
        missing = set(SOURCE_KEYS) - set(source_roots)
        if unknown or missing:
            raise ValueError(
                "source_roots must provide an explicit source mapping for "
                f"{sorted(SOURCE_KEYS)}; unknown={sorted(unknown)}, missing={sorted(missing)}"
            )
        resolved = {key: Path(value) for key, value in source_roots.items()}
    for key in SOURCE_KEYS:
        root = resolved[key]
        if not root.is_dir():
            raise ValueError(f"missing mapped source directory {key}: {root}")
    return resolved


def _source_file(source_roots: Mapping[str, Path], key: str, relative: str) -> Path:
    path = source_roots[key] / relative
    if not path.is_file():
        raise ValueError(f"missing mapped formal source {key}: {path}")
    return path


def _union_fieldnames(groups: Sequence[tuple[list[str], list[dict[str, str]]]]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for fieldnames, _ in groups:
        for name in fieldnames:
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _count_raw_files(source_roots: Mapping[str, Path]) -> int:
    count = 0
    for root in source_roots.values():
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in RAW_SUFFIXES:
                count += 1
    return count


def collect_formal_evidence(
    repository_root: Path | str,
    output_root: Path | str,
    source_roots: Mapping[str, Path | str] | None = None,
) -> dict[str, object]:
    """Collect the fixed compact formal-evidence contract."""

    repository = Path(repository_root).resolve()
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {output}")
    mapped = _validate_source_roots(
        repository,
        None if source_roots is None else {
            key: Path(value).resolve() for key, value in source_roots.items()
        },
    )

    source_files = {
        "summary.csv": (
            ("geometry", "matrix_summary.csv"),
            ("geometry_nuisance", "nuisance_matrix_summary.csv"),
            ("e2e", "production_metrics.csv"),
            ("e2e", "recovery_metrics.csv"),
        ),
        "correction_decisions.csv": (("deadband", "correction_decisions.csv"),),
        "target_transfer.csv": (("target_transfer", "target_transfer.csv"),),
        "pfa_summary.csv": (("pfa", "pfa_summary.csv"),),
        "servo_estimates.csv": (("servo", "servo_estimates.csv"),),
        "servo_decisions.csv": (("servo", "servo_decisions.csv"),),
        "information_matched_pairwise_aggregate.csv": (
            ("information_matched", "information_matched_pairwise_aggregate.csv"),
        ),
    }
    resolved_files: dict[str, list[Path]] = {}
    for artifact, entries in source_files.items():
        paths = [_source_file(mapped, key, relative) for key, relative in entries]
        if len(paths) != len(set(paths)):
            raise ValueError(f"ambiguous mapped source for {artifact}: {paths}")
        resolved_files[artifact] = paths

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.", dir=str(output.parent))
    )
    try:
        artifact_records: dict[str, object] = {}
        for artifact, paths in resolved_files.items():
            groups = [_read_csv(path) for path in paths]
            if artifact == "summary.csv":
                rows: list[dict[str, object]] = []
                for (key, relative), (_, source_rows) in zip(
                    source_files[artifact], groups
                ):
                    for row in source_rows:
                        rows.append(
                            {
                                "source_key": key,
                                "source_file": relative,
                                **row,
                            }
                        )
                fields = ["source_key", "source_file"] + _union_fieldnames(groups)
            else:
                rows = [dict(row) for _, group_rows in groups for row in group_rows]
                fields = _union_fieldnames(groups)
            destination = temporary / artifact
            _write_csv(destination, fields, rows)
            artifact_records[artifact] = {
                "source_paths": [str(path) for path in paths],
                "source_sha256": [_sha256(path) for path in paths],
                "row_count": len(rows),
                "sha256": _sha256(destination),
            }

        source_status = _git_value(
            repository, "status", "--short", "--untracked-files=no"
        )
        manifest = {
            "schema": "unknown_system_error_formal_evidence_v1",
            "source_commit": _git_value(repository, "rev-parse", "HEAD"),
            "source_dirty": bool(source_status),
            "source_status_tracked_only": source_status,
            "repository_root": str(repository),
            "output_root": str(output),
            "artifact_count": len(REQUIRED_ARTIFACTS),
            "artifacts": artifact_records,
            "raw_artifacts_excluded": True,
            "raw_suffixes_excluded": sorted(RAW_SUFFIXES),
            "raw_artifact_count_seen": _count_raw_files(mapped),
            "source_mapping": {
                key: str(path) for key, path in sorted(mapped.items())
            },
        }
        with (temporary / "manifest.json").open("w", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
        temporary.rename(output)
    except Exception:
        for path in temporary.glob("*"):
            if path.is_file():
                path.unlink()
        temporary.rmdir()
        raise
    return {"manifest": manifest, "output_root": str(output)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/formal_evidence")
    args = parser.parse_args(argv)
    result = collect_formal_evidence(args.repository_root, args.output)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
