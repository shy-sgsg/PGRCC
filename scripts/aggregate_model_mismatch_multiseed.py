#!/usr/bin/env python3
"""Aggregate the compact M1/M2/M3 challenge runs without rerunning CUDA.

The first-round output directories are preserved.  This script writes a
separate multi-seed evidence directory and never replaces the original
single-seed failure map.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path
from typing import Any

from model_mismatch_detection_eval import evaluate_variant
from experiment_provenance import git_provenance


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNS = [
    (2026091011, {
        "M1_fractional_channel_delay": ROOT / "outputs/ai_csi_model_mismatch_m1_rerun",
        "M2_pulse_varying_differential_phase": ROOT / "outputs/ai_csi_model_mismatch_m2",
        "M3_internal_clutter_motion": ROOT / "outputs/ai_csi_model_mismatch_m3",
    }),
    (2026091012, {"all": ROOT / "outputs/ai_csi_model_mismatch_seed2026091012"}),
    (2026091013, {"all": ROOT / "outputs/ai_csi_model_mismatch_seed2026091013"}),
]
DEFAULT_ORACLES = [
    (2026091011, ROOT / "outputs/ai_csi_model_mismatch/mechanism_oracle"),
    (2026091012, ROOT / "outputs/ai_csi_model_mismatch_oracle_seed2026091012"),
    (2026091013, ROOT / "outputs/ai_csi_model_mismatch_oracle_seed2026091013"),
]
DEFAULT_CONTROLS_ROOT = ROOT / "outputs/ai_csi_model_mismatch_metric_controls"
DEFAULT_DIAGNOSTICS_CSV = (
    ROOT / "outputs/ai_csi_model_mismatch_p38_cfar_diagnostics"
    / "production_diagnostic_metrics.csv"
)

METRICS = (
    "rho_median",
    "coherence_gate_pass_fraction",
    "coherence_gate_bypass_fraction",
    "phase_rmse_rad",
    "csi_coherence",
    "csi_rmse_rad",
    "csi_cancellation_db",
    "cfar_selected",
)
DETECTION_METRICS = (
    "Pd",
    "visible_target_count",
    "detection_count",
    "matched_target_count",
    "false_alarm_count",
)
CONTROL_METRICS = (
    "target_loss",
    "Pfa",
)
ORACLE_METRICS = (
    "delta_cancellation_db",
    "delta_phase_rmse_rad",
    "delta_coherence",
    "current_cancellation_db",
    "oracle_cancellation_db",
    "current_phase_rmse_rad",
    "oracle_phase_rmse_rad",
    "current_coherence",
    "oracle_coherence",
    "oracle_bypass_fraction",
)
DIAGNOSTIC_METRICS = (
    "p38_inlier_ratio",
    "cfar_margin",
)


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_diagnostic_overrides(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    overrides: dict[str, dict[str, str]] = {}
    for row in read_rows(path):
        source_variant = row.get("source_variant", "")
        if source_variant:
            overrides[str(Path(source_variant).resolve())] = row
    return overrides


def manifest_seed(path: Path, fallback: int) -> int:
    manifest = path / "manifest.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        override = data.get("random_seed_override")
        if override is not None:
            return int(override)
        base_seed = data.get("baseline", {}).get("scenario")
        if base_seed:
            scenario = Path(base_seed).resolve()
            if scenario.is_file():
                payload = json.loads(scenario.read_text(encoding="utf-8"))
                return int(payload["random"]["random_seed"])
    return fallback


def resolve_runs(specs: list[str] | None) -> list[tuple[int, dict[str, Path]]]:
    if not specs:
        return DEFAULT_RUNS
    runs: list[tuple[int, dict[str, Path]]] = []
    for spec in specs:
        try:
            seed_text, path_text = spec.split("=", 1)
            seed = int(seed_text)
        except ValueError as exc:
            raise SystemExit(f"--run-dir 格式应为 SEED=PATH：{spec}") from exc
        runs.append((seed, {"all": (ROOT / path_text).resolve()}))
    return runs


def load_control_metrics(variant: Path, controls_root: Path) -> dict[str, Any]:
    try:
        relative = variant.relative_to(ROOT)
    except ValueError:
        return {metric: None for metric in CONTROL_METRICS}
    result = {metric: None for metric in CONTROL_METRICS}
    target_path = controls_root / relative / "target_only/control_metrics.json"
    negative_path = controls_root / relative / "negative_control/control_metrics.json"
    if target_path.is_file():
        payload = json.loads(target_path.read_text(encoding="utf-8"))
        result["target_loss"] = finite(payload.get("metrics", {}).get("target_loss_dB"))
    if negative_path.is_file():
        payload = json.loads(negative_path.read_text(encoding="utf-8"))
        result["Pfa"] = finite(payload.get("metrics", {}).get("Pfa"))
    return result


def load_records(runs: list[tuple[int, dict[str, Path]]],
                 controls_root: Path,
                 diagnostics: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for fallback_seed, family_dirs in runs:
        any_dir = next(iter(family_dirs.values()))
        seed = manifest_seed(any_dir, fallback_seed)
        for family_hint, root in family_dirs.items():
            summary = root / "model_mismatch_summary.csv"
            if not summary.is_file():
                raise SystemExit(f"缺少 model_mismatch_summary.csv：{summary}")
            for row in read_rows(summary):
                family = row.get("family", "") or family_hint
                if family_hint != "all" and family != family_hint:
                    raise SystemExit(f"family 不一致：{summary}: {family} != {family_hint}")
                record: dict[str, Any] = {
                    "seed": seed,
                    "family": family,
                    "level": row.get("level", ""),
                    "value": finite(row.get("value")),
                    "status": row.get("status", ""),
                    "same_as_zero_baseline": row.get("same_as_zero_baseline", ""),
                    "source_root": str(root),
                }
                variant = root / family / str(row.get("level", ""))
                detection = evaluate_variant(variant)
                for metric in DETECTION_METRICS:
                    record[metric] = finite(detection.get(metric))
                controls = load_control_metrics(variant, controls_root)
                for metric in CONTROL_METRICS:
                    record[metric] = controls[metric]
                diagnostic = diagnostics.get(str(variant.resolve()), {})
                record["p38_inlier_ratio"] = finite(diagnostic.get("p38_inlier_ratio_mean"))
                record["cfar_margin"] = finite(diagnostic.get("cfar_margin_db_mean"))
                for metric in METRICS:
                    record[metric] = finite(row.get(metric))
                records.append(record)
    return records


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_oracle_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for seed, root in DEFAULT_ORACLES:
        summary = root / "mechanism_oracle_summary.csv"
        if not summary.is_file():
            raise SystemExit(f"缺少 mechanism_oracle_summary.csv：{summary}")
        for row in read_rows(summary):
            record: dict[str, Any] = {
                "seed": seed,
                "family": row.get("family", ""),
                "status": row.get("status", ""),
                "source_root": str(root),
            }
            for metric in ORACLE_METRICS:
                record[metric] = finite(row.get(metric))
            records.append(record)
    return records


def aggregate_oracles(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(record["family"], []).append(record)
    rows: list[dict[str, Any]] = []
    for family, group in sorted(groups.items()):
        row: dict[str, Any] = {
            "family": family,
            "seed_count": len({item["seed"] for item in group}),
            "all_status_pass": all(item["status"] == "pass" for item in group),
        }
        for metric in ORACLE_METRICS:
            values = [item[metric] for item in group if item[metric] is not None]
            row[f"{metric}_mean"] = statistics.fmean(values) if values else None
            row[f"{metric}_std_population"] = statistics.pstdev(values) if len(values) > 1 else 0.0 if values else None
            row[f"{metric}_min"] = min(values) if values else None
            row[f"{metric}_max"] = max(values) if values else None
        rows.append(row)
    return rows


def aggregate(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault((record["family"], record["level"]), []).append(record)
    rows: list[dict[str, Any]] = []
    for (family, level), group in sorted(groups.items()):
        row: dict[str, Any] = {
            "family": family,
            "level": level,
            "value": group[0]["value"],
            "seed_count": len({item["seed"] for item in group}),
            "all_status_pass": all(item["status"] == "pass" for item in group),
            "baseline_regression_rate": sum(
                item["same_as_zero_baseline"] == "True" for item in group
            ) / len(group),
            "failure_type": (
                "none" if level in ("zero", "static_regression")
                else "Type_I_calibration_model_failure"
                if family.startswith(("M1_", "M2_"))
                else "Type_II_decorrelation_failure"
            ),
            "failure_flag": (
                "baseline_regression" if level in ("zero", "static_regression")
                else "candidate_failure_boundary"
            ),
        }
        for metric in (*DETECTION_METRICS, *CONTROL_METRICS, *DIAGNOSTIC_METRICS, *METRICS):
            values = [item[metric] for item in group if item[metric] is not None]
            row[f"{metric}_mean"] = statistics.fmean(values) if values else None
            row[f"{metric}_std_population"] = statistics.pstdev(values) if len(values) > 1 else 0.0 if values else None
            row[f"{metric}_min"] = min(values) if values else None
            row[f"{metric}_max"] = max(values) if values else None
        rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path,
        default=ROOT / "outputs/ai_csi_model_mismatch_multiseed",
    )
    parser.add_argument(
        "--run-dir", action="append", metavar="SEED=PATH",
        help="可重复指定额外 run；默认读取首轮 seed 2026091011/12/13",
    )
    parser.add_argument(
        "--controls-root", type=Path,
        default=DEFAULT_CONTROLS_ROOT,
        help="paired target-only/negative-control 输出根目录",
    )
    parser.add_argument(
        "--diagnostics-csv", type=Path,
        default=DEFAULT_DIAGNOSTICS_CSV,
        help="生产 CUDA 诊断 CSV；用于 multi-seed 的 P38/CFAR 字段",
    )
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    controls_root = args.controls_root.resolve()
    diagnostics_csv = args.diagnostics_csv.resolve()
    diagnostics = load_diagnostic_overrides(diagnostics_csv)
    records = load_records(resolve_runs(args.run_dir), controls_root, diagnostics)
    if len({record["seed"] for record in records}) < 3:
        raise SystemExit("至少需要 3 个独立 seed 才能生成 multi-seed 汇总")
    raw_fields = ["seed", "family", "level", "value", "status",
                  "same_as_zero_baseline", "source_root",
                  *DETECTION_METRICS, *CONTROL_METRICS,
                  *DIAGNOSTIC_METRICS, *METRICS]
    write_csv(output_dir / "model_mismatch_by_seed.csv", raw_fields, records)
    aggregate_rows = aggregate(records)
    aggregate_fields = ["family", "level", "value", "seed_count",
                        "all_status_pass", "baseline_regression_rate",
                        "failure_type", "failure_flag"]
    for metric in (*DETECTION_METRICS, *CONTROL_METRICS, *DIAGNOSTIC_METRICS, *METRICS):
        aggregate_fields.extend([
            f"{metric}_mean", f"{metric}_std_population",
            f"{metric}_min", f"{metric}_max",
        ])
    write_csv(output_dir / "failure_map_multiseed.csv", aggregate_fields, aggregate_rows)
    oracle_records = load_oracle_records()
    oracle_raw_fields = ["seed", "family", "status", "source_root", *ORACLE_METRICS]
    write_csv(output_dir / "mechanism_oracle_by_seed.csv", oracle_raw_fields, oracle_records)
    oracle_rows = aggregate_oracles(oracle_records)
    oracle_fields = ["family", "seed_count", "all_status_pass"]
    for metric in ORACLE_METRICS:
        oracle_fields.extend([
            f"{metric}_mean", f"{metric}_std_population",
            f"{metric}_min", f"{metric}_max",
        ])
    write_csv(output_dir / "mechanism_oracle_summary_multiseed.csv", oracle_fields, oracle_rows)
    manifest = {
        "schema_version": 1,
        "ai_training": False,
        **git_provenance(ROOT),
        "seed_count": len({record["seed"] for record in records}),
        "seeds": sorted({record["seed"] for record in records}),
        "record_count": len(records),
        "oracle_record_count": len(oracle_records),
        "input_roots": sorted({record["source_root"] for record in records}),
        "controls_root": str(controls_root),
        "diagnostic_override_csv": str(diagnostics_csv) if diagnostics else "",
        "diagnostic_override_rows": len(diagnostics),
        "outputs": {
            "by_seed": str(output_dir / "model_mismatch_by_seed.csv"),
            "failure_map": str(output_dir / "failure_map_multiseed.csv"),
            "oracle_by_seed": str(output_dir / "mechanism_oracle_by_seed.csv"),
            "oracle_summary": str(output_dir / "mechanism_oracle_summary_multiseed.csv"),
        },
        "definitions": {
            "mean": "arithmetic mean over independent seed records",
            "std_population": "population standard deviation over those records",
            "baseline_regression_rate": "fraction of zero/static rows whose raw SHA matches that run's baseline",
            "Pd": "positive-run production detection snapshot matched one-to-one to visible truth with the repository localization matcher; detection target_*_truth diagnostic columns are not used as item positions",
            "target_loss": "target-only production control: 10log10(mean after ROI power / mean before ROI power), negative means target power loss",
            "Pfa": "negative-control production run with targets disabled: CFAR hit cells / valid CFAR test cells reconstructed from CUDA cfar_detect_kernel geometry; configured pf is not substituted",
            "P38_inlier_ratio": "mean production diagnostic p38_raw_inlier_ratio or p38_refit_inlier_ratio over detections",
            "cfar_margin": "mean production diagnostic 10log10(power_map / CFAR threshold_map) over detections",
            "M3_phase_rmse": "not compared as a recovery objective; M3 headroom is interpreted through cancellation and coherence",
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output_dir": str(output_dir),
        "seeds": manifest["seeds"],
        "records": len(records),
        "groups": len(aggregate_rows),
        "oracle_records": len(oracle_records),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
