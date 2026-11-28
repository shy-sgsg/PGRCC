#!/usr/bin/env python3
"""Offline R22 V3.3 residual task-gap audit from compact V3.2 ZIP archives.

The script reads selected ZIP members in place. It never extracts archives or
starts Stage2/Production/CUDA. Target associations use the frozen V3.2
evaluator's sample_key and (period, beam, det_id) lineage.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import os
import re
import statistics
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_r import r19_task_evaluator as r19  # noqa: E402


SOURCE_COMMIT = "74bccce61f3ec2f74746512d67271c815ce6a3a8"
EXPECTED_SOURCE_SHA256 = {
    "scripts/phase_r/r19_task_evaluator.py": "c852bb828a561597296f1b1346da1d3e4c4554856435524084f4bb21b967fb14",
    "scripts/phase_r/r20_task_evaluator.py": "0a5400c9e2d693f28da9948927ba4f0ede7d3f3eedfc74b5d06992f03aabf9b1",
    "scripts/phase_r/r20_harmonized_task_review.py": "198ab502ad11e055c1d3803ea14dde4a02934a2199573140b305013f99445f3a",
    "scripts/phase_r/r21_task_evaluator.py": "c1820472f6fae354e09e4a331a71212b6ba41c20595f729fc80caa2a01d49a1c",
    "scripts/phase_r/r21_historical_task_review.py": "b3b6142f9968af619bce3d2a6917073d720246584c623a8acac1ae1eec4a10a5",
    "scripts/phase_r/run_r22_cuda_pilot.py": "e2882ba2d10b30d85c1ad779d1fd94978fbeb0a351106c6d41d4da05f725e5c6",
}
DESIGN_PATH = ROOT / "docs/research/phase_r/R22/config/r22_extension_experiment_design_v3_2_identity_closed_20261106.json"
METHOD_CONFIG_PATH = ROOT / "docs/research/phase_r/R22/config/r22_method_config_v2_range_roi.json"
ARCHIVE_INDEX_PATH = ROOT / "docs/research/phase_r/R22/evidence/v3_2_screening_20261106/archive_index.csv"
ARCHIVE_ROOT = ROOT / "outputs/r22_v3_2_screening_20261106/archives/screening"
DEFAULT_OUTPUT = ROOT / "docs/research/phase_r/R22/V3_3_residual_task_gap_audit/evidence"

CORE_LABELS = ("PK", "Fixed_D3", "Huber_D3_ON", "M1_Physics_Matched")
CORE_PAIRS = tuple(
    (CORE_LABELS[left], CORE_LABELS[right])
    for left in range(len(CORE_LABELS))
    for right in range(left + 1, len(CORE_LABELS))
)
PARAMETER_METRICS = (
    "angle_deg", "position_m", "range_m", "folded_doppler_hz",
    "radial_velocity_mps", "ground_speed_mps",
)
RECOMPUTABLE_METRICS = PARAMETER_METRICS[:-1]

METHODS: tuple[dict[str, str], ...] = (
    {"method_id": "A0", "mode": "not_applicable", "label": "A0", "branch": "A0_not_applicable_ON"},
    {"method_id": "C0", "mode": "not_applicable", "label": "C0", "branch": "C0_not_applicable_ON"},
    {"method_id": "PK", "mode": "not_applicable", "label": "PK", "branch": "PK_not_applicable_ON"},
    {"method_id": "R22_R9_Fixed_D3", "mode": "not_applicable", "label": "Fixed_D3", "branch": "R22_R9_Fixed_D3_ON"},
    {"method_id": "R22_D3_Huber_B_ON", "mode": "not_applicable", "label": "Huber_D3_ON", "branch": "R22_D3_Huber_B_ON_ON"},
    {"method_id": "R22_M1_Physics_Matched", "mode": "not_applicable", "label": "M1_Physics_Matched", "branch": "R22_M1_Physics_Matched_ON"},
    {"method_id": "P1", "mode": "not_applicable", "label": "P1_OFF_estimate", "branch": "P1_not_applicable_ON"},
    {"method_id": "P2", "mode": "Mode-A", "label": "P2_Mode-A", "branch": "P2_modea_ON"},
    {"method_id": "P2", "mode": "Mode-B", "label": "P2_Mode-B", "branch": "P2_modeb_ON"},
    {"method_id": "C4", "mode": "Mode-A", "label": "C4_Mode-A", "branch": "C4_modea_ON"},
    {"method_id": "C4", "mode": "Mode-B", "label": "C4_Mode-B", "branch": "C4_modeb_ON"},
    {"method_id": "PKR", "mode": "Mode-A", "label": "PKR_Mode-A", "branch": "PKR_modea_ON"},
    {"method_id": "PKR", "mode": "Mode-B", "label": "PKR_Mode-B", "branch": "PKR_modeb_ON"},
)
METHOD_BY_KEY = {(item["method_id"], item["mode"]): item for item in METHODS}
METHOD_BY_LABEL = {item["label"]: item for item in METHODS}
RECOVERY_TRIPLETS = (
    {"id": "Fixed_D3_Current_Known_Estimated", "mode": "not_applicable", "current": "C0", "known": "PK", "estimated": "R22_R9_Fixed_D3"},
    {"id": "Huber_D3_ON_Current_Known_Estimated", "mode": "not_applicable", "current": "C0", "known": "PK", "estimated": "R22_D3_Huber_B_ON"},
    {"id": "M1_Physics_Matched_Current_Known_Estimated", "mode": "not_applicable", "current": "C0", "known": "PK", "estimated": "R22_M1_Physics_Matched"},
    {"id": "P1_Current_Known_Estimated", "mode": "not_applicable", "current": "C0", "known": "PK", "estimated": "P1"},
    {"id": "P2_Mode-A_C4A_PKR-A_P2A", "mode": "Mode-A", "current": "C4", "known": "PKR", "estimated": "P2"},
    {"id": "P2_Mode-B_C4B_PKR-B_P2B", "mode": "Mode-B", "current": "C4", "known": "PKR", "estimated": "P2"},
)
INFORMATION_CONDITIONS = (
    {"label": "A0", "information": "No-error reference; zero applied physical error.", "comparability": "Reference endpoint."},
    {"label": "C0", "information": "Production Current with +4 ns unknown channel delay and no correction.", "comparability": "Unknown-error endpoint."},
    {"label": "PK", "information": "Known physical correction; true delay is evaluator-only and not estimator input.", "comparability": "Upper-bound reference; not deployable estimator."},
    {"label": "Fixed_D3", "information": "Static R9-frozen D3 correction; same fixed value across cases, no case truth used.", "comparability": "Historical deterministic calibration baseline."},
    {"label": "Huber_D3_ON", "information": "Huber delay fit from same-case target-present A1/Current raw C1-C2; fit record is derived from the full 10-period case (1300 pulses).", "comparability": "ON-only and truth-free, but batch/offline over the complete case; causal real-time availability and estimator runtime cost are not verified."},
    {"label": "M1_Physics_Matched", "information": "Physics-matched delay plus constant-complex-gain fit from same-case target-present raw C1/C2, with runtime geometry; full 10-period case (1300 pulses).", "comparability": "ON-only and truth-free, but batch/offline over the complete case; later periods are available to earlier-period outputs. Causal real-time availability and estimator runtime cost are not verified."},
    {"label": "P1_OFF_estimate", "information": "Blind physical delay estimate from paired target-free OFF input; applies correction to ON.", "comparability": "Diagnostic-only information; does not establish target-present online estimation."},
    {"label": "P2_Mode-A", "information": "Paired target-free OFF physical-delay estimate; robust DDC-RB Mode-A reference policy.", "comparability": "Compare only with C4 Mode-A and PKR Mode-A; not directly with PK."},
    {"label": "P2_Mode-B", "information": "Paired target-free OFF physical-delay estimate; robust DDC-RB Mode-B uses ON reference observations.", "comparability": "Compare only with C4 Mode-B and PKR Mode-B; incomplete triplet coverage."},
    {"label": "C4_Mode-A", "information": "Robust DDC-RB Mode-A, paired OFF reference policy.", "comparability": "P2 Mode-A current reference."},
    {"label": "C4_Mode-B", "information": "Robust DDC-RB Mode-B, ON reference policy; frozen min_support=8, phase gate=0.35 rad.", "comparability": "P2 Mode-B current reference; report coverage separately."},
    {"label": "PKR_Mode-A", "information": "Known physical correction then Robust DDC-RB Mode-A.", "comparability": "P2 Mode-A known reference; evaluator-only delay."},
    {"label": "PKR_Mode-B", "information": "Known physical correction then Robust DDC-RB Mode-B.", "comparability": "P2 Mode-B known reference; evaluator-only delay and method gaps."},
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_csv_member(archive: zipfile.ZipFile, member: str) -> list[dict[str, str]]:
    try:
        content = archive.read(member)
    except KeyError as exc:
        raise ValueError(f"archive member is missing: {member}") from exc
    return list(csv.DictReader(io.StringIO(content.decode("utf-8-sig", errors="strict"))))


def _read_json_member(archive: zipfile.ZipFile, member: str) -> dict[str, Any]:
    try:
        value = json.loads(archive.read(member))
    except KeyError as exc:
        raise ValueError(f"archive member is missing: {member}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"archive JSON root is not an object: {member}")
    return value


def _float(value: Any) -> float | None:
    return r19._finite(value)


def _int(value: Any) -> int | None:
    return r19._integer(value)


def _truth(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off"}:
        return False
    return None


def _case_prefix(members: Sequence[str]) -> str:
    candidates = [
        name.split("/production/", 1)[0]
        for name in members
        if name.startswith("pipeline/cases/") and "/production/" in name and name.endswith("/branch_manifest.json")
    ]
    unique = sorted(set(candidates))
    if len(unique) != 1:
        raise ValueError(f"expected one pipeline case root in archive; found {len(unique)}")
    return unique[0]


def _branch_name(method_id: str, mode: str, role: str) -> str:
    special = {"R22_R9_Fixed_D3", "R22_D3_Huber_B_ON", "R22_M1_Physics_Matched"}
    if method_id in special:
        return f"{method_id}_{role}"
    mode_part = "not_applicable" if mode == "not_applicable" else mode.lower().replace("-", "")
    return f"{method_id}_{mode_part}_{role}"


def _index_rows(rows: Sequence[Mapping[str, Any]], key_fields: Sequence[str], description: str) -> dict[tuple[str, ...], Mapping[str, Any]]:
    indexed: dict[tuple[str, ...], Mapping[str, Any]] = {}
    for row in rows:
        key = tuple(str(row.get(field, "")).strip() for field in key_fields)
        if any(not value for value in key):
            raise ValueError(f"{description}: empty identity component for {key_fields}: {row}")
        if key in indexed:
            raise ValueError(f"{description}: duplicate identity {key}")
        indexed[key] = row
    return indexed


def _sample_key(target_id: str, period_id: int, beam_id: int) -> str:
    return f"{target_id}/{period_id}/{beam_id}"


def _association_position_errors(
    rows: Sequence[Mapping[str, Any]], description: str,
) -> dict[str, float]:
    """Read frozen-evaluator position errors for every matched truth opportunity."""

    output: dict[str, float] = {}
    seen: set[str] = set()
    for row in rows:
        sample_key = str(row.get("sample_key", "")).strip()
        if not sample_key:
            raise ValueError(f"{description}: association has empty sample_key")
        if sample_key in seen:
            raise ValueError(f"{description}: duplicate association sample_key {sample_key}")
        seen.add(sample_key)
        matched = _truth(row.get("matched"))
        if matched is None:
            raise ValueError(f"{description}: malformed association match flag for {sample_key}")
        error = _float(row.get("position_error_m"))
        if matched:
            if error is None:
                raise ValueError(
                    f"{description}: matched association lacks finite position_error_m for {sample_key}"
                )
            output[sample_key] = error
        elif error is not None:
            raise ValueError(
                f"{description}: unmatched association has position_error_m for {sample_key}"
            )
    return output


def _same_key_sets(*sets: Iterable[str]) -> bool:
    if not sets:
        return True
    first = set(sets[0])
    return all(set(values) == first for values in sets[1:])


def _rmse(errors: Iterable[float]) -> tuple[int, float | None]:
    values = [float(value) for value in errors if math.isfinite(float(value))]
    return len(values), math.sqrt(sum(value * value for value in values) / len(values)) if values else None


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _sd(values: Sequence[float]) -> float | None:
    return statistics.stdev(values) if len(values) >= 2 else (0.0 if len(values) == 1 else None)


def _metric_row_map(rows: Sequence[Mapping[str, Any]], method_id: str, mode: str) -> dict[str, Mapping[str, Any]]:
    selected = [row for row in rows if row.get("role") == "ON" and row.get("method_id") == method_id and row.get("mode") == mode]
    result: dict[str, Mapping[str, Any]] = {}
    for row in selected:
        metric = str(row.get("metric", ""))
        if not metric or metric in result:
            raise ValueError(f"duplicate or empty parameter metric for {method_id}/{mode}: {metric}")
        result[metric] = row
    return result


def _validate_source_identity() -> dict[str, Any]:
    identities: dict[str, dict[str, Any]] = {}
    for relative, expected in EXPECTED_SOURCE_SHA256.items():
        path = ROOT / relative
        actual = _sha256_file(path)
        if actual != expected:
            raise ValueError(f"frozen V3.2 evaluator source changed: {relative}: {actual} != {expected}")
        historic = subprocess.run(
            ["git", "show", f"{SOURCE_COMMIT}:{relative}"], cwd=ROOT,
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout
        historic_sha = _sha256_bytes(historic)
        if historic_sha != expected:
            raise ValueError(f"V3.2 source commit identity mismatch for {relative}: {historic_sha}")
        identities[relative] = {"sha256": actual, "v3_2_source_commit_sha256": historic_sha, "matches": True}
    return identities


def _load_detections(archive: zipfile.ZipFile, case_prefix: str, branch: str) -> tuple[dict[tuple[str, str, str], Mapping[str, str]], dict[int, int], int]:
    prefix = f"{case_prefix}/production/{branch}/result/"
    members = [
        name for name in archive.namelist()
        if name.startswith(prefix) and re.fullmatch(r"detection_results_GMTI\d+\.csv", PurePosixPath(name).name)
    ]
    if not members:
        raise ValueError(f"Production per-result detections missing for {branch}")
    detection_map: dict[tuple[str, str, str], Mapping[str, str]] = {}
    result_period_map: dict[int, int] = {}
    all_rows = 0
    for member in sorted(members):
        match = re.search(r"GMTI(\d+)", PurePosixPath(member).name)
        if not match:
            raise ValueError(f"unparseable Production result ID: {member}")
        result_number = int(match.group(1))
        rows = _read_csv_member(archive, member)
        periods = {_int(row.get("period_id")) for row in rows if _int(row.get("period_id")) is not None}
        if len(periods) != 1:
            raise ValueError(f"{member}: expected exactly one Production period, got {periods}")
        period = next(iter(periods))
        if result_number in result_period_map:
            raise ValueError(f"duplicate Production result number {result_number} for {branch}")
        result_period_map[result_number] = int(period)
        for row in rows:
            period_id, beam_id, det_id = _int(row.get("period_id")), _int(row.get("beam_id")), str(row.get("det_id", "")).strip()
            if period_id is None or beam_id is None or not det_id:
                raise ValueError(f"{member}: invalid detection identity")
            key = (str(period_id), str(beam_id), det_id)
            if key in detection_map:
                raise ValueError(f"{branch}: duplicate Production detection key {key}")
            detection_map[key] = row
            all_rows += 1
    if not result_period_map:
        raise ValueError(f"{branch}: no period-indexed Production detection products found")
    for result_number, period in result_period_map.items():
        if result_number != period + 1:
            raise ValueError(f"{branch}: result ID GMTI{result_number:02d} does not map to period_id={period}")
    return detection_map, result_period_map, all_rows


def _audit_production_output(
    cycle_rows: Sequence[Mapping[str, Any]],
    lineage_rows: Sequence[Mapping[str, Any]],
    association_rows: Sequence[Mapping[str, Any]],
    *,
    task_status: str,
    expected_period_count: int = 10,
    expected_opportunities_per_period: int = 20,
    retained_detection_counts: Mapping[int, int] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Reconcile period Detection counts with retained Production lineage.

    Target-unmatched Detection rows are derived only when the frozen evaluator
    supplied a complete EVALUABLE target-association table. Cluster rows are
    never called false/non-target without a cluster-to-truth association.
    """
    if expected_period_count <= 0 or expected_opportunities_per_period <= 0:
        raise ValueError("expected period and opportunity counts must be positive")
    expected_periods = set(range(expected_period_count))
    cycle_by_period: dict[int, dict[str, Any]] = {}
    for row in cycle_rows:
        period = _int(row.get("period_id"))
        result_id = _int(row.get("result_id"))
        detection_count = _int(row.get("detection_count"))
        if period is None or result_id is None or detection_count is None or detection_count < 0:
            raise ValueError(f"invalid cycle_metrics row: {row}")
        if period not in expected_periods:
            raise ValueError(f"cycle_metrics period_id outside expected range: {period}")
        if result_id != period + 1:
            raise ValueError(f"cycle_metrics result_id does not map to period_id={period}")
        if period in cycle_by_period:
            raise ValueError(f"duplicate cycle_metrics period_id={period}")
        cycle_by_period[period] = {"result_id": result_id, "detection_count": detection_count}
    if set(cycle_by_period) != expected_periods:
        raise ValueError(f"cycle_metrics periods do not cover expected periods: {sorted(cycle_by_period)}")
    for raw_period, raw_count in (retained_detection_counts or {}).items():
        period, count = _int(raw_period), _int(raw_count)
        if period is None or period not in expected_periods or count is None or count < 0:
            raise ValueError(f"invalid retained Detection period/count: {raw_period}={raw_count}")
        if count != cycle_by_period[period]["detection_count"]:
            raise ValueError(
                f"retained Detection row count disagrees with cycle_metrics for period {period}: "
                f"{count} != {cycle_by_period[period]['detection_count']}"
            )

    summaries: dict[tuple[int, int], dict[str, int]] = {}
    cluster_rows: dict[tuple[int, int], list[Mapping[str, Any]]] = defaultdict(list)
    for row in lineage_rows:
        record_type = str(row.get("record_type", "")).strip()
        if record_type not in {"beam_summary", "cluster"}:
            continue
        period, beam = _int(row.get("period_id")), _int(row.get("beam_id"))
        if period is None or beam is None or period not in expected_periods:
            raise ValueError(f"invalid Production lineage identity: {row}")
        key = (period, beam)
        if record_type == "beam_summary":
            values = {
                field: _int(row.get(field))
                for field in ("postfilter_cluster_count", "selected_candidate_count", "raw_detection_count")
            }
            if any(value is None or value < 0 for value in values.values()):
                raise ValueError(f"invalid beam_summary counts for period/beam {key}: {row}")
            if key in summaries:
                raise ValueError(f"duplicate beam_summary for period/beam {key}")
            summaries[key] = {field: int(value) for field, value in values.items()}
        else:
            cluster_rows[key].append(row)

    summary_periods = {period for period, _ in summaries}
    if summary_periods != expected_periods:
        raise ValueError(f"cluster lineage summaries do not cover expected periods: {sorted(summary_periods)}")
    if set(cluster_rows) - set(summaries):
        raise ValueError("cluster lineage contains rows without a beam_summary")

    period_counts: dict[int, dict[str, int]] = {
        period: {
            "postfilter_cluster_count": 0,
            "selected_candidate_count": 0,
            "raw_detection_count": 0,
        }
        for period in expected_periods
    }
    for key, summary in summaries.items():
        rows = cluster_rows.get(key, [])
        selected_count = 0
        raw_count = 0
        for row in rows:
            selected, raw = _truth(row.get("selected")), _truth(row.get("raw_detection"))
            if selected is None or raw is None:
                raise ValueError(f"invalid cluster selected/raw_detection flag for {key}: {row}")
            selected_count += int(selected)
            raw_count += int(raw)
        period, _ = key
        observed = {
            "postfilter_cluster_count": len(rows),
            "selected_candidate_count": selected_count,
            "raw_detection_count": raw_count,
        }
        if observed != summary:
            raise ValueError(f"cluster lineage rows disagree with beam_summary for {key}: {observed} != {summary}")
        for field, value in summary.items():
            period_counts[period][field] += value

    period_rows: list[dict[str, Any]] = []
    association_by_period: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    association_keys: set[str] = set()
    if task_status == "EVALUABLE":
        for row in association_rows:
            period = _int(row.get("period_id"))
            beam = _int(row.get("beam_id"))
            sample_key = str(row.get("sample_key", "")).strip()
            matched = _truth(row.get("matched"))
            if period is None or period not in expected_periods or beam is None or not sample_key or matched is None:
                raise ValueError(f"invalid target association row: {row}")
            if sample_key in association_keys:
                raise ValueError(f"duplicate target association sample_key: {sample_key}")
            association_keys.add(sample_key)
            association_by_period[period].append({**row, "_beam": beam, "_matched": matched})
        expected_association_count = expected_period_count * expected_opportunities_per_period
        if len(association_keys) != expected_association_count:
            raise ValueError(f"evaluable target associations expected {expected_association_count} rows, got {len(association_keys)}")
        if set(association_by_period) != expected_periods or any(
            len(association_by_period[period]) != expected_opportunities_per_period
            for period in expected_periods
        ):
            raise ValueError("evaluable target association opportunities do not cover every period uniformly")

    total_detections = 0
    total_clusters = 0
    total_selected = 0
    total_raw = 0
    total_hits = 0
    total_misses = 0
    total_matched_detections = 0
    for period in range(expected_period_count):
        counts = period_counts[period]
        cycle_detection_count = cycle_by_period[period]["detection_count"]
        if cycle_detection_count != counts["selected_candidate_count"]:
            raise ValueError(
                f"cycle_metrics detection_count disagrees with selected_candidate_count for period {period}: "
                f"{cycle_detection_count} != {counts['selected_candidate_count']}"
            )
        matched_keys: set[tuple[int, int, str]] = set()
        matched_opportunities = 0
        missed_opportunities: int | None = None
        unmatched_detections: int | None = None
        if task_status == "EVALUABLE":
            for row in association_by_period[period]:
                if not row["_matched"]:
                    continue
                det_id = str(row.get("det_id", "")).strip()
                if not det_id:
                    raise ValueError(f"matched target association lacks det_id in period {period}")
                key = (period, int(row["_beam"]), det_id)
                if key in matched_keys:
                    raise ValueError(f"one Production detection assigned to multiple truth opportunities: {key}")
                matched_keys.add(key)
            matched_opportunities = len(matched_keys)
            if matched_opportunities > cycle_detection_count:
                raise ValueError(f"matched target detections exceed final Detection rows in period {period}")
            missed_opportunities = expected_opportunities_per_period - matched_opportunities
            unmatched_detections = cycle_detection_count - matched_opportunities
            total_hits += matched_opportunities
            total_misses += missed_opportunities
            total_matched_detections += matched_opportunities
        total_detections += cycle_detection_count
        total_clusters += counts["postfilter_cluster_count"]
        total_selected += counts["selected_candidate_count"]
        total_raw += counts["raw_detection_count"]
        period_rows.append({
            "period_id": period,
            "final_detection_count": cycle_detection_count,
            **counts,
            "target_opportunity_count": expected_opportunities_per_period if task_status == "EVALUABLE" else None,
            "target_hit_opportunity_count": matched_opportunities if task_status == "EVALUABLE" else None,
            "target_missed_opportunity_count": missed_opportunities,
            "truth_matched_final_detection_count": matched_opportunities if task_status == "EVALUABLE" else None,
            "truth_unmatched_final_detection_count": unmatched_detections,
            "non_target_cluster_count": None,
            "non_target_cluster_status": "NOT_EVALUABLE_NO_CLUSTER_TRUTH_ASSOCIATION",
        })

    summary = {
        "production_output_status": "EVALUABLE" if task_status == "EVALUABLE" else "PRODUCTION_OUTPUTS_ONLY",
        "production_period_count": expected_period_count,
        "final_detection_count": total_detections,
        "postfilter_cluster_count": total_clusters,
        "selected_candidate_count": total_selected,
        "raw_detection_count": total_raw,
        "target_association_status": "EVALUABLE_FROZEN_TARGET_ASSOCIATIONS" if task_status == "EVALUABLE" else "NOT_EVALUABLE_TASK_STATUS",
        "target_opportunity_count": expected_period_count * expected_opportunities_per_period if task_status == "EVALUABLE" else None,
        "target_hit_opportunity_count": total_hits if task_status == "EVALUABLE" else None,
        "target_missed_opportunity_count": total_misses if task_status == "EVALUABLE" else None,
        "truth_matched_final_detection_count": total_matched_detections if task_status == "EVALUABLE" else None,
        "truth_unmatched_final_detection_count": total_detections - total_matched_detections if task_status == "EVALUABLE" else None,
        "non_target_cluster_count": None,
        "non_target_cluster_status": "NOT_EVALUABLE_NO_CLUSTER_TRUTH_ASSOCIATION",
        "evaluator_formula": "final Detection rows minus unique truth-matched det_id rows; NOT system Pfa",
    }
    return period_rows, summary


def _load_case(
    archive_path: Path,
    archive_record: Mapping[str, str],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    case_id = str(archive_record["case_id"])
    seed = int(archive_record["seed"])
    speed = float(archive_record["speed_mps"])
    snr = float(archive_record["snr_db"])
    delay = float(archive_record["delay_ns"])
    archive_sha = _sha256_file(archive_path)
    if archive_sha != archive_record["archive_sha256"]:
        raise ValueError(f"archive SHA-256 mismatch for {case_id}")
    if archive_path.stat().st_size != int(archive_record["archive_bytes"]):
        raise ValueError(f"archive byte-size mismatch for {case_id}")

    with zipfile.ZipFile(archive_path, "r") as archive:
        members = archive.namelist()
        run = _read_json_member(archive, "r22_run_manifest.json")
        review = _read_json_member(archive, "case_manifest_review_extract.json")
        task_rows = _read_csv_member(archive, "evaluation/task_metrics.csv")
        target_rows = _read_csv_member(archive, "evaluation/target_level_metrics.csv")
        association_rows = _read_csv_member(archive, "evaluation/target_associations.csv")
        parameter_rows = _read_csv_member(archive, "evaluation/parameter_metrics.csv")
        track_rows = _read_csv_member(archive, "evaluation/track_level_metrics.csv")

        design = run.get("design") or {}
        expected_identity = {
            "seed": seed,
            "radial_speed_mps": speed,
            "target_snr_db": snr,
            "delay_error_ns": delay,
            "target_count": 20,
            "period_count": 10,
        }
        for field, expected in expected_identity.items():
            actual = design.get(field)
            if actual is None or not math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=1e-9):
                raise ValueError(f"{case_id}: run manifest design {field}={actual!r}, expected {expected!r}")
        if run.get("source_identity", {}).get("commit") != SOURCE_COMMIT:
            raise ValueError(f"{case_id}: experiment source commit does not match V3.2")
        if run.get("production_binary", {}).get("sha256") != "d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e":
            raise ValueError(f"{case_id}: Production binary SHA differs from V3.2 report")
        if run.get("stage2_binary", {}).get("sha256") != "47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f":
            raise ValueError(f"{case_id}: Stage2 binary SHA differs from V3.2 report")
        if run.get("production_binary", {}).get("use_corex") is not False:
            raise ValueError(f"{case_id}: archived Production is not explicitly local Native CUDA")
        if str(run.get("production_binary", {}).get("backend", "")).lower() not in {"local native cuda", "native cuda"}:
            raise ValueError(f"{case_id}: archived backend is not Native CUDA")

        area = run.get("area_clutter_gate") or review.get("area_clutter_gate") or {}
        if str(area.get("status", "")).lower() != "passed":
            raise ValueError(f"{case_id}: effective area-clutter gate not passed")
        area_packets = int(area.get("continuous_area_packets", 0))
        area_samples = int(area.get("continuous_area_samples", 0))
        if area_packets <= 0 or area_samples <= 0:
            raise ValueError(f"{case_id}: area-clutter injection is zero")
        pair = review.get("pair_identity") or {}
        if int(pair.get("seed", -1)) != seed or float(pair.get("delay_error_ns", math.nan)) != delay:
            raise ValueError(f"{case_id}: paired role identity mismatch")
        if not str(pair.get("background_id", "")).strip():
            raise ValueError(f"{case_id}: paired background identity is missing")

        case_prefix = _case_prefix(members)
        truth_member = f"{case_prefix}/scenes/A1_ON/truth/truth_targets_by_beam.csv"
        truth_source = "A1_ON"
        if truth_member not in members:
            truth_member = f"{case_prefix}/scenes/A0_ON/truth/truth_targets_by_beam.csv"
            truth_source = "A0_ON_FALLBACK"
        truths = _read_csv_member(archive, truth_member)
        truth_map: dict[tuple[str, str, str], Mapping[str, str]] = {}
        for truth in truths:
            key = (str(_int(truth.get("period_id"))), str(_int(truth.get("beam_id"))), str(truth.get("target_id", "")).strip())
            if not all(key) or key in truth_map:
                raise ValueError(f"{case_id}: duplicate/invalid truth key {key}")
            truth_map[key] = truth
        visible_truth_keys = {
            key for key, row in truth_map.items()
            if str(row.get("visible", "1")).strip().lower() in {"", "1", "true", "yes", "y"}
        }
        if len(visible_truth_keys) != 200:
            raise ValueError(f"{case_id}: expected 200 visible truth opportunities, got {len(visible_truth_keys)}")

        tasks_by_key = _index_rows(task_rows, ("method_id", "mode", "role"), f"{case_id} task rows")
        target_by_unit: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        assoc_by_unit: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        for row in target_rows:
            if row.get("role") == "ON":
                target_by_unit[(str(row.get("method_id")), str(row.get("mode")))].append(row)
        for row in association_rows:
            if row.get("role") == "ON":
                assoc_by_unit[(str(row.get("method_id")), str(row.get("mode")))].append(row)
        track_by_unit = _index_rows(
            [row for row in track_rows if row.get("role") == "ON"],
            ("method_id", "mode", "role"), f"{case_id} track rows",
        )
        branch_member_by_name: dict[str, str] = {}
        for member in members:
            if member.startswith(f"{case_prefix}/production/") and member.endswith("/branch_manifest.json"):
                branch_member_by_name[PurePosixPath(member).parent.name] = member

        case_units: dict[str, dict[str, Any]] = {}
        case_metric_rows: list[dict[str, Any]] = []
        opportunity_audit_rows: list[dict[str, Any]] = []
        parameter_parity_rows: list[dict[str, Any]] = []
        delay_rows: list[dict[str, Any]] = []
        detection_file_rows: dict[str, int] = {}
        detection_periods_by_label: dict[str, set[int]] = {}
        core_truth_keys: dict[str, set[str]] = {}
        for method in METHODS:
            method_id, mode, label = method["method_id"], method["mode"], method["label"]
            task = tasks_by_key.get((method_id, mode, "ON"))
            if task is None:
                raise ValueError(f"{case_id}: missing selected ON task unit {method_id}/{mode}")
            task_status = str(task.get("status", ""))
            hits_value = _int(task.get("hits")) if task_status == "EVALUABLE" else None
            misses_value = _int(task.get("misses")) if task_status == "EVALUABLE" else None
            opportunities_value = _int(task.get("evaluable_truth_opportunities")) if task_status == "EVALUABLE" else None
            if task_status == "EVALUABLE" and (
                hits_value is None or misses_value is None or opportunities_value is None
                or hits_value + misses_value != opportunities_value or opportunities_value <= 0
            ):
                raise ValueError(f"{case_id}: malformed task endpoint for {label}")

            method_target_rows = target_by_unit.get((method_id, mode), [])
            target_index = _index_rows(method_target_rows, ("period_id", "beam_id", "target_id"), f"{case_id} {label} target rows")
            valid_keys = {
                _sample_key(key[2], int(key[0]), int(key[1]))
                for key, row in target_index.items() if row.get("status") == "EVALUABLE"
            }
            method_associations = assoc_by_unit.get((method_id, mode), [])
            assoc_index = _index_rows(method_associations, ("sample_key",), f"{case_id} {label} association rows")
            if task_status == "EVALUABLE":
                if len(valid_keys) != opportunities_value or set(assoc_index) != {(key,) for key in valid_keys}:
                    raise ValueError(f"{case_id}: task/target/association opportunity identities disagree for {label}")
            if label in CORE_LABELS:
                core_truth_keys[label] = valid_keys

            parameter_saved = _metric_row_map(parameter_rows, method_id, mode)
            association_position_errors: dict[str, float] = {}
            if label in CORE_LABELS:
                association_position_errors = _association_position_errors(
                    method_associations, f"{case_id} {label}"
                )
            track_row = track_by_unit.get((method_id, mode, "ON"))
            branch_name = method["branch"]
            branch_manifest_member = branch_member_by_name.get(branch_name)
            branch_status = "NOT_READ"
            branch_manifest: dict[str, Any] = {}
            branch_correction: dict[str, Any] = {}
            detection_count = 0
            detection_map: dict[tuple[str, str, str], Mapping[str, str]] | None = None
            detection_periods: set[int] = set()
            errors_by_metric: dict[str, dict[str, float]] = {metric: {} for metric in RECOMPUTABLE_METRICS}
            parameter_available_keys: set[str] = set()
            hit_set: set[str] = set()
            if task_status == "EVALUABLE":
                if branch_manifest_member is None:
                    raise ValueError(f"{case_id}: missing Production branch manifest for {label}")
                branch_manifest = _read_json_member(archive, branch_manifest_member)
                branch_status = str(branch_manifest.get("status", ""))
                if branch_status.lower() != "passed" or str(branch_manifest.get("production_status", "")).lower() != "passed" or int(branch_manifest.get("production_returncode", -1)) != 0:
                    raise ValueError(f"{case_id}: Production branch failed for evaluable unit {label}")

                if label in CORE_LABELS:
                    if branch_manifest.get("truth_used_in_estimator") is not False:
                        raise ValueError(f"{case_id}: {label} truth-use flag is not false")
                    if label in {"Fixed_D3", "Huber_D3_ON", "M1_Physics_Matched"} and branch_manifest.get("truth_used_to_apply_correction") is not False:
                        raise ValueError(f"{case_id}: {label} correction truth-use flag is not false")
                    branch_correction = {
                        "correction_source": branch_manifest.get("correction_source"),
                        "applied_delay_ns": _float(branch_manifest.get("applied_delay_ns")),
                        "input_role": branch_manifest.get("input_role"),
                        "truth_used_to_apply_correction": branch_manifest.get("truth_used_to_apply_correction"),
                    }
                    if label == "Fixed_D3" and branch_correction["correction_source"] != "R9_frozen_calibration":
                        raise ValueError(f"{case_id}: Fixed D3 is not the frozen R9 calibration")
                    if label in {"Huber_D3_ON", "M1_Physics_Matched"}:
                        expected_source = "same_seed_B_ON_raw_C1_C2_Huber_fit" if label == "Huber_D3_ON" else "same_seed_B_ON_raw_C1_C2_M1_fit"
                        if branch_correction["correction_source"] != expected_source:
                            raise ValueError(f"{case_id}: unexpected correction source for {label}")

                if label in CORE_LABELS:
                    detection_map, result_period_map, detection_count = _load_detections(archive, case_prefix, branch_name)
                    detection_periods = set(result_period_map.values())
                    detection_periods_by_label[label] = detection_periods
                    detection_file_rows[label] = detection_file_rows.get(label, 0) + len(result_period_map)
                    matched_det_ids: set[tuple[str, str, str]] = set()
                    for association in method_associations:
                        target_id = str(association.get("target_id", "")).strip()
                        period = _int(association.get("period_id"))
                        beam = _int(association.get("beam_id"))
                        if period is None or beam is None or not target_id:
                            raise ValueError(f"{case_id}: invalid association opportunity for {label}")
                        sample_key = str(association.get("sample_key", "")).strip()
                        if sample_key != _sample_key(target_id, period, beam):
                            raise ValueError(f"{case_id}: association sample_key not equal to production truth key for {label}")
                        truth = truth_map.get((str(period), str(beam), target_id))
                        if truth is None:
                            raise ValueError(f"{case_id}: association truth key is absent for {label}: {sample_key}")
                        matched = _truth(association.get("matched"))
                        if matched is None:
                            raise ValueError(f"{case_id}: malformed match flag for {label}/{sample_key}")
                        parameter_product_available = period in detection_periods
                        if parameter_product_available:
                            parameter_available_keys.add(sample_key)
                        det_id = str(association.get("det_id", "")).strip() if matched else ""
                        if matched:
                            if not det_id:
                                raise ValueError(f"{case_id}: matched association lacks det_id for {label}/{sample_key}")
                            hit_set.add(sample_key)
                            if parameter_product_available:
                                det_key = (str(period), str(beam), det_id)
                                if det_key not in detection_map:
                                    raise ValueError(f"{case_id}: matched det_id not found in retained Production result for {label}: {det_key}")
                                if det_key in matched_det_ids:
                                    raise ValueError(f"{case_id}: one Production detection assigned to multiple truth opportunities for {label}: {det_key}")
                                matched_det_ids.add(det_key)
                                detection = detection_map[det_key]
                                evaluated_errors = r19._parameter_errors(truth, detection, sample_key)
                                for metric in RECOMPUTABLE_METRICS:
                                    sample = evaluated_errors.get(metric)
                                    if sample is not None:
                                        error = _float(sample.get("error"))
                                        if error is None:
                                            raise ValueError(f"{case_id}: non-finite {metric} error for {label}/{sample_key}")
                                        errors_by_metric[metric][sample_key] = error
                        opportunity_audit_rows.append({
                            "case_id": case_id, "seed": seed, "speed_mps": speed, "snr_db": snr,
                            "delay_ns": delay, "method_label": label, "method_id": method_id,
                            "mode": mode, "sample_key": sample_key, "target_id": target_id,
                            "period_id": period, "beam_id": beam, "matched": matched,
                            "det_id": det_id,
                            "parameter_product_available": parameter_product_available,
                            "position_m_all_periods_association": association_position_errors.get(sample_key),
                            "position_m_all_periods_source": (
                                "evaluation/target_associations.csv; V3.2 r19_task_evaluator._position_error"
                                if label in CORE_LABELS else "NOT_IN_CORE_METHOD_AUDIT"
                            ),
                            **{metric: errors_by_metric[metric].get(sample_key) for metric in RECOMPUTABLE_METRICS},
                            "ground_speed_status": "NOT_RECONSTRUCTABLE_TRACK_DEBUG_NOT_IN_COMPACT_ZIP",
                        })
                    if len(hit_set) != hits_value:
                        raise ValueError(f"{case_id}: {label} association hits ({len(hit_set)}) disagree with task hits ({hits_value})")
                    if len(method_associations) != opportunities_value:
                        raise ValueError(f"{case_id}: {label} association row count disagrees with task denominator")
                    if label in CORE_LABELS and set(association_position_errors) != hit_set:
                        raise ValueError(
                            f"{case_id}: {label} matched opportunities do not have one finite saved position error"
                        )

                    for metric in RECOMPUTABLE_METRICS:
                        sample_errors = errors_by_metric[metric]
                        count, own_rmse = _rmse(sample_errors.values())
                        saved = parameter_saved.get(metric)
                        saved_count = _int(saved.get("own_success_count")) if saved else None
                        saved_rmse = _float(saved.get("own_success_rmse")) if saved else None
                        saved_valid = _int(saved.get("valid_parameter_opportunities")) if saved else None
                        full_product_coverage = detection_periods == set(range(10))
                        count_match = saved_count == count and saved_valid == count if full_product_coverage else None
                        rmse_match = (
                            (own_rmse is None and saved_rmse is None)
                            or (own_rmse is not None and saved_rmse is not None and math.isclose(own_rmse, saved_rmse, rel_tol=0.0, abs_tol=1e-8))
                        ) if full_product_coverage else None
                        parameter_parity_rows.append({
                            "case_id": case_id, "seed": seed, "speed_mps": speed, "snr_db": snr,
                            "method_label": label, "metric": metric, "recomputed_count": count,
                            "saved_count": saved_count, "recomputed_own_success_rmse": own_rmse,
                            "saved_own_success_rmse": saved_rmse, "count_match": count_match,
                            "rmse_match_abs_1e_8": rmse_match,
                            "recomputed_period_ids": ";".join(str(x) for x in sorted(detection_periods)),
                            "recomputed_period_count": len(detection_periods),
                            "status": ("PASS" if count_match and rmse_match else "MISMATCH") if full_product_coverage else "PARTIAL_ARCHIVE_TARGET_PERIOD_PRODUCTS",
                            "evaluator_source": "V3.2 r19_task_evaluator._parameter_errors; target_id/period_id/beam_id/det_id lineage",
                        })

                    association_count, association_rmse = _rmse(association_position_errors.values())
                    saved_position = parameter_saved.get("position_m")
                    saved_position_count = _int(saved_position.get("own_success_count")) if saved_position else None
                    saved_position_rmse = _float(saved_position.get("own_success_rmse")) if saved_position else None
                    saved_position_valid = _int(saved_position.get("valid_parameter_opportunities")) if saved_position else None
                    association_parity = (
                        association_count == saved_position_count == saved_position_valid
                        and association_rmse is not None and saved_position_rmse is not None
                        and math.isclose(association_rmse, saved_position_rmse, rel_tol=0.0, abs_tol=1e-8)
                    )
                    parameter_parity_rows.append({
                        "case_id": case_id, "seed": seed, "speed_mps": speed, "snr_db": snr,
                        "method_label": label, "metric": "position_m_all_periods_association",
                        "recomputed_count": association_count, "saved_count": saved_position_count,
                        "recomputed_own_success_rmse": association_rmse,
                        "saved_own_success_rmse": saved_position_rmse,
                        "count_match": association_count == saved_position_count == saved_position_valid,
                        "rmse_match_abs_1e_8": (
                            math.isclose(association_rmse, saved_position_rmse, rel_tol=0.0, abs_tol=1e-8)
                            if association_rmse is not None and saved_position_rmse is not None else False
                        ),
                        "recomputed_period_ids": "0;1;2;3;4;5;6;7;8;9",
                        "recomputed_period_count": 10,
                        "status": "PASS" if association_parity else "MISMATCH",
                        "evaluator_source": "V3.2 evaluation/target_associations.csv position_error_m; r19_task_evaluator._position_error",
                    })

            for metric in ("ground_speed_mps",):
                saved = parameter_saved.get(metric)
                parameter_parity_rows.append({
                    "case_id": case_id, "seed": seed, "speed_mps": speed, "snr_db": snr,
                    "method_label": label, "metric": metric,
                    "recomputed_count": None, "saved_count": _int(saved.get("own_success_count")) if saved else None,
                    "recomputed_own_success_rmse": None, "saved_own_success_rmse": _float(saved.get("own_success_rmse")) if saved else None,
                    "count_match": None, "rmse_match_abs_1e_8": None,
                    "status": "AGGREGATE_ONLY_TRACK_DEBUG_NOT_ARCHIVED",
                    "evaluator_source": "V3.2 parameter_metrics.csv; no per-opportunity TrackManager output payload in compact ZIP",
                })

            production_output_periods: list[dict[str, Any]] = []
            production_output: dict[str, Any] = {
                "production_output_status": "NOT_AVAILABLE_BRANCH_MANIFEST_MISSING",
                "production_period_count": 0,
                "final_detection_count": None,
                "postfilter_cluster_count": None,
                "selected_candidate_count": None,
                "raw_detection_count": None,
                "target_association_status": "NOT_EVALUABLE_PRODUCTION_OUTPUTS_UNAVAILABLE",
                "target_opportunity_count": None,
                "target_hit_opportunity_count": None,
                "target_missed_opportunity_count": None,
                "truth_matched_final_detection_count": None,
                "truth_unmatched_final_detection_count": None,
                "non_target_cluster_count": None,
                "non_target_cluster_status": "NOT_EVALUABLE_NO_CLUSTER_TRUTH_ASSOCIATION",
                "evaluator_formula": "final Detection rows minus unique truth-matched det_id rows; NOT system Pfa",
                "production_output_error": "branch_manifest_missing",
            }
            if branch_manifest_member is not None:
                if not branch_manifest:
                    branch_manifest = _read_json_member(archive, branch_manifest_member)
                    branch_status = str(branch_manifest.get("status", ""))
                production_passed = (
                    branch_status.lower() == "passed"
                    and str(branch_manifest.get("production_status", "")).lower() == "passed"
                    and _int(branch_manifest.get("production_returncode")) == 0
                )
                if production_passed:
                    cycle_member = f"{case_prefix}/production/{branch_name}/cycle_metrics.csv"
                    lineage_member = f"{case_prefix}/production/{branch_name}/result/debug/cluster_detection_lineage.csv"
                    if cycle_member in members and lineage_member in members:
                        try:
                            retained_detection_counts = None
                            if label in CORE_LABELS and task_status == "EVALUABLE":
                                if detection_map is None:
                                    raise ValueError(f"retained Detection map unavailable for {label}")
                                retained_detection_counts = Counter(int(key[0]) for key in detection_map)
                            production_output_periods, production_output = _audit_production_output(
                                _read_csv_member(archive, cycle_member),
                                _read_csv_member(archive, lineage_member),
                                method_associations,
                                task_status=task_status,
                                expected_period_count=10,
                                expected_opportunities_per_period=20,
                                retained_detection_counts=retained_detection_counts,
                            )
                        except ValueError as exc:
                            production_output_periods = []
                            production_output = {
                                "production_output_status": "NOT_EVALUABLE_PRODUCTION_OUTPUT_AUDIT_FAILED",
                                "production_period_count": 0,
                                "final_detection_count": None,
                                "postfilter_cluster_count": None,
                                "selected_candidate_count": None,
                                "raw_detection_count": None,
                                "target_association_status": "NOT_EVALUABLE_PRODUCTION_OUTPUT_AUDIT_FAILED",
                                "target_opportunity_count": None,
                                "target_hit_opportunity_count": None,
                                "target_missed_opportunity_count": None,
                                "truth_matched_final_detection_count": None,
                                "truth_unmatched_final_detection_count": None,
                                "non_target_cluster_count": None,
                                "non_target_cluster_status": "NOT_EVALUABLE_NO_CLUSTER_TRUTH_ASSOCIATION",
                                "evaluator_formula": "final Detection rows minus unique truth-matched det_id rows; NOT system Pfa",
                                "production_output_error": str(exc),
                            }
                    else:
                        production_output["production_output_status"] = "NOT_AVAILABLE_PRODUCTION_OUTPUT_FILES_MISSING"
                        production_output["production_output_error"] = "cycle_metrics.csv or cluster_detection_lineage.csv missing"
                else:
                    production_output["production_output_status"] = "NOT_AVAILABLE_PRODUCTION_NOT_PASSED"
                    production_output["production_output_error"] = "Production branch manifest does not record a successful process"

            unit = {
                "case_id": case_id, "seed": seed, "speed_mps": speed, "snr_db": snr,
                "method_id": method_id, "mode": mode, "label": label,
                "task_status": task_status, "hits": hits_value,
                "misses": misses_value, "opportunities": opportunities_value,
                "hit_rate": hits_value / opportunities_value if task_status == "EVALUABLE" else None,
                "valid_keys": valid_keys if task_status == "EVALUABLE" else set(),
                "parameter_available_keys": parameter_available_keys,
                "detection_period_ids": detection_periods,
                "hit_set": hit_set if task_status == "EVALUABLE" and label in CORE_LABELS else set(),
                "association_position_errors": association_position_errors,
                "errors": errors_by_metric,
                "saved_parameter_rows": parameter_saved,
                "track_row": track_row,
                "branch_status": branch_status,
                "branch_correction": branch_correction,
                "production_output": production_output,
                "production_output_periods": production_output_periods,
            }
            case_units[label] = unit
            case_metric_rows.append({
                "case_id": case_id, "seed": seed, "speed_mps": speed, "snr_db": snr,
                "delay_ns": delay, "method_label": label, "method_id": method_id, "mode": mode,
                "status": task_status, "hits": hits_value, "misses": misses_value,
                "evaluable_truth_opportunities": opportunities_value, "hit_fraction": unit["hit_rate"],
                "matched_detection_opportunities": _int(parameter_saved.get("position_m", {}).get("matched_detection_opportunities")) if parameter_saved.get("position_m") else None,
                "evaluator_status": task_status,
                "target_confirmed_unique_period_count": _int(track_row.get("target_confirmed_unique_period_count")) if track_row else None,
                "target_confirmed_observation_count": _int(track_row.get("target_confirmed_observation_count")) if track_row else None,
                "post_confirmation_match_rate": _float(track_row.get("post_confirmation_match_rate")) if track_row else None,
                "false_confirmed_track_count": _int(track_row.get("false_confirmed_track_count")) if track_row else None,
                "unmatched_confirmed_payload_count": _int(track_row.get("unmatched_confirmed_payload_count")) if track_row else None,
                "unique_target_track_count": _int(track_row.get("unique_target_track_count")) if track_row else None,
                "track_fragmentation_count_observed": _int(track_row.get("track_fragmentation_count_observed")) if track_row else None,
                "cross_target_association_payload_count": _int(track_row.get("cross_target_association_payload_count")) if track_row else None,
                **{
                    f"{metric}_own_success_rmse": _float(parameter_saved[metric].get("own_success_rmse")) if metric in parameter_saved else None
                    for metric in PARAMETER_METRICS
                },
                **{
                    f"{metric}_own_success_count": _int(parameter_saved[metric].get("own_success_count")) if metric in parameter_saved else None
                    for metric in PARAMETER_METRICS
                },
            })

        # Tie the archived estimator records to the actual correction branches.
        for label in ("Fixed_D3", "Huber_D3_ON", "M1_Physics_Matched"):
            unit = case_units[label]
            correction = unit["branch_correction"]
            applied = correction.get("applied_delay_ns")
            if applied is None:
                raise ValueError(f"{case_id}: {label} has no applied physical-delay record")
            if label == "Huber_D3_ON":
                estimator = (run.get("r22_blind_method_qualification") or {}).get("d3_huber") or {}
                expected_correction = _float(estimator.get("correction_ns"))
                if expected_correction is None or not math.isclose(applied, expected_correction, rel_tol=0.0, abs_tol=1e-12):
                    raise ValueError(f"{case_id}: Huber branch correction differs from qualification record")
                if str((run.get("r22_blind_method_qualification") or {}).get("input_role", "")) != "A1_Current_unknown_error_ON_raw_C1_C2_only":
                    raise ValueError(f"{case_id}: Huber/M1 input is not documented as target-present raw C1/C2")
                fit = estimator.get("source_record") or {}
            elif label == "M1_Physics_Matched":
                estimator = (run.get("r22_blind_method_qualification") or {}).get("m1") or {}
                expected_correction = _float(estimator.get("correction_ns"))
                if expected_correction is None or not math.isclose(applied, expected_correction, rel_tol=0.0, abs_tol=1e-12):
                    raise ValueError(f"{case_id}: M1 branch correction differs from qualification record")
                if str((run.get("r22_blind_method_qualification") or {}).get("input_role", "")) != "A1_Current_unknown_error_ON_raw_C1_C2_only":
                    raise ValueError(f"{case_id}: Huber/M1 input is not documented as target-present raw C1/C2")
                fit = estimator.get("source_record") or {}
            else:
                estimator = {}
                fit = {}
            delay_rows.append({
                "case_id": case_id, "seed": seed, "speed_mps": speed, "snr_db": snr,
                "method_label": label, "true_delay_ns_evaluator_only": delay,
                "applied_delay_ns": applied, "signed_residual_estimate_minus_true_ns": applied - delay,
                "absolute_residual_ns": abs(applied - delay),
                "correction_source": correction.get("correction_source"),
                "input_role": correction.get("input_role"),
                "estimator_input_role": (run.get("r22_blind_method_qualification") or {}).get("input_role") if label != "Fixed_D3" else "historical_static_R9_calibration",
                "estimator_input_period_count": int(design.get("period_count", 0)) if label != "Fixed_D3" else 0,
                "estimator_input_pulse_count": _int((run.get("r22_delay_estimator") or {}).get("input_pulse_count")) if label != "Fixed_D3" else None,
                "estimator_fit_runtime_recorded": False,
                "estimator_execution_scope": "same-case full-period batch before Production" if label != "Fixed_D3" else "historical fixed calibration; no per-case fit",
                "truth_used_in_estimator": False,
                "truth_used_to_apply_correction": correction.get("truth_used_to_apply_correction"),
                "support_count": fit.get("support_count"),
                "fit_phase_rmse_rad": fit.get("phase_rmse_rad"),
                "fit_r2": fit.get("r2"),
                "fit_normalized_model_residual": fit.get("normalized_model_residual"),
                "status": "EVALUABLE_DELAY_ESTIMATE_DIAGNOSTIC",
            })

        # If a selected main method has different truth keys, retain the case
        # but suppress its paired/common-success contribution downstream.
        main_keys_match = all(core_truth_keys.get(label) == core_truth_keys.get(CORE_LABELS[0]) for label in CORE_LABELS)
        common_detection_periods = set.intersection(*(detection_periods_by_label.get(label, set()) for label in CORE_LABELS)) if all(label in detection_periods_by_label for label in CORE_LABELS) else set()
        case_validation = {
            "case_id": case_id, "seed": seed, "speed_mps": speed, "snr_db": snr,
            "delay_ns": delay, "archive_name": archive_path.name,
            "archive_bytes": archive_path.stat().st_size, "archive_sha256": archive_sha,
            "archive_sha_matches_index": True, "experiment_source_commit": SOURCE_COMMIT,
            "target_truth_source": truth_source, "visible_truth_opportunities": len(visible_truth_keys),
            "area_clutter_packets": area_packets, "area_clutter_samples": area_samples,
            "measured_area_clutter_power": _float(area.get("area_mean_power_measured", area.get("mean_power"))),
            "paired_background_id_present": True, "core_methods_evaluable": all(case_units[label]["task_status"] == "EVALUABLE" for label in CORE_LABELS),
            "core_method_truth_keys_match": main_keys_match,
            "core_method_production_branches_passed": all(case_units[label]["branch_status"].lower() == "passed" for label in CORE_LABELS),
            "core_methods_retained_detection_periods": ";".join(
                f"{label}:{','.join(str(x) for x in sorted(detection_periods_by_label.get(label, set())))}" for label in CORE_LABELS
            ),
            "core_common_retained_detection_periods": ";".join(str(x) for x in sorted(common_detection_periods)),
            "core_common_retained_detection_period_count": len(common_detection_periods),
            "full_10_period_parameter_products_available": common_detection_periods == set(range(10)),
            "ground_speed_per_opportunity_available": any("track_debug_runs" in name for name in members),
            "production_detection_csv_count_core_methods": sum(detection_file_rows.get(label, 0) for label in CORE_LABELS),
        }
        return case_validation, case_metric_rows, opportunity_audit_rows, parameter_parity_rows, delay_rows, case_units


def _read_archive_index() -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    with ARCHIVE_INDEX_PATH.open(newline="", encoding="utf-8") as stream:
        index = list(csv.DictReader(stream))
    actual_files = {path.name for path in ARCHIVE_ROOT.glob("*.zip")}
    expected_names = {f"{row['case_id']}__attempt_{int(row['attempt']):03d}.zip" for row in index}
    if len(index) != 288 or len(expected_names) != 288:
        raise ValueError(f"V3.2 archive index expected 288 unique case rows; got {len(index)}")
    if len(actual_files) != 289 or not expected_names.issubset(actual_files):
        raise ValueError(f"expected 289 local ZIPs with 288 indexed final cases; found {len(actual_files)}")
    for row in index:
        expected = f"{row['case_id']}__attempt_{int(row['attempt']):03d}.zip"
        path = ARCHIVE_ROOT / expected
        if not path.is_file() or path.stat().st_size != int(row["archive_bytes"]):
            raise ValueError(f"indexed archive missing or size mismatch: {expected}")
    used = [row for row in index if math.isclose(float(row["delay_ns"]), 4.0, abs_tol=1e-12)]
    if len(used) != 144:
        raise ValueError(f"expected 144 +4 ns cases, got {len(used)}")
    used.sort(key=lambda row: (int(row["seed"]), float(row["speed_mps"]), float(row["snr_db"])))
    extras = sorted(actual_files - expected_names)
    extra_records = [{"archive_name": name, "used_in_v3_3": False, "reason": "unindexed historical attempt; preserved"} for name in extras]
    return used, extra_records


def _aggregate_detection(case_units: Mapping[str, Mapping[str, Any]], case_metric_rows: Sequence[Mapping[str, Any]], seeds: Sequence[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_case_label = {(str(row["case_id"]), str(row["method_label"])): row for row in case_metric_rows}
    all_case_ids = sorted({str(row["case_id"]) for row in case_metric_rows})
    # The tuple key lets each case preserve its own evaluator opportunity IDs.
    units_by_case: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for case_id in all_case_ids:
        for label in METHOD_BY_LABEL:
            row = by_case_label.get((case_id, label))
            if row is not None:
                units_by_case[case_id][label] = row

    seed_rows: list[dict[str, Any]] = []
    for seed in seeds:
        case_ids = sorted({case_id for case_id in all_case_ids if int(by_case_label[(case_id, METHODS[0]["label"])]["seed"]) == seed})
        for method in METHODS:
            label = method["label"]
            rows = [by_case_label[(case_id, label)] for case_id in case_ids if (case_id, label) in by_case_label]
            valid = [row for row in rows if row.get("status") == "EVALUABLE"]
            hits = sum(int(row["hits"]) for row in valid)
            opportunities = sum(int(row["evaluable_truth_opportunities"]) for row in valid)
            seed_rows.append({
                "seed": seed, "method_label": label, "method_id": method["method_id"], "mode": method["mode"],
                "expected_case_count": 12, "case_row_count": len(rows), "evaluable_case_count": len(valid),
                "hits": hits if opportunities else None, "evaluable_truth_opportunities": opportunities if opportunities else None,
                "hit_fraction": hits / opportunities if opportunities else None,
                "coverage_of_planned_truth_opportunities": opportunities / 2400.0,
                "inference_unit": "independent seed; 12 cases and 2400 truth-period opportunities are nested descriptive observations",
            })

    scenario_rows: list[dict[str, Any]] = []
    group: dict[tuple[str, float, float, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in case_metric_rows:
        if row.get("status") == "EVALUABLE":
            group[(str(row["method_label"]), float(row["snr_db"]), float(row["speed_mps"]), str(row["seed"]))].append(row)
    for method in METHODS:
        label = method["label"]
        for snr in (10.0, 20.0, 30.0):
            for speed in (0.5, 1.5, 2.5, 6.7):
                values = [row["hit_fraction"] for (m, s, v, _), rows in group.items() if m == label and s == snr and v == speed for row in rows if row.get("hit_fraction") is not None]
                scenario_rows.append({
                    "method_label": label, "snr_db": snr, "speed_mps": speed,
                    "evaluable_seed_count": len(values), "expected_seed_count": 12,
                    "mean_seed_hit_fraction": _mean(values), "sd_across_seed_hit_fraction": _sd(values),
                    "status": "EVALUABLE" if values else "NOT_EVALUABLE",
                    "inference": "descriptive Screening; seed is the independent unit",
                })

    paired_rows: list[dict[str, Any]] = []
    for method in METHODS:
        label = method["label"]
        if label == "PK":
            continue
        for seed in seeds:
            seed_case_ids = [case_id for case_id in all_case_ids if int(by_case_label[(case_id, "PK")]["seed"]) == seed]
            paired_cases: list[str] = []
            for case_id in seed_case_ids:
                left = by_case_label.get((case_id, label))
                right = by_case_label.get((case_id, "PK"))
                if not left or not right or left.get("status") != "EVALUABLE" or right.get("status") != "EVALUABLE":
                    continue
                uleft = case_units.get(case_id, {})
                if not _same_key_sets(uleft[label].get("valid_keys", set()), uleft["PK"].get("valid_keys", set())):
                    continue
                paired_cases.append(case_id)
            n = 200 * len(paired_cases)
            method_hits = sum(int(by_case_label[(case_id, label)]["hits"]) for case_id in paired_cases)
            known_hits = sum(int(by_case_label[(case_id, "PK")]["hits"]) for case_id in paired_cases)
            paired_rows.append({
                "seed": seed, "method_label": label, "reference_method": "PK",
                "expected_case_count": 12, "paired_evaluable_case_count": len(paired_cases),
                "paired_truth_opportunities": n,
                "method_hits_on_paired_cases": method_hits if n else None,
                "pk_hits_on_same_cases": known_hits if n else None,
                "method_hit_fraction": method_hits / n if n else None,
                "pk_hit_fraction_same_cases": known_hits / n if n else None,
                "residual_gap_pk_minus_method": (known_hits - method_hits) / n if n else None,
                "status": "EVALUABLE" if n else "NOT_EVALUABLE_NO_COMMON_CASES",
                "warning": "method failures are omitted from the detection denominator and reported by coverage; not converted to misses",
            })

    overlap_rows: list[dict[str, Any]] = []
    fourway_rows: list[dict[str, Any]] = []
    for seed in seeds:
        seed_case_ids = [case_id for case_id in all_case_ids if int(by_case_label[(case_id, "PK")]["seed"]) == seed]
        totals = {"both_hit": 0, "left_only": 0, "right_only": 0, "both_miss": 0, "n": 0}
        fourway_n = fourway_both = 0
        for left_label, right_label in CORE_PAIRS:
            counts = {"both_hit": 0, "left_only": 0, "right_only": 0, "both_miss": 0, "n": 0}
            for case_id in seed_case_ids:
                left, right = case_units[case_id][left_label], case_units[case_id][right_label]
                if left["task_status"] != "EVALUABLE" or right["task_status"] != "EVALUABLE":
                    continue
                if not _same_key_sets(left["valid_keys"], right["valid_keys"]):
                    continue
                keys = left["valid_keys"]
                lh, rh = left["hit_set"], right["hit_set"]
                counts["both_hit"] += len(lh & rh)
                counts["left_only"] += len(lh - rh)
                counts["right_only"] += len(rh - lh)
                counts["both_miss"] += len(keys - (lh | rh))
                counts["n"] += len(keys)
            overlap_rows.append({
                "seed": seed, "method_a": left_label, "method_b": right_label,
                **counts,
                "common_hit_fraction": counts["both_hit"] / counts["n"] if counts["n"] else None,
                "a_only_fraction": counts["left_only"] / counts["n"] if counts["n"] else None,
                "b_only_fraction": counts["right_only"] / counts["n"] if counts["n"] else None,
                "status": "EVALUABLE" if counts["n"] else "NOT_EVALUABLE",
                "inference": "seed-level paired opportunity counts; no significance claim",
            })
        for case_id in seed_case_ids:
            selected = [case_units[case_id][label] for label in CORE_LABELS]
            if any(row["task_status"] != "EVALUABLE" for row in selected):
                continue
            if not _same_key_sets(*(row["valid_keys"] for row in selected)):
                continue
            keys = selected[0]["valid_keys"]
            common = set.intersection(*(row["hit_set"] for row in selected))
            fourway_n += len(keys)
            fourway_both += len(common)
        fourway_rows.append({
            "seed": seed, "methods": ";".join(CORE_LABELS),
            "evaluable_truth_opportunities": fourway_n,
            "all_four_methods_hit": fourway_both,
            "all_four_common_hit_fraction": fourway_both / fourway_n if fourway_n else None,
            "status": "EVALUABLE" if fourway_n else "NOT_EVALUABLE",
            "inference": "seed-level descriptive overlap; 2400 opportunities are not independent seeds",
        })
    return seed_rows, scenario_rows, paired_rows, overlap_rows, fourway_rows


def _aggregate_parameters(case_units: Mapping[str, Mapping[str, Any]], seeds: Sequence[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    cases_by_seed: dict[int, list[str]] = defaultdict(list)
    for case_id, units in case_units.items():
        if "PK" in units:
            cases_by_seed[int(units["PK"]["seed"])].append(case_id)

    own_seed_rows: list[dict[str, Any]] = []
    pair_seed_rows: list[dict[str, Any]] = []
    fourway_seed_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    own_cache: dict[tuple[int, str, str], tuple[int, float | None, int]] = {}
    for seed in seeds:
        case_ids = sorted(cases_by_seed.get(seed, []))
        denom_by_method = {
            label: sum(int(case_units[case_id][label]["opportunities"] or 0) for case_id in case_ids if case_units[case_id][label]["task_status"] == "EVALUABLE")
            for label in CORE_LABELS
        }
        for method_label in CORE_LABELS:
            for metric in PARAMETER_METRICS:
                retained_product_denominator = sum(
                    len(case_units[case_id][method_label]["parameter_available_keys"])
                    for case_id in case_ids
                    if case_units[case_id][method_label]["task_status"] == "EVALUABLE"
                )
                retained_period_ids = set().union(*(
                    case_units[case_id][method_label]["detection_period_ids"]
                    for case_id in case_ids
                    if case_units[case_id][method_label]["task_status"] == "EVALUABLE"
                )) if case_ids else set()
                archived_full_count = 0
                archived_full_sum_sq = 0.0
                for case_id in case_ids:
                    unit = case_units[case_id][method_label]
                    if unit["task_status"] != "EVALUABLE":
                        continue
                    saved = unit["saved_parameter_rows"].get(metric)
                    if saved:
                        saved_count = _int(saved.get("own_success_count")) or 0
                        saved_rmse = _float(saved.get("own_success_rmse"))
                        if saved_count and saved_rmse is not None:
                            archived_full_count += saved_count
                            archived_full_sum_sq += saved_count * saved_rmse * saved_rmse
                archived_full_rmse = math.sqrt(archived_full_sum_sq / archived_full_count) if archived_full_count else None
                if metric == "ground_speed_mps":
                    n = 0
                    sum_sq = 0.0
                    for case_id in case_ids:
                        saved = case_units[case_id][method_label]["saved_parameter_rows"].get(metric)
                        if saved:
                            count = _int(saved.get("own_success_count")) or 0
                            rmse = _float(saved.get("own_success_rmse"))
                            if count and rmse is not None:
                                n += count
                                sum_sq += count * rmse * rmse
                    rmse = math.sqrt(sum_sq / n) if n else None
                    own_status = "AGGREGATE_ONLY_NO_PER_OPPORTUNITY_TRACK_PAYLOAD"
                else:
                    values: list[float] = []
                    for case_id in case_ids:
                        values.extend(case_units[case_id][method_label]["errors"].get(metric, {}).values())
                    n, rmse = _rmse(values)
                    own_status = "EVALUABLE" if n else "NOT_EVALUABLE_NO_VALID_PARAMETER_OPPORTUNITIES"
                denominator = denom_by_method[method_label]
                own_cache[(seed, method_label, metric)] = (n, rmse, denominator)

                common_counts = {label: 0 for label in CORE_LABELS}
                common_sum_sq = {label: 0.0 for label in CORE_LABELS}
                common_denominator = 0
                common_period_ids: set[int] = set()
                if metric in RECOMPUTABLE_METRICS:
                    for case_id in case_ids:
                        group = [case_units[case_id][label] for label in CORE_LABELS]
                        if any(row["task_status"] != "EVALUABLE" for row in group):
                            continue
                        if not _same_key_sets(*(row["valid_keys"] for row in group)):
                            continue
                        common_product_keys = set.intersection(*(row["parameter_available_keys"] for row in group))
                        common_denominator += len(common_product_keys)
                        common_period_ids.update(set.intersection(*(row["detection_period_ids"] for row in group)))
                        metric_sets = [set(row["errors"].get(metric, {})) for row in group]
                        common = set.intersection(*metric_sets) & common_product_keys if metric_sets else set()
                        for label, row in zip(CORE_LABELS, group):
                            errors = row["errors"].get(metric, {})
                            common_counts[label] += len(common)
                            common_sum_sq[label] += sum(errors[key] ** 2 for key in common)
                    common_n = common_counts[method_label]
                    if common_n:
                        common_rmse = math.sqrt(common_sum_sq[method_label] / common_n)
                    else:
                        common_rmse = None
                    fourway_status = "EVALUABLE" if common_n else "NOT_EVALUABLE_NO_FOURWAY_COMMON_PARAMETER_SUCCESS"
                else:
                    common_n = 0
                    common_rmse = None
                    fourway_status = "NOT_EVALUABLE_TRACK_DEBUG_NOT_ARCHIVED" if metric == "ground_speed_mps" else "NOT_EVALUABLE_NO_VALID_PARAMETER_OPPORTUNITIES"
                common_n_per_method = common_counts[method_label]
                own_seed_rows.append({
                    "seed": seed, "method_label": method_label, "metric": metric,
                    "evaluable_case_count": sum(case_units[c][method_label]["task_status"] == "EVALUABLE" for c in case_ids),
                    "truth_opportunities": denominator,
                    "retained_product_truth_opportunities": retained_product_denominator,
                    "retained_product_period_ids": ";".join(str(x) for x in sorted(retained_period_ids)),
                    "own_success_count": n,
                    "own_success_coverage": n / denominator if denominator else None,
                    "own_success_coverage_retained_products": n / retained_product_denominator if retained_product_denominator and metric in RECOMPUTABLE_METRICS else None,
                    "own_success_rmse": rmse, "own_success_status": own_status,
                    "own_success_scope": "retained_target_period_products_only" if metric in RECOMPUTABLE_METRICS else "archived_full_period_aggregate_only",
                    "archived_full_period_own_success_count": archived_full_count,
                    "archived_full_period_own_success_coverage": archived_full_count / denominator if denominator else None,
                    "archived_full_period_own_success_rmse": archived_full_rmse,
                    "archived_full_period_status": "AGGREGATE_ONLY_NOT_RECOMPUTED_FROM_RETAINED_PRODUCTS",
                    "fourway_common_truth_opportunities": common_denominator,
                    "fourway_common_parameter_success_count": common_n_per_method,
                    "fourway_common_success_coverage": common_n_per_method / common_denominator if common_denominator else None,
                    "fourway_common_success_rmse": common_rmse,
                    "fourway_status": f"{fourway_status}_RETAINED_PERIOD_SUBSET" if fourway_status == "EVALUABLE" else fourway_status,
                    "fourway_common_retained_period_ids": ";".join(str(x) for x in sorted(common_period_ids)),
                    "full_10_period_fourway_status": "EVALUABLE" if common_period_ids == set(range(10)) else "NOT_EVALUABLE_ARCHIVE_RETAINS_INCOMPLETE_PERIOD_PRODUCTS",
                    "seed_is_inference_unit": True,
                })

        for left_label, right_label in CORE_PAIRS:
            for metric in PARAMETER_METRICS:
                n_common = 0
                denom = 0
                retained_product_denom = 0
                retained_period_ids: set[int] = set()
                sum_sq_left = sum_sq_right = 0.0
                for case_id in case_ids:
                    left, right = case_units[case_id][left_label], case_units[case_id][right_label]
                    if left["task_status"] != "EVALUABLE" or right["task_status"] != "EVALUABLE":
                        continue
                    if not _same_key_sets(left["valid_keys"], right["valid_keys"]):
                        continue
                    denom += len(left["valid_keys"])
                    pair_product_keys = left["parameter_available_keys"] & right["parameter_available_keys"]
                    retained_product_denom += len(pair_product_keys)
                    retained_period_ids.update(left["detection_period_ids"] & right["detection_period_ids"])
                    if metric == "ground_speed_mps":
                        continue
                    left_errors, right_errors = left["errors"].get(metric, {}), right["errors"].get(metric, {})
                    common = set(left_errors) & set(right_errors) & pair_product_keys
                    n_common += len(common)
                    sum_sq_left += sum(left_errors[key] ** 2 for key in common)
                    sum_sq_right += sum(right_errors[key] ** 2 for key in common)
                left_rmse = math.sqrt(sum_sq_left / n_common) if n_common else None
                right_rmse = math.sqrt(sum_sq_right / n_common) if n_common else None
                status = "EVALUABLE" if n_common else (
                    "NOT_EVALUABLE_TRACK_DEBUG_NOT_ARCHIVED" if metric == "ground_speed_mps"
                    else "NOT_EVALUABLE_NO_PAIRWISE_COMMON_PARAMETER_SUCCESS"
                )
                pair_seed_rows.append({
                    "seed": seed, "scope": "pairwise", "method_a": left_label, "method_b": right_label,
                    "metric": metric, "paired_truth_opportunities": denom,
                    "retained_pairwise_product_truth_opportunities": retained_product_denom,
                    "retained_product_period_ids": ";".join(str(x) for x in sorted(retained_period_ids)),
                    "common_parameter_success_count": n_common,
                    "common_parameter_success_coverage": n_common / retained_product_denom if retained_product_denom and metric != "ground_speed_mps" else None,
                    "method_a_common_success_rmse": left_rmse,
                    "method_b_common_success_rmse": right_rmse,
                    "rmse_difference_b_minus_a": right_rmse - left_rmse if left_rmse is not None and right_rmse is not None else None,
                    "status": f"{status}_RETAINED_PERIOD_SUBSET" if status == "EVALUABLE" else status,
                    "full_10_period_status": "EVALUABLE" if retained_period_ids == set(range(10)) else "NOT_EVALUABLE_ARCHIVE_RETAINS_INCOMPLETE_PERIOD_PRODUCTS",
                    "key_scope": "(case_id, evaluator sample_key=target_id/period_id/beam_id)",
                })

        for metric in PARAMETER_METRICS:
            own = {label: own_cache[(seed, label, metric)] for label in CORE_LABELS}
            fourway = next(row for row in own_seed_rows if row["seed"] == seed and row["method_label"] == CORE_LABELS[0] and row["metric"] == metric)
            # One row summarizes the same common set for all four methods.
            rmse_by_method: dict[str, float | None] = {}
            common_count = 0
            common_denominator = 0
            common_period_ids: set[int] = set()
            if metric in RECOMPUTABLE_METRICS:
                sums = {label: 0.0 for label in CORE_LABELS}
                common_count = 0
                for case_id in case_ids:
                    group = [case_units[case_id][label] for label in CORE_LABELS]
                    if any(row["task_status"] != "EVALUABLE" for row in group) or not _same_key_sets(*(row["valid_keys"] for row in group)):
                        continue
                    common_product_keys = set.intersection(*(row["parameter_available_keys"] for row in group))
                    common_denominator += len(common_product_keys)
                    common_period_ids.update(set.intersection(*(row["detection_period_ids"] for row in group)))
                    sets = [set(row["errors"].get(metric, {})) for row in group]
                    common = set.intersection(*sets) & common_product_keys if sets else set()
                    common_count += len(common)
                    for label, row in zip(CORE_LABELS, group):
                        errors = row["errors"].get(metric, {})
                        sums[label] += sum(errors[key] ** 2 for key in common)
                rmse_by_method = {label: math.sqrt(sums[label] / common_count) if common_count else None for label in CORE_LABELS}
            else:
                rmse_by_method = {label: None for label in CORE_LABELS}
            fourway_seed_rows.append({
                "seed": seed, "methods": ";".join(CORE_LABELS), "metric": metric,
                "evaluable_truth_opportunities": common_denominator,
                "retained_product_period_ids": ";".join(str(x) for x in sorted(common_period_ids)),
                "fourway_common_parameter_success_count": common_count,
                "fourway_common_success_coverage": common_count / common_denominator if common_denominator else None,
                **{f"{label}_common_success_rmse": rmse_by_method.get(label) for label in CORE_LABELS},
                "full_10_period_status": "EVALUABLE" if common_period_ids == set(range(10)) else "NOT_EVALUABLE_ARCHIVE_RETAINS_INCOMPLETE_PERIOD_PRODUCTS",
                "status": "EVALUABLE_RETAINED_PERIOD_SUBSET" if common_count else (
                    "NOT_EVALUABLE_TRACK_DEBUG_NOT_ARCHIVED" if metric == "ground_speed_mps"
                    else "NOT_EVALUABLE_NO_FOURWAY_COMMON_PARAMETER_SUCCESS"
                ),
            })

    # Equal-weight descriptive summaries across the 12 independent seeds.
    for metric in PARAMETER_METRICS:
        for label in CORE_LABELS:
            own = [row for row in own_seed_rows if row["metric"] == metric and row["method_label"] == label]
            rmse_values = [float(row["own_success_rmse"]) for row in own if row["own_success_rmse"] is not None]
            common_values = [float(row["fourway_common_success_rmse"]) for row in own if row["fourway_common_success_rmse"] is not None]
            archived_values = [float(row["archived_full_period_own_success_rmse"]) for row in own if row["archived_full_period_own_success_rmse"] is not None]
            summary_rows.append({
                "scope": "retained_period_subset_plus_archived_aggregate", "method_a": label, "method_b": "",
                "metric": metric, "evaluable_seeds_own": len(rmse_values),
                "mean_seed_own_success_rmse": _mean(rmse_values), "sd_seed_own_success_rmse": _sd(rmse_values),
                "min_seed_own_success_rmse": min(rmse_values) if rmse_values else None,
                "max_seed_own_success_rmse": max(rmse_values) if rmse_values else None,
                "mean_seed_fourway_common_rmse": _mean(common_values), "sd_seed_fourway_common_rmse": _sd(common_values),
                "mean_seed_archived_full_period_own_success_rmse": _mean(archived_values),
                "sd_seed_archived_full_period_own_success_rmse": _sd(archived_values),
                "mean_fourway_common_coverage": _mean([float(row["fourway_common_success_coverage"]) for row in own if row.get("fourway_common_success_coverage") is not None]),
                "own_success_scope": "archived_full_period_aggregate_only" if metric == "ground_speed_mps" else "retained_target_period_subset_only",
                "full_10_period_common_success_status": "NOT_EVALUABLE_ARCHIVE_RETAINS_INCOMPLETE_PERIOD_PRODUCTS",
                "interpretation": "own/common recomputation uses retained target-period subset; archived full-period own-success aggregates are separate and not independently recomputed; screening only",
            })
        for left, right in CORE_PAIRS:
            pair = [row for row in pair_seed_rows if row["metric"] == metric and row["method_a"] == left and row["method_b"] == right]
            a_values = [float(row["method_a_common_success_rmse"]) for row in pair if row["method_a_common_success_rmse"] is not None]
            b_values = [float(row["method_b_common_success_rmse"]) for row in pair if row["method_b_common_success_rmse"] is not None]
            d_values = [float(row["rmse_difference_b_minus_a"]) for row in pair if row["rmse_difference_b_minus_a"] is not None]
            summary_rows.append({
                "scope": "pairwise_common_success", "method_a": left, "method_b": right,
                "metric": metric, "evaluable_seeds_own": len(d_values),
                "mean_seed_own_success_rmse": None, "sd_seed_own_success_rmse": None,
                "min_seed_own_success_rmse": None, "max_seed_own_success_rmse": None,
                "mean_seed_fourway_common_rmse": None, "sd_seed_fourway_common_rmse": None,
                "mean_method_a_common_rmse": _mean(a_values), "sd_method_a_common_rmse": _sd(a_values),
                "mean_method_b_common_rmse": _mean(b_values), "sd_method_b_common_rmse": _sd(b_values),
                "mean_rmse_difference_b_minus_a": _mean(d_values), "sd_rmse_difference_b_minus_a": _sd(d_values),
                "mean_pairwise_common_coverage": _mean([float(row["common_parameter_success_coverage"]) for row in pair if row.get("common_parameter_success_coverage") is not None]),
                "interpretation": "pairwise common-success on retained target-period products only; full 10-period common set is NOT_EVALUABLE; screening only",
            })
    return own_seed_rows, pair_seed_rows, fourway_seed_rows, summary_rows


def _aggregate_association_position_metrics(
    case_units: Mapping[str, Mapping[str, Any]], seeds: Sequence[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Compare evaluator-saved position errors over all ten archived periods."""

    cases_by_seed: dict[int, list[str]] = defaultdict(list)
    for case_id, units in case_units.items():
        if "PK" in units:
            cases_by_seed[int(units["PK"]["seed"])].append(case_id)

    own_common_rows: list[dict[str, Any]] = []
    pairwise_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    for seed in seeds:
        case_ids = sorted(cases_by_seed.get(seed, []))
        own_count = {label: 0 for label in CORE_LABELS}
        own_sum_sq = {label: 0.0 for label in CORE_LABELS}
        common_sum_sq = {label: 0.0 for label in CORE_LABELS}
        truth_denominator = 0
        fourway_denominator = 0
        fourway_count = 0
        pair_count = {(a, b): 0 for a, b in CORE_PAIRS}
        pair_denominator = {(a, b): 0 for a, b in CORE_PAIRS}
        pair_sum_sq_a = {(a, b): 0.0 for a, b in CORE_PAIRS}
        pair_sum_sq_b = {(a, b): 0.0 for a, b in CORE_PAIRS}
        included_case_count = 0

        for case_id in case_ids:
            group = {label: case_units[case_id][label] for label in CORE_LABELS}
            if any(row["task_status"] != "EVALUABLE" for row in group.values()):
                continue
            if not _same_key_sets(*(row["valid_keys"] for row in group.values())):
                continue
            included_case_count += 1
            valid_keys = set(group[CORE_LABELS[0]]["valid_keys"])
            truth_denominator += len(valid_keys)
            error_maps = {
                label: group[label]["association_position_errors"] for label in CORE_LABELS
            }
            for label, errors in error_maps.items():
                if not set(errors).issubset(valid_keys):
                    raise ValueError(f"{case_id}: {label} position errors include an invalid truth opportunity")
                own_count[label] += len(errors)
                own_sum_sq[label] += sum(value * value for value in errors.values())

            common_fourway = set.intersection(*(set(error_maps[label]) for label in CORE_LABELS))
            fourway_denominator += len(valid_keys)
            fourway_count += len(common_fourway)
            for label in CORE_LABELS:
                common_sum_sq[label] += sum(error_maps[label][key] ** 2 for key in common_fourway)

            for left, right in CORE_PAIRS:
                common = set(error_maps[left]) & set(error_maps[right])
                pair_denominator[(left, right)] += len(valid_keys)
                pair_count[(left, right)] += len(common)
                pair_sum_sq_a[(left, right)] += sum(error_maps[left][key] ** 2 for key in common)
                pair_sum_sq_b[(left, right)] += sum(error_maps[right][key] ** 2 for key in common)

        for label in CORE_LABELS:
            own_rmse = math.sqrt(own_sum_sq[label] / own_count[label]) if own_count[label] else None
            common_rmse = math.sqrt(common_sum_sq[label] / fourway_count) if fourway_count else None
            own_common_rows.append({
                "seed": seed, "method_label": label,
                "included_case_count": included_case_count,
                "truth_opportunities": truth_denominator,
                "own_success_count": own_count[label],
                "own_success_coverage": own_count[label] / truth_denominator if truth_denominator else None,
                "own_success_rmse_m": own_rmse,
                "fourway_common_success_count": fourway_count,
                "fourway_common_success_coverage": fourway_count / fourway_denominator if fourway_denominator else None,
                "fourway_common_success_rmse_m": common_rmse,
                "fourway_common_truth_opportunities": fourway_denominator,
                "evaluator_scope": "full 10 periods; target_associations.csv position_error_m",
                "inference_unit": "seed",
            })

        for left, right in CORE_PAIRS:
            n = pair_count[(left, right)]
            rmse_a = math.sqrt(pair_sum_sq_a[(left, right)] / n) if n else None
            rmse_b = math.sqrt(pair_sum_sq_b[(left, right)] / n) if n else None
            pairwise_rows.append({
                "seed": seed, "method_a": left, "method_b": right,
                "included_case_count": included_case_count,
                "paired_truth_opportunities": pair_denominator[(left, right)],
                "common_success_count": n,
                "common_success_coverage": n / pair_denominator[(left, right)] if pair_denominator[(left, right)] else None,
                "method_a_common_success_rmse_m": rmse_a,
                "method_b_common_success_rmse_m": rmse_b,
                "rmse_difference_b_minus_a_m": rmse_b - rmse_a if rmse_a is not None and rmse_b is not None else None,
                "evaluator_scope": "full 10 periods; target_associations.csv position_error_m",
                "inference_unit": "seed",
            })

    for label in CORE_LABELS:
        rows = [row for row in own_common_rows if row["method_label"] == label]
        for scope, rmse_field, coverage_field, count_field in (
            ("own_success", "own_success_rmse_m", "own_success_coverage", "own_success_count"),
            ("fourway_common_success", "fourway_common_success_rmse_m", "fourway_common_success_coverage", "fourway_common_success_count"),
        ):
            rmse_values = [float(row[rmse_field]) for row in rows if row[rmse_field] is not None]
            coverage_values = [float(row[coverage_field]) for row in rows if row[coverage_field] is not None]
            summary_rows.append({
                "scope": scope, "method_a": label, "method_b": "", "metric": "position_m",
                "evaluable_seed_count": len(rmse_values),
                "mean_seed_rmse_m": _mean(rmse_values), "sd_seed_rmse_m": _sd(rmse_values),
                "mean_seed_coverage": _mean(coverage_values),
                "mean_success_count_per_seed": _mean([float(row[count_field]) for row in rows]),
                "interpretation": "descriptive V3.2 screening; full-period position error from frozen evaluator association records",
            })

    for left, right in CORE_PAIRS:
        rows = [row for row in pairwise_rows if row["method_a"] == left and row["method_b"] == right]
        deltas = [float(row["rmse_difference_b_minus_a_m"]) for row in rows if row["rmse_difference_b_minus_a_m"] is not None]
        a_values = [float(row["method_a_common_success_rmse_m"]) for row in rows if row["method_a_common_success_rmse_m"] is not None]
        b_values = [float(row["method_b_common_success_rmse_m"]) for row in rows if row["method_b_common_success_rmse_m"] is not None]
        summary_rows.append({
            "scope": "pairwise_common_success", "method_a": left, "method_b": right,
            "metric": "position_m", "evaluable_seed_count": len(deltas),
            "mean_method_a_common_rmse_m": _mean(a_values), "sd_method_a_common_rmse_m": _sd(a_values),
            "mean_method_b_common_rmse_m": _mean(b_values), "sd_method_b_common_rmse_m": _sd(b_values),
            "mean_rmse_difference_b_minus_a_m": _mean(deltas), "sd_rmse_difference_b_minus_a_m": _sd(deltas),
            "method_b_lower_rmse_seed_count": sum(value < 0 for value in deltas),
            "method_b_higher_rmse_seed_count": sum(value > 0 for value in deltas),
            "tied_seed_count": sum(value == 0 for value in deltas),
            "mean_common_success_coverage": _mean([
                float(row["common_success_coverage"]) for row in rows if row["common_success_coverage"] is not None
            ]),
            "interpretation": "paired seed differences on full-period common matched opportunities; exploratory screening",
        })

    return own_common_rows, pairwise_rows, summary_rows


def _aggregate_tracks(case_metric_rows: Sequence[Mapping[str, Any]], seeds: Sequence[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    seed_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    labels = [method["label"] for method in METHODS]
    for seed in seeds:
        for label in labels:
            rows = [row for row in case_metric_rows if int(row["seed"]) == seed and row["method_label"] == label]
            track_rows = [row for row in rows if row.get("false_confirmed_track_count") is not None]
            eval_tracks = [row for row in track_rows if row.get("status") == "EVALUABLE"]
            def vals(field: str) -> list[float]:
                return [float(row[field]) for row in eval_tracks if row.get(field) not in (None, "")]
            seed_rows.append({
                "seed": seed, "method_label": label, "expected_case_count": 12,
                "track_evaluable_case_count": len(eval_tracks),
                "mean_target_confirmed_unique_periods_per_case": _mean(vals("target_confirmed_unique_period_count")),
                "mean_target_confirmed_observations_per_case": _mean(vals("target_confirmed_observation_count")),
                "pooled_post_confirmation_truth_periods": None,
                "pooled_post_confirmation_matched_periods": None,
                "mean_post_confirmation_match_rate_case_weighted": _mean(vals("post_confirmation_match_rate")),
                "total_false_confirmed_tracks": sum(int(x) for x in vals("false_confirmed_track_count")),
                "mean_false_confirmed_tracks_per_case": _mean(vals("false_confirmed_track_count")),
                "total_unmatched_confirmed_payloads": sum(int(x) for x in vals("unmatched_confirmed_payload_count")),
                "mean_unique_target_tracks_per_case": _mean(vals("unique_target_track_count")),
                "total_observed_track_fragmentations": sum(int(x) for x in vals("track_fragmentation_count_observed")),
                "total_cross_target_association_payloads": sum(int(x) for x in vals("cross_target_association_payload_count")),
                "interpretation": "TrackManager summary only; not independent H0/Pfa and not a complete scanning-system track claim",
            })
    for label in labels:
        rows = [row for row in seed_rows if row["method_label"] == label]
        summary_rows.append({
            "method_label": label, "expected_independent_seeds": len(seeds),
            "evaluable_seeds": sum(row["track_evaluable_case_count"] > 0 for row in rows),
            "mean_seed_target_confirmed_unique_periods_per_case": _mean([float(row["mean_target_confirmed_unique_periods_per_case"]) for row in rows if row.get("mean_target_confirmed_unique_periods_per_case") is not None]),
            "sd_seed_target_confirmed_unique_periods_per_case": _sd([float(row["mean_target_confirmed_unique_periods_per_case"]) for row in rows if row.get("mean_target_confirmed_unique_periods_per_case") is not None]),
            "mean_seed_post_confirmation_match_rate_case_weighted": _mean([float(row["mean_post_confirmation_match_rate_case_weighted"]) for row in rows if row.get("mean_post_confirmation_match_rate_case_weighted") is not None]),
            "sd_seed_post_confirmation_match_rate_case_weighted": _sd([float(row["mean_post_confirmation_match_rate_case_weighted"]) for row in rows if row.get("mean_post_confirmation_match_rate_case_weighted") is not None]),
            "mean_seed_false_confirmed_tracks_per_case": _mean([float(row["mean_false_confirmed_tracks_per_case"]) for row in rows if row.get("mean_false_confirmed_tracks_per_case") is not None]),
            "sd_seed_false_confirmed_tracks_per_case": _sd([float(row["mean_false_confirmed_tracks_per_case"]) for row in rows if row.get("mean_false_confirmed_tracks_per_case") is not None]),
            "mean_seed_unmatched_confirmed_payloads_total": _mean([float(row["total_unmatched_confirmed_payloads"]) for row in rows]),
            "sd_seed_unmatched_confirmed_payloads_total": _sd([float(row["total_unmatched_confirmed_payloads"]) for row in rows]),
            "mean_seed_track_fragmentations_total": _mean([float(row["total_observed_track_fragmentations"]) for row in rows]),
            "inference": "descriptive seed summaries; no Pfa claim",
        })
    return seed_rows, summary_rows


def _aggregate_production_outputs(
    case_units: Mapping[str, Mapping[str, Any]], seeds: Sequence[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    case_rows: list[dict[str, Any]] = []
    period_rows: list[dict[str, Any]] = []
    seed_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    labels = [method["label"] for method in METHODS]

    for case_id in sorted(case_units):
        units = case_units[case_id]
        for label in labels:
            unit = units[label]
            output = unit.get("production_output") or {}
            output_status = str(output.get("production_output_status", "NOT_AVAILABLE"))
            output_evaluable = output_status in {"EVALUABLE", "PRODUCTION_OUTPUTS_ONLY"}
            association_evaluable = (
                output_evaluable
                and output.get("target_association_status") == "EVALUABLE_FROZEN_TARGET_ASSOCIATIONS"
            )
            case_rows.append({
                "case_id": case_id, "seed": unit["seed"], "speed_mps": unit["speed_mps"],
                "snr_db": unit["snr_db"], "method_label": label,
                "task_status": unit.get("task_status"),
                "production_output_status": output_status,
                "production_output_evaluable": output_evaluable,
                "target_association_status": output.get("target_association_status"),
                "target_association_evaluable": association_evaluable,
                "production_period_count": output.get("production_period_count"),
                "final_detection_count": output.get("final_detection_count") if output_evaluable else None,
                "postfilter_cluster_count": output.get("postfilter_cluster_count") if output_evaluable else None,
                "selected_candidate_count": output.get("selected_candidate_count") if output_evaluable else None,
                "raw_detection_count": output.get("raw_detection_count") if output_evaluable else None,
                "target_opportunity_count": output.get("target_opportunity_count") if association_evaluable else None,
                "target_hit_opportunity_count": output.get("target_hit_opportunity_count") if association_evaluable else None,
                "target_missed_opportunity_count": output.get("target_missed_opportunity_count") if association_evaluable else None,
                "truth_matched_final_detection_count": output.get("truth_matched_final_detection_count") if association_evaluable else None,
                "truth_unmatched_final_detection_count": output.get("truth_unmatched_final_detection_count") if association_evaluable else None,
                "non_target_cluster_count": None,
                "non_target_cluster_status": output.get("non_target_cluster_status", "NOT_EVALUABLE_NO_CLUSTER_TRUTH_ASSOCIATION"),
                "production_output_error": output.get("production_output_error"),
            })
            for period in unit.get("production_output_periods", []):
                period_rows.append({
                    "case_id": case_id, "seed": unit["seed"], "speed_mps": unit["speed_mps"],
                    "snr_db": unit["snr_db"], "method_label": label,
                    "task_status": unit.get("task_status"),
                    "production_output_status": output_status,
                    "target_association_status": output.get("target_association_status"),
                    **period,
                })

    for seed in seeds:
        for label in labels:
            rows = [row for row in case_rows if int(row["seed"]) == seed and row["method_label"] == label]
            output_rows = [row for row in rows if row["production_output_evaluable"]]
            association_rows = [row for row in rows if row["target_association_evaluable"]]
            def means(field: str, selected: Sequence[Mapping[str, Any]]) -> float | None:
                values = [float(row[field]) for row in selected if row.get(field) not in (None, "")]
                return _mean(values)
            seed_rows.append({
                "seed": seed, "method_label": label, "expected_case_count": 12,
                "production_output_evaluable_case_count": len(output_rows),
                "production_output_coverage": len(output_rows) / 12,
                "target_association_evaluable_case_count": len(association_rows),
                "target_association_coverage": len(association_rows) / 12,
                "mean_final_detections_per_output_evaluable_case": means("final_detection_count", output_rows),
                "mean_postfilter_clusters_per_output_evaluable_case": means("postfilter_cluster_count", output_rows),
                "mean_truth_unmatched_final_detections_per_evaluable_case": means("truth_unmatched_final_detection_count", association_rows),
                "mean_target_missed_opportunities_per_evaluable_case": means("target_missed_opportunity_count", association_rows),
                "interpretation": "target-present outputs; truth-unmatched detections are not system Pfa",
            })

    for label in labels:
        rows = [row for row in seed_rows if row["method_label"] == label]
        summary_rows.append({
            "method_label": label,
            "expected_independent_seeds": len(seeds),
            "production_output_evaluable_seeds": sum(int(row["production_output_evaluable_case_count"]) > 0 for row in rows),
            "mean_seed_production_output_coverage": _mean([float(row["production_output_coverage"]) for row in rows]),
            "target_association_evaluable_seeds": sum(int(row["target_association_evaluable_case_count"]) > 0 for row in rows),
            "mean_seed_target_association_coverage": _mean([float(row["target_association_coverage"]) for row in rows]),
            "mean_seed_final_detections_per_output_evaluable_case": _mean([
                float(row["mean_final_detections_per_output_evaluable_case"])
                for row in rows if row.get("mean_final_detections_per_output_evaluable_case") is not None
            ]),
            "sd_seed_final_detections_per_output_evaluable_case": _sd([
                float(row["mean_final_detections_per_output_evaluable_case"])
                for row in rows if row.get("mean_final_detections_per_output_evaluable_case") is not None
            ]),
            "mean_seed_truth_unmatched_final_detections_per_evaluable_case": _mean([
                float(row["mean_truth_unmatched_final_detections_per_evaluable_case"])
                for row in rows if row.get("mean_truth_unmatched_final_detections_per_evaluable_case") is not None
            ]),
            "sd_seed_truth_unmatched_final_detections_per_evaluable_case": _sd([
                float(row["mean_truth_unmatched_final_detections_per_evaluable_case"])
                for row in rows if row.get("mean_truth_unmatched_final_detections_per_evaluable_case") is not None
            ]),
            "inference": "seed-equal descriptive summaries; target-present only; no system Pfa claim",
        })
    return case_rows, period_rows, seed_rows, summary_rows


def _aggregate_delay(delay_rows: Sequence[Mapping[str, Any]], seeds: Sequence[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    seed_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    for seed in seeds:
        for label in ("Fixed_D3", "Huber_D3_ON", "M1_Physics_Matched"):
            rows = [row for row in delay_rows if int(row["seed"]) == seed and row["method_label"] == label]
            residuals = [float(row["signed_residual_estimate_minus_true_ns"]) for row in rows]
            seed_rows.append({
                "seed": seed, "method_label": label, "expected_case_count": 12,
                "evaluable_case_count": len(residuals), "mean_applied_delay_ns": _mean([float(row["applied_delay_ns"]) for row in rows]),
                "mean_signed_residual_ns": _mean(residuals), "mean_absolute_residual_ns": _mean([abs(x) for x in residuals]),
                "sd_residual_across_cases_ns": _sd(residuals),
                "interpretation": "within-seed condition variation is nested; seed is inferential unit",
            })
    for label in ("Fixed_D3", "Huber_D3_ON", "M1_Physics_Matched"):
        rows = [row for row in seed_rows if row["method_label"] == label]
        residuals = [float(row["mean_signed_residual_ns"]) for row in rows if row.get("mean_signed_residual_ns") is not None]
        summary_rows.append({
            "method_label": label, "expected_independent_seeds": len(seeds),
            "evaluable_seeds": len(residuals),
            "mean_seed_applied_delay_ns": _mean([float(row["mean_applied_delay_ns"]) for row in rows if row.get("mean_applied_delay_ns") is not None]),
            "mean_seed_signed_residual_ns": _mean(residuals), "sd_seed_signed_residual_ns": _sd(residuals),
            "min_seed_signed_residual_ns": min(residuals) if residuals else None,
            "max_seed_signed_residual_ns": max(residuals) if residuals else None,
            "mean_seed_absolute_residual_ns": _mean([float(row["mean_absolute_residual_ns"]) for row in rows if row.get("mean_absolute_residual_ns") is not None]),
            "inference": "descriptive screening only",
        })
    return seed_rows, summary_rows


def _aggregate_recovery(case_units: Mapping[str, Mapping[str, Any]], seeds: Sequence[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    case_rows: list[dict[str, Any]] = []
    seed_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    case_ids = sorted(case_units)
    for triplet in RECOVERY_TRIPLETS:
        cid = triplet["id"]
        mode = triplet["mode"]
        method_labels = [
            next(item["label"] for item in METHODS if item["method_id"] == method and item["mode"] == mode)
            for method in (triplet["current"], triplet["known"], triplet["estimated"])
        ]
        current_label, known_label, estimated_label = method_labels
        by_seed: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for case_id in case_ids:
            units = case_units[case_id]
            current, known, estimated = (units[label] for label in method_labels)
            endpoints_evaluable = all(row["task_status"] == "EVALUABLE" for row in (current, known, estimated))
            keys_match = endpoints_evaluable and _same_key_sets(current["valid_keys"], known["valid_keys"], estimated["valid_keys"])
            if keys_match:
                rates = [row["hit_rate"] for row in (current, known, estimated)]
                recoverable = rates[1] - rates[0]
                actual = rates[2] - rates[0]
                ratio = actual / recoverable if recoverable > 1e-12 else None
                status = "EVALUABLE" if ratio is not None else "NO_POSITIVE_RECOVERABLE_SPACE"
                n = len(current["valid_keys"])
            else:
                rates = [None, None, None]
                recoverable = actual = ratio = None
                status = "NOT_EVALUABLE_TRIPLET_OR_TRUTH_KEYS"
                n = 0
            row = {
                "case_id": case_id, "seed": current["seed"], "speed_mps": current["speed_mps"],
                "snr_db": current["snr_db"], "comparison_id": cid,
                "current_method": current_label, "known_method": known_label,
                "estimated_method": estimated_label,
                "current_rate": rates[0], "known_rate": rates[1], "estimated_rate": rates[2],
                "known_minus_estimated": rates[1] - rates[2] if rates[1] is not None else None,
                "recoverable_space_known_minus_current": recoverable,
                "actual_recovered_estimated_minus_current": actual,
                "recovery_ratio": ratio, "common_opportunities": n,
                "triplet_status": status,
            }
            case_rows.append(row)
            by_seed[int(current["seed"])].append(row)
        for seed in seeds:
            rows = by_seed.get(seed, [])
            evaluable = [row for row in rows if row["common_opportunities"] > 0]
            total_n = sum(int(row["common_opportunities"]) for row in evaluable)
            # Use matched opportunity counts across paired cases, preserving the
            # V3.2 seed-level pooled-rate convention.
            rates: dict[str, float | None] = {}
            for field, label in (("current_rate", current_label), ("known_rate", known_label), ("estimated_rate", estimated_label)):
                hits = sum(int(case_units[row["case_id"]][label]["hits"]) for row in evaluable)
                rates[field] = hits / total_n if total_n else None
            recoverable = rates["known_rate"] - rates["current_rate"] if total_n else None
            actual = rates["estimated_rate"] - rates["current_rate"] if total_n else None
            ratio = actual / recoverable if recoverable is not None and recoverable > 1e-12 else None
            seed_rows.append({
                "seed": seed, "comparison_id": cid, "expected_condition_count": 12,
                "triplet_common_case_count": len(evaluable), "triplet_common_opportunities": total_n,
                **rates, "known_minus_estimated": rates["known_rate"] - rates["estimated_rate"] if total_n else None,
                "recoverable_space_known_minus_current": recoverable,
                "actual_recovered_estimated_minus_current": actual, "recovery_ratio": ratio,
                "status": "EVALUABLE" if total_n else "NOT_EVALUABLE",
                "inference": "pooled within one seed; descriptive Screening only",
            })
        selected_seed_rows = [row for row in seed_rows if row["comparison_id"] == cid]
        ratio_values = [float(row["recovery_ratio"]) for row in selected_seed_rows if row.get("recovery_ratio") is not None]
        summary_rows.append({
            "comparison_id": cid, "expected_independent_seeds": len(seeds),
            "seeds_with_triplet": sum(row["triplet_common_case_count"] > 0 for row in selected_seed_rows),
            "seeds_with_full_12_case_triplet": sum(row["triplet_common_case_count"] == 12 for row in selected_seed_rows),
            "mean_seed_recoverable_space": _mean([float(row["recoverable_space_known_minus_current"]) for row in selected_seed_rows if row.get("recoverable_space_known_minus_current") is not None]),
            "sd_seed_recoverable_space": _sd([float(row["recoverable_space_known_minus_current"]) for row in selected_seed_rows if row.get("recoverable_space_known_minus_current") is not None]),
            "mean_seed_actual_recovered": _mean([float(row["actual_recovered_estimated_minus_current"]) for row in selected_seed_rows if row.get("actual_recovered_estimated_minus_current") is not None]),
            "sd_seed_actual_recovered": _sd([float(row["actual_recovered_estimated_minus_current"]) for row in selected_seed_rows if row.get("actual_recovered_estimated_minus_current") is not None]),
            "mean_seed_recovery_ratio": _mean(ratio_values), "sd_seed_recovery_ratio": _sd(ratio_values),
            "inference": "equal-weight seed summaries; descriptive Screening only",
        })
    return case_rows, seed_rows, summary_rows


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as stream:
        fieldnames = list(dict.fromkeys(key for row in rows for key in row.keys()))
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_gzip_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        with gzip.open(path, "wt", newline="", encoding="utf-8") as stream:
            stream.write("")
        return
    with gzip.open(path, "wt", newline="", encoding="utf-8", compresslevel=6) as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()), extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_plots(
    output: Path,
    scenario_rows: Sequence[Mapping[str, Any]],
    pair_rows: Sequence[Mapping[str, Any]],
    fourway_rows: Sequence[Mapping[str, Any]],
    own_rows: Sequence[Mapping[str, Any]],
    fourway_param_rows: Sequence[Mapping[str, Any]],
    pair_parameter_rows: Sequence[Mapping[str, Any]],
    track_summary: Sequence[Mapping[str, Any]],
    production_output_summary: Sequence[Mapping[str, Any]],
    delay_seed_rows: Sequence[Mapping[str, Any]],
    position_own_common_rows: Sequence[Mapping[str, Any]],
    position_pairwise_rows: Sequence[Mapping[str, Any]],
    position_summary_rows: Sequence[Mapping[str, Any]],
) -> list[str]:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/pgrcc-r22-v33-matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figures = output / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    generated: list[str] = []
    plot_labels = ["A0", "C0", "PK", "Fixed_D3", "Huber_D3_ON", "M1_Physics_Matched", "P1_OFF_estimate", "P2_Mode-A", "P2_Mode-B"]
    colors = dict(zip(plot_labels, plt.cm.tab10.colors[:len(plot_labels)]))
    speeds = (0.5, 1.5, 2.5, 6.7)
    fig, axes = plt.subplots(1, 3, figsize=(15.5, 4.8), sharey=True)
    for ax, snr in zip(axes, (10.0, 20.0, 30.0)):
        for label in plot_labels:
            rows = [row for row in scenario_rows if row["method_label"] == label and float(row["snr_db"]) == snr]
            means = [100.0 * float(next((row["mean_seed_hit_fraction"] for row in rows if float(row["speed_mps"]) == speed), math.nan)) for speed in speeds]
            sds = [100.0 * float(next((row["sd_across_seed_hit_fraction"] for row in rows if float(row["speed_mps"]) == speed and row.get("sd_across_seed_hit_fraction") is not None), 0.0)) for speed in speeds]
            counts = [int(next((row["evaluable_seed_count"] for row in rows if float(row["speed_mps"]) == speed), 0)) for speed in speeds]
            if not any(counts):
                continue
            ax.errorbar(speeds, means, yerr=sds, marker="o", linewidth=1.2, capsize=2, label=label, color=colors[label])
        ax.set_title(f"SNR {int(snr)} dB")
        ax.set_xlabel("Radial speed (m/s)")
        ax.set_xticks(speeds)
        ax.grid(alpha=0.25)
    axes[0].set_ylabel("Correct target-period hits (%)")
    axes[-1].legend(fontsize=7, loc="center left", bbox_to_anchor=(1.02, 0.5), frameon=False)
    fig.suptitle("R22 V3.2 +4 ns target-present screening (mean ± seed SD)")
    fig.tight_layout()
    path = figures / "detection_rate_by_snr_speed.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    generated.append(str(path.relative_to(output)))

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.8))
    matrix = []
    labels = []
    for a, b in CORE_PAIRS:
        rows = [row for row in pair_rows if row["method_a"] == a and row["method_b"] == b]
        labels.append(f"{a} / {b}")
        matrix.append([100.0 * float(row["common_hit_fraction"]) for row in rows if row.get("common_hit_fraction") is not None])
    means = [_mean(values) for values in matrix]
    sds = [_sd(values) for values in matrix]
    positions = list(range(len(labels)))
    axes[0].barh(positions, [float(v) if v is not None else math.nan for v in means], xerr=[float(v) if v is not None else 0.0 for v in sds], color="#4472C4", alpha=0.85, capsize=2)
    axes[0].set_xlim(0.0, 100.0)
    axes[0].set_yticks(positions, labels, fontsize=7)
    axes[0].set_xlabel("Both methods hit (%) of shared truth opportunities")
    axes[0].grid(axis="x", alpha=0.25)
    fourway = [row for row in fourway_rows if row.get("all_four_common_hit_fraction") is not None]
    axes[1].bar([str(row["seed"]) for row in fourway], [100 * float(row["all_four_common_hit_fraction"]) for row in fourway], color="#70AD47")
    axes[1].set_xlabel("Independent seed")
    axes[1].set_ylabel("All-four common hits (%)")
    axes[1].tick_params(axis="x", rotation=45)
    axes[1].grid(axis="y", alpha=0.25)
    fig.suptitle("Detection opportunity overlap (four core calibration methods)")
    fig.tight_layout()
    path = figures / "detection_overlap_core_methods.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    generated.append(str(path.relative_to(output)))

    fig, axes = plt.subplots(1, 2, figsize=(16, 6.2))
    display_pairs = [(left, right, f"{right} − {left}") for left, right in CORE_PAIRS]
    for ax, metric, title, source_rows, value_field in (
        (axes[0], "angle_deg", "Angle, retained period 8", pair_parameter_rows, "rmse_difference_b_minus_a"),
        (axes[1], "position_m", "Position, all 10 periods", position_pairwise_rows, "rmse_difference_b_minus_a_m"),
    ):
        y_positions = list(range(len(display_pairs)))
        for y, (left, right, label) in zip(y_positions, display_pairs):
            values = [
                float(row[value_field]) for row in source_rows
                if row.get("method_a") == left and row.get("method_b") == right
                and (metric == "position_m" or row.get("metric") == metric)
                and row.get(value_field) is not None
            ]
            seed_rows = [
                row for row in source_rows
                if row.get("method_a") == left and row.get("method_b") == right
                and (metric == "position_m" or row.get("metric") == metric)
                and row.get(value_field) is not None
            ]
            for idx, row in enumerate(sorted(seed_rows, key=lambda item: int(item["seed"]))):
                jitter = ((idx % 5) - 2) * 0.035
                ax.scatter(float(row[value_field]), y + jitter, s=20, color="#4472C4", alpha=0.65)
            if values:
                ax.scatter(_mean(values), y, marker="D", s=44, color="#C00000", zorder=3)
        ax.axvline(0.0, color="black", linewidth=0.9)
        ax.set_yticks(y_positions, [item[2] for item in display_pairs], fontsize=8)
        ax.invert_yaxis()
        ax.set_title(title)
        ax.set_xlabel("Paired ΔRMSE (method B − method A); negative favors B")
        ax.grid(axis="x", alpha=0.25)
    fig.suptitle("Common-success parameter differences by independent seed")
    fig.tight_layout()
    path = figures / "parameter_common_success_paired_differences.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    generated.append(str(path.relative_to(output)))

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.5))
    for ax, metric, title, unit in zip(axes, ("angle_deg", "position_m"), ("Azimuth error", "Position error"), ("deg", "m")):
        x = list(range(len(CORE_LABELS)))
        own_means, own_sds, common_means, common_sds = [], [], [], []
        for label in CORE_LABELS:
            if metric == "position_m":
                own_values = [
                    float(row["own_success_rmse_m"]) for row in position_own_common_rows
                    if row["method_label"] == label and row.get("own_success_rmse_m") is not None
                ]
                common_values = [
                    float(row["fourway_common_success_rmse_m"]) for row in position_own_common_rows
                    if row["method_label"] == label and row.get("fourway_common_success_rmse_m") is not None
                ]
            else:
                own_values = [float(row["own_success_rmse"]) for row in own_rows if row["method_label"] == label and row["metric"] == metric and row.get("own_success_rmse") is not None]
                common_values = [float(row[f"{label}_common_success_rmse"]) for row in fourway_param_rows if row["metric"] == metric and row.get(f"{label}_common_success_rmse") is not None]
            own_mean, own_sd = _mean(own_values), _sd(own_values)
            common_mean, common_sd = _mean(common_values), _sd(common_values)
            own_means.append(own_mean if own_mean is not None else math.nan)
            own_sds.append(own_sd if own_sd is not None else 0.0)
            common_means.append(common_mean if common_mean is not None else math.nan)
            common_sds.append(common_sd if common_sd is not None else 0.0)
        width = 0.36
        ax.bar([value-width/2 for value in x], own_means, width, yerr=own_sds, label="Own-success", color="#5B9BD5", capsize=2)
        ax.bar([value+width/2 for value in x], common_means, width, yerr=common_sds, label="Four-way common success", color="#ED7D31", capsize=2)
        ax.set_xticks(x, [label.replace("_", "\n") for label in CORE_LABELS], fontsize=7)
        ax.set_ylabel(f"RMSE ({unit})")
        ax.set_title(f"{title} ({'all 10 periods' if metric == 'position_m' else 'period 8 only'})")
        ax.grid(axis="y", alpha=0.25)
    axes[-1].legend(fontsize=8, frameon=False)
    fig.suptitle("Own-success vs four-way common-success parameter RMSE (mean ± seed SD)")
    fig.tight_layout()
    path = figures / "parameter_rmse_own_vs_fourway_common.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    generated.append(str(path.relative_to(output)))

    track_labels = [label for label in CORE_LABELS]
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.5))
    metrics = (
        ("mean_seed_post_confirmation_match_rate_case_weighted", "sd_seed_post_confirmation_match_rate_case_weighted", "Post-confirmation match rate", "%", 100.0),
        ("mean_seed_false_confirmed_tracks_per_case", "sd_seed_false_confirmed_tracks_per_case", "False-confirmed tracks per case", "tracks", 1.0),
        ("mean_seed_target_confirmed_unique_periods_per_case", "sd_seed_target_confirmed_unique_periods_per_case", "Target-confirmed unique periods", "periods", 1.0),
    )
    for ax, (field, sd_field, title, ylabel, scale) in zip(axes, metrics):
        means, sds = [], []
        for label in track_labels:
            rows = [row for row in track_summary if row["method_label"] == label and row.get(field) is not None]
            mean = scale * float(rows[0][field]) if rows else None
            sd = scale * float(rows[0][sd_field]) if rows and rows[0].get(sd_field) not in (None, "") else None
            means.append(mean if mean is not None else math.nan)
            sds.append(sd if sd is not None else 0.0)
        ax.bar(range(len(track_labels)), means, yerr=sds, color="#A5A5A5", capsize=2)
        ax.set_xticks(range(len(track_labels)), [label.replace("_", "\n") for label in track_labels], fontsize=7)
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.grid(axis="y", alpha=0.25)
    fig.suptitle("TrackManager summary indicators (mean ± seed SD; not Pfa)")
    fig.tight_layout()
    path = figures / "track_retention_summary.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    generated.append(str(path.relative_to(output)))

    output_labels = [method["label"] for method in METHODS]
    output_summary_by_label = {str(row["method_label"]): row for row in production_output_summary}
    track_summary_by_label = {str(row["method_label"]): row for row in track_summary}
    fig, axes = plt.subplots(2, 2, figsize=(16.5, 10.0))
    output_metrics = (
        ("mean_seed_final_detections_per_output_evaluable_case", "sd_seed_final_detections_per_output_evaluable_case", "Final Detection rows / output-evaluable case", "rows"),
        ("mean_seed_truth_unmatched_final_detections_per_evaluable_case", "sd_seed_truth_unmatched_final_detections_per_evaluable_case", "Truth-unmatched final Detection rows / association-evaluable case", "rows (not Pfa)"),
    )
    for ax, (field, sd_field, title, ylabel) in zip(axes.flat[:2], output_metrics):
        means = [
            float(output_summary_by_label[label][field]) if output_summary_by_label.get(label, {}).get(field) is not None else math.nan
            for label in output_labels
        ]
        sds = [
            float(output_summary_by_label[label][sd_field]) if output_summary_by_label.get(label, {}).get(sd_field) is not None else 0.0
            for label in output_labels
        ]
        ax.bar(range(len(output_labels)), means, yerr=sds, color="#4472C4", capsize=2)
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.set_xticks(range(len(output_labels)), [label.replace("_", "\n") for label in output_labels], fontsize=7)
        ax.grid(axis="y", alpha=0.25)

    for ax, field, sd_field, title, ylabel in (
        (axes.flat[2], "mean_seed_false_confirmed_tracks_per_case", "sd_seed_false_confirmed_tracks_per_case", "False-confirmed tracks / target-present case", "tracks (not Pfa)"),
        (axes.flat[3], "mean_seed_target_confirmed_unique_periods_per_case", "sd_seed_target_confirmed_unique_periods_per_case", "Target-confirmed unique periods / case", "periods (of 10)"),
    ):
        means = [
            float(track_summary_by_label[label][field]) if track_summary_by_label.get(label, {}).get(field) is not None else math.nan
            for label in output_labels
        ]
        sds = [
            float(track_summary_by_label[label][sd_field]) if track_summary_by_label.get(label, {}).get(sd_field) is not None else 0.0
            for label in output_labels
        ]
        ax.bar(range(len(output_labels)), means, yerr=sds, color="#70AD47", capsize=2)
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.set_xticks(range(len(output_labels)), [label.replace("_", "\n") for label in output_labels], fontsize=7)
        ax.grid(axis="y", alpha=0.25)
    fig.suptitle("Target-present Production outputs and TrackManager summaries (mean ± seed SD; no system Pfa claim)")
    fig.tight_layout()
    path = figures / "detection_cluster_track_output_audit.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    generated.append(str(path.relative_to(output)))

    fig, ax = plt.subplots(figsize=(8.5, 4.5))
    seed_values = sorted({int(row["seed"]) for row in delay_seed_rows})
    seed_position = {seed: index + 1 for index, seed in enumerate(seed_values)}
    for label, color, marker in (("Fixed_D3", "#A5A5A5", "o"), ("Huber_D3_ON", "#4472C4", "s"), ("M1_Physics_Matched", "#ED7D31", "^") ):
        rows = [row for row in delay_seed_rows if row["method_label"] == label and row.get("mean_signed_residual_ns") is not None]
        ax.scatter([seed_position[int(row["seed"])] for row in rows], [float(row["mean_signed_residual_ns"]) for row in rows], label=label, color=color, marker=marker, alpha=0.85)
    ax.axhline(0.0, color="black", linewidth=0.8)
    ax.set_xlabel("Independent seed order (12 seeds; IDs in delay_estimation_by_seed.csv)")
    ax.set_xticks(list(seed_position.values()))
    ax.set_ylabel("Mean applied-delay residual (ns), estimate − 4 ns")
    ax.set_title("Per-seed residual physical delay estimate")
    ax.grid(alpha=0.25)
    ax.legend(frameon=False)
    fig.tight_layout()
    path = figures / "delay_estimation_residual_by_seed.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    generated.append(str(path.relative_to(output)))

    fig, axes = plt.subplots(1, 2, figsize=(15.5, 6.0), gridspec_kw={"width_ratios": [1.0, 1.45]})
    m1_residual = {
        int(row["seed"]): float(row["mean_signed_residual_ns"])
        for row in delay_seed_rows
        if row["method_label"] == "M1_Physics_Matched" and row.get("mean_signed_residual_ns") is not None
    }
    m1_fixed_position = {
        int(row["seed"]): float(row["rmse_difference_b_minus_a_m"])
        for row in position_pairwise_rows
        if row["method_a"] == "Fixed_D3" and row["method_b"] == "M1_Physics_Matched"
        and row.get("rmse_difference_b_minus_a_m") is not None
    }
    paired_seeds = sorted(set(m1_residual) & set(m1_fixed_position))
    axes[0].scatter(
        [m1_residual[seed] for seed in paired_seeds],
        [m1_fixed_position[seed] for seed in paired_seeds],
        color="#4472C4", s=48,
    )
    for seed in paired_seeds:
        axes[0].annotate(str(seed), (m1_residual[seed], m1_fixed_position[seed]), xytext=(3, 3), textcoords="offset points", fontsize=7)
    axes[0].axhline(0.0, color="black", linewidth=0.8)
    axes[0].set_xlabel("M1 mean applied-delay residual (ns; estimate − 4 ns)")
    axes[0].set_ylabel("M1 − Fixed D3 common position RMSE (m)")
    axes[0].set_title("Across 12 seeds; descriptive association only")
    axes[0].grid(alpha=0.25)

    fixed_m1_position = next((
        row for row in position_summary_rows
        if row.get("scope") == "pairwise_common_success"
        and row.get("method_a") == "Fixed_D3" and row.get("method_b") == "M1_Physics_Matched"
    ), {})
    fixed_m1_angle_values = [
        float(row["rmse_difference_b_minus_a"])
        for row in pair_parameter_rows
        if row.get("metric") == "angle_deg" and row.get("method_a") == "Fixed_D3"
        and row.get("method_b") == "M1_Physics_Matched" and row.get("rmse_difference_b_minus_a") is not None
    ]
    mean_fourway_hit = _mean([
        100.0 * float(row["all_four_common_hit_fraction"])
        for row in fourway_rows if row.get("all_four_common_hit_fraction") is not None
    ])
    track_by_label = {row["method_label"]: row for row in track_summary}
    false_fixed = track_by_label.get("Fixed_D3", {}).get("mean_seed_false_confirmed_tracks_per_case")
    false_m1 = track_by_label.get("M1_Physics_Matched", {}).get("mean_seed_false_confirmed_tracks_per_case")
    position_text = (
        f"{float(fixed_m1_position['mean_rmse_difference_b_minus_a_m']):.2f} m; "
        f"M1 lower in {fixed_m1_position['method_b_lower_rmse_seed_count']}/12 seeds"
        if fixed_m1_position.get("mean_rmse_difference_b_minus_a_m") is not None else "NOT_EVALUABLE"
    )
    angle_text = (
        f"{_mean(fixed_m1_angle_values):.5f} deg; period 8 only"
        if fixed_m1_angle_values else "NOT_EVALUABLE"
    )
    detection_text = f"four-way common correct hits {mean_fourway_hit:.1f}% of truth opportunities" if mean_fourway_hit is not None else "NOT_EVALUABLE"
    track_text = (
        f"false-confirmed tracks/case: Fixed {float(false_fixed):.1f}, M1 {float(false_m1):.1f}; not Pfa"
        if false_fixed is not None and false_m1 is not None else "case-level TrackManager summary only"
    )
    table_rows = [
        ["Evidence", "Observed result", "Interpretation limit"],
        ["Position", position_text, "All 10 periods; common matched opportunities"],
        ["Angle", angle_text, "Four-method common set from retained period 8"],
        ["Detection", detection_text, "12 seeds are the inference units"],
        ["Track output", track_text, "No target-specific causal attribution"],
        ["CSI phase", "Target-only complex response not archived", "NOT_EVALUABLE"],
        ["Ground speed", "Per-opportunity TrackManager payload absent", "NOT_EVALUABLE for common-success RMSE"],
    ]
    axes[1].axis("off")
    table = axes[1].table(cellText=table_rows, loc="center", cellLoc="left", colWidths=[0.16, 0.44, 0.40])
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1.0, 2.0)
    for (row, col), cell in table.get_celld().items():
        if row == 0:
            cell.set_facecolor("#D9EAF7")
            cell.set_text_props(weight="bold")
        elif row % 2:
            cell.set_facecolor("#F3F6F9")
    fig.suptitle("Residual task-gap evidence and unresolved mechanisms")
    fig.tight_layout()
    path = figures / "residual_mechanism_summary.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    generated.append(str(path.relative_to(output)))
    return generated


def _write_manifest(
    output: Path,
    case_validation: Sequence[Mapping[str, Any]],
    archive_identity: Sequence[Mapping[str, Any]],
    source_identity: Mapping[str, Any],
    figure_paths: Sequence[str],
    production_output_summary: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    files = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            files.append({"path": str(path.relative_to(output)), "bytes": path.stat().st_size, "sha256": _sha256_file(path)})
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
    manifest = {
        "schema_version": 1,
        "analysis_id": "R22_V3_3_RESIDUAL_TASK_GAP_OFFLINE_AUDIT",
        "created_date": "2026-11-10",
        "analysis_worktree_head": head,
        "analysis_branch": branch,
        "analysis_script_path": str(Path(__file__).resolve().relative_to(ROOT)),
        "analysis_script_sha256": _sha256_file(Path(__file__).resolve()),
        "v3_2_experiment_source_commit": SOURCE_COMMIT,
        "v3_2_frozen_design_id": "R22_EXTENSION_20261106_V3_2_IDENTITY_CLOSED_FULL_SCREENING",
        "v3_2_frozen_design_sha256": _sha256_file(DESIGN_PATH),
        "v3_2_method_config_sha256": _sha256_file(METHOD_CONFIG_PATH),
        "master_plan_blob_expected_unchanged": "e637e3769bde7814298ff012d0f42301884e2985",
        "master_plan_blob_current": subprocess.run(["git", "rev-parse", f"HEAD:docs/research/RESEARCH_MASTER_PLAN.md"], cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE).stdout.strip(),
        "v3_2_evaluator_sources": dict(source_identity),
        "archive_index_path": str(ARCHIVE_INDEX_PATH.relative_to(ROOT)),
        "archive_index_sha256": _sha256_file(ARCHIVE_INDEX_PATH),
        "archive_root_local_only": str(ARCHIVE_ROOT.relative_to(ROOT)),
        "indexed_archive_count": len(archive_identity),
        "used_plus4_archive_count": sum(float(row["delay_ns"]) == 4.0 for row in archive_identity),
        "archives_copied_or_extracted": False,
        "stage2_or_production_or_cuda_started": False,
        "ai_training_or_router_started": False,
        "case_count": len(case_validation),
        "independent_seed_count": len({int(row["seed"]) for row in case_validation}),
        "visible_truth_opportunities_per_case": 200,
        "period_count": 10,
        "target_count": 20,
        "status_labels": {
            "IMPLEMENTED": True,
            "EXECUTED": True,
            "EVALUABLE": {
                "detection_and_track_screening": True,
                "parameter_common_success_retained_period_subset": True,
                "position_m_full_10_period_common_success_from_evaluator_associations": True,
                "angle_range_doppler_full_10_period_common_success": False,
                "ground_speed_common_success": False,
                "radial_velocity_common_success": False,
                "period_detection_cluster_and_truth_unmatched_summary": True,
                "non_target_cluster_truth_attribution": False,
            },
            "PERFORMANCE_VERIFIED": False,
        },
        "core_common_retained_detection_period_ids": sorted({
            period
            for row in case_validation
            for period in str(row.get("core_common_retained_detection_periods", "")).split(";")
            if period
        }),
        "cases_with_full_10_period_parameter_products": sum(bool(row.get("full_10_period_parameter_products_available")) for row in case_validation),
        "production_output_method_coverage": [
            {
                "method_label": row["method_label"],
                "mean_seed_production_output_coverage": row["mean_seed_production_output_coverage"],
                "mean_seed_target_association_coverage": row["mean_seed_target_association_coverage"],
            }
            for row in production_output_summary
        ],
        "non_target_cluster_status": "NOT_EVALUABLE_NO_CLUSTER_TRUTH_ASSOCIATION",
        "truth_unmatched_detection_status": "TARGET_PRESENT_FROZEN_EVALUATOR_ASSOCIATION; NOT_SYSTEM_PFA",
        "ground_speed_common_success_reason": "Compact ZIPs retain aggregate own-success parameter_metrics.csv and track_level_metrics.csv but omit per-opportunity track_debug_runs/track_output_payloads.csv required by the frozen evaluator.",
        "figure_paths": list(figure_paths),
        "outputs": files,
        "disk_policy": "Read existing ZIP members in place; no raw or compact archive deleted, copied, or extracted.",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def run_analysis(output: Path) -> dict[str, Any]:
    source_identity = _validate_source_identity()
    design_sha = _sha256_file(DESIGN_PATH)
    method_config_sha = _sha256_file(METHOD_CONFIG_PATH)
    if design_sha != "9fcc56e27c3a78a96ccfbecdf6ef1b3098fb1298d4bac9886058ea9af6081063":
        raise ValueError("V3.2 frozen design SHA changed")
    if method_config_sha != "588c36904f62febad8476ddccb789ba67de2e9c23dc2ff12b52418eb06706bbc":
        raise ValueError("V3.2 method config SHA changed")
    plus4_rows, extras = _read_archive_index()
    archive_identity_rows: list[dict[str, Any]] = []
    case_validation_rows: list[dict[str, Any]] = []
    case_method_rows: list[dict[str, Any]] = []
    opportunity_audit_rows: list[dict[str, Any]] = []
    parameter_parity_rows: list[dict[str, Any]] = []
    delay_rows: list[dict[str, Any]] = []
    case_units: dict[str, dict[str, Any]] = {}
    for record in plus4_rows:
        archive_name = f"{record['case_id']}__attempt_{int(record['attempt']):03d}.zip"
        archive_path = ARCHIVE_ROOT / archive_name
        archive_sha = _sha256_file(archive_path)
        archive_identity_rows.append({
            "case_id": record["case_id"], "seed": record["seed"],
            "speed_mps": record["speed_mps"], "snr_db": record["snr_db"],
            "delay_ns": record["delay_ns"], "archive_name": archive_name,
            "archive_bytes": archive_path.stat().st_size,
            "archive_sha256_index": record["archive_sha256"],
            "archive_sha256_recomputed": archive_sha,
            "sha256_match": archive_sha == record["archive_sha256"],
        })
        validation, metrics, opportunities, parity, delays, units = _load_case(archive_path, record)
        case_validation_rows.append(validation)
        case_method_rows.extend(metrics)
        opportunity_audit_rows.extend(opportunities)
        parameter_parity_rows.extend(parity)
        delay_rows.extend(delays)
        case_units[str(record["case_id"])] = units

    if len(case_validation_rows) != 144:
        raise ValueError(f"expected 144 analyzed +4 cases, got {len(case_validation_rows)}")
    all_seeds = sorted({int(row["seed"]) for row in case_validation_rows})
    if len(all_seeds) != 12:
        raise ValueError(f"expected 12 independent seeds, got {all_seeds}")
    if not all(row["sha256_match"] for row in archive_identity_rows):
        raise ValueError("one or more used archive SHA identities failed")
    # Validate full-period parameter aggregates only where all ten period
    # detection products were retained. A partial archive is reported as such.
    parity_recomputed = [row for row in parameter_parity_rows if row["metric"] != "ground_speed_mps"]
    parity_mismatches = [row for row in parity_recomputed if row["status"] == "MISMATCH"]
    if parity_mismatches:
        raise ValueError(f"full-period parameter metrics mismatch saved V3.2 aggregate in {len(parity_mismatches)} rows")

    task_seed, scenario_rows, paired_rows, overlap_rows, fourway_detection_rows = _aggregate_detection(case_units, case_method_rows, all_seeds)
    own_seed_rows, pair_parameter_rows, fourway_parameter_rows, parameter_summary_rows = _aggregate_parameters(case_units, all_seeds)
    position_own_common_rows, position_pairwise_rows, position_summary_rows = _aggregate_association_position_metrics(case_units, all_seeds)
    track_seed_rows, track_summary_rows = _aggregate_tracks(case_method_rows, all_seeds)
    production_output_case_rows, production_output_period_rows, production_output_seed_rows, production_output_summary_rows = _aggregate_production_outputs(case_units, all_seeds)
    delay_seed_rows, delay_summary_rows = _aggregate_delay(delay_rows, all_seeds)
    recovery_case_rows, recovery_seed_rows, recovery_summary_rows = _aggregate_recovery(case_units, all_seeds)

    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "archive_identity.csv", archive_identity_rows)
    _write_csv(output / "unindexed_archive_preservation.csv", extras)
    _write_csv(output / "case_validation.csv", case_validation_rows)
    _write_csv(output / "method_information_conditions.csv", INFORMATION_CONDITIONS)
    _write_csv(output / "case_method_screening.csv", case_method_rows)
    _write_csv(output / "seed_method_detection_summary.csv", task_seed)
    _write_csv(output / "detection_by_speed_snr.csv", scenario_rows)
    _write_csv(output / "paired_detection_vs_PK.csv", paired_rows)
    _write_csv(output / "detection_pairwise_overlap_by_seed.csv", overlap_rows)
    _write_csv(output / "detection_fourway_common_by_seed.csv", fourway_detection_rows)
    _write_csv(output / "parameter_seed_metrics.csv", own_seed_rows)
    _write_csv(output / "parameter_pairwise_common_success_by_seed.csv", pair_parameter_rows)
    _write_csv(output / "parameter_fourway_common_success_by_seed.csv", fourway_parameter_rows)
    _write_csv(output / "parameter_common_success_summary.csv", parameter_summary_rows)
    _write_csv(output / "position_all_periods_own_vs_fourway_common_by_seed.csv", position_own_common_rows)
    _write_csv(output / "position_all_periods_pairwise_common_by_seed.csv", position_pairwise_rows)
    _write_csv(output / "position_all_periods_common_success_summary.csv", position_summary_rows)
    _write_csv(output / "parameter_evaluator_parity.csv", parameter_parity_rows)
    _write_gzip_csv(output / "parameter_opportunity_audit.csv.gz", opportunity_audit_rows)
    _write_csv(output / "track_seed_summary.csv", track_seed_rows)
    _write_csv(output / "track_screening_summary.csv", track_summary_rows)
    _write_csv(output / "production_output_by_case.csv", production_output_case_rows)
    _write_gzip_csv(output / "production_output_by_period.csv.gz", production_output_period_rows)
    _write_csv(output / "production_output_by_seed.csv", production_output_seed_rows)
    _write_csv(output / "production_output_screening_summary.csv", production_output_summary_rows)
    _write_csv(output / "delay_estimation_by_case.csv", delay_rows)
    _write_csv(output / "delay_estimation_by_seed.csv", delay_seed_rows)
    _write_csv(output / "delay_estimation_summary.csv", delay_summary_rows)
    _write_csv(output / "paired_recovery_by_case.csv", recovery_case_rows)
    _write_csv(output / "paired_recovery_by_seed.csv", recovery_seed_rows)
    _write_csv(output / "paired_recovery_screening_summary.csv", recovery_summary_rows)
    plots = _write_plots(
        output, scenario_rows, overlap_rows, fourway_detection_rows, own_seed_rows,
        fourway_parameter_rows, pair_parameter_rows, track_summary_rows,
        production_output_summary_rows, delay_seed_rows,
        position_own_common_rows, position_pairwise_rows, position_summary_rows,
    )

    summary = {
        "analysis_status": "COMPLETE_OFFLINE_AUDIT",
        "delay_ns": 4.0, "case_count": 144, "independent_seed_count": 12,
        "speed_mps": [0.5, 1.5, 2.5, 6.7], "snr_db": [10, 20, 30],
        "truth_opportunities_per_case": 200,
        "total_truth_opportunities_per_primary_method": 28800,
        "core_methods": list(CORE_LABELS),
        "core_method_parameter_evaluator_parity_mismatches": len(parity_mismatches),
        "core_method_parameter_evaluator_parity_status": (
            "FULL_PERIOD_POSITION_ASSOCIATION_VERIFIED; OTHER_RECOMPUTED_PARAMETERS_PERIOD_8_ONLY"
            if all(row["status"] == "PASS" for row in parity_recomputed if row["metric"] == "position_m_all_periods_association")
            else "POSITION_ASSOCIATION_PARITY_FAILED"
        ),
        "core_common_retained_detection_period_ids": sorted({
            int(period)
            for row in case_validation_rows
            for period in str(row.get("core_common_retained_detection_periods", "")).split(";")
            if period
        }),
        "cases_with_full_10_period_parameter_products": sum(bool(row["full_10_period_parameter_products_available"]) for row in case_validation_rows),
        "area_clutter_positive_cases": sum(row["area_clutter_packets"] > 0 and row["area_clutter_samples"] > 0 for row in case_validation_rows),
        "all_core_truth_association_sets_match": all(row["core_method_truth_keys_match"] for row in case_validation_rows),
        "all_core_production_branches_passed": all(row["core_method_production_branches_passed"] for row in case_validation_rows),
        "parameter_opportunity_rows": len(opportunity_audit_rows),
        "production_output_case_method_rows": len(production_output_case_rows),
        "production_output_period_rows": len(production_output_period_rows),
        "production_output_methods": production_output_summary_rows,
        "non_target_cluster_count": None,
        "non_target_cluster_status": "NOT_EVALUABLE_NO_CLUSTER_TRUTH_ASSOCIATION",
        "truth_unmatched_final_detections_are_system_pfa": False,
        "position_m_full_10_period_common_success_status": "EVALUABLE_FROM_FROZEN_EVALUATOR_ASSOCIATION_RECORDS",
        "position_m_full_10_period_evaluator_parity_checks": sum(row["metric"] == "position_m_all_periods_association" for row in parameter_parity_rows),
        "position_m_full_10_period_evaluator_parity_failures": sum(row["metric"] == "position_m_all_periods_association" and row["status"] != "PASS" for row in parameter_parity_rows),
        "angle_range_doppler_full_10_period_common_success_status": "NOT_EVALUABLE_ARCHIVE_RETAINS_ONLY_PERIOD_8_DETECTION_PRODUCTS",
        "parameter_common_success_retained_period_status": "EVALUABLE_SUBSET_ONLY_IF_COMMON_SUPPORT_IS_NONZERO",
        "ground_speed_common_success": "NOT_EVALUABLE_TRACK_DEBUG_NOT_ARCHIVED",
        "radial_velocity_common_success": "NOT_EVALUABLE_NO_RESOLVED_VALID_SAMPLES_EXPECTED",
        "performance_verified": False,
        "statistical_significance_claim": False,
        "cuda_executed": False,
        "archives_deleted": 0,
        "plots": plots,
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = _write_manifest(output, case_validation_rows, archive_identity_rows, source_identity, plots, production_output_summary_rows)
    print(json.dumps({"summary": summary, "manifest": str((output / "manifest.json").relative_to(ROOT)), "output_bytes": sum(item["bytes"] for item in manifest["outputs"])}, ensure_ascii=False, indent=2))
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    global ARCHIVE_ROOT, ARCHIVE_INDEX_PATH
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path, default=ARCHIVE_ROOT)
    parser.add_argument("--archive-index", type=Path, default=ARCHIVE_INDEX_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    ARCHIVE_ROOT = args.archive_root.resolve()
    ARCHIVE_INDEX_PATH = args.archive_index.resolve()
    run_analysis(args.output_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
