#!/usr/bin/env python3
"""Run a compact mixed/OOD production design and retain estimator summaries.

The design is intentionally small and serial: three cases each for M1+M2,
M1+M3, M2+M3 and M1+M2+mild-M3.  It reuses the production Stage2/GMTI path,
fits raw-observable M1/M2 estimators only when their mechanisms are present,
and writes a compact feature/confusion table.  It does not train AI.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_mechanism_feature_table import classify  # noqa: E402
from experiment_provenance import git_provenance  # noqa: E402
from run_model_mismatch_audit import run_one_variant  # noqa: E402


DESIGN: list[dict[str, Any]] = [
    {"id": "m1m2_ood_a", "label": "M1+M2", "seed": 2026091021,
     "delay_ns": 2.083333333333333, "phase_deg": 0.05, "rho": 1.0,
     "squint_deg": -0.55, "range_bin": 192, "snr_db": 3.0,
     "ve_mps": -20.0, "vn_mps": 9.0, "texture_sigma": 0.35},
    {"id": "m1m2_ood_b", "label": "M1+M2", "seed": 2026091022,
     "delay_ns": 8.333333333333334, "phase_deg": 0.5, "rho": 1.0,
     "squint_deg": 0.55, "range_bin": 768, "snr_db": -3.0,
     "ve_mps": -6.0, "vn_mps": 16.0, "texture_sigma": 0.55},
    {"id": "m1m2_ood_c", "label": "M1+M2", "seed": 2026091023,
     "delay_ns": 4.166666666666667, "phase_deg": 0.2, "rho": 1.0,
     "squint_deg": 0.25, "range_bin": 1280, "snr_db": 0.0,
     "ve_mps": -12.0, "vn_mps": 4.0, "texture_sigma": 0.4},
    {"id": "m1m3_ood_a", "label": "M1+M3", "seed": 2026091024,
     "delay_ns": 2.083333333333333, "phase_deg": 0.0, "rho": 0.99,
     "squint_deg": -0.45, "range_bin": 256, "snr_db": 3.0,
     "ve_mps": -18.0, "vn_mps": 7.0, "texture_sigma": 0.35},
    {"id": "m1m3_ood_b", "label": "M1+M3", "seed": 2026091025,
     "delay_ns": 8.333333333333334, "phase_deg": 0.0, "rho": 0.8,
     "squint_deg": 0.45, "range_bin": 896, "snr_db": -3.0,
     "ve_mps": -8.0, "vn_mps": 14.0, "texture_sigma": 0.55},
    {"id": "m1m3_ood_c", "label": "M1+M3", "seed": 2026091026,
     "delay_ns": 4.166666666666667, "phase_deg": 0.0, "rho": 0.95,
     "squint_deg": 0.2, "range_bin": 1408, "snr_db": 0.0,
     "ve_mps": -12.0, "vn_mps": 4.0, "texture_sigma": 0.4},
    {"id": "m2m3_ood_a", "label": "M2+M3", "seed": 2026091027,
     "delay_ns": 0.0, "phase_deg": 0.05, "rho": 0.99,
     "squint_deg": -0.4, "range_bin": 320, "snr_db": 3.0,
     "ve_mps": -20.0, "vn_mps": 9.0, "texture_sigma": 0.35},
    {"id": "m2m3_ood_b", "label": "M2+M3", "seed": 2026091028,
     "delay_ns": 0.0, "phase_deg": 0.5, "rho": 0.8,
     "squint_deg": 0.4, "range_bin": 1024, "snr_db": -3.0,
     "ve_mps": -6.0, "vn_mps": 16.0, "texture_sigma": 0.55},
    {"id": "m2m3_ood_c", "label": "M2+M3", "seed": 2026091029,
     "delay_ns": 0.0, "phase_deg": 0.2, "rho": 0.95,
     "squint_deg": 0.2, "range_bin": 1536, "snr_db": 0.0,
     "ve_mps": -12.0, "vn_mps": 4.0, "texture_sigma": 0.4},
    {"id": "all_ood_a", "label": "M1+M2+M3", "seed": 2026091030,
     "delay_ns": 2.083333333333333, "phase_deg": 0.05, "rho": 0.99,
     "squint_deg": -0.35, "range_bin": 448, "snr_db": 3.0,
     "ve_mps": -18.0, "vn_mps": 7.0, "texture_sigma": 0.35},
    {"id": "all_ood_b", "label": "M1+M2+M3", "seed": 2026091031,
     "delay_ns": 4.166666666666667, "phase_deg": 0.2, "rho": 0.95,
     "squint_deg": 0.35, "range_bin": 1152, "snr_db": -3.0,
     "ve_mps": -8.0, "vn_mps": 14.0, "texture_sigma": 0.55},
    {"id": "all_ood_c", "label": "M1+M2+M3", "seed": 2026091032,
     "delay_ns": 8.333333333333334, "phase_deg": 0.2, "rho": 0.95,
     "squint_deg": 0.2, "range_bin": 1664, "snr_db": 0.0,
     "ve_mps": -12.0, "vn_mps": 4.0, "texture_sigma": 0.4},
]


def finite(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def run_estimator(case_dir: Path, output_dir: Path, mechanisms: set[str]) -> tuple[int, dict[str, Any]]:
    command = [sys.executable, str(ROOT / "scripts/run_mechanism_estimator_audit.py"),
               "--output-dir", str(output_dir), "--input-mode", "raw"]
    if "M1" in mechanisms:
        command.extend(["--m1-case", str(case_dir)])
    if "M2" in mechanisms:
        command.extend(["--m2-case", str(case_dir)])
    completed = subprocess.run(command, cwd=ROOT, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               check=False)
    (output_dir / "estimator_command.txt").write_text(
        "$ " + " ".join(command) + "\n" + completed.stdout,
        encoding="utf-8")
    audit_path = output_dir / "mechanism_estimator_audit.json"
    return completed.returncode, json.loads(audit_path.read_text(encoding="utf-8")) \
        if audit_path.is_file() else {"records": []}


def summary_for(audit: dict[str, Any], name: str) -> dict[str, Any] | None:
    for record in audit.get("records", []):
        if record.get("mechanism") == name:
            path = Path(record["summary"])
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
    return None


def row_from_case(spec: dict[str, Any], result: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    m1 = summary_for(audit, "M1")
    m2 = summary_for(audit, "M2")
    d3 = (m1 or {}).get("methods", {}).get("D3_Huber_weighted_LS", {})
    p1 = (m2 or {}).get("methods", {}).get("P1_robust_constant_linear", {})
    p2 = (m2 or {}).get("methods", {}).get("P2_robust_quadratic", {})
    features = {
        "clutter_energy": 1.0,
        "channel_coherence": finite(result.get("csi_coherence"), math.nan),
        "deterministic_phase_confidence": finite(
            d3.get("confidence"), finite(p1.get("confidence"), math.nan)),
        "phase_vs_frequency_slope_rad_per_hz": finite(d3.get("slope_rad_per_hz"), 0.0),
        "phase_vs_frequency_r2": finite(d3.get("r2"), 0.0),
        "wideband_residual_rad": finite(d3.get("rmse_rad"), 0.0),
        "pulse_phase_slope_rad_per_pulse": finite(p1.get("slope_rad_per_pulse"), 0.0),
        "pulse_phase_curvature_rad_per_pulse2": finite(
            (p2.get("coefficients") or [0.0, 0.0, 0.0])[2], 0.0),
    }
    feature_row = {**features, "diagnosis": classify(features)}
    mechanisms = set(spec["label"].split("+"))
    return {
        "case_id": spec["id"], "actual_mechanism": spec["label"],
        "seed": spec["seed"], "squint_deg": spec["squint_deg"],
        "range_bin": spec["range_bin"], "snr_db": spec["snr_db"],
        "true_delay_ns": spec["delay_ns"] if "M1" in mechanisms else 0.0,
        "estimated_delay_ns_D3": finite(d3.get("delta_tau_ns")),
        "delay_error_ns": (finite(d3.get("delta_tau_ns")) - spec["delay_ns"]
                            if "M1" in mechanisms and math.isfinite(finite(d3.get("delta_tau_ns"))) else None),
        "true_cross_slope_deg_per_pulse": -spec["phase_deg"] if "M2" in mechanisms else 0.0,
        "estimated_cross_slope_deg_per_pulse_P1": finite(p1.get("slope_deg_per_pulse")),
        "phase_slope_error_deg_per_pulse": (finite(p1.get("slope_deg_per_pulse")) + spec["phase_deg"]
                                             if "M2" in mechanisms and math.isfinite(finite(p1.get("slope_deg_per_pulse"))) else None),
        "truth_rho": spec["rho"], "observed_rho_median": finite(result.get("rho_median")),
        "csi_coherence": finite(result.get("csi_coherence")),
        "feature_diagnosis": feature_row["diagnosis"],
        "feature_confidence": (max(0.0, min(1.0, finite(features["deterministic_phase_confidence"], 0.0)))
                               if math.isfinite(finite(features["deterministic_phase_confidence"])) else 0.0),
        "status": result.get("status"),
        "estimator_status": "pass" if audit.get("records") else "not_run",
        "estimator_feature_table": audit.get("feature_table"),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path,
                        default=ROOT / "configs/research/ai_csi_model_mismatch_suite.json")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--reuse-existing", action="store_true",
                        help="reuse a completed case with compact variant and estimator summaries")
    args = parser.parse_args()
    suite = json.loads(args.suite.resolve().read_text(encoding="utf-8"))
    base = copy.deepcopy(suite["base_scenario"])
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    design = DESIGN[:args.limit] if args.limit is not None else DESIGN
    result_rows: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    for index, spec in enumerate(design, 1):
        print(f"[mixed] {index}/{len(design)} {spec['id']} start", flush=True)
        case = copy.deepcopy(base)
        case["random"]["random_seed"] = spec["seed"]
        case["targets"][0]["init"]["azimuth_offset_deg"] = spec["squint_deg"]
        case["targets"][0]["init"]["expected_bin"] = spec["range_bin"]
        case["targets"][0]["amplitude"]["snr_db"] = spec["snr_db"]
        case["targets"][0]["motion"]["ve_mps"] = spec["ve_mps"]
        case["targets"][0]["motion"]["vn_mps"] = spec["vn_mps"]
        case["scene"]["area_clutter"]["texture_sigma"] = spec["texture_sigma"]
        case["scene"]["area_clutter"]["temporal_correlation_rho"] = spec["rho"]
        case["channel_impairments"]["channel_time_delay_ns"] = spec["delay_ns"]
        case["channel_impairments"]["per_pulse_phase_drift_deg"] = spec["phase_deg"]
        case["channel_impairments"]["enabled"] = bool(spec["delay_ns"] or spec["phase_deg"])
        case_dir = output / "cases" / spec["id"]
        existing_summary = case_dir / "variant_summary.json"
        existing_audit = case_dir / "estimator_audit/mechanism_estimator_audit.json"
        if args.reuse_existing and existing_summary.is_file() and existing_audit.is_file():
            result = json.loads(existing_summary.read_text(encoding="utf-8"))
            audit = json.loads(existing_audit.read_text(encoding="utf-8"))
            result_rows.append({**spec, **result, "estimator_records": len(audit.get("records", []))})
            feature_rows.append(row_from_case(spec, result, audit))
            print(f"[mixed] {index}/{len(design)} {spec['id']} reuse", flush=True)
            continue
        primary_path = ("channel_impairments.channel_time_delay_ns"
                        if spec["delay_ns"] else "channel_impairments.per_pulse_phase_drift_deg"
                        if spec["phase_deg"] else "scene.area_clutter.temporal_correlation_rho")
        primary_value = spec["delay_ns"] if spec["delay_ns"] else spec["phase_deg"] if spec["phase_deg"] else spec["rho"]
        result = run_one_variant(
            case, {"id": spec["label"], "json_path": primary_path},
            {"name": spec["id"], "value": primary_value}, case_dir,
            args.build_dir.resolve(), None)
        estimator_dir = case_dir / "estimator_audit"
        mechanisms = set(spec["label"].split("+")) & {"M1", "M2"}
        estimator_rc, audit = (run_estimator(case_dir, estimator_dir, mechanisms)
                               if mechanisms and result.get("status") == "pass" else (0, {"records": []}))
        result["estimator_exit_code"] = estimator_rc
        result["design_label"] = spec["label"]
        result["case_id"] = spec["id"]
        (case_dir / "variant_summary.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8")
        row = row_from_case(spec, result, audit)
        result_rows.append({**spec, **result, "estimator_records": len(audit.get("records", []))})
        feature_rows.append(row)
        print(f"[mixed] {index}/{len(design)} {spec['id']} {result.get('status')} estimator_rc={estimator_rc}", flush=True)
    write_csv(output / "mixed_production_summary.csv", result_rows)
    write_csv(output / "mechanism_feature_table.csv", feature_rows)
    counts: dict[tuple[str, str], int] = {}
    for row in feature_rows:
        key = (str(row["actual_mechanism"]), str(row["feature_diagnosis"]))
        counts[key] = counts.get(key, 0) + 1
    confusion = [{"actual_mechanism": actual, "predicted_diagnosis": predicted, "count": count}
                 for (actual, predicted), count in sorted(counts.items())]
    with (output / "mechanism_confusion_matrix.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["actual_mechanism", "predicted_diagnosis", "count"])
        writer.writeheader()
        writer.writerows(confusion)
    manifest = {
        "schema_version": 1, "ai_training": False,
        **git_provenance(ROOT),
        "design_count": len(design), "completed": sum(row.get("status") == "pass" for row in result_rows),
        "estimator_pass_count": sum(row.get("estimator_exit_code") == 0 for row in result_rows),
        "design": design, "confusion_matrix": confusion,
        "cleanup_note": "raw BIN/NPY/log/PNG are retained until the audit is reviewed, then can be removed by the explicit cleanup command",
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "design_count": len(design),
                      "completed": manifest["completed"],
                      "estimator_pass_count": manifest["estimator_pass_count"]}, ensure_ascii=False))
    return 0 if manifest["completed"] == len(design) and manifest["estimator_pass_count"] == len(design) else 1


if __name__ == "__main__":
    raise SystemExit(main())
