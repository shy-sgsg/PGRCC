#!/usr/bin/env python3
"""Audit the corrected R18 CUDA pilot's clutter/target overlap and task outputs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_r import evaluate_r3_minimal_cuda_causal_pilot as r3
from scripts.phase_r import evaluate_r7_production_matrix as r7
from scripts.phase_r import evaluate_r7_task_metrics as task_metrics


AREA_WINDOW_M = (84_500.0, 85_500.0)
NOISE_WINDOWS_M = ((83_500.0, 84_300.0), (85_700.0, 86_500.0))
ROI_HALF_RANGE_BINS = 2
ROI_HALF_DOPPLER_BINS = 2
SUPPORT_ENERGY_FRACTION = 0.95
ROLES = ("OFF", "ON", "TO")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    if not fields:
        fields = ["status"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": sha256(path)}


def manifest_rows(result_dir: Path) -> dict[int, tuple[Path, dict[str, str]]]:
    found: dict[int, tuple[Path, dict[str, str]]] = {}
    for path in sorted(result_dir.rglob("csi_roi_manifest.csv")):
        for row in read_csv(path):
            period = int(row["period_id"])
            if period in found:
                raise ValueError(f"multiple CSI manifests for {result_dir}, period {period}")
            found[period] = (path, row)
    return found


def array_from(manifest: Path, row: Mapping[str, str], key: str) -> np.ndarray:
    raw = Path(row[key])
    path = raw if raw.is_absolute() else (manifest.parent / raw).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"missing {key}: {path}")
    return np.asarray(np.load(path))


def center_on_axis(axis: np.ndarray, truth_hz: float) -> tuple[int, float, float]:
    if axis.ndim != 1 or axis.size < 2:
        raise ValueError("invalid production Doppler axis")
    step = float(np.median(np.diff(axis)))
    prf = step * int(axis.size)
    center_hz = 0.5 * (float(axis[0]) + float(axis[-1]))
    wrapped_hz = truth_hz + round((center_hz - truth_hz) / prf) * prf
    return int(np.argmin(np.abs(axis - wrapped_hz))), wrapped_hz, prf


def pulse_truth_by_period(path: Path) -> dict[int, dict[str, float]]:
    grouped: dict[int, dict[str, list[float]]] = {}
    for row in read_csv(path):
        period = int(row["period_id"])
        values = grouped.setdefault(period, {"range_m": [], "af_total_truth_hz": []})
        values["range_m"].append(float(row["range_m"]))
        values["af_total_truth_hz"].append(float(row["af_total_truth_hz"]))
    return {
        period: {key: float(np.median(values)) for key, values in fields.items()}
        for period, fields in grouped.items()
    }


def build_overlap_audit(
    case_root: Path, truths: Mapping[int, Mapping[str, float]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[int, dict[str, Any]]]:
    c0_off = case_root / "production/C0_not_applicable_OFF/result/csi_metrics"
    periods = manifest_rows(c0_off)
    if set(periods) != set(truths):
        raise ValueError(f"C0 OFF periods {sorted(periods)} != target truth {sorted(truths)}")

    overlap_rows: list[dict[str, Any]] = []
    spectrum_rows: list[dict[str, Any]] = []
    period_data: dict[int, dict[str, Any]] = {}
    for period, (manifest, tap) in sorted(periods.items()):
        power = np.asarray(array_from(manifest, tap, "before_power_path"), dtype=np.float64)
        fa_axis = np.asarray(array_from(manifest, tap, "fa_axis_path"), dtype=np.float64).reshape(-1)
        range_axis = np.asarray(array_from(manifest, tap, "range_axis_path"), dtype=np.float64).reshape(-1)
        if power.shape != (fa_axis.size, range_axis.size):
            raise ValueError(f"CSI shape/axis mismatch for period {period}: {power.shape}")

        area_mask = (range_axis >= AREA_WINDOW_M[0]) & (range_axis <= AREA_WINDOW_M[1])
        noise_mask = np.zeros(range_axis.size, dtype=bool)
        for low, high in NOISE_WINDOWS_M:
            noise_mask |= (range_axis >= low) & (range_axis <= high)
        if not np.any(area_mask) or not np.any(noise_mask):
            raise ValueError(f"area/noise range window not covered for period {period}")

        area_spectrum = np.mean(power[:, area_mask], axis=1, dtype=np.float64)
        noise_spectrum = np.mean(power[:, noise_mask], axis=1, dtype=np.float64)
        positive_excess = np.maximum(area_spectrum - noise_spectrum, 0.0)
        total_excess = float(np.sum(positive_excess, dtype=np.float64))
        if not math.isfinite(total_excess) or total_excess <= 0.0:
            raise ValueError(f"non-positive net area clutter for period {period}")

        # Stable sort makes equal-power ties reproducible by ascending axis row.
        order = np.argsort(-positive_excess, kind="stable")
        cumulative = np.cumsum(positive_excess[order], dtype=np.float64)
        support_count = int(np.searchsorted(
            cumulative, SUPPORT_ENERGY_FRACTION * total_excess, side="left",
        )) + 1
        support = np.zeros(fa_axis.size, dtype=bool)
        support[order[:support_count]] = True
        cumulative_fraction = np.zeros_like(positive_excess)
        cumulative_fraction[order] = cumulative / total_excess

        truth = truths[period]
        doppler_center, wrapped_truth_hz, prf = center_on_axis(
            fa_axis, truth["af_total_truth_hz"],
        )
        range_center = int(np.argmin(np.abs(range_axis - truth["range_m"])))
        roi_doppler = np.arange(
            max(0, doppler_center - ROI_HALF_DOPPLER_BINS),
            min(fa_axis.size, doppler_center + ROI_HALF_DOPPLER_BINS + 1),
        )
        roi_range = np.arange(
            max(0, range_center - ROI_HALF_RANGE_BINS),
            min(range_axis.size, range_center + ROI_HALF_RANGE_BINS + 1),
        )
        support_overlap = roi_doppler[support[roi_doppler]]
        range_overlap = bool(np.any(area_mask[roi_range]))
        doppler_overlap = bool(support_overlap.size)
        support_indices = np.flatnonzero(support)
        folded_axis = (fa_axis + 0.5 * prf) % prf - 0.5 * prf
        area_mean = float(np.mean(area_spectrum, dtype=np.float64))
        noise_mean = float(np.mean(noise_spectrum, dtype=np.float64))

        row = {
            "period_id": period,
            "status": "evaluable",
            "truth_source": "A0_ON/truth/truth_pulse.csv; per-period median",
            "target_range_m_median": truth["range_m"],
            "target_af_total_truth_hz_median": truth["af_total_truth_hz"],
            "wrapped_target_frequency_hz": wrapped_truth_hz,
            "prf_hz_from_axis": prf,
            "doppler_bin_hz": float(np.median(np.diff(fa_axis))),
            "target_center_doppler_row": doppler_center,
            "target_center_range_col": range_center,
            "target_roi_doppler_rows": ";".join(map(str, roi_doppler.tolist())),
            "target_roi_doppler_hz": ";".join(f"{x:.9g}" for x in fa_axis[roi_doppler]),
            "target_roi_range_m_min": float(np.min(range_axis[roi_range])),
            "target_roi_range_m_max": float(np.max(range_axis[roi_range])),
            "range_overlap_with_area_window": range_overlap,
            "doppler_support_overlap": doppler_overlap,
            "target_roi_support_doppler_rows": ";".join(map(str, support_overlap.tolist())),
            "target_roi_support_overlap_bins": int(support_overlap.size),
            "joint_opportunity": bool(range_overlap and doppler_overlap),
            "area_window_min_m": AREA_WINDOW_M[0],
            "area_window_max_m": AREA_WINDOW_M[1],
            "area_window_bin_count": int(np.count_nonzero(area_mask)),
            "noise_sideband_bin_count": int(np.count_nonzero(noise_mask)),
            "area_mean_power_csi_before": area_mean,
            "noise_mean_power_csi_before": noise_mean,
            "area_to_noise_power_ratio": area_mean / noise_mean if noise_mean > 0 else None,
            "positive_excess_energy_sum": total_excess,
            "support_energy_fraction": SUPPORT_ENERGY_FRACTION,
            "support_bin_count": support_count,
            "support_fraction_of_doppler_bins": support_count / fa_axis.size,
            "support_rows": ";".join(map(str, support_indices.tolist())),
            "support_wrapped_frequency_min_hz": float(np.min(folded_axis[support])),
            "support_wrapped_frequency_max_hz": float(np.max(folded_axis[support])),
            "range_overlap_rule": "target ROI range-bin set has non-empty intersection with area window",
        }
        overlap_rows.append(row)
        period_data[period] = {
            "manifest": manifest,
            "tap": tap,
            "fa_axis": fa_axis,
            "range_axis": range_axis,
            "area_mask": area_mask,
            "noise_mask": noise_mask,
            "support": support,
            "target_roi_doppler": np.isin(np.arange(fa_axis.size), roi_doppler),
            "area_spectrum": area_spectrum,
            "noise_spectrum": noise_spectrum,
            "positive_excess": positive_excess,
            "cumulative_fraction": cumulative_fraction,
            "folded_axis": folded_axis,
        }
        for index in range(fa_axis.size):
            spectrum_rows.append({
                "period_id": period,
                "doppler_row": index,
                "fa_axis_hz": float(fa_axis[index]),
                "folded_doppler_hz": float(folded_axis[index]),
                "area_mean_power": float(area_spectrum[index]),
                "noise_sideband_mean_power": float(noise_spectrum[index]),
                "positive_excess_power": float(positive_excess[index]),
                "cumulative_positive_excess_fraction": float(cumulative_fraction[index]),
                "in_95pct_empirical_support": bool(support[index]),
                "in_target_5x5_doppler_roi": bool(index in roi_doppler),
            })
    return overlap_rows, spectrum_rows, period_data


def branch_inventory(case_root: Path) -> dict[tuple[str, str], tuple[Path, dict[str, Any]]]:
    inventory: dict[tuple[str, str], tuple[Path, dict[str, Any]]] = {}
    for branch_dir in sorted((case_root / "production").iterdir()):
        manifest_path = branch_dir / "branch_manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        condition = str(manifest.get("condition", ""))
        method = condition.split("_", 1)[0]
        role = str(manifest.get("role", "")).upper()
        if method not in {"C0", "C1", "C2", "C3"} or role not in ROLES:
            continue
        key = (method, role)
        if key in inventory:
            raise ValueError(f"duplicate production branch identity: {key}")
        inventory[key] = (branch_dir, manifest)
    return inventory


def audit_inputs_and_branches(
    inventory: Mapping[tuple[str, str], tuple[Path, dict[str, Any]]],
    methods: Sequence[str], expected_periods: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    input_rows: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    checks: list[str] = []
    definition_signatures: set[tuple[str, str, str]] = set()
    methods_complete = all((method, role) in inventory for method in methods for role in ROLES)
    for role in ROLES:
        for period in range(expected_periods):
            hashes: dict[str, str] = {}
            for method in methods:
                if (method, role) not in inventory:
                    continue
                branch_dir, bm = inventory[(method, role)]
                period_paths = bm.get("period_paths", [])
                if len(period_paths) != expected_periods:
                    checks.append(f"{method}/{role}: period input count mismatch")
                    continue
                item = period_paths[period]
                digest = str(item.get("sha256", ""))
                hashes[method] = digest
                input_rows.append({
                    "method_id": method,
                    "role": role,
                    "period_id": period,
                    "input_path": item.get("path"),
                    "input_bytes": item.get("bytes"),
                    "input_sha256": digest,
                })
                if bm.get("status") != "passed" or bm.get("production_status") != "passed":
                    checks.append(f"{method}/{role}: Production branch did not pass")
                if int(bm.get("production_returncode", -1)) != 0:
                    checks.append(f"{method}/{role}: nonzero Production return code")
                csi_rows = manifest_rows(Path(str(bm.get("result_dir", ""))))
                if len(csi_rows) != expected_periods:
                    checks.append(f"{method}/{role}: incomplete CSI period taps")
                elif csi_rows:
                    first_manifest, first_tap = next(iter(csi_rows.values()))
                    definition_signatures.add((
                        first_tap.get("before_definition", ""),
                        first_tap.get("after_definition", ""),
                        first_tap.get("cfar_input_definition", ""),
                    ))
                if bm.get("truth_used_in_estimator") is True:
                    checks.append(f"{method}/{role}: truth-use flag is true")
                xml_path = Path(str(bm.get("production_xml", "")))
                log_path = Path(str(bm.get("production_log", "")))
                records.append({
                    "method_id": method,
                    "role": role,
                    "branch_name": bm.get("branch_name"),
                    "status": bm.get("status"),
                    "production_status": bm.get("production_status"),
                    "production_returncode": bm.get("production_returncode"),
                    "csi_manifest_count": len(csi_rows),
                    "before_definition": first_tap.get("before_definition") if csi_rows else None,
                    "after_definition": first_tap.get("after_definition") if csi_rows else None,
                    "cfar_input_definition": first_tap.get("cfar_input_definition") if csi_rows else None,
                    "production_xml": file_record(xml_path) if xml_path.is_file() else None,
                    "production_log": file_record(log_path) if log_path.is_file() else None,
                    "branch_manifest": file_record(branch_dir / "branch_manifest.json"),
                })
            if len(set(hashes.values())) != 1 or len(hashes) != len(methods):
                checks.append(f"{role}/period{period}: method input SHA mismatch or missing")
    if len(definition_signatures) != 1:
        checks.append("CSI before/after/CFAR input definitions differ across production branches")
    return input_rows, {
        "methods_complete": methods_complete,
        "all_selected_role_inputs_identical_by_sha256": not any("input SHA mismatch" in x for x in checks),
        "all_production_and_csi_gates_passed": not any(
            "did not pass" in x or "return code" in x or "CSI" in x for x in checks
        ),
        "no_branch_truth_use_flag": not any("truth-use" in x for x in checks),
        "same_csi_and_cfar_definitions_across_methods": len(definition_signatures) == 1,
        "checks": checks,
    }, records


def csi_clutter_metrics(
    inventory: Mapping[tuple[str, str], tuple[Path, dict[str, Any]]],
    period_data: Mapping[int, Mapping[str, Any]], methods: Sequence[str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for period, support_data in sorted(period_data.items()):
        support = np.asarray(support_data["support"], dtype=bool)
        area = np.asarray(support_data["area_mask"], dtype=bool)
        period_values: dict[str, dict[str, float]] = {}
        for method in methods:
            branch_dir, bm = inventory[(method, "OFF")]
            rows = manifest_rows(Path(str(bm["result_dir"])))
            manifest, tap = rows[period]
            before = np.asarray(array_from(manifest, tap, "before_power_path"), dtype=np.float64)
            real = np.asarray(array_from(manifest, tap, "csi_after_real_path"), dtype=np.float64)
            imag = np.asarray(array_from(manifest, tap, "csi_after_imag_path"), dtype=np.float64)
            fa = np.asarray(array_from(manifest, tap, "fa_axis_path"), dtype=np.float64).reshape(-1)
            rg = np.asarray(array_from(manifest, tap, "range_axis_path"), dtype=np.float64).reshape(-1)
            if before.shape != real.shape or before.shape != imag.shape or before.shape != (fa.size, rg.size):
                raise ValueError(f"incompatible H0 CSI tap dimensions: {method}, period {period}")
            if not np.allclose(fa, support_data["fa_axis"], rtol=0.0, atol=0.05):
                raise ValueError(f"Doppler axes differ across methods: {method}, period {period}")
            if not np.allclose(rg, support_data["range_axis"], rtol=0.0, atol=0.05):
                raise ValueError(f"range axes differ across methods: {method}, period {period}")
            selected = np.ix_(support, area)
            before_mean = float(np.mean(before[selected], dtype=np.float64))
            after_power = real * real + imag * imag
            after_mean = float(np.mean(after_power[selected], dtype=np.float64))
            period_values[method] = {
                "before_mean_power": before_mean,
                "csi_output_mean_power": after_mean,
                "before_to_csi_output_db": 10.0 * math.log10(before_mean / after_mean),
            }
        c0_after = period_values["C0"]["csi_output_mean_power"]
        for method in methods:
            values = period_values[method]
            result.append({
                "method_id": method,
                "period_id": period,
                "empirical_support_bins": int(np.count_nonzero(support)),
                "area_range_bins": int(np.count_nonzero(area)),
                **values,
                "output_power_relative_to_C0_db": 10.0 * math.log10(
                    values["csi_output_mean_power"] / c0_after
                ),
                "definition": "Method-specific H0 OFF CSI output on shared C0 OFF 95%-excess Doppler support × 84.5–85.5 km range",
            })
    return result


def target_transfer_metrics(
    case_root: Path, case: Mapping[str, Any], inventory: Mapping[tuple[str, str], tuple[Path, dict[str, Any]]],
    methods: Sequence[str],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for method in methods:
        branches = {
            role: {"result_dir": str(inventory[(method, role)][1]["result_dir"])}
            for role in ROLES
        }
        output[method] = r3._target_transfer(case_root, case, method, branches)
    return output


def production_task_metrics(
    inventory: Mapping[tuple[str, str], tuple[Path, dict[str, Any]]],
    methods: Sequence[str], truth_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    truths = task_metrics._read_csv(truth_path)
    expected_keys = {(int(row["period_id"]), int(row["beam_id"])) for row in truths}
    output: dict[str, Any] = {}
    association_rows: list[dict[str, Any]] = []
    for method in methods:
        output[method] = {}
        for role in ROLES:
            branch_dir, bm = inventory[(method, role)]
            result_dir = Path(str(bm["result_dir"]))
            detection_paths = [result_dir / f"detection_results_GMTI{i:02d}.csv" for i in range(1, 4)]
            detections = [row for path in detection_paths for row in task_metrics._read_csv(path)]
            cfar = task_metrics._read_csv(result_dir / "debug/cfar_geometry_diagnostics.csv")
            lineage_path = result_dir / "debug/cluster_detection_lineage.csv"
            lineage = task_metrics._read_csv(lineage_path) if lineage_path.is_file() else None
            raw_track_dir = str(bm.get("track_debug_dir", ""))
            track_dir: Path | None = Path(raw_track_dir) if raw_track_dir else None
            if track_dir is None or not track_dir.is_dir():
                candidates = sorted((branch_dir / "track_debug_runs").glob("local_test_*"))
                if len(candidates) == 1:
                    track_dir = candidates[0]
                else:
                    track_dir = None
            if track_dir is not None and track_dir.is_dir():
                track_detections = task_metrics._read_csv(track_dir / "track_detections.csv")
                track_payloads = task_metrics._read_csv(track_dir / "track_output_payloads.csv")
                frames = task_metrics._read_csv(track_dir / "track_frames.csv")
                associations = task_metrics.track_associations_from_debug(
                    track_detections=track_detections,
                    track_payloads=track_payloads,
                    final_detections=detections,
                    result_to_period={"GMTI01": 0, "GMTI02": 1, "GMTI03": 2},
                )
            else:
                associations, frames = [], []

            response = None
            if role == "TO":
                response = r7._target_only_response(result_dir, truth_path)
            metrics = task_metrics.evaluate_case(
                mode=role,
                truths=truths if role == "ON" else (),
                final_detections=detections,
                lineage_rows=lineage,
                track_associations=associations,
                track_frames=frames,
                cfar_rows=cfar,
                expected_cfar_period_beam_keys=expected_keys,
                doppler_rows=130,
                target_only_response=response,
            )
            output[method][role] = {
                "metrics": metrics["metrics"],
                "target_only_pre_post_response": response,
                "track_debug_status": "evaluable" if track_dir is not None and track_dir.is_dir() else "NOT_EVALUABLE",
                "result_dir": str(result_dir.resolve()),
            }
            if role == "ON":
                association_rows.extend({"method_id": method, **row} for row in metrics["association_evidence"])
    return output, association_rows


def save_plot(output_dir: Path, period_data: Mapping[int, Mapping[str, Any]], overlap: Sequence[Mapping[str, Any]]) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(len(period_data), 1, figsize=(10, 8), sharex=True)
    if len(period_data) == 1:
        axes = [axes]
    overlap_by_period = {int(row["period_id"]): row for row in overlap}
    for ax, (period, data) in zip(axes, sorted(period_data.items())):
        x = data["folded_axis"]
        excess = data["positive_excess"]
        support = data["support"]
        target = data["target_roi_doppler"]
        order = np.argsort(x)
        ax.plot(x[order], np.maximum(excess[order], 1.0e-12), color="#62748a", lw=1, label="positive area excess")
        ax.scatter(x[support], np.maximum(excess[support], 1.0e-12), s=16, color="#d97706", label="95% support")
        if np.any(target):
            ax.axvspan(float(np.min(x[target])), float(np.max(x[target])), color="#2563eb", alpha=0.16, label="target ROI")
        row = overlap_by_period[period]
        ax.set_yscale("log")
        ax.set_ylabel(f"Period {period}\nPower")
        ax.set_title(
            f"range overlap={row['range_overlap_with_area_window']}; "
            f"Doppler overlap bins={row['target_roi_support_overlap_bins']}"
        )
        ax.grid(True, which="both", alpha=0.2)
    axes[0].legend(loc="upper right", ncol=3, fontsize=8)
    axes[-1].set_xlabel("Folded Doppler frequency (Hz)")
    fig.suptitle("R18 corrected pilot: H0 area clutter support and truth-centered target ROI")
    fig.tight_layout()
    path = output_dir / "r18_geometry_corrected_pilot_clutter_target_overlap.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-root", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    case_root = args.case_root.resolve()
    config_path = args.config.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    case_path = case_root / "case_manifest.json"
    case = json.loads(case_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    methods = [str(value) for value in case.get("selected_methods", []) if str(value).startswith("C")]
    methods = [value for value in methods if value in {"C0", "C1", "C2", "C3"}]
    expected_periods = int(case.get("scene_identity", {}).get("period_count", 0))
    if not methods or expected_periods <= 0:
        raise ValueError("case manifest lacks selected methods or periods")
    configured_pilot = [
        configured
        for group in config.get("pilot", {}).get("case_groups", [])
        for configured in group.get("cases", [])
        if int(configured.get("seed", -1)) == int(case.get("scene_identity", {}).get("seed", -2))
        and configured.get("working_point") == "r18_geometry_corrected"
        and int(configured.get("scene_parameters", {}).get("expected_bin", -1)) == 880
    ]
    if not configured_pilot:
        raise ValueError("analysis config is not the frozen geometry-corrected pilot config")

    simulation = case.get("simulation", {})
    on_output = Path(str(simulation.get("A0_ON", {}).get("output_dir", "")))
    truth_pulse = on_output / "truth/truth_pulse.csv"
    truth_targets = on_output / "truth/truth_targets_by_beam.csv"
    stage2_report = on_output / "reports/stage2_simulation_report.md"
    if not all(path.is_file() for path in (truth_pulse, truth_targets, stage2_report)):
        raise FileNotFoundError("corrected pilot Stage2 truth/report evidence is incomplete")
    truths = pulse_truth_by_period(truth_pulse)
    overlap, spectra, period_data = build_overlap_audit(case_root, truths)

    inventory = branch_inventory(case_root)
    input_rows, branch_gate, branch_records = audit_inputs_and_branches(
        inventory, methods, expected_periods,
    )
    clutter_rows = csi_clutter_metrics(inventory, period_data, methods)
    target_metrics = target_transfer_metrics(case_root, case, inventory, methods)
    task_results, association_rows = production_task_metrics(
        inventory, methods, truth_targets,
    )

    area_gate = case.get("area_clutter_gate", {})
    joint_count = sum(bool(row["joint_opportunity"]) for row in overlap)
    geometry_gate = len(overlap) == expected_periods and joint_count == expected_periods
    pilot_gate = bool(
        area_gate.get("status") == "passed"
        and int(area_gate.get("continuous_area_packets", 0)) > 0
        and int(area_gate.get("continuous_area_samples", 0)) > 0
        and geometry_gate
        and branch_gate["methods_complete"]
        and branch_gate["all_selected_role_inputs_identical_by_sha256"]
        and branch_gate["all_production_and_csi_gates_passed"]
        and branch_gate["no_branch_truth_use_flag"]
        and branch_gate["same_csi_and_cfar_definitions_across_methods"]
    )

    write_csv(output_dir / "r18_geometry_corrected_pilot_overlap_by_period.csv", overlap)
    write_csv(output_dir / "r18_geometry_corrected_pilot_doppler_spectra.csv", spectra)
    write_csv(output_dir / "r18_geometry_corrected_pilot_method_inputs.csv", input_rows)
    write_csv(output_dir / "r18_geometry_corrected_pilot_branch_inventory.csv", [
        {**row, "production_xml_sha256": (row.get("production_xml") or {}).get("sha256"),
         "production_log_sha256": (row.get("production_log") or {}).get("sha256"),
         "branch_manifest_sha256": (row.get("branch_manifest") or {}).get("sha256")}
        for row in branch_records
    ])
    write_csv(output_dir / "r18_geometry_corrected_pilot_h0_clutter_metrics.csv", clutter_rows)
    write_csv(output_dir / "r18_geometry_corrected_pilot_target_associations.csv", association_rows)

    method_summary: list[dict[str, Any]] = []
    for method in methods:
        h0 = [row for row in clutter_rows if row["method_id"] == method]
        metric = target_metrics[method]
        task_on = task_results[method]["ON"]["metrics"]
        off_power = float(np.mean([row["csi_output_mean_power"] for row in h0]))
        method_summary.append({
            "method_id": method,
            "h0_csi_output_power_on_empirical_support_mean": off_power,
            "h0_csi_output_power_relative_to_c0_db_mean": float(np.mean([
                row["output_power_relative_to_C0_db"] for row in h0
            ])),
            "pre_to_csi_output_ratio_db_mean": float(np.mean([
                row["before_to_csi_output_db"] for row in h0
            ])),
            "target_transfer_status": metric["status"],
            "paired_target_amplitude_ratio": metric.get("paired_complex_transfer", {}).get("amplitude_ratio"),
            "paired_target_amplitude_ratio_db": metric.get("paired_complex_transfer", {}).get("amplitude_ratio_db"),
            "paired_target_phase_error_rad": metric.get("paired_complex_transfer", {}).get("phase_error_rad"),
            "paired_target_normalized_residual_rms": metric.get("paired_complex_transfer", {}).get("normalized_residual_rms"),
            "target_only_csi_retention_db": metric.get("target_only_csi_retention_db"),
            "output_scnr_proxy_db": metric.get("output_scnr_proxy_db"),
            "visible_truth_periods": task_on.get("truth_opportunities"),
            "unique_correct_detections": task_on.get("unique_correct_detections"),
            "truth_period_detection_rate_descriptive": task_on.get("detection_rate"),
            "false_associations_not_pfa": task_on.get("false_associations"),
            "final_detections": task_on.get("final_detections"),
            "confirmed_tracks": task_on.get("confirmed_tracks"),
            "position_2d_rmse_m": task_on.get("parameter_errors", {}).get("position_2d_rmse_m"),
            "angle_rmse_deg": task_on.get("parameter_errors", {}).get("angle_rmse_deg"),
            "ground_speed_rmse_mps": task_on.get("parameter_errors", {}).get("ground_speed_rmse_mps"),
            "folded_doppler_frequency_rmse_hz": task_on.get("parameter_errors", {}).get("folded_doppler_frequency_rmse_hz"),
            "radial_velocity_status": task_on.get("parameter_errors", {}).get("radial_velocity", {}).get("status"),
            "empirical_pfa_status": "NOT_EVALUABLE",
            "single_seed_descriptive_only": True,
        })
    write_csv(output_dir / "r18_geometry_corrected_pilot_method_summary.csv", method_summary)

    figure = save_plot(output_dir, period_data, overlap)
    config_path_expected = config_path
    input_records = [
        file_record(config_path_expected), file_record(case_path), file_record(truth_pulse),
        file_record(truth_targets), file_record(stage2_report),
    ]
    for period, data in sorted(period_data.items()):
        manifest = data["manifest"]
        input_records.append(file_record(manifest))
        for key in ("before_power_path", "fa_axis_path", "range_axis_path"):
            input_records.append(file_record(Path(data["tap"][key])))

    summary = {
        "status": "PILOT_GATE_PASSED" if pilot_gate else "PILOT_GATE_FAILED",
        "claim_scope": "single seed, low-speed corrected-geometry pilot; feasibility and correctness only",
        "algorithmic_results_claimed": False,
        "performance_verified": False,
        "area_clutter_gate": area_gate,
        "geometry_gate": {
            "status": "passed" if geometry_gate else "failed",
            "joint_opportunity_periods": joint_count,
            "period_count": expected_periods,
            "range_overlap_definition": "non-empty intersection between truth-centered ±2-bin range ROI and 84.5–85.5 km area window",
            "doppler_support_definition": "minimum H0 C0 OFF Doppler-bin set accumulating 95% of positive area-minus-sideband excess power",
        },
        "branch_gate": branch_gate,
        "evidence_gate_from_runner": case.get("evidence_gate", {}),
        "target_transfer_metrics": target_metrics,
        "production_task_metrics": task_results,
        "method_summary": method_summary,
        "overlap_by_period": overlap,
        "source_files": input_records,
        "outputs": {
            "overlap_csv": str((output_dir / "r18_geometry_corrected_pilot_overlap_by_period.csv").resolve()),
            "spectra_csv": str((output_dir / "r18_geometry_corrected_pilot_doppler_spectra.csv").resolve()),
            "h0_clutter_csv": str((output_dir / "r18_geometry_corrected_pilot_h0_clutter_metrics.csv").resolve()),
            "method_summary_csv": str((output_dir / "r18_geometry_corrected_pilot_method_summary.csv").resolve()),
            "association_csv": str((output_dir / "r18_geometry_corrected_pilot_target_associations.csv").resolve()),
            "plot": str(figure.resolve()),
        },
    }
    summary_path = output_dir / "r18_geometry_corrected_pilot_analysis.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    analysis_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2], text=True,
    ).strip()
    path_status = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--", str(Path(__file__).resolve().relative_to(Path(__file__).resolve().parents[2]))],
        cwd=Path(__file__).resolve().parents[2], text=True,
    ).strip()
    analysis_manifest = {
        "analysis_script": file_record(Path(__file__).resolve()),
        "analysis_commit": analysis_commit,
        "analysis_script_worktree_status": path_status or "tracked_and_clean",
        "experiment_case_id": case.get("case_id"),
        "experiment_source_commit": "839dde524d751c27fbd2cfb7debeea807009f9e6",
        "config": file_record(config_path),
        "case_manifest": file_record(case_path),
        "analysis_summary": file_record(summary_path),
        "figure": file_record(figure),
    }
    (output_dir / "analysis_manifest.json").write_text(
        json.dumps(analysis_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    print(json.dumps({
        "status": summary["status"],
        "joint_opportunities": f"{joint_count}/{expected_periods}",
        "methods": methods,
        "output_dir": str(output_dir),
        "empirical_pfa": "NOT_EVALUABLE",
        "performance_verified": False,
    }, sort_keys=True))
    return 0 if pilot_gate else 2


if __name__ == "__main__":
    raise SystemExit(main())
