#!/usr/bin/env python3
"""按一个 family 一个 family 运行 Stage2 M1/M2/M3 challenge suite。

脚本只调用当前 ``simulate_stage2_statistical`` 和 ``GMTI_core``，不训练 AI。
每个 level 保持同一 Stage2 seed，仅改变一个明确的物理字段；zero/static
level 与独立 baseline 做 raw BIN SHA-256 回归。生产诊断由
``audit_csi_residual_texture.py`` 读取当前 CSI tap 产物。
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = ROOT / "scripts" / "audit_csi_residual_texture.py"
from experiment_provenance import git_provenance  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def set_path(document: dict[str, Any], dotted_path: str, value: Any) -> None:
    pieces = dotted_path.split(".")
    target: Any = document
    for piece in pieces[:-1]:
        if not isinstance(target, dict) or piece not in target:
            raise KeyError(f"JSON path 不存在：{dotted_path}")
        target = target[piece]
    if not isinstance(target, dict):
        raise KeyError(f"JSON path 父对象非法：{dotted_path}")
    target[pieces[-1]] = value


def run_logged(command: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as handle:
        handle.write("$ " + " ".join(command) + "\n")
        handle.flush()
        completed = subprocess.run(command, cwd=ROOT, stdout=handle,
                                   stderr=subprocess.STDOUT, check=False)
    return int(completed.returncode)


def patch_production_xml(xml_path: Path) -> None:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    parameter = root.find(".//GMTI_parameter")
    if parameter is None:
        raise RuntimeError(f"XML 没有 GMTI_parameter：{xml_path}")
    values = {
        "runtime_diagnostics_enabled": "1",
        "csi_metrics_enable": "1",
        "csi_metrics_dump_power_maps": "1",
        "csi_metrics_dump_intermediate_maps": "1",
        "csi_metrics_beam_id": "-1",
        "csi_row_coherence_gate_enable": "1",
        "csi_row_coherence_min": "0.5",
    }
    for tag, value in values.items():
        element = parameter.find(tag)
        if element is None:
            element = ET.SubElement(parameter, tag)
        element.text = value
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)


def find_single_manifest(variant_dir: Path) -> Path:
    manifests = sorted(variant_dir.glob(
        "stage2/algorithm_result/period_0000/csi_metrics/*/csi_roi_manifest.csv"))
    if len(manifests) != 1:
        raise RuntimeError(f"CSI manifest 数量不是 1：{variant_dir} -> {manifests}")
    return manifests[0]


def read_csv_row(path: Path, predicate=None) -> dict[str, str] | None:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if predicate is None:
        return rows[0] if rows else None
    for row in rows:
        if predicate(row):
            return row
    return None


def read_csi_summary(manifest: Path) -> dict[str, str]:
    summary = manifest.with_name("csi_metric_tap_summary.csv")
    row = read_csv_row(summary, lambda item: item.get("roi_name") == "active_support_unmasked")
    if row is None:
        raise RuntimeError(f"CSI summary 没有 active_support_unmasked：{summary}")
    return row


def read_gate_from_log(log_path: Path) -> dict[str, float | int | None]:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    matches = re.findall(
        r"\[CSI\]\[LEGACY-GATE\] rows=(\d+) gated_rows=(\d+) "
        r"coherence_mean=([0-9eE+\-.]+) min=([0-9eE+\-.]+)", text)
    if not matches:
        return {"rows": None, "gated_rows": None, "coherence_mean": None, "threshold": None}
    rows, gated, mean, threshold = matches[-1]
    return {"rows": int(rows), "gated_rows": int(gated),
            "coherence_mean": float(mean), "threshold": float(threshold)}


def read_cfar_from_log(log_path: Path) -> dict[str, float | int | None]:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    matches = re.findall(
        r"\[CFAR\]\[SUMMARY\].*?hit_cells=(\d+).*?clusters=(\d+)"
        r".*?selected=(\d+).*?min_points=([0-9eE+\-.]+)", text)
    if not matches:
        return {"hit_cells": None, "clusters": None, "selected": None, "min_points": None}
    hit, clusters, selected, min_points = matches[-1]
    return {"hit_cells": int(hit), "clusters": int(clusters),
            "selected": int(selected), "min_points": float(min_points)}


def first_period_data(variant_dir: Path) -> Path:
    files = sorted((variant_dir / "stage2" / "data").glob("*period_0000.bin"))
    if len(files) != 1:
        raise RuntimeError(f"period_0000 BIN 数量不是 1：{variant_dir} -> {files}")
    return files[0]


def raw_fast_time_phase_slope(data_path: Path, waveform: dict[str, Any]) -> dict[str, float | None]:
    pulse_len = int(waveform["pulse_len"])
    channel_count = int(waveform["new_protocol_channel_count"])
    fs_hz = float(waveform["fs_mhz"]) * 1.0e6
    iq_type = str(waveform.get("iq_data_type", "float32"))
    if iq_type not in ("float32", "float"):
        return {"measured_slope_rad_per_hz": None, "phase_slope_status": "unsupported_iq_type"}
    raw = np.fromfile(data_path, dtype=np.float32, offset=256,
                      count=pulse_len * channel_count * 2)
    if raw.size != pulse_len * channel_count * 2:
        return {"measured_slope_rad_per_hz": None, "phase_slope_status": "short_packet"}
    raw = raw.reshape(pulse_len, channel_count, 2)
    x1 = raw[:, 0, 0] + 1j * raw[:, 0, 1]
    x2 = raw[:, 1, 0] + 1j * raw[:, 1, 1]
    spectrum_1 = np.fft.fft(x1)
    spectrum_2 = np.fft.fft(x2)
    cross = spectrum_1 * np.conj(spectrum_2)
    freq = np.fft.fftfreq(pulse_len, d=1.0 / fs_hz)
    magnitude = np.abs(cross)
    mask = (freq > 0.0) & np.isfinite(magnitude) & (magnitude >= np.percentile(magnitude, 80.0))
    if int(np.sum(mask)) < 3:
        return {"measured_slope_rad_per_hz": None,
                "phase_slope_status": "insufficient_spectral_support"}
    order = np.argsort(freq[mask])
    phase = np.unwrap(np.angle(cross[mask][order]))
    slope, _ = np.polyfit(freq[mask][order], phase, 1)
    return {"measured_slope_rad_per_hz": float(slope), "phase_slope_status": "measured"}


def impairment_truth_slope(variant_dir: Path) -> dict[str, float | None]:
    path = variant_dir / "stage2" / "truth" / "channel_impairment_truth.csv"
    if not path.is_file():
        return {"truth_phase_slope_deg_per_pulse": None, "truth_effective_shift_samples": None}
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return {"truth_phase_slope_deg_per_pulse": None, "truth_effective_shift_samples": None}
    pulse = np.array([float(row["pulse_id"]) for row in rows], dtype=np.float64)
    phase = np.array([float(row["relative_phase_deg"]) for row in rows], dtype=np.float64)
    slope = float(np.polyfit(pulse, phase, 1)[0]) if len(rows) >= 2 else None
    return {"truth_phase_slope_deg_per_pulse": slope,
            "truth_effective_shift_samples": float(rows[0]["effective_shift_samples"])}


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run_one_variant(base: dict[str, Any], family: dict[str, Any], level: dict[str, Any],
                    variant_dir: Path, build_dir: Path, baseline_sha: str | None) -> dict[str, Any]:
    scenario = copy.deepcopy(base)
    scenario["case_id"] = f"{family['id']}_{level['name']}"
    scenario["output_dir"] = str((variant_dir / "stage2").resolve())
    parameter_path = str(family["json_path"])
    value = level["value"]
    set_path(scenario, parameter_path, value)
    if parameter_path.startswith("channel_impairments."):
        scenario["channel_impairments"]["enabled"] = True
    else:
        scenario["channel_impairments"]["enabled"] = False
    variant_dir.mkdir(parents=True, exist_ok=True)
    scenario_path = variant_dir / "scenario.json"
    scenario_path.write_text(json.dumps(scenario, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    simulate_log = variant_dir / "simulate_stage2.log"
    simulate_rc = run_logged(
        [str(build_dir / "simulate_stage2_statistical"), "--config", str(scenario_path)],
        simulate_log)
    result: dict[str, Any] = {
        "family": family["id"], "level": level["name"], "parameter": parameter_path,
        "value": value, "scenario": str(scenario_path), "simulate_exit_code": simulate_rc,
        "gmticore_exit_code": None, "audit_exit_code": None,
        "status": "simulate_failed" if simulate_rc else "pending",
    }
    if simulate_rc != 0:
        return result
    xml_path = variant_dir / "stage2" / "config" / "temp_config_stage2_period_0000.xml"
    if not xml_path.is_file():
        result["status"] = "missing_generated_xml"
        return result
    patch_production_xml(xml_path)
    gmti_log = variant_dir / "gmticore.log"
    gmti_rc = run_logged(
        [str(build_dir / "GMTI_core"), str(xml_path), "--runtime-mode=debug",
         "--runtime-diagnostics=on"], gmti_log)
    result["gmticore_exit_code"] = gmti_rc
    if gmti_rc != 0:
        result["status"] = "gmticore_failed"
        return result
    manifest = find_single_manifest(variant_dir)
    diagnostic_dir = variant_dir / "residual_texture"
    audit_rc = run_logged(
        [sys.executable, str(AUDIT_SCRIPT), "--manifest", str(manifest),
         "--output-dir", str(diagnostic_dir), "--period-id", "0", "--beam-id", "1"],
        variant_dir / "residual_texture.log")
    result["audit_exit_code"] = audit_rc
    if audit_rc != 0:
        result["status"] = "residual_audit_failed"
        return result
    summary = json.loads((diagnostic_dir / "residual_texture_summary.json").read_text(encoding="utf-8"))
    csi_row = read_csi_summary(manifest)
    gate = read_gate_from_log(gmti_log)
    cfar = read_cfar_from_log(gmti_log)
    raw_path = first_period_data(variant_dir)
    raw_sha = sha256_file(raw_path)
    result.update({
        "status": "pass",
        "raw_sha256": raw_sha,
        "same_as_zero_baseline": baseline_sha is not None and raw_sha == baseline_sha,
        "manifest": str(manifest),
        "residual_summary": str(diagnostic_dir / "residual_texture_summary.json"),
        "coherence_gate_pass_fraction": summary["row_classification"]["coherence_gate_pass_fraction"],
        "coherence_gate_bypass_fraction": summary["row_classification"]["coherence_gate_bypass_fraction"],
        "rho_median": summary["row_classification"]["rho_median"],
        "phase_rmse_rad": summary["phase_residual"]["summary"]["rmse_rad"],
        "phase_signature": summary["phase_residual"]["signature"],
        "wideband_delay_ns": summary["phase_residual"]["vs_range_frequency_linear_fit"]["delta_tau_ns"],
        "amplitude_median_db": summary["amplitude_residual_db"]["median"],
        "csi_rmse_rad": float(csi_row["phase_model_weighted_circular_rmse_rad"]),
        "csi_coherence": float(csi_row["phase_model_weighted_coherence"]),
        "csi_cancellation_db": float(csi_row["CA_ROI_dB"]),
        "p38_k_rad_per_hz": float(manifest_row(manifest)["p38_k_rad_per_hz"]),
        "p38_b_rad": float(manifest_row(manifest)["p38_b_rad"]),
        "gate_rows": gate["rows"], "gate_bypassed_rows": gate["gated_rows"],
        "gate_coherence_mean": gate["coherence_mean"],
        "cfar_hit_cells": cfar["hit_cells"], "cfar_clusters": cfar["clusters"],
        "cfar_selected": cfar["selected"],
    })
    result.update(impairment_truth_slope(variant_dir))
    if family["id"] == "M1_fractional_channel_delay":
        result.update(raw_fast_time_phase_slope(raw_path, base["waveform"]))
        delay_s = float(value) * 1.0e-9
        result["expected_phase_slope_rad_per_hz"] = float(2.0 * np.pi * delay_s)
        result["measured_minus_baseline_slope_rad_per_hz"] = None
    return result


def manifest_row(manifest: Path) -> dict[str, str]:
    row = read_csv_row(manifest)
    if row is None:
        raise RuntimeError(f"空 CSI manifest：{manifest}")
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path,
                        default=ROOT / "configs/research/ai_csi_model_mismatch_suite.json")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/ai_csi_model_mismatch")
    parser.add_argument("--random-seed", type=int, default=None,
                        help="覆盖 base_scenario.random.random_seed，用于独立 seed 回归")
    parser.add_argument("--family", choices=("all", "M1_fractional_channel_delay",
                                               "M2_pulse_varying_differential_phase",
                                               "M3_internal_clutter_motion"), default="all")
    parser.add_argument("--reuse-existing", action="store_true")
    args = parser.parse_args()
    suite = json.loads(args.suite.resolve().read_text(encoding="utf-8"))
    build_dir = args.build_dir.resolve()
    for binary in ("simulate_stage2_statistical", "GMTI_core"):
        if not (build_dir / binary).is_file():
            raise SystemExit(f"找不到构建产物：{build_dir / binary}")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    base = copy.deepcopy(suite["base_scenario"])
    if args.random_seed is not None:
        base["random"]["random_seed"] = args.random_seed
    baseline_dir = output_dir / "baseline"
    baseline = {
        "id": "baseline", "name": "phase_corrected_current_zero_mismatch",
        "json_path": "", "levels": [{"name": "zero", "value": 0.0}]
    }
    baseline_result = run_one_variant(
        base, {"id": "baseline", "json_path": "scene.area_clutter.temporal_correlation_rho"},
        {"name": "zero", "value": 1.0}, baseline_dir, build_dir, None)
    # The baseline helper above sets channel impairments false and rho=1, so it
    # is the intended strict Current zero-mismatch reference.
    if baseline_result.get("status") != "pass":
        raise SystemExit(f"baseline 失败：{baseline_result}")
    baseline_result.update(raw_fast_time_phase_slope(
        first_period_data(baseline_dir), base["waveform"]))
    baseline_sha = str(baseline_result["raw_sha256"])
    selected = [family for family in suite["families"]
                if args.family == "all" or family["id"] == args.family]
    if not selected:
        raise SystemExit(f"没有选择到 family：{args.family}")
    results: list[dict[str, Any]] = []
    for family in selected:
        family_dir = output_dir / family["id"]
        for level in family["levels"]:
            variant_dir = family_dir / str(level["name"])
            summary_path = variant_dir / "variant_summary.json"
            if args.reuse_existing and summary_path.is_file():
                result = json.loads(summary_path.read_text(encoding="utf-8"))
            else:
                result = run_one_variant(base, family, level, variant_dir, build_dir, baseline_sha)
                if result.get("status") == "pass" and family["id"] == "M1_fractional_channel_delay":
                    result["measured_minus_baseline_slope_rad_per_hz"] = (
                        (result.get("measured_slope_rad_per_hz") or 0.0)
                        - (baseline_result.get("measured_slope_rad_per_hz") or 0.0)
                    )
                summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")
            results.append(result)

    manifest = {
        "schema_version": 1, "suite": str(args.suite.resolve()),
        "family_selection": args.family, "ai_training": False,
        **git_provenance(ROOT),
        "random_seed_override": args.random_seed,
        "baseline": baseline_result, "variants": results,
        "forbidden_as_mismatch": suite.get("forbidden_as_mismatch", []),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fields = ["family", "level", "parameter", "value", "status", "simulate_exit_code",
              "gmticore_exit_code", "audit_exit_code", "same_as_zero_baseline",
              "rho_median", "coherence_gate_pass_fraction", "coherence_gate_bypass_fraction",
              "phase_rmse_rad", "phase_signature", "wideband_delay_ns",
              "amplitude_median_db", "csi_coherence", "csi_rmse_rad", "csi_cancellation_db",
              "p38_k_rad_per_hz", "p38_b_rad", "gate_rows", "gate_bypassed_rows",
              "gate_coherence_mean", "cfar_hit_cells", "cfar_clusters", "cfar_selected",
              "truth_phase_slope_deg_per_pulse", "truth_effective_shift_samples",
              "expected_phase_slope_rad_per_hz", "measured_slope_rad_per_hz",
              "measured_minus_baseline_slope_rad_per_hz", "phase_slope_status",
              "raw_sha256", "residual_summary"]
    write_csv(output_dir / "model_mismatch_summary.csv", fields, results)
    write_csv(output_dir / "coherence_gate_summary.csv",
              ["family", "level", "value", "rho_median", "gate_rows", "gate_bypassed_rows",
               "coherence_gate_pass_fraction", "coherence_gate_bypass_fraction", "csi_coherence"], results)
    write_csv(output_dir / "phase_residual_summary.csv",
              ["family", "level", "value", "phase_rmse_rad", "phase_signature",
               "wideband_delay_ns", "csi_rmse_rad", "residual_summary"], results)
    write_csv(output_dir / "p38_mismatch_summary.csv",
              ["family", "level", "value", "p38_k_rad_per_hz", "p38_b_rad",
               "csi_coherence", "csi_cancellation_db"], results)
    print(json.dumps({"output_dir": str(output_dir), "family": args.family,
                      "baseline_sha256": baseline_sha,
                      "completed": len(results),
                      "failed": sum(item.get("status") != "pass" for item in results)},
                     ensure_ascii=False))
    return 0 if all(item.get("status") == "pass" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
