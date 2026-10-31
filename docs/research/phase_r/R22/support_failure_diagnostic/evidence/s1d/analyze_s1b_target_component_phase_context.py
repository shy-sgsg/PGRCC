#!/usr/bin/env python3
"""Audit target-only signal localization and paired CSI phase changes in S1b."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import struct
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
S1B_PUBLIC_MANIFEST = ROOT.parent / "s1b" / "s1b_manifest.json"
S1B_SOURCE_MANIFEST = ROOT / "s1b_context_source_manifest.json"
TARGET_AUDIT = ROOT.parent / "s1c" / "v2" / "s1c_target_overlap_audit.csv"
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
PERIOD_ID = 5
PHASE_COUNT = 4096


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def wrapped_delta(first: float, second: float) -> float:
    return math.atan2(math.sin(first - second), math.cos(first - second))


def read_phase(path: Path) -> tuple[float, ...]:
    raw = path.read_bytes()
    if len(raw) != PHASE_COUNT * 4:
        raise ValueError(f"expected {PHASE_COUNT} float32 values: {path}")
    values = struct.unpack(f"<{PHASE_COUNT}f", raw)
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"non-finite phase value in {path}")
    return values


def mean(values: list[float]) -> float:
    if not values:
        raise ValueError("cannot summarize an empty set")
    return sum(values) / len(values)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot take a percentile of an empty set")
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def phase_summary(values: dict[int, float], target_bins: set[int]) -> dict[str, Any]:
    return {
        "all_133_bins": {
            "count": len(values),
            "mean_abs_wrapped_rad": mean(list(values.values())),
            "count_over_0_35_rad_descriptive_only": sum(v > 0.35 for v in values.values()),
        },
        "truth_target_bins": {
            "count": len(target_bins),
            "mean_abs_wrapped_rad": mean([values[i] for i in target_bins]),
            "count_over_0_35_rad_descriptive_only": sum(values[i] > 0.35 for i in target_bins),
        },
        "other_bins": {
            "count": len(values) - len(target_bins),
            "mean_abs_wrapped_rad": mean([values[i] for i in values if i not in target_bins]),
            "count_over_0_35_rad_descriptive_only": sum(
                values[i] > 0.35 for i in values if i not in target_bins
            ),
        },
    }


def pearson(first: list[float], second: list[float]) -> float | None:
    first_mean, second_mean = mean(first), mean(second)
    numerator = sum((x - first_mean) * (y - second_mean) for x, y in zip(first, second))
    first_norm = math.sqrt(sum((x - first_mean) ** 2 for x in first))
    second_norm = math.sqrt(sum((y - second_mean) ** 2 for y in second))
    if first_norm == 0.0 or second_norm == 0.0:
        return None
    return numerator / (first_norm * second_norm)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--s1b-root",
        type=Path,
        default=None,
        help="optional local S1b output root; if present, verifies the original Stage2 period-4 inputs",
    )
    args = parser.parse_args()

    public_manifest = json.loads(S1B_PUBLIC_MANIFEST.read_text(encoding="utf-8"))
    source_manifest = json.loads(S1B_SOURCE_MANIFEST.read_text(encoding="utf-8"))
    s1d_manifest = json.loads((ROOT / "evidence_manifest.json").read_text(encoding="utf-8"))
    role_manifests = {row["role"]: row for row in public_manifest["branches"]}
    source_case_root = Path(public_manifest["case_root"])
    if set(role_manifests) != set(ROLES):
        raise ValueError("S1b manifest must contain ON, OFF and TO branches")
    if (
        source_manifest["execution_git_commit"] != public_manifest["git_commit"]
        or source_manifest["production_binary_sha256"] != public_manifest["production_binary_sha256"]
    ):
        raise ValueError("compact S1b source manifest identity mismatch")
    source_records = {
        (row["role"], Path(row["published_path"]).name): row
        for row in source_manifest["files"]
    }

    target_bins: set[int] = set()
    target_ids_by_bin: dict[int, set[str]] = {}
    for row in read_csv(TARGET_AUDIT):
        if (
            int(row["period_index_zero_based"]) == PERIOD_ID - 1
            and int(row["beam_id"]) == BEAM_ID
            and RANGE_START <= int(row["range_bin"]) <= RANGE_END
        ):
            bin_index = int(row["range_bin"])
            target_bins.add(bin_index)
            target_ids_by_bin.setdefault(bin_index, set()).add(row["target_id"])
    if len(target_bins) != 11:
        raise ValueError(f"expected 11 truth target bins, got {len(target_bins)}")

    traces: dict[str, dict[str, dict[int, dict[str, float]]]] = {}
    vectors: dict[str, dict[str, tuple[float, ...]]] = {"raw": {}, "csi": {}}
    input_identity: dict[str, dict[str, Any]] = {}
    local_input_verification: dict[str, Any] = {"status": "NOT_AVAILABLE", "roles": {}}
    s1b_root = args.s1b_root.resolve() if args.s1b_root else None
    if s1b_root is None:
        candidate = Path("outputs/r22_s1b_phase_boundary_20261029").resolve()
        if candidate.is_dir():
            s1b_root = candidate
    if s1b_root is not None:
        local_input_verification["status"] = "VERIFIED_MATCH"
    expected_ranges = set(range(RANGE_START, RANGE_END + 1))

    for role in ROLES:
        public_period = role_manifests[role]["input_periods"][PERIOD_ID - 1]
        input_identity[role] = {
            "case_relative_path": Path(public_period["path"]).relative_to(source_case_root).as_posix(),
            "bytes_from_public_manifest": public_period["bytes"],
            "sha256_from_public_manifest": public_period["sha256"],
        }
        if s1b_root is not None:
            branch_manifest_path = (
                s1b_root / "snr30" / "production" / f"C4_modea_S1b_{role}" / "branch_manifest.json"
            )
            branch_manifest = json.loads(branch_manifest_path.read_text(encoding="utf-8"))
            if branch_manifest["role"] != role or branch_manifest["period_count_expected"] != 10:
                raise ValueError(f"unexpected S1b branch identity: {branch_manifest_path}")
            input_period = Path(branch_manifest["input_path"]) / "period_0004.bin"
            actual_hash = sha256(input_period)
            matched = (
                actual_hash == public_period["sha256"]
                and input_period.stat().st_size == public_period["bytes"]
            )
            local_input_verification["roles"][role] = {
                "bytes": input_period.stat().st_size,
                "actual_sha256": actual_hash,
                "matches_public_manifest": matched,
            }
            if not matched:
                local_input_verification["status"] = "MISMATCH"
                raise ValueError(f"S1b input period identity mismatch for {role}")

        published_dir = ROOT / "s1b_context_source" / role
        trace_path = published_dir / "production_phase_boundary_trace.csv"
        trace_record = source_records.get((role, trace_path.name))
        if trace_record is None or sha256(trace_path) != trace_record["published_sha256"]:
            raise ValueError(f"published S1b trace identity mismatch for {role}")
        all_trace_rows = read_csv(trace_path)
        traces[role] = {}
        for stage in STAGES:
            selected = [
                row for row in all_trace_rows
                if row["stage"] == stage
                and int(row["period_id"]) == PERIOD_ID
                and row["result_id"] == "GMTI05"
                and int(row["beam_id"]) == BEAM_ID
                and int(row["azimuth_index"]) == AZIMUTH_INDEX
                and RANGE_START <= int(row["range_index"]) <= RANGE_END
            ]
            by_range = {int(row["range_index"]): row for row in selected}
            if len(selected) != len(expected_ranges) or set(by_range) != expected_ranges:
                raise ValueError(f"incomplete/duplicate stage trace for {role}/{stage}")
            traces[role][stage] = {
                range_index: {
                    key: float(row[key])
                    for key in (
                        "f1_real", "f1_imag", "f2_real", "f2_imag", "range_phase_rad"
                    )
                }
                for range_index, row in by_range.items()
            }

        debug_dir = published_dir / "debug"
        vectors["raw"][role] = read_phase(
            debug_dir / "paired_range_phase_raw_period050_beam050.f32"
        )
        vectors["csi"][role] = read_phase(
            debug_dir / "paired_range_phase_csi_period050_beam050.f32"
        )
        for vector_kind, filename in (
            ("raw", "paired_range_phase_raw_period050_beam050.f32"),
            ("csi", "paired_range_phase_csi_period050_beam050.f32"),
        ):
            record = source_records.get((role, filename))
            vector_path = debug_dir / filename
            if record is None or sha256(vector_path) != record["published_sha256"]:
                raise ValueError(f"published S1b {vector_kind} phase identity mismatch for {role}")

    s1d_input_hashes = s1d_manifest["input_identities"]
    s1d_crosscheck = {
        "ON_matches_S1d_SNR30_input": input_identity["ON"]["sha256_from_public_manifest"]
        == s1d_input_hashes["seed1932001_snr30_gmti05"],
        "OFF_matches_S1d_target_free_input": input_identity["OFF"]["sha256_from_public_manifest"]
        == s1d_input_hashes["paired_target_free_off_gmti05"],
    }
    if not all(s1d_crosscheck.values()):
        raise ValueError("S1b and S1d ON/OFF Stage2 input identities do not match")

    phase_deltas: dict[str, dict[str, float]] = {}
    for vector_kind in ("raw", "csi"):
        phase_deltas[f"{vector_kind}_ON_vs_OFF"] = {
            i: abs(wrapped_delta(vectors[vector_kind]["ON"][i], vectors[vector_kind]["OFF"][i]))
            for i in expected_ranges
        }
        phase_deltas[f"{vector_kind}_ON_vs_TO"] = {
            i: abs(wrapped_delta(vectors[vector_kind]["ON"][i], vectors[vector_kind]["TO"][i]))
            for i in expected_ranges
        }
        phase_deltas[f"{vector_kind}_OFF_vs_TO"] = {
            i: abs(wrapped_delta(vectors[vector_kind]["OFF"][i], vectors[vector_kind]["TO"][i]))
            for i in expected_ranges
        }

    closure: dict[str, Any] = {}
    per_bin: dict[int, dict[str, Any]] = {i: {} for i in expected_ranges}
    for stage in STAGES:
        closure[stage] = {}
        for channel in ("f1", "f2"):
            residuals: dict[int, float] = {}
            on_magnitudes: dict[int, float] = {}
            to_magnitudes: dict[int, float] = {}
            for i in expected_ranges:
                values = {
                    role: complex(
                        traces[role][stage][i][f"{channel}_real"],
                        traces[role][stage][i][f"{channel}_imag"],
                    )
                    for role in ROLES
                }
                if not all(math.isfinite(v.real) and math.isfinite(v.imag) for v in values.values()):
                    raise ValueError(f"non-finite {channel.upper()} sample at {stage}/{i}")
                residuals[i] = abs(values["ON"] - values["OFF"] - values["TO"])
                on_magnitudes[i] = abs(values["ON"])
                to_magnitudes[i] = abs(values["TO"])
                per_bin[i][f"{stage}_{channel}_on_real"] = values["ON"].real
                per_bin[i][f"{stage}_{channel}_on_imag"] = values["ON"].imag
                per_bin[i][f"{stage}_{channel}_off_real"] = values["OFF"].real
                per_bin[i][f"{stage}_{channel}_off_imag"] = values["OFF"].imag
                per_bin[i][f"{stage}_{channel}_to_real"] = values["TO"].real
                per_bin[i][f"{stage}_{channel}_to_imag"] = values["TO"].imag
                per_bin[i][f"{stage}_{channel}_closure_abs"] = residuals[i]
                per_bin[i][f"{stage}_{channel}_to_abs"] = to_magnitudes[i]
            residual_energy = {i: residuals[i] ** 2 for i in expected_ranges}
            closure[stage][channel.upper()] = {
                "relative_l2_to_ON": math.sqrt(sum(residual_energy.values()))
                / math.sqrt(sum(on_magnitudes[i] ** 2 for i in expected_ranges)),
                "mean_TO_abs_target_bins": mean([to_magnitudes[i] for i in target_bins]),
                "mean_TO_abs_other_bins": mean([to_magnitudes[i] for i in expected_ranges if i not in target_bins]),
                "median_TO_abs_target_bins": statistics.median([to_magnitudes[i] for i in target_bins]),
                "median_TO_abs_other_bins": statistics.median([to_magnitudes[i] for i in expected_ranges if i not in target_bins]),
                "target_bin_share_of_TO_energy": sum(to_magnitudes[i] ** 2 for i in target_bins)
                / sum(to_magnitudes[i] ** 2 for i in expected_ranges),
                "target_bin_share_of_closure_residual_energy": sum(
                    residual_energy[i] for i in target_bins
                ) / sum(residual_energy.values()),
            }

    phase_summary_json = {
        label: phase_summary(values, target_bins)
        for label, values in phase_deltas.items()
    }
    raw_to_f1 = {
        i: per_bin[i][f"{STAGES[0]}_f1_to_abs"] for i in expected_ranges
    }
    raw_to_f2 = {
        i: per_bin[i][f"{STAGES[0]}_f2_to_abs"] for i in expected_ranges
    }
    phase_to_energy_correlations = {}
    for vector_kind in ("raw", "csi"):
        delta = phase_deltas[f"{vector_kind}_ON_vs_OFF"]
        phase_to_energy_correlations[vector_kind] = {
            "pearson_abs_ON_OFF_phase_delta_vs_TO_F1_magnitude": pearson(
                [raw_to_f1[i] for i in expected_ranges], [delta[i] for i in expected_ranges]
            ),
            "pearson_abs_ON_OFF_phase_delta_vs_TO_F2_magnitude": pearson(
                [raw_to_f2[i] for i in expected_ranges], [delta[i] for i in expected_ranges]
            ),
        }

    csv_path = ROOT / "s1b_target_component_phase_context.csv"
    columns = ["range_bin", "is_truth_target_bin", "target_ids"]
    for vector_kind in ("raw", "csi"):
        for role in ROLES:
            columns.append(f"{vector_kind}_phase_{role}_rad")
        for comparison in ("ON_vs_OFF", "ON_vs_TO", "OFF_vs_TO"):
            columns.append(f"{vector_kind}_abs_delta_{comparison}_rad")
    for stage in STAGES:
        for channel in ("f1", "f2"):
            columns.extend([
                f"{stage}_{channel}_on_real", f"{stage}_{channel}_on_imag",
                f"{stage}_{channel}_off_real", f"{stage}_{channel}_off_imag",
                f"{stage}_{channel}_to_real", f"{stage}_{channel}_to_imag",
                f"{stage}_{channel}_closure_abs", f"{stage}_{channel}_to_abs",
            ])
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for i in sorted(expected_ranges):
            row: dict[str, Any] = {
                "range_bin": i,
                "is_truth_target_bin": i in target_bins,
                "target_ids": ";".join(sorted(target_ids_by_bin.get(i, set()))),
            }
            for vector_kind in ("raw", "csi"):
                for role in ROLES:
                    row[f"{vector_kind}_phase_{role}_rad"] = vectors[vector_kind][role][i]
                for comparison in ("ON_vs_OFF", "ON_vs_TO", "OFF_vs_TO"):
                    row[f"{vector_kind}_abs_delta_{comparison}_rad"] = phase_deltas[
                        f"{vector_kind}_{comparison}"
                    ][i]
            row.update(per_bin[i])
            writer.writerow(row)

    summary = {
        "analysis": "S1b ON/OFF/TO per-bin component closure and phase-vector context",
        "source_stage": "R22-S1b Mode-A reference-assisted diagnostic",
        "period_id": PERIOD_ID,
        "result_id": "GMTI05",
        "beam_id": BEAM_ID,
        "azimuth_index": AZIMUTH_INDEX,
        "range_bins": [RANGE_START, RANGE_END],
        "target_truth_bins": sorted(target_bins),
        "target_truth_bin_count": len(target_bins),
        "execution_git_commit": public_manifest["git_commit"],
        "production_binary_sha256": public_manifest["production_binary_sha256"],
        "source_worktree_was_dirty": bool(public_manifest.get("worktree_status", "").strip()),
        "stage2_regenerated_for_this_analysis": False,
        "input_period4_identity": input_identity,
        "crosscheck_against_s1d_input_hashes": s1d_crosscheck,
        "local_input_period4_sha_verification": local_input_verification,
        "published_source_files": source_manifest["files"],
        "per_stage_complex_closure": closure,
        "phase_vector_differences": phase_summary_json,
        "pearson_context_only_phase_delta_vs_TO_magnitude": phase_to_energy_correlations,
        "gate_threshold_note": "0.35 rad counts are descriptive pairwise vector differences; C4 gate uses per-cell residual from the group phase center",
        "cross_stage_limit": "S1b Mode-A binary/source is not identical to S1d Mode-B execution; use this as signal-component context, not a replacement for the S1d Mode-B causal result",
        "raw_stage2_inputs_in_git": False,
        "output_csv": csv_path.name,
    }
    summary_path = ROOT / "s1b_target_component_phase_context_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output_csv": csv_path.name,
        "summary_json": summary_path.name,
        "target_truth_bins": sorted(target_bins),
        "closure": closure,
        "phase_vector_differences": phase_summary_json,
        "pearson_context_only_phase_delta_vs_TO_magnitude": phase_to_energy_correlations,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
