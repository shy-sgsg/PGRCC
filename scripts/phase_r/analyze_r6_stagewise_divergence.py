#!/usr/bin/env python3
"""Compare full complex CSI taps from the frozen R6 CUDA production runs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = ROOT / "outputs/phase_r6_stagewise_divergence_20261022"
PRODUCTION_CASES = {
    "A0_ON": "A0_ON_complexdiag",
    "A0_OFF": "A0_OFF",
    "A0_TO": "A0_TO",
    "C_ON": "C_ON",
    "C_OFF": "C_OFF",
    "C_TO": "C_TO",
}
CONTROL_CASES = {
    "A0_ON_rep1": "A0_ON_rep1",
    "A0_ON_rep2": "A0_ON_rep2",
}
TAPS = (
    "channel1",
    "channel2",
    "ctdr_channel1",
    "ctdr_channel2",
    "csi_after",
    "after",
)


def complex_map_metrics(reference: np.ndarray, candidate: np.ndarray) -> dict[str, Any]:
    """Return shape-checked full-map error metrics with wrapped phase residuals."""
    reference = np.asarray(reference)
    candidate = np.asarray(candidate)
    if reference.shape != candidate.shape:
        raise ValueError(f"maps must have same shape: {reference.shape} != {candidate.shape}")
    if not np.iscomplexobj(reference) or not np.iscomplexobj(candidate):
        raise ValueError("complex maps are required")
    reference = reference.astype(np.complex128, copy=False)
    candidate = candidate.astype(np.complex128, copy=False)
    finite = (
        np.isfinite(reference.real)
        & np.isfinite(reference.imag)
        & np.isfinite(candidate.real)
        & np.isfinite(candidate.imag)
    )
    if not np.all(finite):
        raise ValueError(f"complex maps contain non-finite values ({np.count_nonzero(~finite)})")
    if reference.size == 0:
        raise ValueError("complex maps must not be empty")

    delta = candidate - reference
    ref_rms = float(np.sqrt(np.mean(np.abs(reference) ** 2)))
    power_reference = np.abs(reference) ** 2
    power_delta = np.abs(candidate) ** 2 - power_reference
    power_ref_rms = float(np.sqrt(np.mean(power_reference**2)))
    phase_defined = (np.abs(reference) > 0) & (np.abs(candidate) > 0)
    phase_delta = np.angle(candidate[phase_defined] * np.conj(reference[phase_defined]))
    return {
        "shape": "x".join(str(value) for value in reference.shape),
        "finite_count": int(reference.size),
        "max_abs_error": float(np.max(np.abs(delta))),
        "rms_abs_error": float(np.sqrt(np.mean(np.abs(delta) ** 2))),
        "relative_complex_rms": (
            float(np.sqrt(np.mean(np.abs(delta) ** 2)) / ref_rms) if ref_rms > 0 else None
        ),
        "phase_defined_count": int(phase_delta.size),
        "phase_rms_rad": (
            float(np.sqrt(np.mean(phase_delta**2))) if phase_delta.size else None
        ),
        "phase_abs_p95_rad": (
            float(np.quantile(np.abs(phase_delta), 0.95)) if phase_delta.size else None
        ),
        "power_relative_rms": (
            float(np.sqrt(np.mean(power_delta**2)) / power_ref_rms)
            if power_ref_rms > 0 else None
        ),
        "power_max_abs_error": float(np.max(np.abs(power_delta))),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_complex_map(root: Path, case: str, period: int, tap: str) -> tuple[np.ndarray, Path, str, str]:
    case_root = root / "production" / case / "result/csi_metrics"
    stem = f"period{period:03d}_beam050_{tap}"
    real_paths = sorted(case_root.glob(f"*/{stem}_real.npy"))
    imag_paths = sorted(case_root.glob(f"*/{stem}_imag.npy"))
    if len(real_paths) != 1 or len(imag_paths) != 1:
        raise ValueError(
            f"expected exactly one real/imag map for {case}/{stem}; "
            f"found {len(real_paths)} real and {len(imag_paths)} imag files"
        )
    real_path, imag_path = real_paths[0], imag_paths[0]
    if real_path.parent != imag_path.parent:
        raise ValueError(f"real/imag maps came from different runs for {case}/{stem}")
    real = np.load(real_path, allow_pickle=False)
    imag = np.load(imag_path, allow_pickle=False)
    if real.shape != imag.shape:
        raise ValueError(f"real/imag shape mismatch for {case}/{stem}")
    if not np.isfinite(real).all() or not np.isfinite(imag).all():
        raise ValueError(f"non-finite CSI tap in {case}/{stem}")
    return real.astype(np.float64) + 1j * imag.astype(np.float64), real_path.parent, _sha256(real_path), _sha256(imag_path)


def _compare(
    root: Path,
    reference_name: str,
    reference_case: str,
    candidate_name: str,
    candidate_case: str,
    period: int,
    tap: str,
) -> dict[str, Any]:
    reference, reference_dir, reference_real_sha, reference_imag_sha = _load_complex_map(
        root, reference_case, period, tap
    )
    candidate, candidate_dir, candidate_real_sha, candidate_imag_sha = _load_complex_map(
        root, candidate_case, period, tap
    )
    result = complex_map_metrics(reference, candidate)
    result.update({
        "reference": reference_name,
        "candidate": candidate_name,
        "period_id": period,
        "tap": tap,
        "reference_run_dir": str(reference_dir.relative_to(root)),
        "candidate_run_dir": str(candidate_dir.relative_to(root)),
        "reference_real_sha256": reference_real_sha,
        "reference_imag_sha256": reference_imag_sha,
        "candidate_real_sha256": candidate_real_sha,
        "candidate_imag_sha256": candidate_imag_sha,
    })
    return result


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write an empty CSV: {path}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def analyze(root: Path, output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError(f"refusing to overwrite existing analysis directory: {output_dir}")
    if not root.is_dir():
        raise ValueError(f"missing R6 output root: {root}")

    stagewise_rows = []
    for role in ("ON", "OFF", "TO"):
        for period in range(3):
            for tap in TAPS:
                stagewise_rows.append(_compare(
                    root, f"A0_{role}", PRODUCTION_CASES[f"A0_{role}"],
                    f"C_{role}", PRODUCTION_CASES[f"C_{role}"], period, tap,
                ))

    control_rows = []
    for control_name, control_case in CONTROL_CASES.items():
        for period in range(3):
            for tap in TAPS:
                control_rows.append(_compare(
                    root, "A0_ON_complexdiag", PRODUCTION_CASES["A0_ON"],
                    control_name, control_case, period, tap,
                ))

    executable = ROOT / "build/GMTI_pipe_core"
    source = ROOT / "src/processOnePeriod.cpp"
    if not executable.is_file() or not source.is_file():
        raise ValueError("missing frozen R6 executable or processOnePeriod.cpp source")
    resolved_root = root.resolve()
    try:
        root_identity = resolved_root.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        root_identity = str(resolved_root)
    manifest = {
        "analysis": "R6 full-map CSI stagewise divergence and A0/A0 controls",
        "root": root_identity,
        "map_shape_expected": "130x4096",
        "period_ids": [0, 1, 2],
        "tap_order": list(TAPS),
        "relative_complex_rms_definition": "RMS(candidate-reference)/RMS(reference)",
        "phase_definition": "angle(candidate * conjugate(reference)); zero-magnitude cells excluded",
        "power_relative_rms_definition": "RMS(|candidate|^2-|reference|^2)/RMS(|reference|^2)",
        "executable_sha256": _sha256(executable),
        "processOnePeriod_source_sha256": _sha256(source),
        "stagewise_rows": len(stagewise_rows),
        "control_rows": len(control_rows),
        "checks": {
            "stagewise_full_map_count": len(stagewise_rows) == 54,
            "a0_repeat_full_map_count": len(control_rows) == 36,
            "all_maps_finite_and_shape_matched": True,
        },
    }
    if not all(manifest["checks"].values()):
        raise ValueError("stagewise analysis acceptance checks incomplete")

    output_dir.mkdir(parents=True, exist_ok=False)
    _write_csv(output_dir / "r6_a0_c_stagewise_map_metrics.csv", stagewise_rows)
    _write_csv(output_dir / "r6_a0_control_map_metrics.csv", control_rows)
    (output_dir / "analysis_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    output = args.output or args.root / "analysis/stagewise_map_metrics"
    try:
        manifest = analyze(args.root, output)
    except (OSError, ValueError) as exc:
        print(f"R6 stagewise analysis failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
