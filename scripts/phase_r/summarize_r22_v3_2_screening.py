#!/usr/bin/env python3
"""Audit and summarize R22 V3.2 Screening from verified compact archives."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import statistics
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_r import r22_experiment_matrix as base_matrix  # noqa: E402
from scripts.phase_r import r22_v3_experiment_matrix as v3_matrix  # noqa: E402


DEFAULT_DESIGN = ROOT / (
    "docs/research/phase_r/R22/config/"
    "r22_extension_experiment_design_v3_2_identity_closed_20261106.json"
)
DEFAULT_MATRIX = ROOT / "outputs/r22_v3_2_screening_20261106/matrix/matrix_manifest.json"
DEFAULT_OUTPUT = ROOT / "outputs/r22_v3_2_screening_20261106/analysis"
PASS_STATES = {"PASSED", "PASSED_WITH_METHOD_GAPS"}
TRIPLET_RECOVERY_SPECS = (
    {
        "comparison_id": "P1_blind_physical_delay",
        "mode": "not_applicable",
        "current_method": "C0",
        "known_method": "PK",
        "estimated_method": "P1",
    },
    {
        "comparison_id": "P2_mode_a_blind_physical_delay",
        "mode": "Mode-A",
        "current_method": "C4",
        "known_method": "PKR",
        "estimated_method": "P2",
    },
    {
        "comparison_id": "P2_mode_b_blind_physical_delay",
        "mode": "Mode-B",
        "current_method": "C4",
        "known_method": "PKR",
        "estimated_method": "P2",
    },
)


def _finite_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _nonnegative_int(value: Any, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a nonnegative integer") from exc
    if number < 0 or str(value).strip() != str(number):
        raise ValueError(f"{field} must be a nonnegative integer")
    return number


def parse_task_endpoint(row: Mapping[str, Any]) -> dict[str, Any]:
    """Parse one task row without converting non-evaluable rows into misses."""
    status = str(row.get("status") or "UNKNOWN_STATUS").strip()
    reason = str(row.get("reason") or "").strip()
    if status != "EVALUABLE":
        return {
            "status": status,
            "hits": None,
            "misses": None,
            "opportunities": None,
            "hit_rate": None,
            "reason": reason,
        }

    hits = _nonnegative_int(row.get("hits"), "hits")
    misses = _nonnegative_int(row.get("misses"), "misses")
    opportunities = _nonnegative_int(
        row.get("evaluable_truth_opportunities"), "evaluable_truth_opportunities"
    )
    if opportunities <= 0 or hits + misses != opportunities:
        raise ValueError("evaluable task row requires hits + misses == opportunities > 0")
    hit_rate = hits / opportunities
    reported = _finite_float(row.get("hit_fraction"))
    if reported is not None and not math.isclose(reported, hit_rate, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("reported hit_fraction disagrees with hits / opportunities")
    return {
        "status": status,
        "hits": hits,
        "misses": misses,
        "opportunities": opportunities,
        "hit_rate": hit_rate,
        "reason": reason,
    }


def compute_recovery(
    *, current_rate: float | None, known_rate: float | None, estimated_rate: float | None
) -> dict[str, Any]:
    """Compute frozen recovery quantities; rates use correct-detection fractions."""
    rates = (current_rate, known_rate, estimated_rate)
    if any(value is None or not math.isfinite(value) for value in rates):
        return {
            "status": "NOT_EVALUABLE",
            "recoverable_space": None,
            "actual_recovered": None,
            "recovery_ratio": None,
            "known_minus_estimated": None,
        }
    assert current_rate is not None and known_rate is not None and estimated_rate is not None
    recoverable = known_rate - current_rate
    actual = estimated_rate - current_rate
    ratio = actual / recoverable if recoverable > 1e-12 else None
    return {
        "status": "EVALUABLE" if ratio is not None else "NO_POSITIVE_RECOVERABLE_SPACE",
        "recoverable_space": recoverable,
        "actual_recovered": actual,
        "recovery_ratio": ratio,
        "known_minus_estimated": known_rate - estimated_rate,
    }


def same_opportunity_sets(*sets: set[tuple[str, ...]]) -> bool:
    """Only allow paired rates when every method used the identical truth rows."""
    if not sets or not sets[0]:
        return False
    return all(item == sets[0] for item in sets[1:])


def latest_attempts_by_case(records: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """Select newest attempt per case; never fall back around a newer RUNNING attempt."""
    latest: dict[str, Mapping[str, Any]] = {}
    seen_attempts: set[tuple[str, int]] = set()
    for record in records:
        case_id = str(record.get("case_id") or "")
        if not case_id:
            raise ValueError("matrix attempt is missing case_id")
        attempt = _nonnegative_int(record.get("attempt"), "attempt")
        if attempt <= 0:
            raise ValueError("matrix attempt must be positive")
        identity = (case_id, attempt)
        if identity in seen_attempts:
            raise ValueError(f"duplicate attempt identity: {identity}")
        seen_attempts.add(identity)
        previous = latest.get(case_id)
        if previous is None or attempt > int(previous["attempt"]):
            latest[case_id] = record
    return latest


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json_member(archive: zipfile.ZipFile, name: str) -> dict[str, Any]:
    try:
        value = json.loads(archive.read(name))
    except KeyError as exc:
        raise ValueError(f"compact archive is missing {name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"compact archive member {name} must contain a JSON object")
    return value


def _read_csv_member(archive: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    try:
        with archive.open(name) as raw:
            return list(csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")))
    except KeyError as exc:
        raise ValueError(f"compact archive is missing {name}") from exc


def _case_id(spec: Mapping[str, Any]) -> str:
    return base_matrix.r22.case_identifier(
        phase=str(spec["phase"]),
        seed=int(spec["seed"]),
        speed_mps=float(spec["speed_mps"]),
        snr_db=float(spec["snr_db"]),
        delay_ns=float(spec["delay_ns"]),
        target_count=int(spec["target_count"]),
        period_count=int(spec["period_count"]),
    )


def _verify_design_and_manifest_identity(
    design_path: Path, design: Mapping[str, Any], matrix_manifest: Mapping[str, Any]
) -> dict[str, str]:
    identity = v3_matrix.validate_design_identity(design_path)
    canonical_sha = base_matrix._design_digest(design)
    execution = matrix_manifest.get("execution_identity")
    if not isinstance(execution, Mapping):
        raise ValueError("matrix manifest lacks execution_identity")
    if matrix_manifest.get("design_id") != v3_matrix.V3_2_DESIGN_ID:
        raise ValueError("matrix manifest is not for the frozen V3.2 design")
    if matrix_manifest.get("phase") != "screening":
        raise ValueError("matrix manifest phase is not screening")
    if matrix_manifest.get("design_sha256") != canonical_sha:
        raise ValueError("matrix canonical design digest does not match the committed V3.2 design")
    if Path(str(execution.get("design_path", ""))).expanduser().resolve() != design_path.resolve():
        raise ValueError("matrix execution design path differs from the requested frozen design")
    if execution.get("design_file_sha256") != identity["design_file_sha256"]:
        raise ValueError("matrix raw design-file SHA-256 differs from the frozen V3.2 file")
    expected = int(design["v3_method_unit_applicability"]["expected_screening_case_count"])
    if int(matrix_manifest.get("planned_case_count", -1)) != expected:
        raise ValueError("matrix planned case count differs from the frozen V3.2 denominator")
    return {
        **identity,
        "canonical_design_sha256": canonical_sha,
        "design_id": str(matrix_manifest["design_id"]),
    }


def _verify_archive(
    record: Mapping[str, Any], archive_root: Path
) -> tuple[zipfile.ZipFile, dict[str, Any]]:
    metadata = record.get("compact_archive")
    if not isinstance(metadata, Mapping):
        raise ValueError(f"{record.get('case_id')}: compact archive metadata is missing")
    if record.get("compact_archive_status") != "VERIFIED_AND_RAW_REMOVED":
        raise ValueError(f"{record.get('case_id')}: archive/raw cleanup gate is not verified")
    if record.get("raw_output_removed_after_archive_verification") is not True:
        raise ValueError(f"{record.get('case_id')}: raw removal is not recorded after verification")
    if metadata.get("raw_case_removed") is not True:
        raise ValueError(f"{record.get('case_id')}: compact archive does not record raw removal")
    path = Path(str(metadata.get("archive_path", ""))).expanduser().resolve()
    try:
        path.relative_to(archive_root.resolve())
    except ValueError as exc:
        raise ValueError(f"archive path is outside the V3.2 archive root: {path}") from exc
    if not path.is_file():
        raise ValueError(f"{record.get('case_id')}: compact archive is missing: {path}")
    actual_sha = _sha256_file(path)
    if actual_sha != metadata.get("archive_sha256"):
        raise ValueError(f"{record.get('case_id')}: compact archive SHA-256 mismatch")

    archive = zipfile.ZipFile(path, "r")
    bad_member = archive.testzip()
    if bad_member is not None:
        archive.close()
        raise ValueError(f"{record.get('case_id')}: ZIP CRC error in {bad_member}")
    manifest_bytes = archive.read("ARCHIVE_MANIFEST.json")
    if _sha256_bytes(manifest_bytes) != metadata.get("archive_manifest_sha256"):
        archive.close()
        raise ValueError(f"{record.get('case_id')}: embedded archive manifest SHA-256 mismatch")
    archive_manifest = json.loads(manifest_bytes)
    included = archive_manifest.get("included_files")
    if not isinstance(included, list):
        archive.close()
        raise ValueError(f"{record.get('case_id')}: embedded archive manifest has no included_files")
    names = archive.namelist()
    if len(names) != len(set(names)):
        archive.close()
        raise ValueError(f"{record.get('case_id')}: compact archive has duplicate member names")
    expected_names = {str(item["path"]) for item in included} | {"ARCHIVE_MANIFEST.json"}
    if set(names) != expected_names:
        archive.close()
        raise ValueError(f"{record.get('case_id')}: archive member list differs from its manifest")
    sizes = {info.filename: info.file_size for info in archive.infolist()}
    for item in included:
        if sizes.get(str(item["path"])) != int(item["bytes"]):
            archive.close()
            raise ValueError(f"{record.get('case_id')}: archive member size mismatch: {item['path']}")
    return archive, archive_manifest


def _expected_units_for_spec(
    design: Mapping[str, Any], spec: Mapping[str, Any]
) -> list[tuple[str, str]]:
    return [
        (row["method_id"], row["mode"])
        for row in v3_matrix.expected_method_units(design, spec)
    ]


def expected_production_branch_names(
    units: Sequence[tuple[str, str]],
    unit_statuses: Mapping[tuple[str, str], str],
    condition_ids: Mapping[tuple[str, str], str],
) -> set[str]:
    """Omit OFF/TO only when that method's ON reference is non-evaluable."""
    expected: set[str] = set()
    for method_id, mode in units:
        condition = str(condition_ids.get((method_id, mode)) or "")
        if not condition:
            raise ValueError(f"missing branch condition for {method_id}/{mode}")
        roles = (
            ("ON",)
            if unit_statuses.get((method_id, mode)) == "REFERENCE_NOT_EVALUABLE"
            else ("OFF", "ON", "TO")
        )
        expected.update(f"{condition}_{role}" for role in roles)
    return expected


def _branch_condition_for_unit(
    method_id: str, mode: str, task_condition: Any
) -> str:
    """Map evaluator condition IDs to the Production branch naming contract."""
    condition = str(task_condition or "")
    if (
        mode == "not_applicable"
        and method_id.startswith("R22_")
        and condition == f"{method_id}_not_applicable"
    ):
        # The R22 correction pipeline names these branches by method ID; its
        # task table appends the mode for evaluator identity only.
        return method_id
    return condition


def _validate_background_reuse_mappings(
    archive: zipfile.ZipFile, period_count: int
) -> str:
    """Require byte-identical OFF/TO/ON background-to-beam mappings."""
    suffixes = (
        "/scenes/A0_OFF/reports/background_reuse_mapping.csv",
        "/scenes/A0_TO/reports/background_reuse_mapping.csv",
        "/scenes/A1_ON/reports/background_reuse_mapping.csv",
    )
    selected: list[tuple[str, bytes]] = []
    names = archive.namelist()
    for suffix in suffixes:
        matches = [name for name in names if name.endswith(suffix)]
        if len(matches) != 1:
            raise ValueError(
                f"expected exactly one background mapping ending {suffix}; found {len(matches)}"
            )
        selected.append((matches[0], archive.read(matches[0])))
    if len({payload for _, payload in selected}) != 1:
        raise ValueError("OFF/TO/ON background reuse mapping CSVs are not byte-identical")

    rows = _read_csv_member(archive, selected[0][0])
    expected_periods = {str(period) for period in range(period_count)}
    periods = {str(row.get("period_id", "")) for row in rows}
    if len(rows) != period_count or periods != expected_periods:
        raise ValueError("background reuse mapping does not cover every expected period exactly once")
    if any(str(row.get("mapping_status", "")).lower() != "matched" for row in rows):
        raise ValueError("background reuse mapping contains an unmatched period")
    return "PASSED_IDENTICAL_MATCHED_THREE_ROLE_MAPPINGS"


def _validate_case_run_identity(
    run: Mapping[str, Any],
    record: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> None:
    expected_case_id = str(spec["case_id"])
    if str(record.get("case_id")) != expected_case_id or str(run.get("case_id")) != expected_case_id:
        raise ValueError(f"{expected_case_id}: matrix and archived case identities do not match")
    if int(record.get("seed", -1)) != int(spec["seed"]):
        raise ValueError(f"{expected_case_id}: matrix seed differs from the frozen case")
    design = run.get("design")
    if not isinstance(design, Mapping):
        raise ValueError(f"{expected_case_id}: archived run design identity is missing")
    numeric_fields = {
        "seed": ("seed", int),
        "radial_speed_mps": ("speed_mps", float),
        "target_snr_db": ("snr_db", float),
        "delay_error_ns": ("delay_ns", float),
        "target_count": ("target_count", int),
        "period_count": ("period_count", int),
        "first_beam": ("first_beam", int),
        "beam_count": ("beam_count", int),
    }
    for run_field, (spec_field, convert) in numeric_fields.items():
        try:
            actual = convert(design[run_field])
            expected = convert(spec[spec_field])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{expected_case_id}: invalid run identity field {run_field}") from exc
        if actual != expected:
            raise ValueError(
                f"{expected_case_id}: archived {run_field}={actual} differs from frozen {expected}"
            )
    if design.get("ai_training") is not False or design.get("router_enabled") is not False:
        raise ValueError(f"{expected_case_id}: archived run unexpectedly enables AI training or Router")


def _valid_truth_keys(
    rows: Iterable[Mapping[str, Any]], method_id: str, mode: str
) -> set[tuple[str, ...]]:
    keys: set[tuple[str, ...]] = set()
    for row in rows:
        if (
            row.get("role") == "ON"
            and row.get("method_id") == method_id
            and row.get("mode") == mode
            and row.get("status") == "EVALUABLE"
        ):
            key = (str(row.get("period_id")), str(row.get("beam_id")), str(row.get("target_id")))
            if key in keys:
                raise ValueError(f"duplicate valid truth opportunity for {method_id}/{mode}: {key}")
            keys.add(key)
    return keys


def _parse_branch_name(name: str) -> tuple[str, str, str]:
    for role in ("OFF", "ON", "TO"):
        suffix = f"_{role}"
        if name.endswith(suffix):
            condition = name[: -len(suffix)]
            lower = condition.lower()
            if lower.endswith("_modea"):
                return condition[:-6], "Mode-A", role
            if lower.endswith("_modeb"):
                return condition[:-6], "Mode-B", role
            return condition, "not_applicable", role
    raise ValueError(f"unrecognized production branch name: {name}")


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _sample_sd(values: Sequence[float]) -> float | None:
    return statistics.stdev(values) if len(values) >= 2 else None


def _extract_case_archive(
    record: Mapping[str, Any],
    spec: Mapping[str, Any],
    expected_units: Sequence[tuple[str, str]],
    archive_root: Path,
) -> dict[str, Any]:
    archive, archive_manifest = _verify_archive(record, archive_root)
    try:
        run = _read_json_member(archive, "r22_run_manifest.json")
        review = _read_json_member(archive, "case_manifest_review_extract.json")
        _validate_case_run_identity(run, record, spec)
        task_rows = _read_csv_member(archive, "evaluation/task_metrics.csv")
        target_rows = _read_csv_member(archive, "evaluation/target_level_metrics.csv")
        parameter_rows = _read_csv_member(archive, "evaluation/parameter_metrics.csv")
        track_rows = _read_csv_member(archive, "evaluation/track_level_metrics.csv")
        false_cut_rows = _read_csv_member(archive, "evaluation/h0_false_alarm_metrics.csv")

        area = review.get("area_clutter_gate")
        if not isinstance(area, Mapping) or str(area.get("status", "")).lower() != "passed":
            raise ValueError(f"{record['case_id']}: area-clutter gate is not passed")
        if int(area.get("continuous_area_packets", 0)) <= 0:
            raise ValueError(f"{record['case_id']}: continuous area packet count is not positive")
        if int(area.get("continuous_area_samples", 0)) <= 0:
            raise ValueError(f"{record['case_id']}: continuous area sample count is not positive")
        area_power = _finite_float(
            area.get("area_mean_power_measured", area.get("mean_power"))
        )
        if area_power is None or area_power < 0:
            raise ValueError(f"{record['case_id']}: measured area-clutter power is invalid")

        triplets = review.get("causal_triplets")
        if not isinstance(triplets, Mapping) or not triplets:
            raise ValueError(f"{record['case_id']}: causal triplet audit is missing")
        if any(str(row.get("status", "")).lower() != "passed" for row in triplets.values()):
            raise ValueError(f"{record['case_id']}: a causal triplet gate did not pass")
        evidence = review.get("evidence_gate")
        if (
            not isinstance(evidence, Mapping)
            or evidence.get("causal_triplets") != "passed"
            or evidence.get("fallback_to_current") is not False
            or evidence.get("truth_used_in_estimator") is not False
        ):
            raise ValueError(f"{record['case_id']}: causal/evaluator evidence gate is invalid")

        pair = review.get("pair_identity")
        if not isinstance(pair, Mapping):
            raise ValueError(f"{record['case_id']}: pair identity is missing")
        if int(pair.get("seed", -1)) != int(spec["seed"]):
            raise ValueError(f"{record['case_id']}: paired seed identity mismatch")
        if float(pair.get("delay_error_ns", math.nan)) != float(spec["delay_ns"]):
            raise ValueError(f"{record['case_id']}: paired delay identity mismatch")
        if not str(pair.get("background_id") or "").strip():
            raise ValueError(f"{record['case_id']}: paired background identity is missing")

        independent_h0 = int(run.get("evaluation_summary", {}).get("independent_h0_seed_rows", 0))
        if independent_h0 != 0:
            raise ValueError(f"{record['case_id']}: target-present Screening unexpectedly contains H0 seeds")

        method_units = record.get("case_evaluation", {}).get("method_units")
        if not isinstance(method_units, list):
            raise ValueError(f"{record['case_id']}: method-level evaluation manifest is missing")
        units_by_key = {
            (str(item.get("method_id")), str(item.get("mode"))): item for item in method_units
        }
        if len(units_by_key) != len(method_units) or set(units_by_key) != set(expected_units):
            raise ValueError(f"{record['case_id']}: method applicability differs from frozen design")
        unit_statuses = {key: str(unit.get("status")) for key, unit in units_by_key.items()}
        task_by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
        for row in task_rows:
            if row.get("role") != "ON":
                continue
            key = (str(row.get("method_id")), str(row.get("mode")))
            if key in task_by_key:
                raise ValueError(f"{record['case_id']}: duplicate ON task row for {key}")
            task_by_key[key] = row
        if set(task_by_key) != set(expected_units):
            raise ValueError(f"{record['case_id']}: ON task-row identities differ from frozen units")
        branch_conditions: dict[tuple[str, str], str] = {}
        for method_id, mode in expected_units:
            if mode == "Mode-A":
                branch_conditions[(method_id, mode)] = f"{method_id}_modea"
            elif mode == "Mode-B":
                branch_conditions[(method_id, mode)] = f"{method_id}_modeb"
            else:
                branch_conditions[(method_id, mode)] = _branch_condition_for_unit(
                    method_id,
                    mode,
                    task_by_key[(method_id, mode)].get("condition"),
                )
        expected_branch_names = expected_production_branch_names(
            expected_units, unit_statuses, branch_conditions
        )
        reference_gap_count = sum(
            status == "REFERENCE_NOT_EVALUABLE" for status in unit_statuses.values()
        )

        endpoint_rows: list[dict[str, Any]] = []
        opportunity_sets: dict[tuple[str, str], set[tuple[str, ...]]] = {}
        for method_id, mode in expected_units:
            unit = units_by_key[(method_id, mode)]
            task = task_by_key[(method_id, mode)]
            endpoint = parse_task_endpoint(task)
            manifest_evaluable = str(unit.get("status")) == "EVALUABLE"
            row_evaluable = endpoint["status"] == "EVALUABLE"
            if manifest_evaluable != row_evaluable:
                raise ValueError(
                    f"{record['case_id']}: task row and method-unit status disagree for {method_id}/{mode}"
                )
            keys = _valid_truth_keys(target_rows, method_id, mode) if row_evaluable else set()
            if row_evaluable and len(keys) != endpoint["opportunities"]:
                raise ValueError(
                    f"{record['case_id']}: truth opportunity count mismatch for {method_id}/{mode}"
                )
            opportunity_sets[(method_id, mode)] = keys
            endpoint_rows.append({
                "case_id": str(record["case_id"]),
                "seed": int(spec["seed"]),
                "speed_mps": float(spec["speed_mps"]),
                "snr_db": float(spec["snr_db"]),
                "delay_ns": float(spec["delay_ns"]),
                "method_id": method_id,
                "mode": mode,
                "case_status": str(record.get("status")),
                "unit_status": str(unit.get("status")),
                "unit_reason": str(unit.get("reason") or ""),
                "task_status": endpoint["status"],
                "hits": endpoint["hits"],
                "misses": endpoint["misses"],
                "evaluable_truth_opportunities": endpoint["opportunities"],
                "hit_rate": endpoint["hit_rate"],
                "reason": endpoint["reason"] or str(unit.get("reason") or ""),
            })

        period_count = int(spec["period_count"])
        background_mapping_status = _validate_background_reuse_mappings(
            archive, period_count
        )
        planned_branch_count = len(expected_units) * 3
        expected_branch_count = len(expected_branch_names)
        branch_members = [
            name for name in archive.namelist()
            if name.endswith("/branch_manifest.json") and "/production/" in name
        ]
        branch_by_name: dict[str, dict[str, Any]] = {}
        for name in branch_members:
            branch = _read_json_member(archive, name)
            branch_name = str(branch.get("branch_name") or "")
            if not branch_name or branch_name in branch_by_name:
                raise ValueError(f"{record['case_id']}: duplicate or missing production branch identity")
            if str(branch.get("status", "")).lower() != "passed":
                raise ValueError(f"{record['case_id']}: production branch {branch_name} did not pass")
            if str(branch.get("production_status", "")).lower() != "passed":
                raise ValueError(f"{record['case_id']}: Production failed for {branch_name}")
            if int(branch.get("production_returncode", -1)) != 0:
                raise ValueError(f"{record['case_id']}: nonzero Production return code for {branch_name}")
            branch_by_name[branch_name] = branch
        if set(branch_by_name) != expected_branch_names:
            raise ValueError(
                f"{record['case_id']}: production branch identities differ from method-level gates; "
                f"missing={sorted(expected_branch_names - set(branch_by_name))}, "
                f"extra={sorted(set(branch_by_name) - expected_branch_names)}"
            )
        if int(run.get("production_branch_count", -1)) != planned_branch_count:
            raise ValueError(f"{record['case_id']}: planned Production branch count differs from frozen applicability")

        csi_files: dict[str, list[str]] = defaultdict(list)
        geometry_files: dict[str, list[str]] = defaultdict(list)
        for name in archive.namelist():
            parts = name.split("/")
            if "production" not in parts:
                continue
            index = parts.index("production")
            if len(parts) <= index + 1:
                continue
            branch_name = parts[index + 1]
            if name.endswith("csi_metric_tap_summary.csv"):
                csi_files[branch_name].append(name)
            elif name.endswith("cfar_geometry_diagnostics.csv"):
                geometry_files[branch_name].append(name)
        if set(csi_files) != set(branch_by_name) or set(geometry_files) != set(branch_by_name):
            raise ValueError(f"{record['case_id']}: CSI taps or CFAR geometry are incomplete by branch")

        csi_diagnostics: list[dict[str, Any]] = []
        for branch_name, files in csi_files.items():
            if len(files) != period_count:
                raise ValueError(
                    f"{record['case_id']}: {branch_name} has {len(files)} CSI tap files, "
                    f"expected {period_count}"
                )
            branch_roi_rows: list[dict[str, str]] = []
            for name in files:
                branch_roi_rows.extend(_read_csv_member(archive, name))
            active_rows = [
                row for row in branch_roi_rows
                if row.get("roi_name") == "active_support_unmasked"
            ]
            active_periods = [str(row.get("period_id")) for row in active_rows]
            if (
                len(active_periods) != period_count
                or set(active_periods) != {str(index) for index in range(period_count)}
                or len(active_periods) != len(set(active_periods))
            ):
                raise ValueError(f"{record['case_id']}: {branch_name} CSI tap periods are incomplete")
            method_id, mode, role = _parse_branch_name(branch_name)
            by_roi: dict[str, list[dict[str, str]]] = defaultdict(list)
            for row in branch_roi_rows:
                by_roi[str(row.get("roi_name") or "")].append(row)
            for roi, rows in sorted(by_roi.items()):
                metrics: dict[str, list[float]] = defaultdict(list)
                for row in rows:
                    for field in (
                        "CA_ROI_dB",
                        "phase_model_weighted_coherence",
                        "phase_model_weighted_circular_rmse_rad",
                        "before_mean_power",
                        "after_mean_power",
                    ):
                        value = _finite_float(row.get(field))
                        if value is not None:
                            metrics[field].append(value)
                csi_diagnostics.append({
                    "case_id": str(record["case_id"]),
                    "seed": int(spec["seed"]),
                    "speed_mps": float(spec["speed_mps"]),
                    "snr_db": float(spec["snr_db"]),
                    "delay_ns": float(spec["delay_ns"]),
                    "method_id": method_id,
                    "mode": mode,
                    "role": role,
                    "branch_name": branch_name,
                    "roi_name": roi,
                    "period_count": len({str(row.get("period_id")) for row in rows}),
                    "CA_ROI_dB_mean": _mean(metrics["CA_ROI_dB"]),
                    "phase_weighted_coherence_mean": _mean(
                        metrics["phase_model_weighted_coherence"]
                    ),
                    "phase_weighted_circular_rmse_rad_mean": _mean(
                        metrics["phase_model_weighted_circular_rmse_rad"]
                    ),
                    "before_mean_power_mean": _mean(metrics["before_mean_power"]),
                    "after_mean_power_mean": _mean(metrics["after_mean_power"]),
                    "interpretation": "diagnostic only; active-support CSI ROI mixes target, clutter, and noise",
                })

        geometry_rows = 0
        valid_cut_counts: list[int] = []
        expected_geometry_keys = {
            (str(period), branch)
            for period in range(period_count)
            for branch in ("union_dynamic_in_band", "union_full_band")
        }
        for branch_name, files in geometry_files.items():
            if len(files) != 1:
                raise ValueError(f"{record['case_id']}: {branch_name} must have one CFAR geometry file")
            rows = _read_csv_member(archive, files[0])
            keys = {(str(row.get("period_id")), str(row.get("branch"))) for row in rows}
            if len(rows) != len(expected_geometry_keys) or keys != expected_geometry_keys:
                raise ValueError(f"{record['case_id']}: {branch_name} CFAR geometry periods are incomplete")
            for row in rows:
                threshold_tests = _nonnegative_int(row.get("threshold_test_count"), "threshold_test_count")
                valid_cuts = _nonnegative_int(row.get("valid_cut_count"), "valid_cut_count")
                if valid_cuts <= 0 or threshold_tests != valid_cuts:
                    raise ValueError(f"{record['case_id']}: {branch_name} has invalid CFAR CUT denominator")
                valid_cut_counts.append(valid_cuts)
            geometry_rows += len(rows)

        selected_units = set(expected_units)
        parameter_case_rows: list[dict[str, Any]] = []
        for row in parameter_rows:
            if row.get("role") != "ON":
                continue
            key = (str(row.get("method_id")), str(row.get("mode")))
            if key not in selected_units:
                continue
            parameter_case_rows.append({
                "case_id": str(record["case_id"]),
                "seed": int(spec["seed"]),
                "speed_mps": float(spec["speed_mps"]),
                "snr_db": float(spec["snr_db"]),
                "delay_ns": float(spec["delay_ns"]),
                "method_id": key[0],
                "mode": key[1],
                "metric": str(row.get("metric") or ""),
                "truth_opportunities": _finite_float(row.get("truth_opportunities")),
                "matched_detection_opportunities": _finite_float(
                    row.get("matched_detection_opportunities")
                ),
                "valid_parameter_opportunities": _finite_float(
                    row.get("valid_parameter_opportunities")
                ),
                "missing_parameter_opportunities": _finite_float(
                    row.get("missing_parameter_opportunities")
                ),
                "own_success_rmse": _finite_float(row.get("own_success_rmse")),
                "own_success_count": _finite_float(row.get("own_success_count")),
                "evaluator_status": str(row.get("evaluator_status") or ""),
                "missing_reasons": str(row.get("missing_reasons") or ""),
            })

        track_case_rows: list[dict[str, Any]] = []
        for row in track_rows:
            if row.get("role") != "ON":
                continue
            key = (str(row.get("method_id")), str(row.get("mode")))
            if key not in selected_units:
                continue
            track_case_rows.append({
                "case_id": str(record["case_id"]),
                "seed": int(spec["seed"]),
                "speed_mps": float(spec["speed_mps"]),
                "snr_db": float(spec["snr_db"]),
                "delay_ns": float(spec["delay_ns"]),
                "method_id": key[0],
                "mode": key[1],
                "status": str(row.get("status") or ""),
                "confirmed_observation_count": row.get("confirmed_observation_count"),
                "target_confirmed_observation_count": row.get("target_confirmed_observation_count"),
                "target_confirmed_unique_period_count": row.get("target_confirmed_unique_period_count"),
                "post_confirmation_match_rate": row.get("post_confirmation_match_rate"),
                "false_confirmed_track_count": row.get("false_confirmed_track_count"),
                "unique_target_track_count": row.get("unique_target_track_count"),
                "track_fragmentation_count_observed": row.get("track_fragmentation_count_observed"),
                "cross_target_association_payload_count": row.get("cross_target_association_payload_count"),
                "payload_duplicate_key_count": row.get("payload_duplicate_key_count"),
            })

        false_cut_case_rows: list[dict[str, Any]] = []
        for row in false_cut_rows:
            if row.get("role") != "OFF":
                continue
            if row.get("empirical_pfa_status") not in {
                "NOT_EVALUABLE_NO_INDEPENDENT_H0_CUT_UNIT", "", None
            }:
                raise ValueError(f"{record['case_id']}: OFF role was mislabeled as independent H0 Pfa")
            false_cut_case_rows.append({
                "case_id": str(record["case_id"]),
                "seed": int(spec["seed"]),
                "speed_mps": float(spec["speed_mps"]),
                "snr_db": float(spec["snr_db"]),
                "delay_ns": float(spec["delay_ns"]),
                "method_id": str(row.get("method_id") or ""),
                "mode": str(row.get("mode") or ""),
                "cut_band_mode": str(row.get("cut_band_mode") or ""),
                "observed_cut_hit_count": row.get("observed_cut_hit_count"),
                "valid_cut_count": row.get("valid_cut_count"),
                "observed_cut_hit_fraction": row.get("observed_cut_hit_fraction"),
                "status": str(row.get("status") or ""),
                "empirical_pfa_status": "NOT_EVALUABLE_NO_INDEPENDENT_H0_SEED",
                "interpretation": "paired OFF role is descriptive and is not an independent H0 sample",
            })

        return {
            "run": run,
            "review": review,
            "endpoint_rows": endpoint_rows,
            "opportunity_sets": opportunity_sets,
            "parameter_case_rows": parameter_case_rows,
            "track_case_rows": track_case_rows,
            "false_cut_case_rows": false_cut_case_rows,
            "csi_diagnostics": csi_diagnostics,
            "area": area,
            "area_power": area_power,
            "geometry_rows": geometry_rows,
            "valid_cut_min": min(valid_cut_counts),
            "valid_cut_max": max(valid_cut_counts),
            "branch_count": len(branch_by_name),
            "planned_branch_count": planned_branch_count,
            "reference_gap_count": reference_gap_count,
            "background_mapping_status": background_mapping_status,
            "csi_branch_count": len(csi_files),
            "geometry_branch_count": len(geometry_files),
            "archive_manifest": archive_manifest,
        }
    finally:
        archive.close()


def _expected_cases(design: Mapping[str, Any]) -> list[dict[str, Any]]:
    cases = base_matrix.build_case_specs(design, "screening")
    for item in cases:
        item["case_id"] = _case_id(item)
    return cases


def _unit_row(
    spec: Mapping[str, Any],
    method_id: str,
    mode: str,
    *,
    case_status: str,
    unit_status: str,
    unit_reason: str = "",
    endpoint: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    endpoint = endpoint or {}
    return {
        "case_id": spec["case_id"],
        "seed": spec["seed"],
        "speed_mps": spec["speed_mps"],
        "snr_db": spec["snr_db"],
        "delay_ns": spec["delay_ns"],
        "method_id": method_id,
        "mode": mode,
        "case_status": case_status,
        "unit_status": unit_status,
        "unit_reason": unit_reason,
        "task_status": endpoint.get("status", ""),
        "hits": endpoint.get("hits"),
        "misses": endpoint.get("misses"),
        "evaluable_truth_opportunities": endpoint.get("opportunities"),
        "hit_rate": endpoint.get("hit_rate"),
        "reason": endpoint.get("reason", unit_reason),
    }


def _summarize_seed_detection(
    specs: Sequence[Mapping[str, Any]], unit_rows: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped_specs: dict[tuple[int, float, str, str], int] = defaultdict(int)
    for spec in specs:
        for method_id, mode in spec["_expected_units"]:
            grouped_specs[(int(spec["seed"]), float(spec["delay_ns"]), method_id, mode)] += 1

    grouped_rows: dict[tuple[int, float, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in unit_rows:
        grouped_rows[(
            int(row["seed"]), float(row["delay_ns"]), str(row["method_id"]), str(row["mode"])
        )].append(row)

    seed_rows: list[dict[str, Any]] = []
    for key, expected_count in sorted(grouped_specs.items()):
        seed, delay, method_id, mode = key
        rows = grouped_rows.get(key, [])
        evaluated = [row for row in rows if row.get("task_status") == "EVALUABLE"]
        hits = sum(int(row["hits"]) for row in evaluated)
        opportunities = sum(int(row["evaluable_truth_opportunities"]) for row in evaluated)
        seed_rows.append({
            "seed": seed,
            "delay_ns": delay,
            "method_id": method_id,
            "mode": mode,
            "expected_case_count": expected_count,
            "evaluable_case_count": len(evaluated),
            "coverage": len(evaluated) / expected_count if expected_count else None,
            "hits": hits if evaluated else None,
            "evaluable_truth_opportunities": opportunities if evaluated else None,
            "hit_rate_on_evaluable_cases": hits / opportunities if opportunities else None,
            "interpretation": "seed is the independent unit; case opportunities are aggregated within seed",
        })

    summaries: list[dict[str, Any]] = []
    by_method: dict[tuple[float, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in seed_rows:
        by_method[(float(row["delay_ns"]), str(row["method_id"]), str(row["mode"]))].append(row)
    expected_seeds = len({int(spec["seed"]) for spec in specs})
    for (delay, method_id, mode), rows in sorted(by_method.items()):
        evaluable = [float(row["hit_rate_on_evaluable_cases"]) for row in rows
                     if row.get("hit_rate_on_evaluable_cases") is not None]
        full = [float(row["hit_rate_on_evaluable_cases"]) for row in rows
                if row.get("hit_rate_on_evaluable_cases") is not None
                and int(row["evaluable_case_count"]) == int(row["expected_case_count"])]
        summaries.append({
            "delay_ns": delay,
            "method_id": method_id,
            "mode": mode,
            "expected_independent_seeds": expected_seeds,
            "evaluable_independent_seeds": len(evaluable),
            "full_coverage_independent_seeds": len(full),
            "mean_seed_hit_rate_evaluable": _mean(evaluable),
            "sd_seed_hit_rate_evaluable": _sample_sd(evaluable),
            "mean_seed_hit_rate_full_coverage": _mean(full),
            "sd_seed_hit_rate_full_coverage": _sample_sd(full),
            "min_seed_hit_rate_evaluable": min(evaluable) if evaluable else None,
            "max_seed_hit_rate_evaluable": max(evaluable) if evaluable else None,
            "inference": "descriptive Screening summary; no significance claim",
        })
    return seed_rows, summaries


def _paired_recovery(
    specs: Sequence[Mapping[str, Any]],
    endpoints: Mapping[tuple[str, str, str], Mapping[str, Any]],
    opportunity_sets: Mapping[tuple[str, str, str], set[tuple[str, ...]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    condition_rows: list[dict[str, Any]] = []
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for spec in specs:
        if float(spec["delay_ns"]) != 4.0:
            continue
        case_id = str(spec["case_id"])
        for pair in TRIPLET_RECOVERY_SPECS:
            mode = str(pair["mode"])
            current_key = (case_id, str(pair["current_method"]), mode)
            known_key = (case_id, str(pair["known_method"]), mode)
            estimated_key = (case_id, str(pair["estimated_method"]), mode)
            current = endpoints.get(current_key, {})
            known = endpoints.get(known_key, {})
            estimated = endpoints.get(estimated_key, {})
            current_eval = current.get("task_status") == "EVALUABLE"
            known_eval = known.get("task_status") == "EVALUABLE"
            estimated_eval = estimated.get("task_status") == "EVALUABLE"
            keys_match_known_estimated = (
                known_eval and estimated_eval
                and same_opportunity_sets(
                    opportunity_sets.get(known_key, set()),
                    opportunity_sets.get(estimated_key, set()),
                )
            )
            keys_match_triplet = (
                current_eval and known_eval and estimated_eval
                and same_opportunity_sets(
                    opportunity_sets.get(current_key, set()),
                    opportunity_sets.get(known_key, set()),
                    opportunity_sets.get(estimated_key, set()),
                )
            )
            known_minus_estimated = (
                float(known["hit_rate"]) - float(estimated["hit_rate"])
                if keys_match_known_estimated else None
            )
            recovery = compute_recovery(
                current_rate=float(current["hit_rate"]) if keys_match_triplet else None,
                known_rate=float(known["hit_rate"]) if keys_match_triplet else None,
                estimated_rate=float(estimated["hit_rate"]) if keys_match_triplet else None,
            )
            if keys_match_triplet:
                status = recovery["status"]
            elif not known_eval or not estimated_eval:
                status = "NOT_EVALUABLE_KNOWN_OR_ESTIMATED_METHOD"
            elif not current_eval:
                status = "NOT_EVALUABLE_CURRENT_REFERENCE"
            elif not (keys_match_known_estimated and keys_match_triplet):
                status = "NOT_COMPARABLE_TRUTH_OPPORTUNITY_SET"
            else:
                status = "NOT_EVALUABLE"
            row = {
                "case_id": case_id,
                "seed": int(spec["seed"]),
                "speed_mps": float(spec["speed_mps"]),
                "snr_db": float(spec["snr_db"]),
                "delay_ns": float(spec["delay_ns"]),
                "comparison_id": pair["comparison_id"],
                "mode": mode,
                "current_method": pair["current_method"],
                "known_method": pair["known_method"],
                "estimated_method": pair["estimated_method"],
                "current_status": current.get("task_status", "NOT_RUN"),
                "known_status": known.get("task_status", "NOT_RUN"),
                "estimated_status": estimated.get("task_status", "NOT_RUN"),
                "known_estimated_common_opportunities": (
                    len(opportunity_sets.get(known_key, set())) if keys_match_known_estimated else None
                ),
                "known_estimated_common_cases": int(keys_match_known_estimated),
                "known_rate": known.get("hit_rate") if keys_match_known_estimated else None,
                "estimated_rate": estimated.get("hit_rate") if keys_match_known_estimated else None,
                "known_minus_estimated": known_minus_estimated,
                "triplet_status": status,
                "triplet_common_opportunities": (
                    len(opportunity_sets.get(current_key, set())) if keys_match_triplet else None
                ),
                "current_rate": current.get("hit_rate") if keys_match_triplet else None,
                "triplet_known_rate": known.get("hit_rate") if keys_match_triplet else None,
                "triplet_estimated_rate": estimated.get("hit_rate") if keys_match_triplet else None,
                **recovery,
            }
            condition_rows.append(row)
            grouped[(int(spec["seed"]), str(pair["comparison_id"]))].append(row)

    seed_rows: list[dict[str, Any]] = []
    for (seed, comparison_id), rows in sorted(grouped.items()):
        expected_cases = len(rows)
        pair_rows = [row for row in rows if row["known_estimated_common_cases"] == 1]
        triplet_rows = [row for row in rows if row["triplet_status"] in {
            "EVALUABLE", "NO_POSITIVE_RECOVERABLE_SPACE"
        } and row["triplet_common_opportunities"] is not None]
        pair_opportunities = sum(int(row["known_estimated_common_opportunities"]) for row in pair_rows)
        triplet_opportunities = sum(int(row["triplet_common_opportunities"]) for row in triplet_rows)
        pair = next(item for item in TRIPLET_RECOVERY_SPECS
                    if item["comparison_id"] == comparison_id)

        def pooled_rate(rows_: Sequence[Mapping[str, Any]], method_field: str) -> float | None:
            total_opportunities = sum(int(row["triplet_common_opportunities"]) for row in rows_)
            if total_opportunities <= 0:
                return None
            total_hits = 0
            method_id = str(pair[method_field])
            for row in rows_:
                endpoint = endpoints[(str(row["case_id"]), method_id, str(pair["mode"]))]
                if endpoint.get("task_status") != "EVALUABLE":
                    raise ValueError("paired recovery aggregation lost an evaluable endpoint")
                total_hits += int(endpoint["hits"])
            return total_hits / total_opportunities

        pair_known_hits = 0
        pair_estimated_hits = 0
        for row in pair_rows:
            pair_known_hits += int(
                endpoints[(str(row["case_id"]), str(pair["known_method"]), str(pair["mode"]))]["hits"]
            )
            pair_estimated_hits += int(
                endpoints[(str(row["case_id"]), str(pair["estimated_method"]), str(pair["mode"]))]["hits"]
            )
        pair_known_rate = pair_known_hits / pair_opportunities if pair_opportunities else None
        pair_estimated_rate = pair_estimated_hits / pair_opportunities if pair_opportunities else None
        current_rate = pooled_rate(triplet_rows, "current_method") if triplet_rows else None
        known_rate = pooled_rate(triplet_rows, "known_method") if triplet_rows else None
        estimated_rate = pooled_rate(triplet_rows, "estimated_method") if triplet_rows else None
        recovery = compute_recovery(
            current_rate=current_rate, known_rate=known_rate, estimated_rate=estimated_rate
        )
        seed_rows.append({
            "seed": seed,
            "delay_ns": 4.0,
            "comparison_id": comparison_id,
            "expected_condition_count": expected_cases,
            "known_estimated_common_case_count": len(pair_rows),
            "known_estimated_common_opportunities": pair_opportunities,
            "known_rate_on_pair_common_cases": pair_known_rate,
            "estimated_rate_on_pair_common_cases": pair_estimated_rate,
            "known_minus_estimated_on_pair_common_cases": (
                pair_known_rate - pair_estimated_rate
                if pair_known_rate is not None and pair_estimated_rate is not None else None
            ),
            "triplet_common_case_count": len(triplet_rows),
            "triplet_common_opportunities": triplet_opportunities,
            "current_rate": current_rate,
            "known_rate": known_rate,
            "estimated_rate": estimated_rate,
            **recovery,
            "interpretation": (
                "pooled across matched speed/SNR cases within one independent seed; "
                "opportunity rows are not independent seeds"
            ),
        })

    summaries: list[dict[str, Any]] = []
    for comparison_id in sorted({str(row["comparison_id"]) for row in seed_rows}):
        rows = [row for row in seed_rows if row["comparison_id"] == comparison_id]
        summaries.append({
            "comparison_id": comparison_id,
            "expected_independent_seeds": len({int(spec["seed"]) for spec in specs}),
            "seeds_with_triplet": sum(row["triplet_common_case_count"] > 0 for row in rows),
            "seeds_with_full_triplet_coverage": sum(
                row["triplet_common_case_count"] == row["expected_condition_count"] for row in rows
            ),
            "mean_recoverable_space_across_seeds": _mean([
                float(row["recoverable_space"]) for row in rows
                if row.get("recoverable_space") is not None
            ]),
            "sd_recoverable_space_across_seeds": _sample_sd([
                float(row["recoverable_space"]) for row in rows
                if row.get("recoverable_space") is not None
            ]),
            "mean_actual_recovered_across_seeds": _mean([
                float(row["actual_recovered"]) for row in rows
                if row.get("actual_recovered") is not None
            ]),
            "sd_actual_recovered_across_seeds": _sample_sd([
                float(row["actual_recovered"]) for row in rows
                if row.get("actual_recovered") is not None
            ]),
            "mean_recovery_ratio_across_seeds": _mean([
                float(row["recovery_ratio"]) for row in rows
                if row.get("recovery_ratio") is not None
            ]),
            "sd_recovery_ratio_across_seeds": _sample_sd([
                float(row["recovery_ratio"]) for row in rows
                if row.get("recovery_ratio") is not None
            ]),
            "inference": "descriptive Screening summary; no significance claim",
        })
    return condition_rows, seed_rows, summaries


def _aggregate_parameters(
    specs: Sequence[Mapping[str, Any]],
    parameter_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    expected: dict[tuple[int, float, str, str], int] = defaultdict(int)
    for spec in specs:
        for method_id, mode in spec["_expected_units"]:
            expected[(int(spec["seed"]), float(spec["delay_ns"]), method_id, mode)] += 1
    grouped: dict[tuple[int, float, str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in parameter_rows:
        grouped[(
            int(row["seed"]), float(row["delay_ns"]), str(row["method_id"]),
            str(row["mode"]), str(row["metric"]),
        )].append(row)
    seed_summary: list[dict[str, Any]] = []
    for (seed, delay, method_id, mode), expected_count in sorted(expected.items()):
        metrics = sorted({metric for (s, d, m, md, metric) in grouped
                          if (s, d, m, md) == (seed, delay, method_id, mode)})
        for metric in metrics:
            rows = grouped[(seed, delay, method_id, mode, metric)]
            successful = [
                row for row in rows
                if row.get("evaluator_status") == "EVALUABLE"
                and row.get("own_success_count") is not None
                and row.get("own_success_rmse") is not None
                and float(row["own_success_count"]) > 0
            ]
            own_count = sum(float(row["own_success_count"]) for row in successful)
            sum_sq = sum(
                float(row["own_success_rmse"]) ** 2 * float(row["own_success_count"])
                for row in successful
            )
            seed_summary.append({
                "seed": seed,
                "delay_ns": delay,
                "method_id": method_id,
                "mode": mode,
                "metric": metric,
                "expected_case_count": expected_count,
                "source_row_count": len(rows),
                "evaluable_case_count": sum(row.get("evaluator_status") == "EVALUABLE" for row in rows),
                "truth_opportunities": sum(
                    float(row["truth_opportunities"]) for row in rows
                    if row.get("truth_opportunities") is not None
                ),
                "matched_detection_opportunities": sum(
                    float(row["matched_detection_opportunities"]) for row in rows
                    if row.get("matched_detection_opportunities") is not None
                ),
                "valid_parameter_opportunities": sum(
                    float(row["valid_parameter_opportunities"]) for row in rows
                    if row.get("valid_parameter_opportunities") is not None
                ),
                "missing_parameter_opportunities": sum(
                    float(row["missing_parameter_opportunities"]) for row in rows
                    if row.get("missing_parameter_opportunities") is not None
                ),
                "own_success_count": own_count,
                "weighted_own_success_rmse": math.sqrt(sum_sq / own_count) if own_count else None,
                "interpretation": "RMSE aggregates own-success observations only; missing estimates are not zero-filled",
            })
    return list(parameter_rows), seed_summary


def _summarize_seed_metric(
    rows: Sequence[Mapping[str, Any]],
    group_fields: Sequence[str],
    value_field: str,
    expected_seeds: int,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row[field] for field in group_fields)].append(row)
    result: list[dict[str, Any]] = []
    for key, group in sorted(grouped.items(), key=lambda item: tuple(map(str, item[0]))):
        values = [float(row[value_field]) for row in group if row.get(value_field) is not None]
        output = dict(zip(group_fields, key))
        output.update({
            "expected_independent_seeds": expected_seeds,
            "evaluable_independent_seeds": len(values),
            "mean_across_seed_summaries": _mean(values),
            "sd_across_seed_summaries": _sample_sd(values),
            "min_across_seed_summaries": min(values) if values else None,
            "max_across_seed_summaries": max(values) if values else None,
            "inference": "descriptive Screening summary; no significance claim",
        })
        result.append(output)
    return result


def analyze_snapshot(
    design_path: Path,
    matrix_manifest_path: Path,
    archive_root: Path,
) -> dict[str, Any]:
    design_path = design_path.expanduser().resolve()
    matrix_manifest_path = matrix_manifest_path.expanduser().resolve()
    archive_root = archive_root.expanduser().resolve()
    design = v3_matrix.load_v3_design(design_path)
    matrix_bytes = matrix_manifest_path.read_bytes()
    matrix_manifest = json.loads(matrix_bytes)
    identity = _verify_design_and_manifest_identity(design_path, design, matrix_manifest)
    specs = _expected_cases(design)
    if len(specs) != int(design["v3_method_unit_applicability"]["expected_screening_case_count"]):
        raise ValueError("frozen design expands to an unexpected number of Screening cases")
    for spec in specs:
        spec["_expected_units"] = _expected_units_for_spec(design, spec)

    latest = latest_attempts_by_case(matrix_manifest.get("cases", []))
    expected_ids = {str(spec["case_id"]) for spec in specs}
    unexpected_ids = set(latest) - expected_ids
    if unexpected_ids:
        raise ValueError(f"matrix contains case IDs outside the frozen design: {sorted(unexpected_ids)[:5]}")

    unit_rows: list[dict[str, Any]] = []
    endpoint_map: dict[tuple[str, str, str], dict[str, Any]] = {}
    opportunity_sets: dict[tuple[str, str, str], set[tuple[str, ...]]] = {}
    case_audit_rows: list[dict[str, Any]] = []
    parameter_rows: list[dict[str, Any]] = []
    track_rows: list[dict[str, Any]] = []
    false_cut_rows: list[dict[str, Any]] = []
    csi_rows: list[dict[str, Any]] = []
    valid_area_powers: dict[int, list[float]] = defaultdict(list)
    verified_archives = 0

    for spec in specs:
        case_id = str(spec["case_id"])
        record = latest.get(case_id)
        if record is None:
            status = "NOT_RUN"
            case_audit_rows.append({
                "case_id": case_id,
                "seed": spec["seed"],
                "speed_mps": spec["speed_mps"],
                "snr_db": spec["snr_db"],
                "delay_ns": spec["delay_ns"],
                "case_status": status,
                "reason": "no_matrix_attempt",
            })
            for method_id, mode in spec["_expected_units"]:
                row = _unit_row(
                    spec, method_id, mode, case_status=status, unit_status=status,
                    unit_reason="no_matrix_attempt",
                )
                unit_rows.append(row)
                endpoint_map[(case_id, method_id, mode)] = row
                opportunity_sets[(case_id, method_id, mode)] = set()
            continue

        status = str(record.get("status") or "UNKNOWN_STATUS")
        base_audit = {
            "case_id": case_id,
            "seed": spec["seed"],
            "speed_mps": spec["speed_mps"],
            "snr_db": spec["snr_db"],
            "delay_ns": spec["delay_ns"],
            "attempt": record.get("attempt"),
            "case_status": status,
            "scenario_status": record.get("case_evaluation", {}).get("scenario_status", ""),
            "scenario_reason": record.get("case_evaluation", {}).get("scenario_reason", ""),
            "archive_status": record.get("compact_archive_status", ""),
            "archive_sha256": record.get("compact_archive", {}).get("archive_sha256", ""),
            "archive_bytes": record.get("compact_archive", {}).get("archive_bytes", ""),
            "raw_removed_after_verification": record.get(
                "raw_output_removed_after_archive_verification", False
            ),
            "applicable_method_units": len(spec["_expected_units"]),
        }
        if status not in PASS_STATES:
            base_audit.update({
                "reason": status,
                "area_status": "",
                "continuous_area_packets": "",
                "continuous_area_samples": "",
                "area_mean_power_measured": "",
                "causal_triplet_status": "",
                "production_branch_count": "",
                "csi_branch_count": "",
                "cfar_geometry_branch_count": "",
                "cfar_geometry_row_count": "",
                "valid_cut_min": "",
                "valid_cut_max": "",
            })
            case_audit_rows.append(base_audit)
            for method_id, mode in spec["_expected_units"]:
                reason = f"case_status_{status.lower()}"
                row = _unit_row(
                    spec, method_id, mode, case_status=status, unit_status=status,
                    unit_reason=reason,
                )
                unit_rows.append(row)
                endpoint_map[(case_id, method_id, mode)] = row
                opportunity_sets[(case_id, method_id, mode)] = set()
            continue

        extracted = _extract_case_archive(
            record, spec, spec["_expected_units"], archive_root
        )
        verified_archives += 1
        area = extracted["area"]
        valid_area_powers[int(spec["seed"])].append(float(extracted["area_power"]))
        case_eval = record.get("case_evaluation", {})
        method_eval = {
            (str(row["method_id"]), str(row["mode"])): row
            for row in extracted["endpoint_rows"]
        }
        for method_id, mode in spec["_expected_units"]:
            row = method_eval[(method_id, mode)]
            endpoint_map[(case_id, method_id, mode)] = row
            opportunity_sets[(case_id, method_id, mode)] = extracted["opportunity_sets"][(method_id, mode)]
            unit_rows.append(row)
        base_audit.update({
            "area_status": area.get("status"),
            "continuous_area_packets": area.get("continuous_area_packets"),
            "continuous_area_samples": area.get("continuous_area_samples"),
            "area_mean_power_measured": extracted["area_power"],
            "area_rho_lag1_measured": area.get("report_values", {}).get("area_rho_lag1_measured", ""),
            "area_power_cv_measured": area.get("report_values", {}).get("area_power_cv_measured", ""),
            "causal_triplet_status": "passed",
            "background_mapping_status": extracted["background_mapping_status"],
            "reference_gap_count": int(case_eval.get("reference_gap_count", 0)),
            "evaluable_method_units": int(case_eval.get("evaluable_count", 0)),
            "production_branch_count": extracted["branch_count"],
            "csi_branch_count": extracted["csi_branch_count"],
            "cfar_geometry_branch_count": extracted["geometry_branch_count"],
            "cfar_geometry_row_count": extracted["geometry_rows"],
            "valid_cut_min": extracted["valid_cut_min"],
            "valid_cut_max": extracted["valid_cut_max"],
            "production_elapsed_sec_sum": extracted["run"].get("production_elapsed_sec_sum"),
            "reason": "",
        })
        case_audit_rows.append(base_audit)
        parameter_rows.extend(extracted["parameter_case_rows"])
        track_rows.extend(extracted["track_case_rows"])
        false_cut_rows.extend(extracted["false_cut_case_rows"])
        csi_rows.extend(extracted["csi_diagnostics"])

    coverage: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for row in unit_rows:
        status = str(row.get("task_status") or row.get("unit_status") or "NOT_RUN")
        method_key = (str(row["method_id"]), str(row["mode"]))
        if status == "EVALUABLE":
            coverage[method_key]["evaluable"] += 1
        elif status == "NOT_EVALUABLE":
            coverage[method_key]["not_evaluable"] += 1
            if "reference" in str(row.get("reason", "")).lower() or "support" in str(
                row.get("reason", "")
            ).lower():
                coverage[method_key]["reference_gap"] += 1
            else:
                coverage[method_key]["other_not_evaluable"] += 1
        else:
            coverage[method_key]["not_run"] += 1
        coverage[method_key]["applicable"] += 1
    coverage_rows = []
    for (method_id, mode), counts in sorted(coverage.items()):
        denominator = counts["applicable"]
        coverage_rows.append({
            "method_id": method_id,
            "mode": mode,
            "applicable_units": denominator,
            "evaluable_units": counts["evaluable"],
            "reference_gap_units": counts["reference_gap"],
            "other_not_evaluable_units": counts["other_not_evaluable"],
            "not_run_or_global_failure_units": counts["not_run"],
            "coverage": counts["evaluable"] / denominator if denominator else None,
        })

    seed_detection, seed_detection_summary = _summarize_seed_detection(specs, unit_rows)
    pair_condition, pair_seed, pair_summary = _paired_recovery(
        specs, endpoint_map, opportunity_sets
    )
    parameter_case, parameter_seed = _aggregate_parameters(specs, parameter_rows)
    parameter_summary = _summarize_seed_metric(
        parameter_seed,
        ("delay_ns", "method_id", "mode", "metric"),
        "weighted_own_success_rmse",
        len(design["seed_sets"]["screening"]),
    )
    clutter_seed_rows = []
    for seed, powers in sorted(valid_area_powers.items()):
        minimum = min(powers)
        maximum = max(powers)
        mean_power = _mean(powers)
        clutter_seed_rows.append({
            "seed": seed,
            "case_count": len(powers),
            "area_mean_power_min": minimum,
            "area_mean_power_max": maximum,
            "relative_power_span": (maximum - minimum) / abs(mean_power)
            if mean_power not in (None, 0.0) else None,
            "interpretation": "measured Stage2 power across this seed's completed cases",
        })

    expected_count = len(specs)
    completed_count = sum(row["case_status"] in PASS_STATES for row in case_audit_rows)
    expected_units = int(design["v3_method_unit_applicability"]["expected_applicable_on_method_units"])
    unit_classified_count = len(unit_rows)
    matrix_complete = (
        matrix_manifest.get("status") == "COMPLETED"
        and completed_count == expected_count
        and unit_classified_count == expected_units
        and all(row["evaluable_units"] + row["reference_gap_units"]
                + row["other_not_evaluable_units"] + row["not_run_or_global_failure_units"]
                == row["applicable_units"] for row in coverage_rows)
    )
    status_counts = dict(Counter(str(row["case_status"]) for row in case_audit_rows))
    return {
        "metadata": {
            "analysis_status": "COMPLETE_SCREENING" if matrix_complete else "PARTIAL_SCREENING",
            "screening_performance_verified": False,
            "inference": "V3.2 Screening is feasibility/coverage evidence; no statistical significance claim",
            "matrix_status": matrix_manifest.get("status"),
            "matrix_manifest_path": str(matrix_manifest_path),
            "matrix_manifest_sha256": _sha256_bytes(matrix_bytes),
            "design_path": str(design_path),
            "design_file_sha256": identity["design_file_sha256"],
            "canonical_design_sha256": identity["canonical_design_sha256"],
            "design_id": identity["design_id"],
            "planned_case_count": expected_count,
            "latest_attempt_case_count": len(latest),
            "completed_evaluable_case_count": completed_count,
            "verified_archive_count": verified_archives,
            "expected_method_units": expected_units,
            "classified_method_units": unit_classified_count,
            "case_status_counts": status_counts,
            "pfa_status": "NOT_EVALUABLE_NO_INDEPENDENT_H0_SEED",
            "paired_off_cut_fraction_status": "DESCRIPTIVE_ONLY_NOT_STATISTICAL_PFA",
            "raw_policy": "analysis reads compact ZIP members in place; does not extract or modify raw data",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "case_audit": case_audit_rows,
        "method_coverage": coverage_rows,
        "seed_condition_detection": unit_rows,
        "seed_level_detection": seed_detection,
        "seed_detection_summary": seed_detection_summary,
        "paired_condition_recovery": pair_condition,
        "paired_seed_recovery": pair_seed,
        "paired_recovery_summary": pair_summary,
        "parameter_metrics_by_case": parameter_case,
        "parameter_metrics_by_seed": parameter_seed,
        "parameter_summary": parameter_summary,
        "track_metrics_by_case": track_rows,
        "off_false_cut_descriptive": false_cut_rows,
        "csi_diagnostics_by_case_branch_roi": csi_rows,
        "clutter_power_by_seed": clutter_seed_rows,
    }


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    else:
        fieldnames = ["status"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_outputs(result: Mapping[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    file_map = {
        "case_audit.csv": "case_audit",
        "method_coverage.csv": "method_coverage",
        "seed_condition_detection.csv": "seed_condition_detection",
        "seed_level_detection.csv": "seed_level_detection",
        "seed_detection_summary.csv": "seed_detection_summary",
        "paired_condition_recovery.csv": "paired_condition_recovery",
        "paired_seed_recovery.csv": "paired_seed_recovery",
        "paired_recovery_summary.csv": "paired_recovery_summary",
        "parameter_metrics_by_case.csv": "parameter_metrics_by_case",
        "parameter_metrics_by_seed.csv": "parameter_metrics_by_seed",
        "parameter_summary.csv": "parameter_summary",
        "track_metrics_by_case.csv": "track_metrics_by_case",
        "off_false_cut_descriptive.csv": "off_false_cut_descriptive",
        "csi_diagnostics_by_case_branch_roi.csv": "csi_diagnostics_by_case_branch_roi",
        "clutter_power_by_seed.csv": "clutter_power_by_seed",
    }
    for filename, key in file_map.items():
        _write_csv(output_dir / filename, result[key])

    metadata = dict(result["metadata"])
    metadata["output_file_rows"] = {
        filename: len(result[key]) for filename, key in file_map.items()
    }
    (output_dir / "analysis_manifest.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    statuses = metadata["case_status_counts"]
    text = [
        "# R22 V3.2 Screening archive summary",
        "",
        f"- Analysis status: {metadata['analysis_status']}",
        f"- Matrix status: {metadata['matrix_status']}",
        f"- Completed evaluable cases: {metadata['completed_evaluable_case_count']} / "
        f"{metadata['planned_case_count']}",
        f"- Verified compact archives: {metadata['verified_archive_count']}",
        f"- Applicable method units classified: {metadata['classified_method_units']} / "
        f"{metadata['expected_method_units']}",
        f"- Case states: {json.dumps(statuses, sort_keys=True)}",
        f"- Independent H0/Pfa: {metadata['pfa_status']}",
        "",
        "## Interpretation limits",
        "",
        "- Independent inferential unit is the seed; target-period opportunities and CUT counts are not independent seeds.",
        "- This is Screening feasibility and coverage evidence. It does not establish statistical significance or final performance verification.",
        "- A method-level reference gap remains non-evaluable; it is never converted to a miss. Other evaluable methods from that case remain in their own coverage and paired comparisons.",
        "- Recovery quantities use known minus current, estimated minus current, and their ratio only on identical truth-opportunity sets.",
        "- RMSE includes own-success estimates only. Missing estimates are reported as missing, not zero.",
        "- Paired OFF false-cut fractions are descriptive; this target-present matrix has no independent H0 seed, so empirical system Pfa is not evaluable.",
        "- CSI ROI quantities are diagnostics; active-support summaries mix target, clutter, and noise and are not interpreted as target retention.",
        "",
        "## Reproduction",
        "",
        "python3 scripts/phase_r/summarize_r22_v3_2_screening.py --design "
        "docs/research/phase_r/R22/config/r22_extension_experiment_design_v3_2_identity_closed_20261106.json "
        "--matrix-manifest outputs/r22_v3_2_screening_20261106/matrix/matrix_manifest.json "
        "--archive-root outputs/r22_v3_2_screening_20261106/archives "
        "--output-dir outputs/r22_v3_2_screening_20261106/analysis",
        "",
    ]
    (output_dir / "summary.md").write_text("\n".join(text), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", type=Path, default=DEFAULT_DESIGN)
    parser.add_argument("--matrix-manifest", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_MATRIX.parent.parent / "archives")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="return nonzero unless all frozen cases and method units are terminal and archived",
    )
    args = parser.parse_args(argv)
    result = analyze_snapshot(args.design, args.matrix_manifest, args.archive_root)
    write_outputs(result, args.output_dir.expanduser().resolve())
    print(json.dumps(result["metadata"], ensure_ascii=False, sort_keys=True))
    return 0 if not args.require_complete or result["metadata"]["analysis_status"] == "COMPLETE_SCREENING" else 2


if __name__ == "__main__":
    raise SystemExit(main())
