#!/usr/bin/env python3
"""Aggregate R22 independent H0 seeds without treating CUTs as independent."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_r import r22_experiment_matrix as matrix


DEFAULT_DESIGN = ROOT / "docs/research/phase_r/R22/config/r22_extension_experiment_design_v2_range_roi.json"
DEFAULT_MATRIX = ROOT / "outputs/r22_h0_engineering_post_review_20261029/matrix/matrix_manifest.json"
DEFAULT_OUTPUT = ROOT / "outputs/r22_h0_engineering_post_review_20261029/analysis"
CUT_BANDS = ("union_dynamic_in_band", "union_full_band")
REFERENCE_FIT_METHODS = {"scc", "robust_ddc", "robust_ddc_rb"}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _display_path(path: Path) -> str:
    """Use repository-relative paths in review manifests; hide machine-local roots."""
    resolved = Path(path).expanduser().resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return f"<external>/{resolved.name}"


def _validate_execution_identity(
    run_manifest: Mapping[str, Any],
    expected: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Require one reproducible Native CUDA build and eligible case status."""
    source = run_manifest.get("source_identity")
    stage2 = run_manifest.get("stage2_binary")
    production = run_manifest.get("production_binary")
    preflight = run_manifest.get("native_cuda_preflight")
    labels = run_manifest.get("status_labels")
    if not all(isinstance(value, Mapping) for value in (source, stage2, production, preflight, labels)):
        raise ValueError("H0 run manifest is missing execution identity fields")

    commit = str(source.get("commit", ""))
    if not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise ValueError("H0 execution source commit must be a full Git SHA")
    source_files = source.get("files")
    if not isinstance(source_files, Mapping) or not source_files:
        raise ValueError("H0 execution source file SHA inventory is missing")
    source_file_shas: dict[str, str] = {}
    for name, metadata in source_files.items():
        if not isinstance(metadata, Mapping):
            raise ValueError("H0 execution source file identity is invalid")
        digest = str(metadata.get("sha256", ""))
        if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError(f"H0 execution source SHA-256 is missing: {name}")
        source_file_shas[str(name)] = digest.lower()

    stage2_sha = str(stage2.get("sha256", ""))
    production_sha = str(production.get("sha256", ""))
    if not re.fullmatch(r"[0-9a-fA-F]{64}", stage2_sha):
        raise ValueError("H0 Stage2 binary SHA-256 is missing")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", production_sha):
        raise ValueError("H0 Production binary SHA-256 is missing")
    if production.get("backend") != "local Native CUDA":
        raise ValueError("H0 Production binary must use local Native CUDA")
    if production.get("use_corex") is not False:
        raise ValueError("H0 Production binary must record USE_COREX=false")
    gpu = preflight.get("gpu")
    if not isinstance(gpu, Mapping) or gpu.get("available") is not True:
        raise ValueError("H0 Native CUDA GPU preflight did not pass")
    if not all(labels.get(key) is True for key in ("IMPLEMENTED", "EXECUTED", "EVALUABLE")):
        raise ValueError("H0 case must be IMPLEMENTED, EXECUTED, and EVALUABLE")
    if labels.get("PERFORMANCE_VERIFIED") is not False:
        raise ValueError("single-case H0 run must not claim PERFORMANCE_VERIFIED")

    identity = {
        "source_commit": commit.lower(),
        "execution_source_file_sha256": dict(sorted(source_file_shas.items())),
        "stage2_binary_sha256": stage2_sha.lower(),
        "production_binary_sha256": production_sha.lower(),
        "production_backend": "local Native CUDA",
        "use_corex": False,
        "cuda_gpu_preflight_available": True,
    }
    if expected is not None and dict(expected) != identity:
        raise ValueError("H0 execution identity differs across cases")
    return identity


def _read_json(data: bytes, source: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON in {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {source}")
    return value


def expected_method_modes(
    design: Mapping[str, Any], method_config: Mapping[str, Any], delays_ns: Sequence[float],
) -> dict[float, list[tuple[str, str]]]:
    """Build the frozen H0 method/mode roster from the V2 design and config."""

    common = design.get("common", {})
    selected_ids = list(common.get("method_ids", []))
    modes = tuple(str(value) for value in common.get("calibration_modes", []))
    zero_policy = common.get("zero_delay_reference_policy", {})
    zero_delay = float(zero_policy.get("delay_error_ns", 0.0))
    zero_na = {str(value) for value in zero_policy.get("not_applicable_method_ids", [])}
    configured = {
        str(row.get("method_id")): row
        for row in method_config.get("methods", [])
        if isinstance(row, Mapping) and row.get("method_id")
    }
    if set(selected_ids) - set(configured):
        raise ValueError("R22 design method IDs are missing from its method config")

    base_pairs: list[tuple[str, str]] = [("A0", "not_applicable")]
    for method_id in selected_ids:
        method = configured[method_id]
        name = str(method.get("research_calibration_method", ""))
        has_reference_fit = bool(method.get("research_calibration_enable")) and name in REFERENCE_FIT_METHODS
        method_modes = modes if has_reference_fit else ("not_applicable",)
        base_pairs.extend((method_id, mode) for mode in method_modes)

    extra_ids = sorted(zero_na - set(selected_ids))
    result: dict[float, list[tuple[str, str]]] = {}
    for delay in delays_ns:
        delay = float(delay)
        pairs = list(base_pairs)
        if delay != zero_delay:
            pairs.extend((method_id, "not_applicable") for method_id in extra_ids)
        else:
            pairs = [pair for pair in pairs if pair[0] not in zero_na]
        result[delay] = sorted(set(pairs))
    return result


def _parse_count(row: Mapping[str, Any], field: str, *, minimum: int = 0) -> int:
    try:
        value = int(str(row.get(field, "")))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if value < minimum:
        if minimum == 1:
            raise ValueError(f"{field} must be positive")
        raise ValueError(f"{field} must be at least {minimum}")
    return value


def _bootstrap_mean_ci(values: Sequence[float], rng: np.random.Generator, resamples: int) -> tuple[float, float]:
    data = np.asarray(values, dtype=np.float64)
    indices = rng.integers(0, data.size, size=(resamples, data.size))
    means = data[indices].mean(axis=1)
    lower, upper = np.quantile(means, (0.025, 0.975), method="linear")
    return float(lower), float(upper)


def aggregate_h0_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    expected_seeds: Sequence[int],
    delays_ns: Sequence[float] = (0.0, 4.0),
    not_applicable_zero_delay: set[str] | None = None,
    expected_method_modes: Mapping[float, Sequence[tuple[str, str]]] | None = None,
    cut_bands: Sequence[str] = CUT_BANDS,
    resamples: int = 10_000,
    rng_seed: int = 22_029,
) -> dict[str, list[dict[str, Any]]]:
    """Summarize per-seed CUT-hit fractions and paired delay differences."""

    if resamples <= 0:
        raise ValueError("resamples must be positive")
    seeds = tuple(int(seed) for seed in expected_seeds)
    if not seeds or len(seeds) != len(set(seeds)):
        raise ValueError("expected_seeds must be non-empty and unique")
    delays = tuple(float(delay) for delay in delays_ns)
    if len(delays) != 2 or len(delays) != len(set(delays)):
        raise ValueError("exactly two unique paired H0 delays are required")

    zero_na = set(not_applicable_zero_delay or ())
    seed_counts: dict[tuple[str, str, str, float], dict[int, tuple[int, int, float]]] = defaultdict(dict)
    invalid_seeds: dict[tuple[str, str, str, float], set[int]] = defaultdict(set)
    seen: set[tuple[str, str, str, float, int]] = set()
    observed_pairs: dict[float, set[tuple[str, str]]] = defaultdict(set)
    per_seed: list[dict[str, Any]] = []

    for row in rows:
        try:
            seed = int(str(row.get("seed", "")))
            delay = float(str(row.get("delay_ns", "")))
        except (TypeError, ValueError) as exc:
            raise ValueError("H0 row requires numeric seed and delay_ns") from exc
        method_id = str(row.get("method_id", ""))
        mode = str(row.get("mode", ""))
        band = str(row.get("cut_band_mode", ""))
        if not method_id or not mode or band not in set(cut_bands):
            raise ValueError("H0 row has an unknown method, mode, or CUT band")
        if seed not in set(seeds) or delay not in set(delays):
            raise ValueError("H0 row is outside the frozen seed or delay set")
        if str(row.get("role", "")) != "OFF":
            raise ValueError("H0 false-alarm rows must use role OFF")
        if str(row.get("h0_scope", "")) != "independent_target_free_seed":
            raise ValueError("H0 row is not an independent target-free seed")
        if delay == 0.0 and method_id in zero_na:
            raise ValueError(f"{method_id} is frozen not applicable at zero delay")

        pair = (method_id, mode)
        observed_pairs[delay].add(pair)
        if expected_method_modes is not None and pair not in set(expected_method_modes.get(delay, ())):
            raise ValueError(f"unexpected H0 method/mode at {delay:g} ns: {pair}")

        key = (method_id, mode, band, delay)
        row_key = (*key, seed)
        if row_key in seen:
            raise ValueError(f"duplicate H0 seed row: {row_key}")
        seen.add(row_key)

        status = str(row.get("status", ""))
        if status != "EVALUABLE_DESCRIPTIVE_H0_SEED":
            invalid_seeds[key].add(seed)
            continue
        hits = _parse_count(row, "observed_cut_hit_count")
        valid_cuts = _parse_count(row, "valid_cut_count", minimum=1)
        if hits > valid_cuts:
            raise ValueError("hit count cannot exceed valid CUT count")
        rate = hits / valid_cuts
        seed_counts[key][seed] = (hits, valid_cuts, rate)
        per_seed.append({
            "seed": seed,
            "delay_ns": delay,
            "method_id": method_id,
            "mode": mode,
            "cut_band_mode": band,
            "observed_cut_hit_count": hits,
            "valid_cut_count": valid_cuts,
            "seed_cut_hit_fraction": rate,
            "status": status,
        })

    method_modes = set()
    if expected_method_modes is not None:
        method_modes.update(pair for pairs in expected_method_modes.values() for pair in pairs)
    else:
        method_modes.update(pair for pairs in observed_pairs.values() for pair in pairs)
    method_modes = set(method_modes)
    rng = np.random.Generator(np.random.PCG64(int(rng_seed)))
    per_condition: list[dict[str, Any]] = []
    paired_delay: list[dict[str, Any]] = []

    for method_id, mode in sorted(method_modes):
        for band in cut_bands:
            by_delay: dict[float, dict[int, tuple[int, int, float]]] = {}
            condition_rows: dict[float, dict[str, Any]] = {}
            for delay in delays:
                key = (method_id, mode, band, delay)
                is_na = delay == 0.0 and method_id in zero_na
                valid = seed_counts.get(key, {})
                actual_seeds = set(valid)
                missing = sorted(set(seeds) - actual_seeds)
                invalid = sorted(invalid_seeds.get(key, set()))
                complete = not is_na and not missing and not invalid and len(actual_seeds) == len(seeds)
                fractions = [valid[seed][2] for seed in seeds if seed in valid]
                mean = float(np.mean(fractions)) if fractions else None
                ci = _bootstrap_mean_ci(fractions, rng, resamples) if complete and len(fractions) > 1 else None
                status = (
                    "NOT_APPLICABLE_ZERO_DELAY_REFERENCE" if is_na else
                    "EVALUABLE" if complete else
                    "INCOMPLETE"
                )
                summary = {
                    "method_id": method_id,
                    "mode": mode,
                    "cut_band_mode": band,
                    "delay_ns": delay,
                    "status": status,
                    "expected_seed_count": len(seeds),
                    "n_seed": len(actual_seeds),
                    "mean_seed_fraction": mean,
                    "ci95_low": ci[0] if ci else None,
                    "ci95_high": ci[1] if ci else None,
                    "total_cut_hits": sum(valid[seed][0] for seed in actual_seeds),
                    "total_valid_cuts": sum(valid[seed][1] for seed in actual_seeds),
                    "missing_seeds": [] if is_na else missing,
                    "invalid_seeds": invalid,
                    "bootstrap_resamples": resamples if complete else None,
                    "bootstrap_rng": "NumPy PCG64",
                    "bootstrap_seed": int(rng_seed),
                }
                per_condition.append(summary)
                by_delay[delay] = valid
                condition_rows[delay] = summary

            zero_delay = next((delay for delay in delays if delay == 0.0), None)
            plus_delay = next((delay for delay in delays if delay == 4.0), None)
            if zero_delay is None or plus_delay is None:
                raise ValueError("paired H0 delays must be 0 ns and +4 ns")
            if method_id in zero_na:
                paired_delay.append({
                    "method_id": method_id,
                    "mode": mode,
                    "cut_band_mode": band,
                    "status": "NOT_APPLICABLE_ZERO_DELAY_REFERENCE",
                    "expected_seed_count": len(seeds),
                    "n_seed": 0,
                    "mean_delta_plus4_minus0": None,
                    "ci95_low": None,
                    "ci95_high": None,
                    "missing_seeds": [],
                })
                continue

            zero_rows, plus_rows = by_delay[zero_delay], by_delay[plus_delay]
            paired_seeds = sorted(set(zero_rows) & set(plus_rows))
            missing_pairs = sorted(set(seeds) - set(paired_seeds))
            deltas = [plus_rows[seed][2] - zero_rows[seed][2] for seed in paired_seeds]
            complete_pair = (
                not missing_pairs
                and condition_rows[zero_delay]["status"] == "EVALUABLE"
                and condition_rows[plus_delay]["status"] == "EVALUABLE"
            )
            mean_delta = float(np.mean(deltas)) if deltas else None
            ci_delta = _bootstrap_mean_ci(deltas, rng, resamples) if complete_pair and len(deltas) > 1 else None
            paired_delay.append({
                "method_id": method_id,
                "mode": mode,
                "cut_band_mode": band,
                "status": "EVALUABLE" if complete_pair else "INCOMPLETE",
                "expected_seed_count": len(seeds),
                "n_seed": len(paired_seeds),
                "mean_delta_plus4_minus0": mean_delta,
                "ci95_low": ci_delta[0] if ci_delta else None,
                "ci95_high": ci_delta[1] if ci_delta else None,
                "missing_seeds": missing_pairs,
                "bootstrap_resamples": resamples if complete_pair else None,
                "bootstrap_rng": "NumPy PCG64",
                "bootstrap_seed": int(rng_seed),
            })

    return {"per_seed": per_seed, "per_condition": per_condition, "paired_delay": paired_delay}


def _load_matrix_h0_rows(
    matrix_path: Path,
    design: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    matrix_path = Path(matrix_path).expanduser().resolve()
    matrix_data = json.loads(matrix_path.read_text(encoding="utf-8"))
    if matrix_data.get("phase") != "h0":
        raise ValueError("matrix manifest phase must be h0")
    expected_specs = matrix.build_case_specs(design, "h0")
    expected_cases = {
        matrix.r22.case_identifier(
            phase="h0", seed=item["seed"], speed_mps=item["speed_mps"],
            snr_db=item["snr_db"], delay_ns=item["delay_ns"],
            target_count=item["target_count"], period_count=item["period_count"],
        ): item for item in expected_specs
    }
    completed_statuses = {"PASSED", "H0_EVALUABLE_WITH_GAPS"}
    completed: set[str] = set()
    rows: list[dict[str, Any]] = []
    archive_identities: list[dict[str, Any]] = []
    execution_identity: dict[str, Any] | None = None
    case_records = {str(item.get("case_id")): item for item in matrix_data.get("cases", [])}

    for case_id, record in case_records.items():
        if case_id not in expected_cases:
            raise ValueError(f"unexpected case in H0 matrix: {case_id}")
        status = str(record.get("status", ""))
        if status not in completed_statuses:
            continue
        archive_record = record.get("compact_archive")
        if not isinstance(archive_record, Mapping):
            continue
        archive_path = Path(str(archive_record.get("archive_path", ""))).expanduser()
        if not archive_path.is_file():
            raise FileNotFoundError(f"H0 compact archive is missing: {archive_path}")
        archive_sha = _sha256_file(archive_path)
        if archive_sha != archive_record.get("archive_sha256"):
            raise ValueError(f"H0 archive SHA-256 mismatch: {archive_path}")
        with zipfile.ZipFile(archive_path, "r") as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ValueError(f"H0 archive CRC failure in {archive_path}: {bad_member}")
            archive_manifest_bytes = archive.read("ARCHIVE_MANIFEST.json")
            if _sha256_bytes(archive_manifest_bytes) != archive_record.get("archive_manifest_sha256"):
                raise ValueError(f"H0 archive manifest SHA-256 mismatch: {archive_path}")
            review = _read_json(archive.read("case_manifest_review_extract.json"), str(archive_path))
            run_manifest = _read_json(archive.read("r22_run_manifest.json"), str(archive_path))
            execution_identity = _validate_execution_identity(run_manifest, execution_identity)
            scene = review.get("scene_identity", {})
            area = review.get("area_clutter_gate", {})
            spec = expected_cases[case_id]
            if int(scene.get("seed", -1)) != int(spec["seed"]):
                raise ValueError(f"H0 scene seed mismatch: {case_id}")
            if float(scene.get("delay_error_ns", math.nan)) != float(spec["delay_ns"]):
                raise ValueError(f"H0 scene delay mismatch: {case_id}")
            if int(scene.get("target_count", -1)) != 0:
                raise ValueError(f"H0 scene contains targets: {case_id}")
            if area.get("status") != "passed":
                raise ValueError(f"H0 area-clutter gate did not pass: {case_id}")
            if int(area.get("continuous_area_packets", 0)) <= 0 or int(area.get("continuous_area_samples", 0)) <= 0:
                raise ValueError(f"H0 continuous area clutter is absent: {case_id}")
            with archive.open("evaluation/h0_false_alarm_metrics.csv", "r") as stream:
                csv_rows = list(csv.DictReader(line.decode("utf-8") for line in stream))
        expected_rows = int(record.get("evaluation_summary", {}).get("independent_h0_seed_rows", -1))
        if len(csv_rows) != expected_rows:
            raise ValueError(f"H0 summary row count disagrees with matrix manifest: {case_id}")
        for row in csv_rows:
            row["delay_ns"] = float(spec["delay_ns"])
            rows.append(row)
        completed.add(case_id)
        archive_identities.append({
            "case_id": case_id,
            "archive_path": _display_path(archive_path),
            "archive_sha256": archive_sha,
            "archive_manifest_sha256": archive_record.get("archive_manifest_sha256"),
            "h0_row_count": len(csv_rows),
            "area_packets": int(area["continuous_area_packets"]),
            "area_samples": int(area["continuous_area_samples"]),
        })

    missing_cases = sorted(set(expected_cases) - completed)
    return rows, {
        "matrix_path": _display_path(matrix_path),
        "matrix_sha256": _sha256_file(matrix_path),
        "matrix_status": matrix_data.get("status"),
        "matrix_planned_case_count": int(matrix_data.get("planned_case_count", 0)),
        "expected_case_count": len(expected_cases),
        "completed_archived_case_count": len(completed),
        "missing_case_ids": missing_cases,
        "archive_identities": archive_identities,
        "execution_identity": execution_identity,
    }


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(str(key))
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        if not fields:
            stream.write("\n")
            return
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                if isinstance(value, (dict, list, tuple, set)) else value
                for key, value in row.items()
            })


def analyze_matrix(
    *, design_path: Path, matrix_path: Path, output_dir: Path,
    resamples: int = 10_000, rng_seed: int = 22_029,
) -> dict[str, Any]:
    design = matrix.load_design(Path(design_path))
    method_config_path = (ROOT / str(design["common"]["method_config"])).resolve()
    method_config = json.loads(method_config_path.read_text(encoding="utf-8"))
    delays = [float(item["delay_error_ns"]) for item in design["phases"]["h0"]["conditions"]]
    rows, input_audit = _load_matrix_h0_rows(Path(matrix_path), design)
    zero_policy = design["common"]["zero_delay_reference_policy"]
    zero_na = set(str(value) for value in zero_policy["not_applicable_method_ids"])
    roster = expected_method_modes(design, method_config, delays)
    summary = aggregate_h0_rows(
        rows,
        expected_seeds=design["seed_sets"]["h0"],
        delays_ns=delays,
        not_applicable_zero_delay=zero_na,
        expected_method_modes=roster,
        resamples=resamples,
        rng_seed=rng_seed,
    )

    output_dir = Path(output_dir).expanduser().resolve()
    _write_csv(output_dir / "h0_per_seed_rates.csv", summary["per_seed"])
    _write_csv(output_dir / "h0_per_condition_summary.csv", summary["per_condition"])
    _write_csv(output_dir / "h0_paired_delay_summary.csv", summary["paired_delay"])
    complete = (
        not input_audit["missing_case_ids"]
        and all(row["status"] in {"EVALUABLE", "NOT_APPLICABLE_ZERO_DELAY_REFERENCE"}
                for row in summary["per_condition"])
        and all(row["status"] in {"EVALUABLE", "NOT_APPLICABLE_ZERO_DELAY_REFERENCE"}
                for row in summary["paired_delay"])
    )
    analysis_manifest = {
        "schema_version": 1,
        "analysis": "R22 independent H0 seed-clustered empirical CUT-hit fractions",
        "design_id": design["design_id"],
        "status": "COMPLETE_EVALUABLE" if complete else "INCOMPLETE_NOT_EVALUABLE_AS_FULL_MATRIX",
        "inferential_unit": "independent H0 seed",
        "seed_count_expected_per_delay": len(design["seed_sets"]["h0"]),
        "seed_ids": design["seed_sets"]["h0"],
        "delay_conditions_ns": delays,
        "cut_band_modes": list(CUT_BANDS),
        "metric": "observed_cut_hit_count / valid_cut_count per independent seed",
        "interval_method": {
            "type": "percentile bootstrap over whole seed rows",
            "paired_delay_resampling": "resample matched seed indices jointly",
            "confidence_level": 0.95,
            "resamples": int(resamples),
            "rng": "NumPy PCG64",
            "rng_seed": int(rng_seed),
            "quantile_method": "linear",
        },
        "zero_delay_not_applicable_methods": sorted(zero_na),
        "condition_summary_count": len(summary["per_condition"]),
        "complete_condition_summary_count": sum(row["status"] == "EVALUABLE" for row in summary["per_condition"]),
        "paired_summary_count": len(summary["paired_delay"]),
        "complete_paired_summary_count": sum(row["status"] == "EVALUABLE" for row in summary["paired_delay"]),
        "claims": {
            "independent_cut_binomial_inference": False,
            "confirmatory_method_ranking": False,
            "target_detection_performance_verified": False,
        },
        "execution_identity": input_audit["execution_identity"],
        **input_audit,
    }
    (output_dir / "h0_analysis_manifest.json").write_text(
        json.dumps(analysis_manifest, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return analysis_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", type=Path, default=DEFAULT_DESIGN)
    parser.add_argument("--matrix-manifest", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--bootstrap-resamples", type=int, default=10_000)
    parser.add_argument("--bootstrap-seed", type=int, default=22_029)
    args = parser.parse_args()
    manifest = analyze_matrix(
        design_path=args.design,
        matrix_path=args.matrix_manifest,
        output_dir=args.output_dir,
        resamples=args.bootstrap_resamples,
        rng_seed=args.bootstrap_seed,
    )
    print(f"H0_ANALYSIS_STATUS={manifest['status']}")
    print(
        f"COMPLETED_CASES={manifest['completed_archived_case_count']}/"
        f"{manifest['expected_case_count']}"
    )
    print(f"COMPLETE_METHOD_CONDITION_SUMMARIES={manifest['complete_condition_summary_count']}/"
          f"{manifest['condition_summary_count']}")
    print(f"COMPLETE_PAIRED_SUMMARIES={manifest['complete_paired_summary_count']}/"
          f"{manifest['paired_summary_count']}")
    print(f"OUTPUT_DIR={Path(args.output_dir).expanduser().resolve()}")
    return 0 if manifest["status"] == "COMPLETE_EVALUABLE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
