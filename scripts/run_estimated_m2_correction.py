#!/usr/bin/env python3
"""Replay M2 with phase trajectories estimated from raw channel observations."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_mechanism_aware_oracle import (  # noqa: E402
    run_logged,
    single_manifest,
    summary_metrics,
    patch_xml,
    inverse_channel_phase_trajectory_frequency_domain,
)


def read_trajectory(summary_path: Path, selected: set[str] | None = None) -> tuple[Path, dict[str, np.ndarray]]:
    summary = json.loads(summary_path.resolve().read_text(encoding="utf-8"))
    artifact = Path(summary["artifacts"]["temporal_phase_trajectory"])
    if not artifact.is_absolute():
        artifact = (summary_path.parent / artifact).resolve()
    with artifact.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit(f"empty estimated phase trajectory: {artifact}")
    names = [name for name in (
        "P1_robust_constant_linear", "P2_robust_quadratic",
        "P3_smooth_spline_Kalman_like") if name in rows[0]]
    trajectories = {
        name: np.asarray([float(row[name]) for row in rows], dtype=np.float64)
        for name in names
        if not selected or name in selected
    }
    return artifact, trajectories


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--estimator-summary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--method", dest="methods", action="append",
                        choices=("P1_robust_constant_linear", "P2_robust_quadratic",
                                 "P3_smooth_spline_Kalman_like"),
                        help="只重放指定估计方法；可重复传入，默认全部")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    args = parser.parse_args()

    case = args.case.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    xml_path = case / "stage2/config/temp_config_stage2_period_0000.xml"
    input_path = Path(ET.parse(xml_path).getroot().findtext(".//GMTI_data_new") or "")
    if not input_path.is_file():
        raise SystemExit(f"missing production raw input: {input_path}")
    artifact, trajectories = read_trajectory(args.estimator_summary, set(args.methods or []))
    source_manifest = single_manifest(case)
    current = summary_metrics(source_manifest)
    rows: list[dict[str, Any]] = []
    for method, observed_phase in trajectories.items():
        # angle(F1*conj(F2)) is the negative of channel-2 injected phase.
        injected_phase_deg = -np.degrees(observed_phase)
        method_dir = output / method
        corrected_path = method_dir / "stage2/data/estimated_corrected_period_0000.bin"
        transform = inverse_channel_phase_trajectory_frequency_domain(
            input_path, corrected_path, xml_path, injected_phase_deg)
        result_dir = method_dir / "stage2/algorithm_result/period_0000"
        oracle_xml = method_dir / "stage2/config/estimated_config.xml"
        patch_xml(xml_path, corrected_path, result_dir, oracle_xml)
        log_path = method_dir / "gmticore.log"
        return_code = run_logged(
            [str(args.build_dir.resolve() / "GMTI_core"), str(oracle_xml),
             "--runtime-mode=debug", "--runtime-diagnostics=on"], log_path)
        row: dict[str, Any] = {
            "method": method,
            "status": "gmticore_failed" if return_code else "pending",
            "gmticore_exit_code": return_code,
            "current_cancellation_db": current["cancellation_db"],
            "estimated_phase_slope_deg_per_pulse": float(np.polyfit(
                np.arange(observed_phase.size, dtype=np.float64), injected_phase_deg, 1)[0]),
            "manifest": None,
            "transform": transform,
        }
        if return_code == 0:
            manifest = single_manifest(method_dir)
            estimated = summary_metrics(manifest)
            row.update({
                "status": "pass",
                "estimated_cancellation_db": estimated["cancellation_db"],
                "headroom_gain_db_vs_current": estimated["cancellation_db"] - current["cancellation_db"],
                "current_coherence": current["coherence"],
                "estimated_coherence": estimated["coherence"],
                "current_phase_rmse_rad": current["phase_rmse_rad"],
                "estimated_phase_rmse_rad": estimated["phase_rmse_rad"],
                "manifest": str(manifest.resolve()),
            })
        rows.append(row)
        if corrected_path.is_file():
            corrected_path.unlink()

    compact = [{key: value for key, value in row.items() if key != "transform"}
               for row in rows]
    fields = [
        "method", "status", "gmticore_exit_code", "estimated_phase_slope_deg_per_pulse",
        "current_cancellation_db", "estimated_cancellation_db", "headroom_gain_db_vs_current",
        "current_coherence", "estimated_coherence", "current_phase_rmse_rad",
        "estimated_phase_rmse_rad", "manifest",
    ]
    with (output / "estimated_m2_correction_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(compact)
    (output / "estimated_m2_correction_manifest.json").write_text(
        json.dumps({
            "schema_version": 1,
            "ai_training": False,
            "truth_used_in_estimator": False,
            "case": str(case),
            "source_estimator_summary": str(args.estimator_summary.resolve()),
            "trajectory_artifact": str(artifact),
            "phase_sign_convention": "estimated observed angle(F1*conj(F2)) negated to correct channel 2",
            "correction": "per-pulse frequency-domain phase de-rotation",
            "rows": compact,
        }, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "method_count": len(compact),
                      "failed": sum(row["status"] != "pass" for row in compact)},
                     ensure_ascii=False))
    return 0 if all(row["status"] == "pass" for row in compact) else 1


if __name__ == "__main__":
    raise SystemExit(main())
