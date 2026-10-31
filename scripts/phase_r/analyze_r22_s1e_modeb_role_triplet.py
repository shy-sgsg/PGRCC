#!/usr/bin/env python3
"""Offline evaluator-only analysis for the R22-S1e ON/OFF/TO replay."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Any


ROLES = ("ON", "OFF", "TO")
STAGES = (
    "raw_dbs_pre_range_phase",
    "csi_dbs_pre_range_phase",
    "csi_post_range_phase_pre_c4_adapter",
)
RANGE_START = 1065
RANGE_END = 1197
AZIMUTH_INDEX = 52
BEAM_ID = 50
SOURCE_PERIOD_ID = 4
PRODUCTION_PERIOD_ID = "1"


def mean(values: list[float]) -> float:
    if not values:
        raise ValueError("cannot average an empty set")
    return sum(values) / len(values)


def wrapped_delta(first: float, second: float) -> float:
    return math.atan2(math.sin(first - second), math.cos(first - second))


def read_complex_rows(path: Path) -> dict[str, dict[str, dict[int, complex]]]:
    fields = {"f1": ("f1_real", "f1_imag"), "f2": ("f2_real", "f2_imag")}
    result = {name: {} for name in fields}
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    for stage in STAGES:
        selected = [
            row for row in rows
            if row.get("stage") == stage
            and row.get("period_id") == PRODUCTION_PERIOD_ID
            and row.get("beam_id") == str(BEAM_ID)
            and row.get("azimuth_index") == str(AZIMUTH_INDEX)
            and RANGE_START <= int(row["range_index"]) <= RANGE_END
        ]
        expected = RANGE_END - RANGE_START + 1
        ranges = {int(row["range_index"]) for row in selected}
        if len(selected) != expected or len(ranges) != expected:
            raise ValueError(f"{path}: {stage} has {len(selected)} rows/{len(ranges)} bins, expected {expected}")
        for name, (real_key, imag_key) in fields.items():
            result[name][stage] = {
                int(row["range_index"]): complex(float(row[real_key]), float(row[imag_key]))
                for row in selected
            }
    return result


def read_phase(path: Path) -> tuple[float, ...]:
    raw = path.read_bytes()
    if len(raw) != 4096 * 4:
        raise ValueError(f"CSI phase vector must contain 4096 float32 values: {path}")
    values = struct.unpack("<4096f", raw)
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"non-finite phase value in {path}")
    return values


def relative_l2(values: list[complex], reference: list[complex]) -> float:
    denominator = math.sqrt(sum(abs(value) ** 2 for value in reference))
    if denominator <= 0.0:
        raise ValueError("relative L2 reference norm is zero")
    return math.sqrt(sum(abs(value) ** 2 for value in values)) / denominator


def energy_summary(values: dict[int, complex], target_bins: set[int]) -> dict[str, Any]:
    target = sorted(index for index in target_bins if index in values)
    other = sorted(index for index in values if index not in target_bins)
    total_energy = sum(abs(value) ** 2 for value in values.values())
    target_energy = sum(abs(values[index]) ** 2 for index in target)
    return {
        "target_truth_range_bins": len(target),
        "other_range_bins": len(other),
        "target_mean_magnitude": mean([abs(values[index]) for index in target]),
        "other_mean_magnitude": mean([abs(values[index]) for index in other]),
        "target_energy_fraction": target_energy / total_energy if total_energy > 0.0 else None,
        "total_energy": total_energy,
    }


def phase_delta_summary(first: tuple[float, ...], second: tuple[float, ...], target_bins: set[int]) -> dict[str, Any]:
    indexes = range(RANGE_START, RANGE_END + 1)
    deltas = {index: abs(wrapped_delta(first[index], second[index])) for index in indexes}
    targets = [index for index in deltas if index in target_bins]
    others = [index for index in deltas if index not in target_bins]
    return {
        "all_133_bins_mean_abs_wrapped_rad": mean(list(deltas.values())),
        "truth_target_bins_count": len(targets),
        "truth_target_bins_mean_abs_wrapped_rad": mean([deltas[index] for index in targets]),
        "other_bins_mean_abs_wrapped_rad": mean([deltas[index] for index in others]),
        "max_abs_wrapped_rad": max(deltas.values()),
        "per_range_bin_abs_wrapped_rad": deltas,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--case-root", type=Path, required=True)
    args = parser.parse_args()
    output_root = args.output_root.resolve()
    case_root = args.case_root.resolve()
    manifest_path = output_root / "s1e_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    truth_path = case_root / "scenes/A1_ON/truth/moving_target_truth.csv"
    with truth_path.open(newline="", encoding="utf-8") as stream:
        truth_rows = list(csv.DictReader(stream))
    target_bins = {
        int(row["range_bin"])
        for row in truth_rows
        if row.get("period_id") == str(SOURCE_PERIOD_ID)
        and row.get("beam_id") == str(BEAM_ID)
        and row.get("visible") == "1"
        and RANGE_START <= int(row["range_bin"]) <= RANGE_END
    }
    if not target_bins:
        raise ValueError("no evaluator-only target truth range bins overlap the selected group")

    role_data: dict[str, dict[str, Any]] = {}
    for branch in manifest["branches"]:
        role = branch["role"]
        if branch.get("production_status") != "passed" or branch.get("production_returncode") != 0:
            raise ValueError(f"Production did not pass for role {role}")
        trace_path = Path(branch["record"]["result_dir"]) / "production_phase_boundary_trace.csv"
        debug_root = Path(branch["record"]["result_dir"]) / "debug"
        phase_path = debug_root / "paired_range_phase_csi_period050_beam050.f32"
        with (debug_root / "cfar_geometry_diagnostics.csv").open(newline="", encoding="utf-8") as stream:
            cfar_rows = list(csv.DictReader(stream))
        role_data[role] = {
            "branch": branch,
            "trace": read_complex_rows(trace_path),
            "phase": read_phase(phase_path),
            "trace_path": str(trace_path),
            "phase_path": str(phase_path),
            "cfar_rows": cfar_rows,
        }
    if set(role_data) != set(ROLES):
        raise ValueError(f"expected ON/OFF/TO, found {sorted(role_data)}")

    closure: dict[str, Any] = {}
    cell_rows: list[dict[str, Any]] = []
    for stage in STAGES:
        closure[stage] = {}
        for channel in ("f1", "f2"):
            on = role_data["ON"]["trace"][channel][stage]
            off = role_data["OFF"]["trace"][channel][stage]
            to = role_data["TO"]["trace"][channel][stage]
            residual = {index: on[index] - off[index] - to[index] for index in on}
            closure[stage][channel] = {
                "relative_l2_on_minus_off_minus_to_over_on": relative_l2(
                    list(residual.values()), list(on.values())
                ),
                "residual_l2": math.sqrt(sum(abs(value) ** 2 for value in residual.values())),
                "on_l2": math.sqrt(sum(abs(value) ** 2 for value in on.values())),
            }
            for index in sorted(on):
                cell_rows.append({
                    "stage": stage, "channel": channel, "range_bin": index,
                    "truth_target_range_bin": index in target_bins,
                    "on_real": on[index].real, "on_imag": on[index].imag,
                    "off_real": off[index].real, "off_imag": off[index].imag,
                    "to_real": to[index].real, "to_imag": to[index].imag,
                    "on_minus_off_minus_to_real": residual[index].real,
                    "on_minus_off_minus_to_imag": residual[index].imag,
                })

    to_energy = {
        stage: {
            channel: energy_summary(role_data["TO"]["trace"][channel][stage], target_bins)
            for channel in ("f1", "f2")
        }
        for stage in STAGES
    }
    phase_deltas = {
        "ON_vs_OFF": phase_delta_summary(role_data["ON"]["phase"], role_data["OFF"]["phase"], target_bins),
        "ON_vs_TO": phase_delta_summary(role_data["ON"]["phase"], role_data["TO"]["phase"], target_bins),
        "OFF_vs_TO": phase_delta_summary(role_data["OFF"]["phase"], role_data["TO"]["phase"], target_bins),
    }
    supports = {}
    membership: dict[str, dict[str, Any]] = {}
    for role in ROLES:
        selected = role_data[role]["branch"].get("selected_group", {})
        gamma = selected.get("gamma_row", {})
        diagnostics = selected.get("diagnostics", [])
        retained = {
            int(row["range_index"])
            for row in diagnostics if row.get("retained_by_frozen_gate") is True
        }
        supports[role] = {
            "production_status": role_data[role]["branch"].get("production_status"),
            "gamma_status": gamma.get("status"),
            "support_count": gamma.get("support_count"),
            "min_support": 8,
            "support_recomputed": selected.get("support_recomputed"),
            "support_matches_production": selected.get("support_matches_production"),
            "truth_used_in_estimator": gamma.get("truth_used_in_estimator"),
        }
        membership[role] = {
            "retained_range_bin_count": len(retained),
            "retained_range_bins": sorted(retained),
            "retained_truth_target_range_bins": sorted(retained & target_bins),
            "target_truth_range_bins_rejected": sorted(target_bins - retained),
        }

    membership_comparison: dict[str, Any] = {}
    for first, second in (("ON", "OFF"), ("ON", "TO"), ("OFF", "TO")):
        first_set = set(membership[first]["retained_range_bins"])
        second_set = set(membership[second]["retained_range_bins"])
        union = first_set | second_set
        membership_comparison[f"{first}_vs_{second}"] = {
            "intersection_count": len(first_set & second_set),
            "first_only_count": len(first_set - second_set),
            "second_only_count": len(second_set - first_set),
            "jaccard": len(first_set & second_set) / len(union) if union else None,
        }

    cfar_geometry: dict[str, Any] = {}
    for role in ROLES:
        rows = role_data[role]["cfar_rows"]
        cfar_geometry[role] = {
            "row_count": len(rows),
            "branches": [{
                "branch": row.get("branch"),
                "valid_cut_count": row.get("valid_cut_count"),
                "threshold_test_count": row.get("threshold_test_count"),
                "hit_cut_count": row.get("hit_cut_count"),
                "hit_rate": row.get("hit_rate"),
                "false_counts_status": row.get("false_counts_status"),
            } for row in rows],
        }

    analysis = {
        "status": "COMPUTED_FROM_THREE_MATCHED_NATIVE_CUDA_PRODUCTION_ROLES",
        "condition": manifest["condition"],
        "source_period_index_zero_based": SOURCE_PERIOD_ID,
        "production_period_id": PRODUCTION_PERIOD_ID,
        "selected_group": manifest["selected_group"],
        "evaluator_only_truth": {
            "path": str(truth_path),
            "period_id": SOURCE_PERIOD_ID,
            "beam_id": BEAM_ID,
            "truth_range_bins_inside_selected_group": sorted(target_bins),
            "count": len(target_bins),
            "truth_used_in_estimator": False,
        },
        "support": supports,
        "support_membership": membership,
        "support_membership_comparison": membership_comparison,
        "cfar_geometry_diagnostic_only": cfar_geometry,
        "ON_minus_OFF_minus_TO_complex_closure": closure,
        "target_only_component_energy_by_stage": to_energy,
        "csi_phase_vector_role_differences": phase_deltas,
        "interpretation_limits": [
            "This is a single seed, one period, one beam and one reference group; it is a mechanism diagnostic only.",
            "Target truth was joined only after Production for range-bin labels; no truth was passed to Production.",
            "Different per-role CSI range-phase vectors make post-correction ON/OFF/TO values non-additive; pre-correction closure is the direct component check.",
            "A support-count change is not task-level detection or parameter-estimation recovery.",
        ],
    }
    analysis_path = output_root / "s1e_analysis.json"
    analysis_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cell_path = output_root / "s1e_complex_cell_audit.csv"
    with cell_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(cell_rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(cell_rows)
    manifest["offline_analysis"] = {
        "status": analysis["status"],
        "analysis_json": str(analysis_path),
        "analysis_sha256": hashlib.sha256(analysis_path.read_bytes()).hexdigest(),
        "complex_cell_audit_csv": str(cell_path),
        "complex_cell_audit_sha256": hashlib.sha256(cell_path.read_bytes()).hexdigest(),
        "truth_path_sha256": hashlib.sha256(truth_path.read_bytes()).hexdigest(),
        "truth_used_in_estimator": False,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "support": supports,
        "support_membership": membership,
        "support_membership_comparison": membership_comparison,
        "closure": closure,
        "TO_energy": to_energy,
        "ON_vs_OFF_phase_delta": {
            k: v for k, v in phase_deltas["ON_vs_OFF"].items()
            if k != "per_range_bin_abs_wrapped_rad"
        },
        "analysis": str(analysis_path),
        "audit_csv": str(cell_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
