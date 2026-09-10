#!/usr/bin/env python3
"""汇总已完成的 M1/M2/M3 challenge 结果为 Current failure map。

本脚本只读取已完成的生产 CUDA 运行产物，不重新运行 simulator/GMTI，也不
训练 AI。Pd 来自正例生产检测快照；Pfa 和 target loss 来自独立的
negative-control/target-only 生产控制。缺少控制产物时保留为空，避免把 CFAR
选中数或配置的 Pfa 当成实测结论。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np

from model_mismatch_detection_eval import evaluate_variant
from experiment_provenance import git_provenance


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTROLS_ROOT = ROOT / "outputs/ai_csi_model_mismatch_metric_controls"
DEFAULT_DIAGNOSTICS_CSV = (
    ROOT / "outputs/ai_csi_model_mismatch_p38_cfar_diagnostics"
    / "production_diagnostic_metrics.csv"
)

FAMILY_SPECS = {
    "M1_fractional_channel_delay": {
        "root": ROOT / "outputs/ai_csi_model_mismatch_m1_rerun",
        "value_field": "channel_time_delay_ns",
        "failure_type": "Type_I_calibration_model_failure",
        "failure_evidence": "fractional_delay_injected_in_raw_LFM; residual and cancellation degrade monotonically in the controlled sweep",
    },
    "M2_pulse_varying_differential_phase": {
        "root": ROOT / "outputs/ai_csi_model_mismatch_m2",
        "value_field": "per_pulse_phase_drift_deg",
        "failure_type": "Type_I_calibration_model_failure",
        "failure_evidence": "channel_impairment_truth confirms pulse-varying phase; slow-time residual fit becomes linear with the configured slope",
    },
    "M3_internal_clutter_motion": {
        "root": ROOT / "outputs/ai_csi_model_mismatch_m3",
        "value_field": "temporal_correlation_rho",
        "failure_type": "Type_II_decorrelation_failure",
        "failure_evidence": "rho<1 changes the shared surface realization across pulses; CSI coherence and cancellation degrade without gate bypass in this range",
    },
}


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_diagnostic_overrides(path: Path) -> dict[str, dict[str, str]]:
    """Load production diagnostic values keyed by their exact source variant."""
    if not path.is_file():
        return {}
    overrides: dict[str, dict[str, str]] = {}
    for row in read_rows(path):
        source_variant = row.get("source_variant", "")
        if source_variant:
            overrides[str(Path(source_variant).resolve())] = row
    return overrides


def diagnostic_row(
    variant: Path, overrides: dict[str, dict[str, str]]
) -> dict[str, str]:
    return overrides.get(str(variant.resolve()), {})


def finite(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def first_row(path: Path, roi_name: str | None = None) -> dict[str, str] | None:
    rows = read_rows(path)
    if roi_name is None:
        return rows[0] if rows else None
    return next((row for row in rows if row.get("roi_name") == roi_name), None)


def manifest_row(variant: Path) -> dict[str, str] | None:
    manifests = sorted(
        (variant / "stage2/algorithm_result/period_0000/csi_metrics").glob(
            "*/csi_roi_manifest.csv"
        )
    )
    if len(manifests) != 1:
        return None
    return first_row(manifests[0])


def residual_tails(manifest: dict[str, str] | None) -> dict[str, float]:
    if manifest is None:
        return {"residual_p95": math.nan, "residual_p99": math.nan, "residual_cvar95": math.nan}
    power_path = Path(manifest.get("after_power_path", ""))
    if not power_path.is_file():
        return {"residual_p95": math.nan, "residual_p99": math.nan, "residual_cvar95": math.nan}
    values = np.asarray(np.load(power_path), dtype=np.float64).ravel()
    values = values[np.isfinite(values) & (values >= 0.0)]
    if values.size == 0:
        return {"residual_p95": math.nan, "residual_p99": math.nan, "residual_cvar95": math.nan}
    p95 = float(np.percentile(values, 95.0))
    p99 = float(np.percentile(values, 99.0))
    cvar = float(np.mean(values[values >= p95]))

    def db(power: float) -> float:
        return 10.0 * math.log10(max(power, np.finfo(float).tiny))

    return {"residual_p95": db(p95), "residual_p99": db(p99), "residual_cvar95": db(cvar)}


def p38_metrics(
    variant: Path, overrides: dict[str, dict[str, str]]
) -> dict[str, float]:
    path = variant / "stage2/algorithm_result/period_0000/detection_results_GMTI01.csv"
    rows = read_rows(path) if path.is_file() else []
    rmse = [finite(row.get("p38_raw_rmse")) for row in rows]
    rmse = [value for value in rmse if math.isfinite(value)]
    override = diagnostic_row(variant, overrides)
    override_inlier_ratio = finite(override.get("p38_inlier_ratio_mean"))
    if math.isfinite(override_inlier_ratio):
        inlier_ratio = [override_inlier_ratio]
    else:
        refit_inlier_ratio = [finite(row.get("p38_refit_inlier_ratio")) for row in rows]
        refit_inlier_ratio = [value for value in refit_inlier_ratio if math.isfinite(value)]
        inlier_ratio = refit_inlier_ratio
        if not inlier_ratio:
            inlier_ratio = [finite(row.get("p38_raw_inlier_ratio")) for row in rows]
            inlier_ratio = [value for value in inlier_ratio if math.isfinite(value)]
    return {
        "p38_rmse": float(np.mean(rmse)) if rmse else math.nan,
        "p38_inlier_ratio": float(np.mean(inlier_ratio)) if inlier_ratio else math.nan,
    }


def cfar_metrics(
    variant: Path, overrides: dict[str, dict[str, str]]
) -> dict[str, float]:
    override = diagnostic_row(variant, overrides)
    override_margin = finite(override.get("cfar_margin_db_mean"))
    path = variant / "gmticore.log"
    if not path.is_file():
        return {
            "cfar_selected": math.nan,
            "cfar_margin": override_margin,
            "pfa_proxy_cluster_rate": math.nan,
        }
    text = path.read_text(encoding="utf-8", errors="replace")
    matches = re.findall(
        r"\[CFAR\]\[SUMMARY\].*?hit_cells=(\d+).*?clusters=(\d+)"
        r".*?selected=(\d+)"
        r".*?pf=([0-9eE+\-.]+)",
        text,
    )
    if not matches:
        return {
            "cfar_selected": math.nan,
            "cfar_margin": override_margin,
            "pfa_proxy_cluster_rate": math.nan,
        }
    _, _, selected, _ = matches[-1]
    detection_path = variant / "stage2/algorithm_result/period_0000/detection_results_GMTI01.csv"
    margins = []
    if detection_path.is_file():
        for row in read_rows(detection_path):
            value = finite(row.get("cfar_margin_db"))
            if math.isfinite(value):
                margins.append(value)
    if not margins and math.isfinite(override_margin):
        margins = [override_margin]
    return {
        "cfar_selected": float(selected),
        "cfar_margin": float(np.mean(margins)) if margins else math.nan,
        "pfa_proxy_cluster_rate": math.nan,
    }


def control_metrics(variant: Path, controls_root: Path) -> dict[str, Any]:
    """Load measured target-only/Pfa controls for one exact source variant."""
    try:
        relative = variant.relative_to(ROOT)
    except ValueError:
        return {"control_status": "variant_outside_workspace"}
    result: dict[str, Any] = {"control_status": "missing"}
    target_path = controls_root / relative / "target_only/control_metrics.json"
    negative_path = controls_root / relative / "negative_control/control_metrics.json"
    if target_path.is_file():
        payload = json.loads(target_path.read_text(encoding="utf-8"))
        metrics = payload.get("metrics", {})
        result.update({
            "target_loss": finite(metrics.get("target_loss_dB")),
            "target_loss_status": metrics.get("status", ""),
            "target_power_before": finite(
                (metrics.get("targets") or [{}])[0].get("target_power_before")
            ),
            "target_power_after": finite(
                (metrics.get("targets") or [{}])[0].get("target_power_after")
            ),
            "target_control_path": str(target_path),
        })
    if negative_path.is_file():
        payload = json.loads(negative_path.read_text(encoding="utf-8"))
        metrics = payload.get("metrics", {})
        geometry = metrics.get("cfar_geometry", {})
        result.update({
            "Pfa": finite(metrics.get("Pfa")),
            "Pfa_status": metrics.get("status", ""),
            "Pfa_hit_cells": finite(metrics.get("hit_cells")),
            "Pfa_test_cells": finite(geometry.get("valid_cfar_test_cells")),
            "Pfa_control_path": str(negative_path),
        })
    if target_path.is_file() and negative_path.is_file():
        result["control_status"] = "measured"
    elif target_path.is_file() or negative_path.is_file():
        result["control_status"] = "partial"
    return result


def build_row(family: str, level: str, summary: dict[str, Any], spec: dict[str, Any],
              controls_root: Path,
              diagnostics: dict[str, dict[str, str]]) -> dict[str, Any]:
    variant = Path(summary["scenario"]).parent
    residual = json.loads(Path(summary["residual_summary"]).read_text(encoding="utf-8"))
    csi_row = first_row(
        Path(summary["manifest"]).with_name("csi_metric_tap_summary.csv"),
        "active_support_unmasked",
    ) or {}
    tails = residual_tails(manifest_row(variant))
    p38 = p38_metrics(variant, diagnostics)
    cfar = cfar_metrics(variant, diagnostics)
    detection = evaluate_variant(variant)
    controls = control_metrics(variant, controls_root)
    value = finite(summary.get("value"))
    is_zero = level in {"zero", "static_regression"}
    row = {
        "mismatch_family": family,
        "mismatch_value": value,
        "seed": 2026091011,
        "level": level,
        "coherence": finite(summary.get("rho_median")),
        "gate_pass_fraction": finite(summary.get("coherence_gate_pass_fraction")),
        "gate_bypass_fraction": finite(summary.get("coherence_gate_bypass_fraction")),
        "phase_RMSE": finite(summary.get("phase_rmse_rad")),
        "P38_RMSE": p38["p38_rmse"],
        "P38_inlier_ratio": p38["p38_inlier_ratio"],
        "CSI_cancellation_dB": finite(summary.get("csi_cancellation_db")),
        "residual_p95": tails["residual_p95"],
        "residual_p99": tails["residual_p99"],
        "CVaR95": tails["residual_cvar95"],
        "SCNR_improvement": finite(summary.get("csi_cancellation_db")),
        "target_loss": controls.get("target_loss", math.nan),
        "target_loss_status": controls.get("target_loss_status", ""),
        "target_power_before": controls.get("target_power_before", math.nan),
        "target_power_after": controls.get("target_power_after", math.nan),
        "Pd": finite(detection.get("Pd")),
        "Pfa": controls.get("Pfa", math.nan),
        "Pfa_status": controls.get("Pfa_status", ""),
        "Pfa_hit_cells": controls.get("Pfa_hit_cells", math.nan),
        "Pfa_test_cells": controls.get("Pfa_test_cells", math.nan),
        "control_status": controls.get("control_status", "missing"),
        "visible_target_count": detection.get("visible_target_count"),
        "detection_count": detection.get("detection_count"),
        "matched_target_count": detection.get("matched_target_count"),
        "false_alarm_count": detection.get("false_alarm_count"),
        "detection_eval_status": detection.get("status", ""),
        "CFAR_margin": cfar["cfar_margin"],
        "CFAR_selected": cfar["cfar_selected"],
        "Pfa_proxy_cluster_rate": cfar["pfa_proxy_cluster_rate"],
        "failure_type": "none" if is_zero else spec["failure_type"],
        "failure_flag": "baseline_regression" if is_zero else "candidate_failure_boundary",
        "failure_evidence": "rho=1/zero raw SHA regression" if is_zero else spec["failure_evidence"],
        "source_summary": str(Path(summary["scenario"]).parent / "variant_summary.json"),
        "input_run_status": summary.get("status", ""),
        "target_control_path": controls.get("target_control_path", ""),
        "Pfa_control_path": controls.get("Pfa_control_path", ""),
    }
    return row


def h0_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for gate, bypass, hit_count in (("false", 0.0, 21.0), ("true", 1.0, 0.0)):
        summary_path = next(
            (ROOT / f"outputs/h0_gate_ab/gate_{gate}/result/csi_metrics").glob(
                "*/csi_metric_tap_summary.csv"
            ),
            None,
        )
        csi = first_row(summary_path, "active_support_unmasked") if summary_path else {}
        rows.append({
            "mismatch_family": "H0_pure_thermal_noise",
            "mismatch_value": 0.0 if gate == "false" else 1.0,
            "seed": 2026090901,
            "level": f"gate_{gate}",
            "coherence": finite((csi or {}).get("phase_model_weighted_coherence")),
            "gate_pass_fraction": math.nan if gate == "false" else 0.0,
            "gate_bypass_fraction": math.nan if gate == "false" else bypass,
            "phase_RMSE": finite((csi or {}).get("phase_model_weighted_circular_rmse_rad")),
            "P38_RMSE": math.nan,
            "P38_inlier_ratio": math.nan,
            "CSI_cancellation_dB": finite((csi or {}).get("CA_ROI_dB")),
            "residual_p95": math.nan,
            "residual_p99": math.nan,
            "CVaR95": math.nan,
            "SCNR_improvement": math.nan,
            "target_loss": math.nan,
            "Pd": math.nan,
            "Pfa": math.nan,
            "CFAR_margin": math.nan,
            "CFAR_selected": hit_count,
            "Pfa_proxy_cluster_rate": math.nan,
            "failure_type": "Type_III_H0_statistical_failure",
            "failure_flag": "gate_protection" if gate == "true" else "legacy_H0_distortion",
            "failure_evidence": "pure-noise A/B: gate=true gated 75/75 rows and hits=0; gate=false hits=21",
            "source_summary": str(ROOT / f"outputs/h0_gate_ab/gate_{gate}/result"),
            "input_run_status": "measured",
        })
    return rows


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/ai_csi_model_mismatch")
    parser.add_argument("--controls-root", type=Path, default=DEFAULT_CONTROLS_ROOT)
    parser.add_argument("--diagnostics-csv", type=Path, default=DEFAULT_DIAGNOSTICS_CSV,
                        help="生产 CUDA 诊断 CSV；用于覆盖旧产物缺失的 P38/CFAR 字段")
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    controls_root = args.controls_root.resolve()
    diagnostics_csv = args.diagnostics_csv.resolve()
    diagnostics = load_diagnostic_overrides(diagnostics_csv)
    all_rows: list[dict[str, Any]] = []
    source_manifests: dict[str, str] = {}
    for family, spec in FAMILY_SPECS.items():
        manifest_path = Path(spec["root"]) / "manifest.json"
        if not manifest_path.is_file():
            raise SystemExit(f"缺少已完成 family manifest：{manifest_path}")
        source_manifests[family] = str(manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for summary in manifest.get("variants", []):
            if summary.get("status") != "pass":
                raise SystemExit(f"family 中存在非 pass variant：{family} {summary}")
            all_rows.append(build_row(family, str(summary["level"]), summary, spec,
                                      controls_root, diagnostics))
    all_rows.extend(h0_rows())
    fields = [
        "mismatch_family", "mismatch_value", "seed", "level", "coherence",
        "gate_pass_fraction", "gate_bypass_fraction", "phase_RMSE", "P38_RMSE",
        "P38_inlier_ratio", "CSI_cancellation_dB", "residual_p95", "residual_p99",
        "CVaR95", "SCNR_improvement", "target_loss", "target_loss_status",
        "target_power_before", "target_power_after", "Pd", "Pfa", "Pfa_status",
        "Pfa_hit_cells", "Pfa_test_cells", "control_status",
        "visible_target_count", "detection_count", "matched_target_count",
        "false_alarm_count", "detection_eval_status", "CFAR_margin",
        "CFAR_selected", "Pfa_proxy_cluster_rate", "failure_type", "failure_flag",
        "failure_evidence", "source_summary", "input_run_status",
        "target_control_path", "Pfa_control_path",
    ]
    write_csv(output_dir / "failure_map.csv", fields, all_rows)
    provenance = {
        "schema_version": 1,
        "ai_training": False,
        **git_provenance(ROOT),
        "source_manifests": source_manifests,
        "h0_ab_source": str(ROOT / "outputs/h0_gate_ab"),
        "controls_root": str(controls_root),
        "diagnostic_override_csv": str(diagnostics_csv) if diagnostics else "",
        "diagnostic_override_rows": len(diagnostics),
        "definitions": {
            "coherence": "audit_csi_residual_texture row rho median",
            "phase_RMSE": "valid clutter-cell phase residual RMSE in radians",
            "P38_RMSE": "mean finite p38_raw_rmse from detection_results_GMTI01.csv",
            "P38_inlier_ratio": "mean finite p38_refit_inlier_ratio or p38_raw_inlier_ratio from the production diagnostic detection CSV; fallback to the source production detection CSV when already exported",
            "residual_p95_p99_CVaR95": "10log10 of after_power map percentile/tail mean",
            "SCNR_improvement": "current csi_metric_tap_summary CA_ROI_dB proxy; not a target SCNR measurement",
            "Pd": "positive-run production detection snapshot matched one-to-one to visible truth with the repository localization matcher; detection target_*_truth diagnostic columns are not used as item positions",
            "target_loss": "target-only production control: 10log10(mean after ROI power / mean before ROI power), negative means target power loss; target ROI is located from Stage2 visible truth and the production fa_axis",
            "Pfa": "negative-control production run with targets disabled: CFAR hit cells / valid CFAR test cells reconstructed from CUDA cfar_detect_kernel geometry; configured pf is not substituted",
            "Pfa_hit_cells": "CFAR hit_cells from negative-control [CFAR][SUMMARY]",
            "Pfa_test_cells": "valid CFAR test-cell denominator from runtime rows/cols, guard/background, circularity, row exclusions, and split branch geometry",
            "CFAR_selected": "selected detection cluster count, not Pfa",
            "CFAR_margin": "mean candidate peak margin in dB, 10log10(power_map / production CFAR threshold_map), from the production diagnostic detection CSV; fallback to the source production detection CSV when already exported",
        },
        "interpretation": {
            "Type_I": "calibration/model representation candidate; perfect mechanism correction may have headroom",
            "Type_II": "true temporal decorrelation candidate; perfect phase may not recover all residual",
            "Type_III": "H0 statistical path; bypass is the intended response",
        },
    }
    (output_dir / "failure_map_manifest.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output_dir": str(output_dir), "rows": len(all_rows), "ai_training": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
