#!/usr/bin/env python3
"""Run the separately frozen R22 V3.1 method-level Native CUDA screening."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_r import run_r21_cuda_pilot as r21  # noqa: E402
from scripts.phase_r import run_r22_cuda_pilot as r22  # noqa: E402
from scripts.phase_r import r22_experiment_matrix as matrix  # noqa: E402
from scripts.phase_r.r22_v3_case_evaluator import classify_v3_case  # noqa: E402

V2_DESIGN = ROOT / "docs/research/phase_r/R22/config/r22_extension_experiment_design_v2_range_roi.json"
V3_PARENT_DESIGN_ID = "R22_EXTENSION_20261105_V3_METHOD_LEVEL_CONTINUATION"
DEFAULT_DESIGN = ROOT / "docs/research/phase_r/R22/config/r22_extension_experiment_design_v3_1_resource_replay_20261106.json"
V3_DESIGN_ID = "R22_EXTENSION_20261106_V3_1_POST_REPLAY_FULL_SCREENING"
EXPECTED_SCREENING_CASES = 288
EXPECTED_APPLICABLE_ON_UNITS = 4320


def _sha256(path: Path) -> str:
    return r22.sha256_file(path)


def _repo_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def load_v3_design(path: str | Path = DEFAULT_DESIGN) -> dict[str, Any]:
    design_path = Path(path).expanduser().resolve()
    design = matrix.load_design(design_path)
    if design.get("design_id") != V3_DESIGN_ID:
        raise ValueError(f"V3.1 design_id must be {V3_DESIGN_ID}")
    if design.get("supersedes_design_id") != V3_PARENT_DESIGN_ID:
        raise ValueError("V3.1 design must explicitly supersede the terminal V3 design")
    if design.get("period_count") != 10:
        raise ValueError("V3.1 screening period_count is frozen at 10")
    execution_policy = design.get("v3_execution_policy")
    if (not isinstance(execution_policy, Mapping)
            or execution_policy.get("resource_failure")
            != "stop_and_report_partial_without_reducing_frozen_design"):
        raise ValueError("V3.1 must stop on resource failure without reducing the frozen design")

    provenance = design.get("v3_1_resource_replay_provenance")
    if (not isinstance(provenance, Mapping)
            or provenance.get("parent_design_id") != V3_PARENT_DESIGN_ID
            or provenance.get("parent_matrix_status") != "UNKNOWN_STOP"
            or provenance.get("diagnostic_only") is not True
            or provenance.get("diagnostic_not_in_new_screening_denominator") is not True
            or provenance.get("scientific_factors_changed") is not False):
        raise ValueError("V3.1 must retain the diagnostic provenance without changing scientific factors")

    v2 = matrix.load_design(V2_DESIGN)
    v3_only = {
        "design_id", "state", "supersedes_design_id", "v3_method_unit_applicability",
        "v3_execution_policy", "period_policy", "minimum_period_count_if_resource_gate_fails",
        "v3_1_resource_replay_provenance",
    }
    if any(design.get(key) != value for key, value in v2.items() if key not in v3_only):
        raise ValueError("V3.1 scientific factors or evaluation rules differ from frozen V2")

    applicability = design.get("v3_method_unit_applicability")
    if not isinstance(applicability, Mapping):
        raise ValueError("V3.1 method-unit applicability table is missing")
    zero_units = applicability.get("zero_delay_method_units")
    plus4_units = applicability.get("plus4_delay_method_units")
    if not isinstance(zero_units, list) or len(zero_units) != 12:
        raise ValueError("V3.1 zero-delay applicability must contain 12 method units")
    if not isinstance(plus4_units, list) or len(plus4_units) != 18:
        raise ValueError("V3.1 +4 ns applicability must contain 18 method units")
    for label, rows in (("zero_delay", zero_units), ("plus4_delay", plus4_units)):
        identities = [(row.get("method_id"), row.get("mode")) for row in rows]
        if any(not method or not mode for method, mode in identities):
            raise ValueError(f"V3.1 {label} applicability has an incomplete unit")
        if len(identities) != len(set(identities)):
            raise ValueError(f"V3.1 {label} applicability has duplicate method units")

    specs = matrix.build_case_specs(design, "screening")
    if len(specs) != EXPECTED_SCREENING_CASES:
        raise ValueError(f"V3.1 screening must contain exactly {EXPECTED_SCREENING_CASES} cases")
    denominator = sum(len(expected_method_units(design, spec)) for spec in specs)
    if denominator != EXPECTED_APPLICABLE_ON_UNITS:
        raise ValueError(
            f"V3.1 ON method-unit denominator must be {EXPECTED_APPLICABLE_ON_UNITS}, got {denominator}"
        )
    return design


def expected_method_units(
    design: Mapping[str, Any], spec: Mapping[str, Any]
) -> list[dict[str, str]]:
    delay = float(spec["delay_ns"])
    applicability = design["v3_method_unit_applicability"]
    if delay == 0.0:
        rows = applicability["zero_delay_method_units"]
    elif delay == 4.0:
        rows = applicability["plus4_delay_method_units"]
    else:
        raise ValueError(f"V3 screening has no frozen applicability rule for delay {delay} ns")
    return [{"method_id": str(row["method_id"]), "mode": str(row["mode"])} for row in rows]


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_output_roots(
    output_root: str | Path,
    raw_output_root: str | Path,
    compact_archive_root: str | Path,
) -> tuple[Path, Path, Path]:
    roots = tuple(Path(item).expanduser().resolve() for item in (
        output_root, raw_output_root, compact_archive_root,
    ))
    if any("r22_full_v2" in part.lower() for root in roots for part in root.parts):
        raise ValueError("V2 output root cannot be reused for V3")
    for index, left in enumerate(roots):
        for right in roots[index + 1:]:
            if _is_within(left, right) or _is_within(right, left):
                raise ValueError("V3 output, raw, and compact archive roots must be distinct and non-overlapping")
    return roots


def validate_resume_target(output_root: str | Path, *, resume: bool) -> None:
    root = Path(output_root).expanduser().resolve()
    manifest_path = root / "matrix_manifest.json"
    if not manifest_path.is_file():
        if resume:
            raise FileNotFoundError(f"--resume requires an existing V3 matrix manifest: {manifest_path}")
        if root.exists() and any(root.iterdir()):
            raise FileExistsError(f"refusing to use non-empty V3 matrix output root: {root}")
        return
    if not resume:
        raise FileExistsError(f"V3 matrix already exists; pass --resume: {root}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("design_id") != V3_DESIGN_ID:
        raise ValueError("existing matrix root does not belong to the frozen V3.1 design")
    if manifest.get("status") in {"GATE_FAILED", "UNKNOWN_STOP"}:
        raise RuntimeError(f"refusing to resume a V3.1 matrix in {manifest['status']} state")


def _git_output(*args: str) -> str:
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _gpu_device_identity() -> list[str]:
    command = ["nvidia-smi", "--query-gpu=name,uuid,driver_version", "--format=csv,noheader"]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False, timeout=15)
    if result.returncode or not result.stdout.strip():
        raise RuntimeError(f"cannot establish Native CUDA GPU identity: {result.stderr.strip()}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _disk_snapshot(path: Path) -> dict[str, Any]:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    usage = shutil.disk_usage(probe)
    return {
        "path": str(probe.resolve()), "total_bytes": usage.total,
        "used_bytes": usage.used, "free_bytes": usage.free,
    }


def build_execution_metadata(
    design: Mapping[str, Any],
    *,
    design_path: Path,
    output_root: Path,
    raw_output_root: Path,
    compact_archive_root: Path,
    stage2_binary: Path,
    production_binary: Path,
) -> dict[str, Any]:
    template = _repo_path(design["common"]["scenario_template"])
    method_config = _repo_path(design["common"]["method_config"])
    for required in (design_path, template, method_config, stage2_binary):
        if not required.is_file():
            raise FileNotFoundError(f"required V3 execution input is missing: {required}")

    production_preflight = r21._native_cuda_preflight(production_binary, output_root)
    gpu_identity = _gpu_device_identity()
    source_paths = (
        Path(__file__), Path(__file__).with_name("r22_v3_case_evaluator.py"),
        Path(__file__).with_name("r22_experiment_matrix.py"),
        Path(__file__).with_name("run_r22_cuda_pilot.py"),
        Path(__file__).with_name("r22_scenario_factory.py"),
        ROOT / "scripts/phase_r/run_r21_cuda_pilot.py",
        ROOT / "scripts/phase_r/r21_task_evaluator.py",
        ROOT / "scripts/phase_r/r21_historical_task_review.py",
        ROOT / "scripts/run_delay_stage1_formal.py",
        ROOT / "scripts/run_production_hierarchical_calibration_pilot.py",
        ROOT / "scripts/run_track_manager_e2e.py",
    )
    source_files = {str(path.relative_to(ROOT)): r22.file_identity(path) for path in source_paths}
    if any(not item["exists"] for item in source_files.values()):
        raise FileNotFoundError("one or more V3 runner source identity files are missing")

    immutable_identity = {
        "schema_version": 1,
        "design_id": design["design_id"],
        "design_sha256": _sha256(design_path),
        "method_config": r22.file_identity(method_config),
        "scenario_template": r22.file_identity(template),
        "source": {
            "commit": _git_output("git", "rev-parse", "HEAD"),
            "branch": _git_output("git", "branch", "--show-current"),
            "tracked_dirty_status": _git_output("git", "status", "--short", "--untracked-files=no").splitlines(),
            "files": source_files,
        },
        "stage2_binary": r22.file_identity(stage2_binary),
        "production_binary": production_preflight["binary"],
        "gpu_device_identity": gpu_identity,
    }
    return {
        "execution_identity": immutable_identity,
        "preflight_snapshot": {
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "gpu_status": production_preflight["gpu"],
            "gpu_device_identity": gpu_identity,
            "disk": {
                "matrix": _disk_snapshot(output_root),
                "raw": _disk_snapshot(raw_output_root),
                "compact_archive": _disk_snapshot(compact_archive_root),
            },
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", type=Path, default=DEFAULT_DESIGN)
    parser.add_argument("--phase", required=True, choices=("screening",))
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--raw-output-root", type=Path, required=True)
    parser.add_argument("--compact-archive-root", type=Path, required=True)
    parser.add_argument("--stage2-binary", type=Path, default=r22.DEFAULT_STAGE2)
    parser.add_argument("--production-binary", type=Path, default=r22.DEFAULT_PRODUCTION)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-interrupted", action="store_true")
    args = parser.parse_args(argv)
    try:
        design_path = Path(args.design).expanduser().resolve()
        design = load_v3_design(design_path)
        output_root, raw_root, archive_root = validate_output_roots(
            args.output_root, args.raw_output_root, args.compact_archive_root,
        )
        validate_resume_target(output_root, resume=args.resume)
        if args.retry_interrupted and not args.resume:
            raise ValueError("--retry-interrupted requires --resume")
        if args.max_cases is not None and args.max_cases <= 0:
            raise ValueError("--max-cases must be positive")
        stage2_binary = Path(args.stage2_binary).expanduser().resolve()
        production_binary = Path(args.production_binary).expanduser().resolve()
        metadata = build_execution_metadata(
            design, design_path=design_path, output_root=output_root,
            raw_output_root=raw_root, compact_archive_root=archive_root,
            stage2_binary=stage2_binary, production_binary=production_binary,
        )

        def classify(case_root: Path, run_manifest: Mapping[str, Any]) -> Mapping[str, Any]:
            case_design = run_manifest.get("design")
            if not isinstance(case_design, Mapping) or case_design.get("delay_error_ns") is None:
                raise ValueError("R22 run manifest missing frozen delay identity")
            spec = {"delay_ns": float(case_design["delay_error_ns"])}
            return classify_v3_case(
                case_root, run_manifest,
                expected_units=expected_method_units(design, spec), min_support=8,
            )

        result = matrix.run_matrix(
            design, phase="screening", output_root=output_root,
            stage2_binary=stage2_binary, production_binary=production_binary,
            raw_output_root=raw_root, compact_archive_root=archive_root,
            failure_archive_root=archive_root / "failures",
            max_cases=args.max_cases, resume=args.resume,
            retry_interrupted=args.retry_interrupted,
            case_evaluator_fn=classify, manifest_metadata=metadata,
        )
    except Exception as exc:
        print(f"[r22_v3_experiment_matrix][ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "phase": "screening", "status": result.get("status"),
        "completed_cases": result.get("completed_cases"),
        "skipped_cases": result.get("skipped_cases"),
        "planned_case_count": result.get("planned_case_count"),
        "manifest": str(output_root / "matrix_manifest.json"),
    }, ensure_ascii=False, sort_keys=True))
    return 2 if result.get("status") in {
        "GATE_FAILED", "UNKNOWN_STOP", "ARCHIVE_PENDING", "INTERRUPTED_REVIEW_REQUIRED",
    } else 0


if __name__ == "__main__":
    raise SystemExit(main())
