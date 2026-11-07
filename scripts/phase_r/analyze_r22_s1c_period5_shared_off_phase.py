#!/usr/bin/env python3
"""Validate and summarize the R22-S1c GMTI05 single-period CUDA replay."""

from __future__ import annotations

import argparse
import cmath
import csv
import hashlib
import json
import math
import shutil
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


BRANCHES = (
    "C4_modeb_S1c_period5_baseline_snr20",
    "C4_modeb_S1c_period5_baseline_snr30",
    "C4_modea_S1c_period5_target_free_OFF",
    "C4_modeb_S1c_period5_shared_off_phase_snr20",
    "C4_modeb_S1c_period5_shared_off_phase_snr30",
)
STAGES = {
    "raw_dbs_pre_range_phase",
    "raw_post_range_phase",
    "csi_dbs_pre_range_phase",
    "csi_post_range_phase_pre_c4_adapter",
    "csi_post_c4_adapter",
}
EPSILON = 2.220446049250313e-16


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def copy_evidence_file(source: Path, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    normalization = "none"
    if source.suffix.lower() in {".log", ".csv"}:
        text = source.read_text(encoding="utf-8")
        if source.suffix.lower() == ".log":
            text = "\n".join(line.rstrip(" \t") for line in text.splitlines()) + "\n"
            normalization = "LF_and_trim_trailing_spaces"
        else:
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            normalization = "LF_newlines"
        with destination.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    else:
        shutil.copy2(source, destination)
    return {
        "path": str(destination),
        "source_path": str(source),
        "source_sha256": sha256(source),
        "copy_sha256": sha256(destination),
        "normalization": normalization,
    }


def read_vector(path: Path) -> tuple[float, ...]:
    raw = path.read_bytes()
    require(len(raw) == 4096 * 4, f"unexpected phase vector size: {path}")
    values = struct.unpack("<4096f", raw)
    require(all(math.isfinite(value) for value in values), f"non-finite phase vector: {path}")
    return values


def wrapped_delta(left: float, right: float) -> float:
    return math.atan2(math.sin(left - right), math.cos(left - right))


def check_runtime_xml(path: Path, expected_mode: str) -> dict[str, str]:
    params = ET.parse(path).getroot().find("GMTI_parameter")
    require(params is not None, f"GMTI_parameter missing in {path}")
    values = {node.tag: (node.text or "").strip() for node in params}
    expected = {
        "research_calibration_enable": "true",
        "research_calibration_method": "robust_ddc_rb",
        "research_calibration_mode": expected_mode,
        "research_calibration_min_support": "8",
        "research_calibration_range_band_bins": "133",
        "research_calibration_robust_phase_threshold_rad": "0.35",
        "csi_metrics_enable": "true",
        "csi_metrics_beam_id": "50",
    }
    for key, value in expected.items():
        require(values.get(key) == value, f"runtime XML mismatch {key}: {path}")
    return values


def phase_group(branch_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    gamma_rows = read_csv(branch_root / "result/production_calibration_reference_gamma.csv")
    selected_gamma = [
        row for row in gamma_rows
        if row["beam_id"] == "50" and row["method"] == "robust_ddc_rb"
        and row["az_index"] == "52" and row["range_start"] == "1065"
        and row["range_end"] == "1197" and row["period_id"] == "1"
        and row["result_id"] == "GMTI01"
    ]
    require(len(selected_gamma) == 1, f"expected one selected gamma row in {branch_root}")
    gamma = selected_gamma[0]

    cells = [
        row for row in read_csv(branch_root / "result/production_calibration_selected_cells.csv")
        if row["beam_id"] == "50" and row["az_index"] == "52"
        and row["range_start"] == "1065" and row["range_end"] == "1197"
        and row["period_id"] == "1" and row["result_id"] == "GMTI01"
    ]
    expected_ranges = list(range(1065, 1198))
    require(sorted(int(row["range_index"]) for row in cells) == expected_ranges,
            f"selected-cell range coverage mismatch in {branch_root}")

    units: list[tuple[int, complex, complex, complex]] = []
    for row in cells:
        first = complex(float(row["f1_real"]), float(row["f1_imag"]))
        second = complex(float(row["f2_real"]), float(row["f2_imag"]))
        cross = second * first.conjugate()
        magnitude = abs(cross)
        if math.isfinite(magnitude) and magnitude > EPSILON:
            units.append((int(row["range_index"]), cross / magnitude, first, second))
    require(units, f"no finite cross-spectrum cells in {branch_root}")

    phase_sum = sum((entry[1] for entry in units), 0j)
    coherence = abs(phase_sum / len(units))
    require(math.isfinite(coherence) and coherence > EPSILON,
            f"undefined phase center in {branch_root}")
    center = phase_sum / abs(phase_sum)
    details: list[dict[str, Any]] = []
    kept: list[tuple[int, complex, complex, complex]] = []
    for range_index, unit_phase, first, second in units:
        residual = cmath.phase(unit_phase * center.conjugate())
        keep = abs(residual) <= 0.35
        if keep:
            kept.append((range_index, unit_phase, first, second))
        details.append({
            "range_index": range_index,
            "cross_phase_rad": cmath.phase(unit_phase),
            "phase_center_rad": cmath.phase(center),
            "residual_rad": residual,
            "retained_by_frozen_gate": keep,
        })

    support = len(kept)
    require(support == int(gamma["support_count"]),
            f"CUDA support differs from float-input reconstruction in {branch_root}")
    require(abs(coherence - float(gamma["phase_coherence"])) < 2e-7,
            f"CUDA coherence differs from reconstruction in {branch_root}")
    gamma_fit = None
    if support >= 8:
        numerator = sum((second * first.conjugate() for _, _, first, second in kept), 0j)
        denominator = sum((abs(first) ** 2 for _, _, first, _, in kept), 0.0)
        gamma_fit = numerator / denominator
        require(abs(gamma_fit.real - float(gamma["gamma_real"])) < 2e-6,
                f"CUDA gamma real differs from reconstruction in {branch_root}")
        require(abs(gamma_fit.imag - float(gamma["gamma_imag"])) < 2e-6,
                f"CUDA gamma imag differs from reconstruction in {branch_root}")

    return ({
        "gamma_status": gamma["status"],
        "gamma_reason": gamma["reason"],
        "support_count": support,
        "valid_cross_cells": len(units),
        "phase_coherence": coherence,
        "phase_center_rad": cmath.phase(center),
        "retained_range_bins": [entry[0] for entry in kept],
        "gamma_real_recomputed": gamma_fit.real if gamma_fit is not None else None,
        "gamma_imag_recomputed": gamma_fit.imag if gamma_fit is not None else None,
    }, details)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    output_root = args.output_root.resolve()
    evidence_dir = args.evidence_dir.resolve()
    require(output_root.is_dir(), f"production output root missing: {output_root}")
    require(not evidence_dir.exists(), f"refusing to overwrite evidence directory: {evidence_dir}")

    manifest_path = output_root / "s1c_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("run_status") == "period5_production_branches_passed",
            "Production run is not complete and passed")
    require(manifest.get("production_binary_sha256") ==
            "16cb0f2d35596ff98ab8bdcbc9571b972b99e217053cf0b1b7f6b49168c8c998",
            "unexpected Production binary identity")
    require(manifest.get("native_cuda") == "USE_COREX=OFF", "not a native CUDA build")
    require(manifest.get("truth_used_in_estimator") is False, "truth-use contract failed")
    require(manifest.get("selected_group", {}).get("source_period_index_zero_based") == 4,
            "source period is not period_0004.bin")
    require([row["branch_name"] for row in manifest.get("branches", [])] == list(BRANCHES),
            "unexpected or incomplete Production branch matrix")

    conditions = manifest["conditions"]
    off_hashes_20 = [entry["sha256"] for entry in conditions["20"]["input_identities"]["OFF"]]
    off_hashes_30 = [entry["sha256"] for entry in conditions["30"]["input_identities"]["OFF"]]
    require(len(off_hashes_20) == len(off_hashes_30) == 10 and off_hashes_20 == off_hashes_30,
            "ten-period target-free OFF input pairing is not exact")

    branch_meta = {row["branch_name"]: row for row in manifest["branches"]}
    expected_modes = {name: ("Mode-A" if "target_free_OFF" in name else "Mode-B") for name in BRANCHES}
    group_results: dict[str, dict[str, Any]] = {}
    phase_rows: list[dict[str, Any]] = []
    cfar_rows: dict[str, list[dict[str, str]]] = {}
    tap_summaries: dict[str, list[dict[str, str]]] = {}
    copied_sources: list[tuple[Path, Path]] = []

    for name in BRANCHES:
        meta = branch_meta[name]
        require(meta.get("status") == "passed" and meta.get("production_returncode") == 0,
                f"Production branch failed: {name}")
        require(meta.get("branch_record", {}).get("truth_used_in_estimator") is False,
                f"truth-use audit failed: {name}")
        branch_root = Path(meta["production_branch_manifest"]).parent
        branch_manifest = json.loads(Path(meta["production_branch_manifest"]).read_text(encoding="utf-8"))
        require(branch_manifest.get("status") == "passed", f"branch manifest failed: {name}")
        require(branch_manifest.get("production_returncode") == 0, f"branch return code failed: {name}")
        xml_path = Path(meta["production_xml"])
        xml_values = check_runtime_xml(xml_path, expected_modes[name])

        if "shared_off_phase" in name:
            snr = "20" if name.endswith("snr20") else "30"
            baseline_name = f"C4_modeb_S1c_period5_baseline_snr{snr}"
            baseline_raw = branch_meta[baseline_name]["phase_vectors"]["raw"]
            shared_off = manifest["frozen_target_free_off_csi_phase_vector"]
            require(meta["phase_vectors"]["raw"]["sha256"] == baseline_raw["sha256"],
                    f"raw phase differs from baseline: {name}")
            require(meta["phase_vectors"]["csi"]["sha256"] == shared_off["sha256"],
                    f"shared target-free CSI phase was not applied: {name}")
            require(Path(xml_values["paired_raw_range_phase_override_f32"]).resolve() ==
                    Path(baseline_raw["path"]).resolve(), f"raw override path mismatch: {name}")
            require(Path(xml_values["paired_csi_range_phase_override_f32"]).resolve() ==
                    Path(shared_off["path"]).resolve(), f"CSI override path mismatch: {name}")

        trace_path = branch_root / "result/production_phase_boundary_trace.csv"
        trace = read_csv(trace_path)
        selected_trace = [
            row for row in trace if row["result_id"] == "GMTI01" and row["beam_id"] == "50"
            and row["azimuth_index"] == "52" and 1065 <= int(row["range_index"]) <= 1197
        ]
        require(len(selected_trace) == 665, f"incomplete five-stage trace: {name}")
        require({row["stage"] for row in selected_trace} == STAGES,
                f"unexpected trace stages: {name}")
        require({row["period_id"] for row in selected_trace} == {"1"},
                f"trace result identity mismatch: {name}")

        group, details = phase_group(branch_root)
        group_results[name] = {
            **group,
            "branch_status": meta["status"],
            "production_returncode": meta["production_returncode"],
            "production_elapsed_sec": meta["production_elapsed_sec"],
            "source_role": meta["source_role"],
            "input_sha256": meta["input_sha256"],
            "raw_phase_sha256": meta["phase_vectors"]["raw"]["sha256"],
            "csi_phase_sha256": meta["phase_vectors"]["csi"]["sha256"],
            "trace_rows": len(selected_trace),
            "trace_stages": sorted(STAGES),
            "runtime_xml": str(xml_path),
        }
        for detail in details:
            phase_rows.append({"branch_name": name, **detail})

        geometry_path = branch_root / "result/debug/cfar_geometry_diagnostics.csv"
        geometry = read_csv(geometry_path)
        require(len(geometry) == 2, f"CFAR geometry rows missing: {name}")
        require(all(int(row["valid_cut_count"]) > 0 for row in geometry),
                f"CFAR valid-CUT geometry missing: {name}")
        cfar_rows[name] = geometry

        tap_paths = sorted((branch_root / "result/csi_metrics").glob("*/csi_metric_tap_summary.csv"))
        require(len(tap_paths) == 1, f"CSI tap summary missing or ambiguous: {name}")
        taps = read_csv(tap_paths[0])
        require(len(taps) == 4 and all(int(row["phase_model_valid_cells"]) == 20748 for row in taps),
                f"CSI tap coverage mismatch: {name}")
        tap_summaries[name] = taps

        artifacts = (
            Path(meta["production_branch_manifest"]), xml_path,
            Path(meta["production_log"]), branch_root / "production.configure.log",
            branch_root / "result/run_manifest.json",
            branch_root / "result/runtime_config_dump.json",
            trace_path,
            branch_root / "result/production_calibration_reference_gamma.csv",
            branch_root / "result/production_calibration_selected_cells.csv",
            branch_root / "result/production_calibration_adapter.csv",
            geometry_path,
            tap_paths[0],
            branch_root / "result/debug/paired_range_phase_raw_period050_beam050.f32",
            branch_root / "result/debug/paired_range_phase_csi_period050_beam050.f32",
        )
        for source in artifacts:
            require(source.is_file() and source.stat().st_size > 0,
                    f"required Production evidence missing: {source}")
            destination = evidence_dir / "branches" / name / source.name
            copied_sources.append((source, destination))

    for snr in ("20", "30"):
        baseline = group_results[f"C4_modeb_S1c_period5_baseline_snr{snr}"]
        corrected = group_results[f"C4_modeb_S1c_period5_shared_off_phase_snr{snr}"]
        require(baseline["input_sha256"] == corrected["input_sha256"],
                f"ON input differs between paired branches at SNR{snr}")

    vector_rows = {}
    off_vector_meta = manifest["frozen_target_free_off_csi_phase_vector"]
    off_vector = read_vector(Path(off_vector_meta["path"]))
    for snr in ("20", "30"):
        baseline_name = f"C4_modeb_S1c_period5_baseline_snr{snr}"
        raw_name = f"C4_modeb_S1c_period5_shared_off_phase_snr{snr}"
        baseline_vector = read_vector(Path(branch_meta[baseline_name]["phase_vectors"]["csi"]["path"]))
        shared_vector = read_vector(Path(branch_meta[raw_name]["phase_vectors"]["csi"]["path"]))
        delta = [abs(wrapped_delta(baseline_vector[index], off_vector[index]))
                 for index in range(1065, 1198)]
        require(sha256(Path(branch_meta[raw_name]["phase_vectors"]["csi"]["path"])) ==
                off_vector_meta["sha256"], f"OFF CSI vector byte identity mismatch at SNR{snr}")
        vector_rows[snr] = {
            "baseline_csi_phase_sha256": branch_meta[baseline_name]["phase_vectors"]["csi"]["sha256"],
            "shared_off_csi_phase_sha256": off_vector_meta["sha256"],
            "shared_output_csi_phase_sha256": branch_meta[raw_name]["phase_vectors"]["csi"]["sha256"],
            "roi_bins": len(delta),
            "mean_abs_wrapped_delta_rad_vs_baseline": sum(delta) / len(delta),
            "median_abs_wrapped_delta_rad_vs_baseline": sorted(delta)[len(delta) // 2],
            "max_abs_wrapped_delta_rad_vs_baseline": max(delta),
            "shared_and_output_vectors_byte_identical": True,
        }

    baseline_gap = abs(
        group_results["C4_modeb_S1c_period5_baseline_snr20"]["support_count"]
        - group_results["C4_modeb_S1c_period5_baseline_snr30"]["support_count"]
    )
    shared_gap = abs(
        group_results["C4_modeb_S1c_period5_shared_off_phase_snr20"]["support_count"]
        - group_results["C4_modeb_S1c_period5_shared_off_phase_snr30"]["support_count"]
    )
    summary = {
        "schema_version": 1,
        "task": "R22-S1c GMTI05 selected-group shared target-free OFF CSI phase diagnostic",
        "validation_status": "passed",
        "scope": "single-period single-group mechanism diagnostic; no task-performance ranking",
        "source_period_mapping": {
            "stage2_zero_based_period_index": 4,
            "source_file": "period_0004.bin",
            "production_input_slot": 1,
            "trace_result_id": "GMTI01",
            "trace_period_id": 1,
            "cfar_and_csi_tap_period_id": 0,
            "interpretation": "format-specific IDs for the sole input; source identity is bound by input SHA-256",
        },
        "quality_gate": {"min_support": 8, "phase_threshold_rad": 0.35},
        "truth_used_in_estimator": False,
        "off_inputs_sha256_identical_across_all_ten_periods": True,
        "support_gap_snr20_vs_snr30": {
            "own_on_phase_baseline": baseline_gap,
            "shared_target_free_off_csi_phase": shared_gap,
        },
        "phase_vector_comparison": vector_rows,
        "group_results": group_results,
        "cfar_geometry": {
            name: [{
                "period_id": int(row["period_id"]),
                "branch": row["branch"],
                "threshold_test_count": int(row["threshold_test_count"]),
                "valid_cut_count": int(row["valid_cut_count"]),
                "hit_cut_count": int(row["hit_cut_count"]),
                "hit_rate_status": row["hit_rate_status"],
                "false_counts_status": row["false_counts_status"],
            } for row in rows]
            for name, rows in cfar_rows.items()
        },
        "csi_tap_summary": {
            name: [{
                "roi_name": row["roi_name"],
                "phase_model_valid_cells": int(row["phase_model_valid_cells"]),
                "phase_model_weighted_coherence": float(row["phase_model_weighted_coherence"]),
                "phase_model_weighted_circular_rmse_rad": float(row["phase_model_weighted_circular_rmse_rad"]),
                "status": row["status"],
            } for row in rows]
            for name, rows in tap_summaries.items()
        },
        "interpretation_limit": (
            "Shared target-free OFF phase removes most of the SNR20/SNR30 support-count gap "
            "in this selected group. This isolates sensitivity to the CSI range-phase vector, "
            "but does not identify why the ON-derived vector differs, prove target contamination, "
            "or establish deployment task benefit."
        ),
    }
    analysis_path = output_root / "s1c_period5_analysis.json"
    analysis_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    group_fields = [
        "branch_name", "gamma_status", "gamma_reason", "support_count", "valid_cross_cells",
        "phase_coherence", "phase_center_rad", "retained_range_bins", "input_sha256",
        "gamma_real_recomputed", "gamma_imag_recomputed", "branch_status",
        "production_returncode", "production_elapsed_sec", "source_role",
        "raw_phase_sha256", "csi_phase_sha256", "trace_rows", "trace_stages", "runtime_xml",
    ]
    group_csv = output_root / "s1c_period5_group_comparison.csv"
    write_csv(group_csv, group_fields, [
        {**group_results[name], "branch_name": name,
         "retained_range_bins": ";".join(map(str, group_results[name]["retained_range_bins"])),
         "trace_stages": ";".join(group_results[name]["trace_stages"])}
        for name in BRANCHES
    ])
    phase_csv = output_root / "s1c_period5_phase_bin_diagnostics.csv"
    write_csv(phase_csv, [
        "branch_name", "range_index", "cross_phase_rad", "phase_center_rad",
        "residual_rad", "retained_by_frozen_gate",
    ], phase_rows)

    manifest["analysis"] = {
        "status": "passed",
        "analysis_script": str(Path(__file__).resolve()),
        "analysis_script_sha256": sha256(Path(__file__).resolve()),
        "analysis_json": str(analysis_path),
        "analysis_json_sha256": sha256(analysis_path),
        "group_comparison_csv": str(group_csv),
        "phase_bin_diagnostics_csv": str(phase_csv),
        "scope": "mechanism-only; no Pd/Pfa or detection performance claim",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    evidence_dir.mkdir(parents=True, exist_ok=False)
    copy_provenance = []
    for source, destination in copied_sources:
        copy_provenance.append(copy_evidence_file(source, destination))
    for source in (manifest_path, analysis_path, group_csv, phase_csv):
        copy_provenance.append(copy_evidence_file(source, evidence_dir / source.name))
    provenance_by_path = {
        str(Path(item["path"]).relative_to(evidence_dir)): item
        for item in copy_provenance
    }
    evidence_files = []
    for path in sorted(p for p in evidence_dir.rglob("*") if p.is_file()):
        relative_path = str(path.relative_to(evidence_dir))
        provenance = provenance_by_path[relative_path]
        evidence_files.append({
            "path": relative_path,
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
            "source_path": provenance["source_path"],
            "source_sha256": provenance["source_sha256"],
            "normalization": provenance["normalization"],
        })
    evidence_manifest = {
        "schema_version": 1,
        "source_output_root": str(output_root),
        "production_manifest_sha256": sha256(evidence_dir / "s1c_manifest.json"),
        "analysis_sha256": sha256(evidence_dir / "s1c_period5_analysis.json"),
        "files": evidence_files,
    }
    (evidence_dir / "evidence_manifest.json").write_text(
        json.dumps(evidence_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
