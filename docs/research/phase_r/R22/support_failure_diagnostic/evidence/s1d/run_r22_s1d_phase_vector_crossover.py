#!/usr/bin/env python3
"""Run a period-correct, same-binary 2x3 CSI-phase-vector crossover."""

from __future__ import annotations

import argparse
import csv
import cmath
import json
import math
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_r import run_r22_s1c_period5_shared_off_phase as s1c


PHASE_FILENAME = "paired_range_phase_{stage}_period050_beam050.f32"
SELECTED_GROUP = {
    "beam_id": 50,
    "az_index": 52,
    "range_start": 1065,
    "range_end": 1197,
}
SOURCE_ROLE_NAMES = {
    "self": "same_snr_on",
    "cross": "other_snr_on",
    "off": "paired_target_free_off",
}


def selected_group_rows(branch_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = branch_root / "result"
    gamma_path = result / "production_calibration_reference_gamma.csv"
    cells_path = result / "production_calibration_selected_cells.csv"
    with gamma_path.open(newline="", encoding="utf-8") as stream:
        gamma_rows = list(csv.DictReader(stream))
    selected_gamma = [
        row for row in gamma_rows
        if row.get("period_id") == "1"
        and row.get("beam_id") == str(SELECTED_GROUP["beam_id"])
        and row.get("az_index") == str(SELECTED_GROUP["az_index"])
        and row.get("range_start") == str(SELECTED_GROUP["range_start"])
        and row.get("range_end") == str(SELECTED_GROUP["range_end"])
    ]
    if len(selected_gamma) != 1:
        raise ValueError(f"expected one selected Gamma row, found {len(selected_gamma)}")
    with cells_path.open(newline="", encoding="utf-8") as stream:
        all_cells = list(csv.DictReader(stream))
    selected_cells = [
        row for row in all_cells
        if row.get("period_id") == "1"
        and row.get("beam_id") == str(SELECTED_GROUP["beam_id"])
        and row.get("az_index") == str(SELECTED_GROUP["az_index"])
        and row.get("range_start") == str(SELECTED_GROUP["range_start"])
        and row.get("range_end") == str(SELECTED_GROUP["range_end"])
    ]
    expected_count = SELECTED_GROUP["range_end"] - SELECTED_GROUP["range_start"] + 1
    ranges = {int(row["range_index"]) for row in selected_cells}
    if len(selected_cells) != expected_count or len(ranges) != expected_count:
        raise ValueError(
            f"selected-cell trace incomplete: rows={len(selected_cells)}, "
            f"unique_ranges={len(ranges)}, expected={expected_count}"
        )
    return selected_gamma[0], selected_cells


def recompute_membership(cells: list[dict[str, Any]], threshold: float) -> tuple[int, float, list[dict[str, Any]]]:
    valid: list[tuple[dict[str, Any], complex]] = []
    for row in cells:
        f1 = complex(float(row["f1_real"]), float(row["f1_imag"]))
        f2 = complex(float(row["f2_real"]), float(row["f2_imag"]))
        cross = f2 * f1.conjugate()
        magnitude = abs(cross)
        if math.isfinite(magnitude) and magnitude > 1e-15:
            valid.append((row, cross / magnitude))
    if not valid:
        raise ValueError("selected C4 group has no valid cross-products")
    phase_sum = sum((unit for _, unit in valid), 0j)
    coherence = abs(phase_sum / len(valid))
    if not math.isfinite(coherence) or coherence <= 1e-15:
        raise ValueError("selected C4 group has invalid circular phase center")
    center = phase_sum / abs(phase_sum)
    diagnostics: list[dict[str, Any]] = []
    support = 0
    for row, unit in valid:
        residual = cmath.phase(unit * center.conjugate())
        retained = math.isfinite(residual) and abs(residual) <= threshold
        support += int(retained)
        diagnostics.append({
            **row,
            "cross_phase_rad": cmath.phase(unit),
            "phase_center_rad": cmath.phase(center),
            "residual_rad": residual,
            "retained_by_frozen_gate": retained,
        })
    return support, coherence, diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--pipe-binary", type=Path, required=True)
    parser.add_argument("--simulator-binary", type=Path, required=True)
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    pipe_binary = args.pipe_binary.resolve()
    simulator_binary = args.simulator_binary.resolve()
    if output_root.exists():
        parser.error(f"refusing to reuse or overwrite output root: {output_root}")
    if not pipe_binary.is_file() or not simulator_binary.is_file():
        parser.error("Production or required Stage2 simulator binary is missing")
    cmake_cache = pipe_binary.parent / "CMakeCache.txt"
    if not cmake_cache.is_file():
        parser.error(f"Production build cache is missing: {cmake_cache}")
    cache_text = cmake_cache.read_text(errors="replace")
    if "USE_COREX:BOOL=OFF" not in cache_text or "CMAKE_BUILD_TYPE:STRING=Release" not in cache_text:
        parser.error("Production build is not confirmed as native CUDA Release")
    if "CMAKE_CUDA_ARCHITECTURES" not in cache_text:
        parser.error("Production CMake cache does not record CUDA architecture")

    conditions, _ = s1c.load_conditions(source_root)
    output_root.mkdir(parents=True, exist_ok=False)
    shutil.copy2(Path(__file__).resolve(), output_root / Path(__file__).name)
    source_files = (
        "scripts/phase_r/run_r22_s1d_phase_vector_crossover.py",
        "scripts/phase_r/run_r22_s1c_period5_shared_off_phase.py",
        "scripts/run_delay_stage1_formal.py",
        "scripts/run_track_manager_e2e.py",
        "include/GMTIProcessor.hpp",
        "include/runtime_diagnostics.hpp",
        "src/runtime_diagnostics.cpp",
        "src/processOnePeriod.cpp",
        "src/production_calibration_adapter.cpp",
        "src/gpu/gpu_kernels.cu",
    )
    source_hashes = {relative: s1c.sha256(ROOT / relative) for relative in source_files}

    manifest: dict[str, Any] = {
        "schema_version": 2,
        "task": "R22-S1d period-correct same-binary CSI phase-vector crossover",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "repository": str(ROOT),
        "git_commit": s1c.git_output("rev-parse", "HEAD"),
        "tracked_worktree_status": s1c.git_output("status", "--short", "--untracked-files=no"),
        "runner_path": str(Path(__file__).resolve()),
        "runner_sha256": s1c.sha256(Path(__file__).resolve()),
        "source_hashes": source_hashes,
        "source_root": str(source_root),
        "production_binary": str(pipe_binary),
        "production_binary_sha256": s1c.sha256(pipe_binary),
        "production_binary_source_commit": s1c.git_output("rev-parse", "HEAD"),
        "production_cmake_cache": str(cmake_cache),
        "production_cmake_cache_sha256": s1c.sha256(cmake_cache),
        "production_build": {
            "type": "Release",
            "native_cuda": "USE_COREX=OFF",
            "cuda_architectures": [
                line.split("=", 1)[1]
                for line in cache_text.splitlines()
                if line.startswith("CMAKE_CUDA_ARCHITECTURES") and "=" in line
            ],
        },
        "previous_s1c_binary": {
            "sha256": "16cb0f2d35596ff98ab8bdcbc9571b972b99e217053cf0b1b7f6b49168c8c998",
            "available_at_recorded_path": Path("/tmp/pgrcc_r22_s1b_cuda_20261029/GMTI_pipe_core").is_file(),
            "comparison_policy": "All seven S1d branches use this one rebuilt binary; do not compare its absolute support counts to the unavailable S1c binary without a matched control.",
        },
        "simulator_binary": str(simulator_binary),
        "simulator_used": False,
        "stage2_regenerated": False,
        "condition": {
            "seed": 1932001,
            "target_velocity_mps": 2.5,
            "delay_error_ns": 4.0,
            "target_count": 20,
            "source_period_index_zero_based": 4,
            "source_period_label": "GMTI05",
            "production_period_id": 1,
            "selected_group": SELECTED_GROUP,
            "snr20_input_sha256": conditions[20]["source_ON"]["source_period_sha256"],
            "snr30_input_sha256": conditions[30]["source_ON"]["source_period_sha256"],
            "paired_off_input_sha256": conditions[20]["source_OFF"]["source_period_sha256"],
        },
        "design": {
            "factors": ["ON input SNR (20/30 dB)", "CSI range-phase vector source (same ON / other ON / paired target-free OFF)"],
            "raw_phase_policy": "For each ON input, retain the raw phase vector estimated from that same ON input in the same S1d binary run.",
            "csi_phase_policy": "Estimate same-SNR ON and target-free OFF vectors in this same binary run; cross-apply without using truth.",
            "truth_used_in_estimator": False,
            "not_deployment_performance_evidence": True,
        },
        "frozen_quality_gates": {
            "min_support": 8,
            "robust_phase_threshold_rad": 0.35,
            "range_band_bins": 133,
            "fallback_to_current": False,
        },
        "output_root": str(output_root),
        "branches": [],
        "run_status": "RUNNING",
    }
    manifest_path = output_root / "s1d_manifest.json"
    s1c.write_json(manifest_path, manifest)

    from scripts import run_delay_stage1_formal as stage1
    from scripts import run_track_manager_e2e as e2e

    stage1.SIMULATOR = simulator_binary
    e2e.PIPE = pipe_binary
    s1c.os.environ["PGRCC_C4_TRACE_GROUP"] = s1c.TRACE_SELECTOR
    summary_rows: list[dict[str, Any]] = []
    all_cell_diagnostics: list[dict[str, Any]] = []

    def execute_branch(
        *, name: str, source: dict[str, Any], layout: dict[str, Any],
        mode: str, input_snr: int | None, phase_mode: str,
        raw_vector: Path | None = None, csi_vector: Path | None = None,
    ) -> dict[str, Any]:
        overrides = dict(source["source_xml_overrides"])
        if raw_vector is not None:
            overrides["paired_raw_range_phase_override_f32"] = str(raw_vector.resolve())
        if csi_vector is not None:
            overrides["paired_csi_range_phase_override_f32"] = str(csi_vector.resolve())
        s1c.configure_preflight(Path(source["source_xml"]), overrides, layout, mode)
        input_dir = s1c.make_period_link(
            output_root, f"input_{name}", Path(source["source_period_file"])
        )
        branch = s1c.run_branch(
            output_root=output_root,
            name=name,
            input_dir=input_dir,
            source=source,
            layout=layout,
            overrides=overrides,
            stage1=stage1,
        )
        branch_root = output_root / "production" / name
        gamma_row, cells = selected_group_rows(branch_root)
        support_recomputed, coherence_recomputed, cell_rows = recompute_membership(
            cells, 0.35
        )
        reported_support = int(gamma_row["support_count"])
        if support_recomputed != reported_support:
            raise RuntimeError(
                f"{name}: independent frozen-gate support {support_recomputed} "
                f"does not match Production {reported_support}"
            )
        branch.update({
            "input_snr_db": input_snr,
            "phase_mode": phase_mode,
            "phase_vector_source": SOURCE_ROLE_NAMES[phase_mode],
            "source_role": source["role"],
            "source_period_sha256": source["source_period_sha256"],
            "raw_phase_vector_override_sha256": s1c.sha256(raw_vector) if raw_vector else None,
            "csi_phase_vector_override_sha256": s1c.sha256(csi_vector) if csi_vector else None,
            "selected_group_gamma_row": gamma_row,
            "selected_group_cell_count": len(cells),
            "support_recomputed_from_selected_cells": support_recomputed,
            "phase_coherence_recomputed_from_selected_cells": coherence_recomputed,
        })
        if raw_vector is not None and branch["phase_vectors"]["raw"]["sha256"] != s1c.sha256(raw_vector):
            raise RuntimeError(f"{name}: raw phase override was not applied byte-for-byte")
        if csi_vector is not None and branch["phase_vectors"]["csi"]["sha256"] != s1c.sha256(csi_vector):
            raise RuntimeError(f"{name}: CSI phase override was not applied byte-for-byte")
        for row in cell_rows:
            all_cell_diagnostics.append({
                "branch_name": name,
                "input_snr_db": input_snr if input_snr is not None else "OFF",
                "phase_mode": phase_mode,
                "phase_vector_source": SOURCE_ROLE_NAMES[phase_mode],
                **row,
            })
        summary_rows.append({
            "branch_name": name,
            "input_snr_db": input_snr if input_snr is not None else "OFF",
            "phase_mode": phase_mode,
            "phase_vector_source": SOURCE_ROLE_NAMES[phase_mode],
            "production_status": branch["status"],
            "production_returncode": branch["production_returncode"],
            "reference_status": gamma_row["status"],
            "support_count": reported_support,
            "phase_coherence": float(gamma_row["phase_coherence"]),
            "support_recomputed": support_recomputed,
            "truth_used_in_estimator": gamma_row["truth_used_in_estimator"],
        })
        manifest["branches"].append(branch)
        s1c.write_json(manifest_path, manifest)
        s1c.require_passed(branch)
        return branch

    try:
        native_on: dict[int, dict[str, Any]] = {}
        for snr in (20, 30):
            source = conditions[snr]["source_ON"]
            name = f"C4_modeb_S1d_native_on_snr{snr}"
            native_on[snr] = execute_branch(
                name=name, source=source, layout=conditions[snr]["protocol_layout"],
                mode="Mode-B", input_snr=snr, phase_mode="self",
            )

        off_source = conditions[20]["source_OFF"]
        off_name = "C4_modea_S1d_native_target_free_OFF"
        native_off = execute_branch(
            name=off_name, source=off_source, layout=conditions[20]["protocol_layout"],
            mode="Mode-A", input_snr=None, phase_mode="off",
        )

        for input_snr in (20, 30):
            own = native_on[input_snr]
            own_raw = Path(own["phase_vectors"]["raw"]["path"])
            other = native_on[50 - input_snr]
            cross_name = f"C4_modeb_S1d_input_snr{input_snr}_cross_csi_snr{50-input_snr}"
            execute_branch(
                name=cross_name,
                source=conditions[input_snr]["source_ON"],
                layout=conditions[input_snr]["protocol_layout"],
                mode="Mode-B", input_snr=input_snr, phase_mode="cross",
                raw_vector=own_raw,
                csi_vector=Path(other["phase_vectors"]["csi"]["path"]),
            )
            off_name = f"C4_modeb_S1d_input_snr{input_snr}_shared_off_csi"
            execute_branch(
                name=off_name,
                source=conditions[input_snr]["source_ON"],
                layout=conditions[input_snr]["protocol_layout"],
                mode="Mode-B", input_snr=input_snr, phase_mode="off",
                raw_vector=own_raw,
                csi_vector=Path(native_off["phase_vectors"]["csi"]["path"]),
            )

        with (output_root / "s1d_support_summary.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(summary_rows[0]))
            writer.writeheader()
            writer.writerows(summary_rows)
        with (output_root / "s1d_selected_group_cells.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(all_cell_diagnostics[0]))
            writer.writeheader()
            writer.writerows(all_cell_diagnostics)
        analysis = {
            "status": "COMPUTED_FROM_NATIVE_PRODUCTION_SELECTED_CELLS",
            "independent_gate_recompute_matches_production": True,
            "selected_group": SELECTED_GROUP,
            "quality_gate": manifest["frozen_quality_gates"],
            "support_summary": summary_rows,
            "limitations": [
                "Single seed and one period; this is a local causal sensitivity diagnostic, not a task-performance comparison.",
                "Cross-SNR vectors and paired OFF vector are evaluator-side interventions, not online/deployment methods.",
                "The rebuilt binary differs from the unavailable S1c binary; all S1d conditions are matched within the rebuilt binary.",
            ],
        }
        s1c.write_json(output_root / "s1d_analysis.json", analysis)
        manifest["analysis"] = analysis
        manifest["run_status"] = "same_binary_period5_2x3_production_passed"
        manifest["gpu_snapshot_after"] = s1c.gpu_snapshot()
        s1c.write_json(manifest_path, manifest)
        print(json.dumps({
            "run_status": manifest["run_status"],
            "support_summary": summary_rows,
            "manifest": str(manifest_path),
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        manifest["run_status"] = "failed_closed"
        manifest["failure"] = f"{type(exc).__name__}: {exc}"
        manifest["gpu_snapshot_after"] = s1c.gpu_snapshot()
        s1c.write_json(manifest_path, manifest)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
