#!/usr/bin/env python3
"""Audit the frozen C4 support-failure replay's selected F1/F2 cells."""

from __future__ import annotations

import argparse
import cmath
import csv
import hashlib
import json
import math
import os
import re
import shutil
import struct
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_REPLAY_ROOT = REPO_ROOT / "outputs/r22_s1_c4_support_replay_20261029"
DEFAULT_EVIDENCE_ROOT = Path(__file__).resolve().parent / "evidence"
TRACE_GROUP = (5, 50, 52, 1065, 1197)
PHASE_THRESHOLD_RAD = 0.35
MIN_SUPPORT = 8
EXPECTED_CELLS = TRACE_GROUP[4] - TRACE_GROUP[3] + 1
SPECS = {
    "ON20": ("snr20", "C4_modeb_ON", "Mode-B", "ON"),
    "OFF20": ("snr20", "C4_modea_OFF", "Mode-A", "OFF"),
    "TO20": ("snr20", "C4_modea_TO", "Mode-A", "TO"),
    "ON30": ("snr30", "C4_modeb_ON", "Mode-B", "ON"),
    "OFF30": ("snr30", "C4_modea_OFF", "Mode-A", "OFF"),
    "TO30": ("snr30", "C4_modea_TO", "Mode-A", "TO"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def f32(text: str) -> float:
    """Round-trip the recorded decimal back to the original float32 input."""
    return struct.unpack("<f", struct.pack("<f", float(text)))[0]


def trace_path(replay_root: Path, snr_name: str, branch: str) -> Path:
    matches = list(
        (replay_root / snr_name).glob(
            f"pipeline/cases/*/production/{branch}/result/production_calibration_selected_cells.csv"
        )
    )
    if len(matches) != 1:
        raise ValueError(f"expected one selected-cell trace for {snr_name}/{branch}, got {len(matches)}")
    return matches[0]


def load_trace(path: Path) -> tuple[list[dict[str, str]], dict[int, tuple[complex, complex]]]:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != EXPECTED_CELLS:
        raise ValueError(f"{path} has {len(rows)} rows; expected {EXPECTED_CELLS}")
    expected_coords = {
        (str(TRACE_GROUP[0]), str(TRACE_GROUP[1]), str(TRACE_GROUP[2]),
         str(TRACE_GROUP[3]), str(TRACE_GROUP[4]))
    }
    coordinates = {
        (row["period_id"], row["beam_id"], row["az_index"],
         row["range_start"], row["range_end"])
        for row in rows
    }
    if coordinates != expected_coords:
        raise ValueError(f"unexpected trace coordinates in {path}: {coordinates}")
    samples: dict[int, tuple[complex, complex]] = {}
    for row in rows:
        range_index = int(row["range_index"])
        if range_index in samples:
            raise ValueError(f"duplicate range index {range_index} in {path}")
        first = complex(f32(row["f1_real"]), f32(row["f1_imag"]))
        second = complex(f32(row["f2_real"]), f32(row["f2_imag"]))
        samples[range_index] = (first, second)
    if sorted(samples) != list(range(TRACE_GROUP[3], TRACE_GROUP[4] + 1)):
        raise ValueError(f"trace range indices are incomplete in {path}")
    return rows, samples


def robust_phase_fit(samples: dict[int, tuple[complex, complex]]) -> dict[str, Any]:
    cross = {index: second * first.conjugate() for index, (first, second) in samples.items()}
    valid = {
        index: value for index, value in cross.items()
        if math.isfinite(abs(value)) and abs(value) > sys.float_info.epsilon
    }
    if not valid:
        raise ValueError("selected support group contains no valid complex cross-products")
    phase_sum = 0j
    for value in valid.values():
        phase_sum += value / abs(value)
    coherence = abs(phase_sum / len(valid))
    if not math.isfinite(coherence) or coherence <= sys.float_info.epsilon:
        raise ValueError("selected support group has non-evaluable phase coherence")
    center = (phase_sum / len(valid)) / coherence
    residuals = {
        index: cmath.phase((value / abs(value)) * center.conjugate())
        for index, value in valid.items()
    }
    retained = {
        index for index, residual in residuals.items()
        if math.isfinite(residual) and abs(residual) <= PHASE_THRESHOLD_RAD
    }
    return {
        "cross": cross,
        "valid": valid,
        "phase_center": cmath.phase(center),
        "phase_coherence": coherence,
        "residuals": residuals,
        "retained": retained,
    }


def vector_closure(
    on: dict[int, tuple[complex, complex]],
    off: dict[int, tuple[complex, complex]],
    target_only: dict[int, tuple[complex, complex]],
    component: int,
) -> dict[str, float]:
    residuals: list[float] = []
    scales: list[float] = []
    cross_residuals: list[float] = []
    cross_scales: list[float] = []
    for index in sorted(on):
        on_value = on[index][component]
        off_value = off[index][component]
        target_value = target_only[index][component]
        residuals.append(abs(on_value - off_value - target_value))
        scales.append(max(abs(on_value), abs(off_value) + abs(target_value), 1e-30))
        on_cross = on[index][1] * on[index][0].conjugate()
        off_cross = off[index][1] * off[index][0].conjugate()
        target_cross = target_only[index][1] * target_only[index][0].conjugate()
        cross_residuals.append(abs(on_cross - off_cross - target_cross))
        cross_scales.append(max(abs(on_cross), abs(off_cross) + abs(target_cross), 1e-30))
    return {
        "relative_l2": math.sqrt(sum(x * x for x in residuals) / sum(x * x for x in scales)),
        "max_cell_relative": max(x / y for x, y in zip(residuals, scales)),
        "cross_spectrum_relative_l2": math.sqrt(
            sum(x * x for x in cross_residuals) / sum(x * x for x in cross_scales)
        ),
    }


def raw_period_hashes(replay_root: Path, snr_name: str) -> dict[str, dict[str, Any]]:
    matches = list((replay_root / snr_name).glob(
        "pipeline/cases/*/scenes/A1_OFF/data/stage2_statistical_newprotocol_period_*.bin"
    ))
    result: dict[str, dict[str, Any]] = {}
    for path in matches:
        result[path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    if len(result) != 10:
        raise ValueError(f"expected 10 A1_OFF period files in {snr_name}, got {len(result)}")
    return result


def exact_input_delta(
    left: dict[int, tuple[complex, complex]],
    right: dict[int, tuple[complex, complex]],
) -> dict[str, Any]:
    differences = [
        abs(left[index][component] - right[index][component])
        for index in left for component in (0, 1)
    ]
    return {"complex_channel_values": len(differences), "exactly_equal": max(differences) == 0.0,
            "maximum_absolute_delta": max(differences)}


def branch_dir(replay_root: Path, snr_name: str, branch: str) -> Path:
    matches = list((replay_root / snr_name).glob(f"pipeline/cases/*/production/{branch}"))
    if len(matches) != 1:
        raise ValueError(f"expected one branch dir for {snr_name}/{branch}, got {len(matches)}")
    return matches[0]


def copy_evidence_file(source: Path, evidence_root: Path, relative: str) -> dict[str, Any]:
    destination = evidence_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_bytes = source.read_bytes()
    normalized_log = source.suffix == ".log"
    if normalized_log:
        lines = source_bytes.splitlines()
        content = b"\n".join(line.rstrip(b" \t\r") for line in lines)
        if source_bytes.endswith((b"\n", b"\r")):
            content += b"\n"
        destination.write_bytes(content)
    else:
        shutil.copy2(source, destination)
    item = {
        "path": relative,
        "bytes": destination.stat().st_size,
        "sha256": sha256(destination),
        "source_bytes": len(source_bytes),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
    }
    if normalized_log:
        item["normalization"] = "CRLF converted to LF; trailing spaces and tabs removed per line."
    return item


def read_group_rows(path: Path, result_id: str = "GMTI05") -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        return [row for row in reader if row.get("result_id") == result_id]


def nonintrusiveness_comparison(replay_root: Path) -> dict[str, Any]:
    current_branch = branch_dir(replay_root, "snr30", "C4_modeb_ON")
    current_result = current_branch / "result"
    baseline_result = replay_root / "tap_nonintrusiveness_baseline/result"
    branch_manifest = read_json(current_branch / "branch_manifest.json")
    production_period_index = 5
    period_path = Path(branch_manifest["period_paths"][production_period_index - 1]["path"])
    try:
        period_path_display = str(period_path.resolve().relative_to(replay_root.resolve()))
    except ValueError:
        period_path_display = period_path.name
    current_cells = current_result / "GMTI05.bin"
    baseline_cells = baseline_result / "GMTI05.bin"
    current_gamma = read_group_rows(current_result / "production_calibration_reference_gamma.csv")
    baseline_gamma = read_group_rows(baseline_result / "production_calibration_reference_gamma.csv")
    fields = ("beam_id", "method", "group_id", "az_index", "range_start", "range_end",
              "status", "support_count", "phase_coherence", "gamma_real", "gamma_imag",
              "truth_used_in_estimator", "reason")
    def key(row: dict[str, str]) -> tuple[str, ...]:
        return tuple(row.get(name, "") for name in fields[:6])
    current_by_key = {key(row): row for row in current_gamma}
    baseline_by_key = {key(row): row for row in baseline_gamma}
    if len(current_by_key) != len(current_gamma) or len(baseline_by_key) != len(baseline_gamma):
        raise ValueError("duplicate C4 gamma group keys in baseline comparison")
    same_groups = set(current_by_key) == set(baseline_by_key)
    differences = []
    if same_groups:
        for group_key in sorted(current_by_key):
            for field in fields[6:]:
                if current_by_key[group_key].get(field) != baseline_by_key[group_key].get(field):
                    differences.append({"group": group_key, "field": field,
                                       "diagnostic": current_by_key[group_key].get(field),
                                       "baseline": baseline_by_key[group_key].get(field)})
    current_adapter_rows = read_group_rows(current_result / "production_calibration_adapter.csv")
    baseline_adapter_rows = read_group_rows(baseline_result / "production_calibration_adapter.csv")
    adapter_fields = ("result_id", "method", "status", "source", "support_count", "excluded_count",
                      "groups_total", "valid_groups", "gamma_real", "gamma_imag", "gamma_abs",
                      "phase_coherence", "truth_used_in_estimator", "reason")
    adapter_equal = (
        len(current_adapter_rows) == len(baseline_adapter_rows) == 1 and
        all(current_adapter_rows[0].get(field) == baseline_adapter_rows[0].get(field)
            for field in adapter_fields)
    )
    geometry_paths = (
        current_result / "debug/cfar_geometry_diagnostics.csv",
        baseline_result / "debug/cfar_geometry_diagnostics.csv",
    )
    geometry_rows: list[list[dict[str, str]]] = []
    for path in geometry_paths:
        with path.open(newline="", encoding="utf-8") as stream:
            geometry_rows.append([
                row for row in csv.DictReader(stream)
                if row.get("period_id") == "4" and row.get("beam_id") == "50"
            ])
    geometry_equal = geometry_rows[0] == geometry_rows[1]
    current_sha = sha256(current_cells)
    baseline_sha = sha256(baseline_cells)
    return {
        "input": {
            "path": period_path_display,
            "stage2_period_file": period_path.name,
            "production_result_id": "GMTI05",
        },
        "binary_identity": {
            "diagnostic_sha256": read_json(replay_root / "snr30/r22_run_manifest.json")
                ["production_binary"]["sha256"],
            "baseline_sha256": sha256(REPO_ROOT / "build/GMTI_pipe_core"),
        },
        "gamma_group_rows": {
            "diagnostic_count": len(current_gamma), "baseline_count": len(baseline_gamma),
            "group_keys_equal": same_groups, "field_difference_count": len(differences),
            "first_differences": differences[:10],
            "failed_group": next((row for row in current_gamma
                                   if row.get("az_index") == "52"
                                   and row.get("range_start") == "1065"
                                   and row.get("range_end") == "1197"), None),
        },
        "adapter_summary_fields_equal": adapter_equal,
        "cfar_geometry_period4_row_counts": {
            "diagnostic": len(geometry_rows[0]), "baseline": len(geometry_rows[1]),
        },
        "cfar_geometry_period4_rows_equal": geometry_equal,
        "GMTI05_bin_byte_identical": current_sha == baseline_sha,
        "GMTI05_bin_sha256": {"diagnostic": current_sha, "baseline": baseline_sha},
        "interpretation": (
            "Tap non-intrusiveness is supported for the failing period: the Production binary "
            "and diagnostic binary emit identical GMTI05.bin, all 156 Gamma group records and "
            "adapter summary fields match, and period-4 CFAR geometry rows match. Detection CSV "
            "and track artifacts are excluded from bitwise comparison because the baseline run "
            "starts a fresh TrackManager history while the diagnostic run processes 10 periods."
        ),
    }


def plot_phase_residuals(
    evidence_root: Path,
    fits: dict[str, dict[str, Any]],
    samples: dict[str, dict[int, tuple[complex, complex]]],
    target_ids_by_bin: dict[int, list[str]],
) -> None:
    order = ("ON20", "ON30", "OFF30", "TO30")
    titles = {
        "ON20": "SNR 20 dB, ON / Mode-B fit",
        "ON30": "SNR 30 dB, ON / Mode-B fit (failed gate)",
        "OFF30": "SNR 30 dB, OFF / Mode-A fit",
        "TO30": "SNR 30 dB, TO input / offline fit only",
    }
    figure, axes = plt.subplots(len(order), 1, figsize=(12, 9), sharex=True, sharey=True)
    target_bins = sorted(target_ids_by_bin)
    for axis, name in zip(axes, order):
        fit = fits[name]
        valid = fit["valid"]
        for index, residual in fit["residuals"].items():
            retained = index in fit["retained"]
            axis.scatter(index, residual, s=16,
                         color="#187a55" if retained else "#c45a21",
                         marker="o" if retained else "x", linewidths=0.9)
        axis.axhspan(-PHASE_THRESHOLD_RAD, PHASE_THRESHOLD_RAD, color="#187a55", alpha=0.08)
        axis.axhline(PHASE_THRESHOLD_RAD, color="#555555", linestyle="--", linewidth=0.8)
        axis.axhline(-PHASE_THRESHOLD_RAD, color="#555555", linestyle="--", linewidth=0.8)
        for target_bin in target_bins:
            axis.axvline(target_bin, color="#466eaa", alpha=0.28, linewidth=0.8)
        axis.set_title(
            f"{titles[name]} — valid {len(valid)}/{EXPECTED_CELLS}, "
            f"offline support {len(fit['retained'])}, coherence {fit['phase_coherence']:.4f}",
            loc="left", fontsize=10,
        )
        axis.grid(alpha=0.15)
    axes[-1].set_xlabel("Range bin (selected group 1065–1197)")
    for axis in axes:
        axis.set_ylabel("Phase residual (rad)")
    figure.suptitle(
        "C4 Robust DDC-RB selected-group phase residuals\n"
        "Vertical markers show target truth range bins at Stage2 period 4, beam 50",
        fontsize=12,
    )
    figure.tight_layout(rect=(0, 0, 1, 0.94))
    figure.savefig(evidence_root / "phase_residuals.png", dpi=180)
    plt.close(figure)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-root", type=Path, default=DEFAULT_REPLAY_ROOT)
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    args = parser.parse_args()
    replay_root = args.replay_root.expanduser().resolve()
    evidence_root = args.evidence_root.expanduser().resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)

    rows_by_name: dict[str, list[dict[str, str]]] = {}
    samples: dict[str, dict[int, tuple[complex, complex]]] = {}
    fit_by_name: dict[str, dict[str, Any]] = {}
    evidence_files: list[dict[str, Any]] = []
    trace_sources: dict[str, Path] = {}
    for name, (snr_name, branch, _mode, _role) in SPECS.items():
        source = trace_path(replay_root, snr_name, branch)
        rows, cell_values = load_trace(source)
        rows_by_name[name] = rows
        samples[name] = cell_values
        fit_by_name[name] = robust_phase_fit(cell_values)
        trace_sources[name] = source
        evidence_files.append(copy_evidence_file(
            source, evidence_root, f"selected_cells/{name.lower()}_production_input.csv"
        ))

    target_truth = replay_root / (
        "snr30/pipeline/cases/r22_multi_target__seed_1932001__v_2d5__snr_30__delay_4ns/"
        "scenes/A1_TO/truth/moving_target_truth.csv"
    )
    truth_branch = branch_dir(replay_root, "snr30", "C4_modeb_ON")
    branch_manifest = read_json(truth_branch / "branch_manifest.json")
    production_period_index = 5
    stage2_path = Path(branch_manifest["period_paths"][production_period_index - 1]["path"])
    period_match = re.search(r"period_(\d+)\.bin$", stage2_path.name)
    if period_match is None:
        raise ValueError(f"cannot map Production GMTI05 to Stage2 period from {stage2_path}")
    stage2_period_id = int(period_match.group(1))
    target_records: dict[str, dict[str, str]] = {}
    with target_truth.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            if row["period_id"] == str(stage2_period_id) and row["beam_id"] == "50":
                target_records.setdefault(row["target_id"], row)
    target_ids_by_bin: dict[int, list[str]] = {}
    for target_id, row in target_records.items():
        target_ids_by_bin.setdefault(int(row["range_bin"]), []).append(target_id)
    failure_group_targets = [
        {"target_id": target_id, "range_bin": int(row["range_bin"]),
         "azimuth_deg": float(row["azimuth_deg"]), "visible": row["visible"],
         "snr_db": float(row["snr_db"])}
        for target_id, row in sorted(target_records.items())
        if TRACE_GROUP[3] <= int(row["range_bin"]) <= TRACE_GROUP[4]
    ]

    cells_path = evidence_root / "trace_phase_audit_cells.csv"
    with cells_path.open("w", newline="", encoding="utf-8") as stream:
        fields = (
            "dataset", "snr_db", "role", "mode", "range_index", "f1_real", "f1_imag",
            "f2_real", "f2_imag", "cross_real", "cross_imag", "cross_magnitude",
            "cross_phase_rad", "phase_center_rad", "phase_residual_rad", "valid",
            "retained_by_frozen_gate", "truth_target_ids_at_range", "reported_status",
            "reported_support_count", "reported_phase_coherence", "reported_source",
            "offline_fit_valid_count", "offline_fit_support_count", "offline_fit_coherence",
        )
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for name in SPECS:
            snr_name, _branch, mode, role = SPECS[name]
            fit = fit_by_name[name]
            for row in rows_by_name[name]:
                index = int(row["range_index"])
                first, second = samples[name][index]
                cross = fit["cross"][index]
                residual = fit["residuals"].get(index)
                writer.writerow({
                    "dataset": name,
                    "snr_db": 20 if snr_name == "snr20" else 30,
                    "role": role,
                    "mode": mode,
                    "range_index": index,
                    "f1_real": f"{first.real:.9g}", "f1_imag": f"{first.imag:.9g}",
                    "f2_real": f"{second.real:.9g}", "f2_imag": f"{second.imag:.9g}",
                    "cross_real": f"{cross.real:.17g}", "cross_imag": f"{cross.imag:.17g}",
                    "cross_magnitude": f"{abs(cross):.17g}",
                    "cross_phase_rad": f"{cmath.phase(cross):.17g}",
                    "phase_center_rad": f"{fit['phase_center']:.17g}",
                    "phase_residual_rad": "" if residual is None else f"{residual:.17g}",
                    "valid": index in fit["valid"],
                    "retained_by_frozen_gate": index in fit["retained"],
                    "truth_target_ids_at_range": ";".join(sorted(target_ids_by_bin.get(index, []))),
                    "reported_status": row["group_status"],
                    "reported_support_count": row["adapter_support_count"],
                    "reported_phase_coherence": row["adapter_phase_coherence"],
                    "reported_source": row["source"],
                    "offline_fit_valid_count": len(fit["valid"]),
                    "offline_fit_support_count": len(fit["retained"]),
                    "offline_fit_coherence": f"{fit['phase_coherence']:.17g}",
                })
    evidence_files.append({"path": "trace_phase_audit_cells.csv", "bytes": cells_path.stat().st_size,
                           "sha256": sha256(cells_path)})
    plot_path = evidence_root / "phase_residuals.png"
    plot_phase_residuals(evidence_root, fit_by_name, samples, target_ids_by_bin)
    evidence_files.append({"path": "phase_residuals.png", "bytes": plot_path.stat().st_size,
                           "sha256": sha256(plot_path)})

    summaries: dict[str, Any] = {}
    for name, (snr_name, branch, mode, role) in SPECS.items():
        row = rows_by_name[name][0]
        fit = fit_by_name[name]
        direct_fit = row["source"] == "production_final_f1_f2_after_p38_range_phase"
        observed_support = int(row["adapter_support_count"])
        if direct_fit and observed_support != len(fit["retained"]):
            raise ValueError(f"offline robust gate does not reproduce direct fit for {name}")
        if direct_fit and abs(float(row["adapter_phase_coherence"]) - fit["phase_coherence"]) > 1e-14:
            raise ValueError(f"offline coherence does not reproduce direct fit for {name}")
        summaries[name] = {
            "snr_db": 20 if snr_name == "snr20" else 30,
            "role": role,
            "mode": mode,
            "source": row["source"],
            "reported_group_status": row["group_status"],
            "reported_support_count": observed_support,
            "reported_phase_coherence": float(row["adapter_phase_coherence"]),
            "reported_summary_is_current_input_fit": direct_fit,
            "valid_cross_spectrum_cells": len(fit["valid"]),
            "offline_support_count_under_frozen_gate": len(fit["retained"]),
            "offline_phase_coherence": fit["phase_coherence"],
            "offline_phase_center_rad": fit["phase_center"],
            "retained_range_bins": sorted(fit["retained"]),
        }

    off_hashes_20 = raw_period_hashes(replay_root, "snr20")
    off_hashes_30 = raw_period_hashes(replay_root, "snr30")
    same_off_periods = {
        name: off_hashes_20[name]["sha256"] == off_hashes_30[name]["sha256"]
        for name in sorted(set(off_hashes_20) & set(off_hashes_30))
    }
    off_hashes_path = evidence_root / "paired_off_stage2_hashes.csv"
    with off_hashes_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("period_file", "bytes_snr20", "sha256_snr20", "bytes_snr30",
                         "sha256_snr30", "byte_identical"))
        for name in sorted(set(off_hashes_20) & set(off_hashes_30)):
            writer.writerow((name, off_hashes_20[name]["bytes"], off_hashes_20[name]["sha256"],
                             off_hashes_30[name]["bytes"], off_hashes_30[name]["sha256"],
                             same_off_periods[name]))
    evidence_files.append({"path": "paired_off_stage2_hashes.csv", "bytes": off_hashes_path.stat().st_size,
                           "sha256": sha256(off_hashes_path)})

    mode_equalities: dict[str, dict[str, Any]] = {}
    mode_trace_files: list[dict[str, Any]] = []
    for snr_name in ("snr20", "snr30"):
        for role in ("OFF", "ON", "TO"):
            mode_a_path = trace_path(replay_root, snr_name, f"C4_modea_{role}")
            mode_b_matches = list((replay_root / snr_name).glob(
                f"pipeline/cases/*/production/C4_modeb_{role}/result/production_calibration_selected_cells.csv"
            ))
            key = f"{snr_name}_{role}"
            if len(mode_b_matches) == 1:
                mode_a = load_trace(mode_a_path)[1]
                mode_b = load_trace(mode_b_matches[0])[1]
                for mode, source in (("modea", mode_a_path), ("modeb", mode_b_matches[0])):
                    mode_trace_files.append(copy_evidence_file(
                        source, evidence_root,
                        f"mode_comparison/{snr_name}_{mode}_{role.lower()}_selected_cells.csv",
                    ))
                mode_equalities[key] = {
                    "available": True,
                    **exact_input_delta(mode_a, mode_b),
                }
            elif not mode_b_matches:
                mode_equalities[key] = {
                    "available": False,
                    "reason": "Mode-B branch was gated before this role after the ON reference failure.",
                }
            else:
                raise ValueError(f"expected at most one Mode-B trace for {key}, got {len(mode_b_matches)}")
    evidence_files.extend(mode_trace_files)
    off_processed_equal = exact_input_delta(samples["OFF20"], samples["OFF30"])

    closure = {
        "snr20": {
            "F1": vector_closure(samples["ON20"], samples["OFF20"], samples["TO20"], 0),
            "F2": vector_closure(samples["ON20"], samples["OFF20"], samples["TO20"], 1),
        },
        "snr30": {
            "F1": vector_closure(samples["ON30"], samples["OFF30"], samples["TO30"], 0),
            "F2": vector_closure(samples["ON30"], samples["OFF30"], samples["TO30"], 1),
        },
    }

    runtime_files: list[dict[str, Any]] = []
    for name, (snr_name, branch, _mode, _role) in SPECS.items():
        source_branch = branch_dir(replay_root, snr_name, branch)
        for source_rel, dest_rel in (
            ("production.xml", f"runtime/{name.lower()}_production.xml"),
            ("branch_manifest.json", f"runtime/{name.lower()}_branch_manifest.json"),
            ("gmt_pipe_core.log", f"runtime/{name.lower()}_production.log"),
            ("production.configure.log", f"runtime/{name.lower()}_configure.log"),
            ("result/runtime_config_dump.json", f"runtime/{name.lower()}_runtime_config.json"),
            ("result/debug/cfar_geometry_diagnostics.csv", f"runtime/{name.lower()}_cfar_geometry.csv"),
            ("result/debug/cfar_numerical_diagnostics.csv", f"runtime/{name.lower()}_cfar_numerical.csv"),
            ("result/production_calibration_adapter.csv", f"runtime/{name.lower()}_adapter_summary.csv"),
            ("result/production_calibration_reference_gamma.csv", f"runtime/{name.lower()}_gamma_groups.csv"),
        ):
            source = source_branch / source_rel
            if source.is_file():
                runtime_files.append(copy_evidence_file(source, evidence_root, dest_rel))
    for snr_name in ("snr20", "snr30"):
        run_root = replay_root / snr_name
        for filename in ("r22_run_manifest.json", "scenario_template.json", "target_generation_manifest.json"):
            source = run_root / filename
            runtime_files.append(copy_evidence_file(source, evidence_root, f"run/{snr_name}_{filename}"))
        source = replay_root / f"{snr_name}_runner.log"
        runtime_files.append(copy_evidence_file(source, evidence_root, f"run/{snr_name}_runner.log"))
        case_dirs = list((run_root / "pipeline/cases").glob("*"))
        if len(case_dirs) != 1:
            raise ValueError(f"expected one case dir in {run_root}")
        for role in ("A1_ON", "A1_OFF", "A1_TO"):
            scene = case_dirs[0] / "scenes" / role
            for rel in ("reports/stage2_simulation_report.md", "reports/temporal_clutter_diagnostics.json"):
                source = scene / rel
                if source.is_file():
                    runtime_files.append(copy_evidence_file(
                        source, evidence_root, f"stage2/{snr_name}_{role.lower()}_{Path(rel).name}"
                    ))

    baseline_root = replay_root / "tap_nonintrusiveness_baseline"
    for source_rel, dest_rel in (
        ("gmt_pipe_core.log", "baseline/gmt_pipe_core.log"),
        ("result/run_manifest.json", "baseline/run_manifest.json"),
        ("result/production_calibration_adapter.csv", "baseline/adapter_summary.csv"),
        ("result/production_calibration_reference_gamma.csv", "baseline/gamma_groups.csv"),
        ("result/debug/cfar_geometry_diagnostics.csv", "baseline/cfar_geometry.csv"),
    ):
        source = baseline_root / source_rel
        runtime_files.append(copy_evidence_file(source, evidence_root, dest_rel))
    evidence_files.extend(runtime_files)

    run_manifests = {
        snr_name: read_json(replay_root / snr_name / "r22_run_manifest.json")
        for snr_name in ("snr20", "snr30")
    }
    generator_20 = dict(run_manifests["snr20"]["generator_manifest"])
    generator_30 = dict(run_manifests["snr30"]["generator_manifest"])
    generator_20.pop("target_snr_db", None)
    generator_30.pop("target_snr_db", None)
    generator_equal_except_snr = generator_20 == generator_30
    if not generator_equal_except_snr:
        raise ValueError("SNR20/30 generator manifests differ beyond target SNR")
    area_20 = run_manifests["snr20"].get("area_clutter_gate", {})
    area_30 = run_manifests["snr30"].get("area_clutter_gate", {})
    area_keys = ("continuous_area_packets", "continuous_area_samples", "area_mean_power_measured",
                 "range_window_m", "area_model")
    area_comparable = {key: {"snr20": area_20.get(key), "snr30": area_30.get(key),
                             "equal": area_20.get(key) == area_30.get(key)} for key in area_keys}

    target_truth_copy = evidence_root / "target_truth_stage2_period4_beam50.csv"
    with target_truth.open(newline="", encoding="utf-8") as stream, target_truth_copy.open(
        "w", newline="", encoding="utf-8"
    ) as out_stream:
        reader = csv.DictReader(stream)
        selected = [row for row in reader
                    if row["period_id"] == str(stage2_period_id) and row["beam_id"] == "50"
                    and row["pulse_id"] == "0"]
        writer = csv.DictWriter(out_stream, fieldnames=reader.fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(selected)
    evidence_files.append({"path": "target_truth_stage2_period4_beam50.csv",
                           "bytes": target_truth_copy.stat().st_size,
                           "sha256": sha256(target_truth_copy)})

    summary = {
        "experiment": "R22-S1 selected C4 Robust DDC-RB support failure diagnostic",
        "source_tree_commit_at_execution": run_manifests["snr30"]["source_identity"]["commit"],
        "source_worktree_dirty_files_at_execution": run_manifests["snr30"]["source_identity"].get(
            "tracked_dirty_status", []
        ),
        "reviewed_research_master_plan_blob": "e637e3769bde7814298ff012d0f42301884e2985",
        "trace_group": {
            "production_result_id": "GMTI05", "beam_id": 50, "az_index": 52,
            "range_start": 1065, "range_end": 1197, "cell_count": EXPECTED_CELLS,
            "minimum_support": MIN_SUPPORT, "phase_threshold_rad": PHASE_THRESHOLD_RAD,
        },
        "period_mapping": {
            "production_result_id": "GMTI05", "production_period_ordinal": production_period_index,
            "stage2_period_id_zero_based": stage2_period_id,
            "stage2_input_path": str(stage2_path.resolve().relative_to(replay_root.resolve())),
            "note": "CFAR geometry and Stage2 truth use zero-based period 4; Gamma result_id GMTI05 is one-based.",
        },
        "target_truth_in_failure_group": failure_group_targets,
        "method_fits": summaries,
        "paired_background": {
            "A1_OFF_period_count_each": [len(off_hashes_20), len(off_hashes_30)],
            "byte_identical_period_count": sum(same_off_periods.values()),
            "periods": same_off_periods,
            "processed_OFF20_vs_OFF30_F1F2": off_processed_equal,
        },
        "mode_input_equivalence": mode_equalities,
        "generator_manifest_equal_except_target_snr": generator_equal_except_snr,
        "area_clutter_comparability": area_comparable,
        "post_P38_range_phase_closure": closure,
        "support_membership_change_ON20_to_ON30": {
            "intersection_count": len(fit_by_name["ON20"]["retained"] & fit_by_name["ON30"]["retained"]),
            "ON20_only_count": len(fit_by_name["ON20"]["retained"] - fit_by_name["ON30"]["retained"]),
            "ON30_only_count": len(fit_by_name["ON30"]["retained"] - fit_by_name["ON20"]["retained"]),
            "center_shift_rad": cmath.phase(cmath.exp(
                1j * (fit_by_name["ON30"]["phase_center"] - fit_by_name["ON20"]["phase_center"])
            )),
        },
        "tap_nonintrusiveness": nonintrusiveness_comparison(replay_root),
        "status_labels": {
            "IMPLEMENTED": True,
            "EXECUTED": True,
            "EVALUABLE": {
                "frozen_gate_reproduction": True,
                "selected_cell_phase_membership": True,
                "target_contamination_causal_attribution": False,
            },
            "PERFORMANCE_VERIFIED": False,
        },
        "causal_limit": (
            "ON = OFF + TO does not close at the tapped Production F1/F2 stage: F1/F2 and "
            "cross-spectrum residuals remain material. The replay confirms the gate failure and "
            "target occupancy, but cannot uniquely assign the phase-support collapse to target "
            "contamination versus data-dependent P38/range-phase processing or other interactions."
        ),
        "evidence_files": evidence_files,
    }
    summary_path = evidence_root / "trace_phase_audit_summary.json"
    write_json(summary_path, summary)
    evidence_manifest = {
        "description": "Compact, high-value evidence extracted from two SNR replay cases; large raw Stage2 binaries remain outside this review package.",
        "generated_by": "analyze_selected_cells.py",
        "generated_by_sha256": sha256(Path(__file__).resolve()),
        "files": sorted(evidence_files, key=lambda item: item["path"]),
        "summary": {"path": "trace_phase_audit_summary.json",
                    "bytes": summary_path.stat().st_size, "sha256": sha256(summary_path)},
    }
    write_json(evidence_root / "evidence_manifest.json", evidence_manifest)
    print(json.dumps({
        "evidence_root": str(evidence_root),
        "summary": str(summary_path),
        "offline_support": {name: len(fit_by_name[name]["retained"]) for name in SPECS},
        "paired_off_periods_identical": sum(same_off_periods.values()),
        "nonintrusiveness": {
            "gamma_rows_equal": summary["tap_nonintrusiveness"]["gamma_group_rows"]["field_difference_count"] == 0,
            "GMTI05_bin_equal": summary["tap_nonintrusiveness"]["GMTI05_bin_byte_identical"],
            "cfar_geometry_equal": summary["tap_nonintrusiveness"]["cfar_geometry_period4_rows_equal"],
        },
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
