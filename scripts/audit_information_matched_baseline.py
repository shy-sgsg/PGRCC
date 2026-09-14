#!/usr/bin/env python3
"""Run the information-matched M0--M3 offline baseline matrix.

The matrix uses the existing V2 physical replay implementation and keeps the
comparison boundary explicit:

* M0 production is the deployed Current F1/F2 CSI replay, including its
  target-observation P38 path.
* M0 controlled is the same Current cancellation with background-only P38 and
  is the fair reference for the scientific rows.
* M1 is a two-channel adaptive row-complex LS/Wiener cancellation.
* M2 is the pair-fused equivalent two-channel F1/F2 phase-only cancellation.
* M3 is the native four-channel physical JDL/STAP reference and stays offline.

No AI model is trained or used.  Generated BINs are removed after each case;
the compact result retains the input hashes and all evaluation definitions.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_baseline_v2 as v2


METHOD_SPECS = [
    {
        "matrix_id": "M0_production_Current_F1F2_CSI",
        "implementation": "Current legacy CSI [production replay]",
        "space": "F1/F2 pair-fused two-channel",
        "algorithm": "production Current legacy CSI",
        "fair_reference": False,
    },
    {
        "matrix_id": "M0_controlled_Current_F1F2_CSI",
        "implementation": "Current legacy CSI [scientific controlled]",
        "space": "F1/F2 pair-fused two-channel",
        "algorithm": "Current legacy CSI with background-only P38",
        "fair_reference": True,
    },
    {
        "matrix_id": "M1_F1F2_adaptive_row_LS",
        "implementation": "Row complex LS / Wiener [scientific controlled]",
        "space": "F1/F2 pair-fused two-channel",
        "algorithm": "two-channel adaptive row-complex LS/Wiener",
        "fair_reference": True,
    },
    {
        "matrix_id": "M2_pair_fused_equivalent_2ch",
        "implementation": "Phase-only CSI [scientific controlled]",
        "space": "four-channel input forced through (1,3)/(2,4) pair fusion",
        "algorithm": "pair-fused equivalent two-channel phase-only CSI",
        "fair_reference": True,
    },
    {
        "matrix_id": "M3_native_4ch_JDL_STAP_reference",
        "implementation": "Corrected JDL physical",
        "space": "native four-channel × three Doppler rows",
        "algorithm": "native four-channel JDL/MVDR/STAP reference",
        "fair_reference": True,
    },
]

METRIC_FIELDS = (
    "residual_suppression_dB",
    "output_SCNR_dB",
    "SCNR_improvement_dB",
    "target_loss_dB",
    "background_Pfa",
    "target_detected",
    "target_signal_power_after",
    "background_power_after_roi",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_default(value: object) -> object:
    if hasattr(value, "item"):
        return value.item()  # type: ignore[no-any-return]
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"unsupported JSON value {type(value)!r}")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=_json_default, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    materialized = list(rows)
    if not materialized:
        return
    fields: List[str] = []
    for row in materialized:
        for key in row:
            if key not in fields:
                fields.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(materialized)


def _git_output(*args: str) -> str:
    completed = subprocess.run(
        ["git", "--git-dir=.git-real", "--work-tree=.", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return completed.stdout.strip()


def _provenance(command: Sequence[str], base_config: Path) -> Dict[str, object]:
    tracked_status = _git_output("status", "--short", "--untracked-files=no")
    binary = ROOT / "build/simulate_stage2_statistical"
    return {
        "working_directory": str(ROOT),
        "command": list(command),
        "source_commit": _git_output("rev-parse", "HEAD"),
        "tracked_worktree_status": tracked_status,
        "worktree_dirty_before": bool(tracked_status),
        "untracked_artifacts_excluded_from_dirty_flag": True,
        "base_config": str(base_config),
        "base_config_sha256": _sha256(base_config),
        "simulator_executable": str(binary),
        "simulator_executable_sha256": _sha256(binary) if binary.exists() else None,
        "simulator_executable_bytes": binary.stat().st_size if binary.exists() else None,
        "python": platform.python_version(),
        "numpy": v2.np.__version__,
        "cmake_build_type": "Release" if (ROOT / "build/CMakeCache.txt").exists() else "unknown",
        "gpu_probe": "recorded separately; offline matrix does not claim CUDA evidence",
    }


def _safe_level(value: float) -> str:
    return str(value).replace("-", "m").replace(".", "p")


def _cases(suite: Dict[str, object], seeds: Sequence[int], velocities: Sequence[float]) -> List[Dict[str, object]]:
    base = v2._base_case(suite)
    cases: List[Dict[str, object]] = []
    for velocity in velocities:
        for seed in seeds:
            case = copy.deepcopy(base)
            case["case_id"] = f"information_matched_v{_safe_level(float(velocity))}_s{int(seed)}"
            case["target_id"] = f"IM_TARGET_v{_safe_level(float(velocity))}_s{int(seed)}"
            case["seed"] = int(seed)
            case["velocity_mode"] = "radial"
            case["radial_speed_mps"] = float(velocity)
            case["factor"] = "information_matched_velocity"
            case["factor_family"] = "information_matched"
            case["factor_level"] = float(velocity)
            case["label"] = f"common scene radial velocity {float(velocity):g} m/s, seed {int(seed)}"
            case["use_paired_background"] = True
            cases.append(case)
    return cases


def _run_case(
    base_config: Path,
    suite: Dict[str, object],
    case: Dict[str, object],
    method_config: Dict[str, object],
) -> List[Dict[str, object]]:
    paths = v2.v1.prepare_case_data(base_config, suite, case, keep_data=False)
    try:
        data = v2.v1.load_case_data(case, paths)
        rows: List[Dict[str, object]] = []
        for spec in METHOD_SPECS:
            started = time.perf_counter()
            implementation = str(spec["implementation"])
            if implementation in v2.STRICT_METHODS:
                metrics, details = v2.run_strict_method(
                    data,
                    implementation,
                    method_config=method_config,
                    detection_threshold_scale=1.0,
                )
            else:
                metrics, details = v2.run_adaptive_method(
                    data,
                    implementation,
                    method_config,
                    detection_threshold_scale=1.0,
                )
            row = v2.base_result_row(
                data,
                implementation,
                metrics,
                details,
                1000.0 * (time.perf_counter() - started),
            )
            row.update(
                {
                    "matrix_id": spec["matrix_id"],
                    "input_space": spec["space"],
                    "algorithm_definition": spec["algorithm"],
                    "fair_scientific_reference": bool(spec["fair_reference"]),
                    "ai_training": False,
                }
            )
            rows.append(row)
        return rows
    finally:
        v2.v1.cleanup_case(paths)


def _wilson(successes: int, trials: int) -> tuple[float, float]:
    if trials <= 0:
        return math.nan, math.nan
    p = successes / trials
    z = 1.959963984540054
    denominator = 1.0 + z * z / trials
    center = (p + z * z / (2.0 * trials)) / denominator
    half = z * math.sqrt(p * (1.0 - p) / trials + z * z / (4.0 * trials * trials)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def _aggregate(rows: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    groups: Dict[str, List[Mapping[str, object]]] = {}
    for row in rows:
        groups.setdefault(str(row["matrix_id"]), []).append(row)
    result: List[Dict[str, object]] = []
    for matrix_id, grouped in groups.items():
        summary: Dict[str, object] = {
            "matrix_id": matrix_id,
            "implementation": grouped[0]["method"],
            "input_space": grouped[0]["input_space"],
            "algorithm_definition": grouped[0]["algorithm_definition"],
            "n_cases": len(grouped),
            "n_seeds": len({int(row["seed"]) for row in grouped}),
        }
        for field in METRIC_FIELDS:
            values = [float(row[field]) for row in grouped if math.isfinite(float(row[field]))]
            summary[f"{field}_mean"] = sum(values) / len(values) if values else math.nan
            summary[f"{field}_std"] = (
                math.sqrt(sum((value - summary[f"{field}_mean"]) ** 2 for value in values) / (len(values) - 1))
                if len(values) >= 2 else math.nan
            )
        detections = [float(row["target_detected"]) for row in grouped if math.isfinite(float(row["target_detected"]))]
        successes = sum(value >= 0.5 for value in detections)
        low, high = _wilson(successes, len(detections))
        summary.update({"Pd_successes": successes, "Pd_trials": len(detections), "Pd_Wilson95_low": low, "Pd_Wilson95_high": high})
        result.append(summary)
    return result


def _attribution(rows: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    by_case = {(str(row["case_id"]), str(row["matrix_id"])): row for row in rows}
    baseline_id = "M0_controlled_Current_F1F2_CSI"
    output: List[Dict[str, object]] = []
    for row in rows:
        matrix_id = str(row["matrix_id"])
        if matrix_id == baseline_id:
            continue
        baseline = by_case.get((str(row["case_id"]), baseline_id))
        if baseline is None:
            continue
        record: Dict[str, object] = {
            "case_id": row["case_id"],
            "seed": row["seed"],
            "velocity_level_mps": row["factor_level"],
            "matrix_id": matrix_id,
            "reference_matrix_id": baseline_id,
            "comparison_interpretation": (
                "production-vs-controlled protocol effect"
                if matrix_id == "M0_production_Current_F1F2_CSI"
                else "scientific row delta versus controlled Current"
            ),
        }
        for field in METRIC_FIELDS:
            value = float(row[field])
            reference = float(baseline[field])
            record[f"delta_{field}"] = value - reference if math.isfinite(value) and math.isfinite(reference) else math.nan
        output.append(record)
    return output


def _pairwise_attribution(rows: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    """Emit the contrasts needed to separate the stated comparison layers."""

    pairs = (
        ("M1_F1F2_adaptive_row_LS", "M0_controlled_Current_F1F2_CSI", "two-channel algorithm delta"),
        ("M2_pair_fused_equivalent_2ch", "M0_controlled_Current_F1F2_CSI", "pair-fused two-channel delta"),
        ("M3_native_4ch_JDL_STAP_reference", "M2_pair_fused_equivalent_2ch", "native-4ch versus pair-fused composite delta"),
        ("M3_native_4ch_JDL_STAP_reference", "M1_F1F2_adaptive_row_LS", "native-4ch versus adaptive-2ch composite delta"),
    )
    by_case = {(str(row["case_id"]), str(row["matrix_id"])): row for row in rows}
    output: List[Dict[str, object]] = []
    for candidate, reference, interpretation in pairs:
        case_ids = sorted({str(row["case_id"]) for row in rows})
        for case_id in case_ids:
            candidate_row = by_case.get((case_id, candidate))
            reference_row = by_case.get((case_id, reference))
            if candidate_row is None or reference_row is None:
                continue
            record: Dict[str, object] = {
                "case_id": case_id,
                "seed": candidate_row["seed"],
                "velocity_level_mps": candidate_row["factor_level"],
                "candidate_matrix_id": candidate,
                "reference_matrix_id": reference,
                "comparison_interpretation": interpretation,
            }
            for field in METRIC_FIELDS:
                value = float(candidate_row[field])
                reference_value = float(reference_row[field])
                record[f"delta_{field}"] = (
                    value - reference_value
                    if math.isfinite(value) and math.isfinite(reference_value)
                    else math.nan
                )
            output.append(record)
    return output


def _pairwise_aggregate(rows: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    groups: Dict[tuple[str, str], List[Mapping[str, object]]] = {}
    for row in rows:
        key = (str(row["candidate_matrix_id"]), str(row["reference_matrix_id"]))
        groups.setdefault(key, []).append(row)
    output: List[Dict[str, object]] = []
    for (candidate, reference), grouped in groups.items():
        record: Dict[str, object] = {
            "candidate_matrix_id": candidate,
            "reference_matrix_id": reference,
            "comparison_interpretation": grouped[0]["comparison_interpretation"],
            "n_cases": len(grouped),
        }
        for field in METRIC_FIELDS:
            values = [
                float(row[f"delta_{field}"])
                for row in grouped
                if math.isfinite(float(row[f"delta_{field}"]))
            ]
            record[f"delta_{field}_mean"] = sum(values) / len(values) if values else math.nan
            record[f"delta_{field}_std"] = (
                math.sqrt(sum((value - record[f"delta_{field}_mean"]) ** 2 for value in values) / (len(values) - 1))
                if len(values) >= 2 else math.nan
            )
        output.append(record)
    return output


def run(output_dir: Path, suite_path: Path, seeds: Sequence[int], velocities: Sequence[float]) -> Dict[str, object]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    base_config, source_suite = v2.load_suite(suite_path)
    suite = copy.deepcopy(source_suite)
    suite["output_root"] = str(output_dir / "cases")
    cases = _cases(suite, seeds, velocities)
    method_config = dict(source_suite.get("method_config", {}))
    rows: List[Dict[str, object]] = []
    failures: List[Dict[str, object]] = []
    started = time.perf_counter()
    for index, case in enumerate(cases, start=1):
        print(json.dumps({"stage": "information_matched_case", "index": index, "total": len(cases), "case_id": case["case_id"]}, ensure_ascii=False), flush=True)
        try:
            rows.extend(_run_case(base_config, suite, case, method_config))
        except Exception as exc:  # keep all failures auditable and finish the matrix
            failures.append({"case_id": case["case_id"], "error": repr(exc)})
            print(f"[information-matched][ERR] {case['case_id']}: {exc}", file=sys.stderr, flush=True)
    _write_csv(output_dir / "information_matched_summary.csv", rows)
    _write_csv(output_dir / "information_matched_aggregate.csv", _aggregate(rows))
    _write_csv(output_dir / "information_matched_attribution.csv", _attribution(rows))
    pairwise_rows = _pairwise_attribution(rows)
    _write_csv(output_dir / "information_matched_pairwise_attribution.csv", pairwise_rows)
    _write_csv(output_dir / "information_matched_pairwise_aggregate.csv", _pairwise_aggregate(pairwise_rows))
    contract = {
        "schema": "unknown_system_error_information_matched_baseline_v1",
        "ai_training": False,
        "cases": [{"case_id": c["case_id"], "seed": c["seed"], "velocity_mps": c["radial_speed_mps"], "scene_label": c["label"]} for c in cases],
        "methods": METHOD_SPECS,
        "common_preprocessing": "same Stage2 raw paired scene, four-channel pulse compression, (1,3)/(2,4) fusion where applicable, same RD/ROI and GO-CFAR evaluator",
        "common_training_region": "configured rg_st..rg_ed; method_config range_block_size/cut_guard_bins/diagonal_loading_fraction from baseline V2 suite",
        "common_target_steering": "configured beam-center hypothesis; target truth only labels ROI and metrics",
        "common_cfAR": "GO-CFAR fixed configured alpha/guard/background; no threshold tuning",
        "M0_production_boundary": "production Current intentionally retains target-observation P38; use M0_controlled for fair scientific deltas",
        "M3_boundary": "native four-channel JDL/STAP is offline scientific reference; no production CUDA superiority claim",
        "attribution_boundary": "M1/M2 minus M0_controlled quantifies two-channel algorithm/protocol deltas; M3 minus two-channel rows includes native spatial DOF and its algorithm, so pure DOF is not claimed isolated by this minimum matrix",
        "source_suite": str(suite_path),
        "method_config": method_config,
    }
    _write_json(output_dir / "comparison_contract.json", contract)
    manifest = {
        "schema": "unknown_system_error_information_matched_baseline_manifest_v1",
        "output_dir": str(output_dir),
        "case_count_requested": len(cases),
        "case_count_completed": len({str(row["case_id"]) for row in rows}),
        "row_count": len(rows),
        "failures": failures,
        "elapsed_s": time.perf_counter() - started,
        "source_commit": _git_output("rev-parse", "HEAD"),
        "provenance": _provenance([sys.executable, *sys.argv], base_config),
        "artifacts": {
            "summary": str(output_dir / "information_matched_summary.csv"),
            "aggregate": str(output_dir / "information_matched_aggregate.csv"),
            "attribution": str(output_dir / "information_matched_attribution.csv"),
            "pairwise_attribution": str(output_dir / "information_matched_pairwise_attribution.csv"),
            "pairwise_aggregate": str(output_dir / "information_matched_pairwise_aggregate.csv"),
            "contract": str(output_dir / "comparison_contract.json"),
        },
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=ROOT / "configs/research/ai_csi_baseline_v2_suite.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/unknown_system_error_information_matched_baseline_20260914_v1")
    parser.add_argument("--seeds", type=int, nargs="+", default=[101, 202, 303])
    parser.add_argument("--velocities", type=float, nargs="+", default=[0.5, 1.0, 2.0])
    args = parser.parse_args(argv)
    manifest = run(args.output_dir.resolve(), args.suite.resolve(), args.seeds, args.velocities)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, default=_json_default))
    return 0 if not manifest["failures"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
