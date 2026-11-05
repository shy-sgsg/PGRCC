#!/usr/bin/env python3
"""Read-only V2 archive integrity and V3 method-level classifier dry-run."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import sys
from zipfile import ZipFile
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase_r.r22_v3_case_evaluator import classify_recorded_v2_archive  # noqa: E402
from scripts.phase_r.r22_v3_experiment_matrix import (  # noqa: E402
    DEFAULT_DESIGN as V3_DESIGN,
    expected_method_units, load_v3_design,
)
from scripts.phase_r import r22_experiment_matrix as matrix  # noqa: E402

V2_DESIGN = ROOT / "docs/research/phase_r/R22/config/r22_extension_experiment_design_v2_range_roi.json"
V2_MATRIX = ROOT / "outputs/r22_full_v2_compact/matrices/screening_diskquota_retry/matrix_manifest.json"
V2_FAILURE_ROOT = ROOT / "outputs/r22_full_v2_compact/failures/screening"
DEFAULT_OUTPUT = ROOT / "outputs/r22_v3_offline_dry_run_20261105"
V2_FAILURE_ARCHIVE_SHA256 = "be76237104d6c6f11f1681de556e49ecad5e694658e8d5c94ce3e7c05850890b"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _archive_for_record(record: Mapping[str, Any]) -> tuple[Path, str]:
    if record.get("status") == "PASSED":
        archive = record.get("compact_archive")
        if not isinstance(archive, Mapping):
            raise ValueError(f"V2 passed case lacks compact archive record: {record.get('case_id')}")
        path = Path(str(archive.get("archive_path", ""))).expanduser()
        expected = str(archive.get("archive_sha256", ""))
        if not expected:
            raise ValueError(f"V2 passed case lacks archive SHA-256: {record.get('case_id')}")
        return path, expected
    if record.get("status") == "GATE_FAILED":
        attempt = int(record.get("attempt", 0))
        name = (
            f"{record['case_id']}__GATE_FAILED_attempt_{attempt:03d}.zip"
        )
        return V2_FAILURE_ROOT / name, V2_FAILURE_ARCHIVE_SHA256
    raise ValueError(f"unsupported V2 terminal status: {record.get('status')}")


def audit_archives(
    matrix_path: str | Path = V2_MATRIX,
    *,
    design_path: str | Path = V3_DESIGN,
) -> dict[str, Any]:
    matrix_manifest = json.loads(Path(matrix_path).read_text(encoding="utf-8"))
    if matrix_manifest.get("design_id") != "R22_EXTENSION_20261028_V2_RANGE_ROI":
        raise ValueError("input matrix is not the frozen V2 range/ROI manifest")
    if matrix_manifest.get("status") != "GATE_FAILED":
        raise ValueError("V2 historical matrix terminal status changed; review before dry-run")
    if int(matrix_manifest.get("planned_case_count", -1)) != 288:
        raise ValueError("V2 planned case count is not 288")
    v2_design = matrix.load_design(V2_DESIGN)
    if matrix_manifest.get("design_sha256") != matrix._design_digest(v2_design):
        raise ValueError("V2 matrix design SHA differs from the frozen V2 design file")

    design = load_v3_design(design_path)
    terminal = [row for row in matrix_manifest.get("cases", [])
                if row.get("status") in {"PASSED", "GATE_FAILED"}]
    case_ids = [str(row.get("case_id", "")) for row in terminal]
    if len(terminal) != 18 or len(case_ids) != len(set(case_ids)):
        raise ValueError("expected 18 unique terminal V2 cases")
    if sum(row.get("status") == "PASSED" for row in terminal) != 17:
        raise ValueError("expected exactly 17 historical PASSED cases")
    if sum(row.get("status") == "GATE_FAILED" for row in terminal) != 1:
        raise ValueError("expected exactly one historical GATE_FAILED case")

    results: list[dict[str, Any]] = []
    for record in terminal:
        archive_path, expected_sha = _archive_for_record(record)
        archive_path = archive_path.resolve()
        if not archive_path.is_file():
            raise FileNotFoundError(f"historical V2 archive is missing: {archive_path}")
        actual_sha = _sha256(archive_path)
        if actual_sha != expected_sha:
            raise ValueError(
                f"V2 archive SHA-256 mismatch for {record['case_id']}: {actual_sha} != {expected_sha}"
            )

        with ZipFile(archive_path) as stream:
            run_manifest = json.loads(stream.read("r22_run_manifest.json"))
        delay = float(run_manifest["design"]["delay_error_ns"])
        units = expected_method_units(design, {"delay_ns": delay})
        classified = classify_recorded_v2_archive(
            archive_path, units, original_matrix_status=str(record["status"]), min_support=8,
        )
        if classified.get("scenario_status") != "CASE_RECORDED_GATE_PASS_NOT_REPLAYED":
            raise ValueError(
                f"V2 case did not retain historical-only status: {record['case_id']} "
                f"{classified.get('scenario_status')} {classified.get('scenario_reason')}"
            )
        if classified.get("v3_execution") is not False:
            raise ValueError("historical V2 archive was incorrectly marked as V3 execution")
        if classified.get("recorded_on_task_rows") != classified.get("applicable_count"):
            raise ValueError(f"source ON task rows changed denominator in {record['case_id']}")
        role_counts = classified.get("recorded_task_role_counts", {})
        applicable_count = classified["applicable_count"]
        if any(role_counts.get(role, 0) != applicable_count for role in ("OFF", "ON", "TO")):
            raise ValueError(f"source OFF/ON/TO task rows are incomplete in {record['case_id']}")
        expected_all_rows = 5 if float(classified["recorded_delay_error_ns"]) == 0.0 else 0
        if role_counts.get("ALL", 0) != expected_all_rows:
            raise ValueError(f"source zero-delay NOT_APPLICABLE rows differ in {record['case_id']}")
        if classified["recorded_task_rows"] != 3 * applicable_count + expected_all_rows:
            raise ValueError(f"source task-row total is incomplete in {record['case_id']}")

        expected_state = (
            "CASE_RECORDED_GATE_PASS_NOT_REPLAYED"
            if record["status"] in {"PASSED", "GATE_FAILED"} else None
        )
        if classified["scenario_status"] != expected_state:
            raise ValueError(f"unexpected recorded state in {record['case_id']}")
        if record["status"] == "PASSED":
            if classified["evaluable_count"] != classified["applicable_count"]:
                raise ValueError(f"historical PASSED case lost a method row: {record['case_id']}")
            if classified["reference_gap_count"] != 0 or classified["unknown_count"] != 0:
                raise ValueError(f"historical PASSED case acquired a gap: {record['case_id']}")
        else:
            gaps = [item for item in classified["method_units"]
                    if item["status"] == "REFERENCE_NOT_EVALUABLE"]
            if (len(gaps) != 1 or gaps[0]["method_id"] != "C4"
                    or gaps[0]["mode"] != "Mode-B"
                    or classified["evaluable_count"] != 17
                    or classified["unknown_count"] != 0):
                raise ValueError("historical failure no longer isolates only C4/Mode-B")

        results.append({
            "case_id": record["case_id"],
            "original_v2_status": record["status"],
            "original_attempt": record["attempt"],
            "original_condition_id": record.get("condition_id"),
            "archive_path": str(archive_path),
            "archive_sha256": actual_sha,
            "archive_sha256_source": (
                "V2_matrix_manifest" if record["status"] == "PASSED"
                else "fixed_review_provenance_hash"
            ),
            "archive_crc_status": classified["archive_status"],
            "scenario_status": classified["scenario_status"],
            "v3_execution": False,
            "applicable_method_units": classified["applicable_count"],
            "evaluable_method_units": classified["evaluable_count"],
            "reference_gap_method_units": classified["reference_gap_count"],
            "unknown_method_units": classified["unknown_count"],
            "recorded_task_rows": classified["recorded_task_rows"],
            "recorded_on_task_rows": classified["recorded_on_task_rows"],
            "recorded_task_role_counts": classified["recorded_task_role_counts"],
            "recorded_delay_error_ns": classified["recorded_delay_error_ns"],
            "recorded_task_status_counts": classified["recorded_task_status_counts"],
            "recorded_aggregate_gates": classified["recorded_aggregate_gates"],
            "method_units": classified["method_units"],
        })

    total_applicable = sum(row["applicable_method_units"] for row in results)
    total_evaluable = sum(row["evaluable_method_units"] for row in results)
    total_gaps = sum(row["reference_gap_method_units"] for row in results)
    if (total_applicable, total_evaluable, total_gaps) != (270, 269, 1):
        raise ValueError(
            f"V2 dry-run totals differ from frozen evidence: {total_applicable}/{total_evaluable}/{total_gaps}"
        )

    return {
        "schema_version": 1,
        "audit_type": "read_only_historical_v2_archive_classification",
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "v2_matrix": str(Path(matrix_path).resolve()),
        "v2_design_id": matrix_manifest["design_id"],
        "v2_matrix_status": matrix_manifest["status"],
        "v3_design_id": design["design_id"],
        "v3_execution": False,
        "historical_archives": results,
        "summary": {
            "planned_v2_cases": 288,
            "terminal_historical_cases": len(results),
            "historical_passed_cases": 17,
            "historical_gate_failed_cases": 1,
            "unstarted_v2_cases": 270,
            "applicable_on_method_units": total_applicable,
            "evaluable_on_method_units": total_evaluable,
            "reference_not_evaluable_method_units": total_gaps,
            "unknown_method_units": sum(row["unknown_method_units"] for row in results),
            "only_reference_gap": {"method_id": "C4", "mode": "Mode-B"},
            "v2_aggregate_valid_cut_and_unique_union_gate": "INCOMPLETE",
            "performance_ranking": "NOT_PERFORMED",
            "screening_significance_claim": "NOT_MADE",
        },
    }


def _write_case_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fields = (
        "case_id", "original_v2_status", "original_attempt", "original_condition_id",
        "archive_sha256", "archive_crc_status", "scenario_status", "v3_execution",
        "applicable_method_units", "evaluable_method_units",
        "reference_gap_method_units", "unknown_method_units",
        "recorded_task_rows", "recorded_on_task_rows",
    )
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_method_csv(path: Path, cases: Sequence[Mapping[str, Any]]) -> None:
    fields = (
        "case_id", "original_v2_status", "method_id", "mode", "applicable",
        "status", "reason", "support_count", "min_support",
    )
    rows = []
    for case in cases:
        for unit in case["method_units"]:
            gap = unit.get("reference_gap") or {}
            rows.append({
                "case_id": case["case_id"],
                "original_v2_status": case["original_v2_status"],
                "method_id": unit["method_id"], "mode": unit["mode"],
                "applicable": unit["applicable"], "status": unit["status"],
                "reason": unit["reason"], "support_count": gap.get("support_count"),
                "min_support": gap.get("min_support"),
            })
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_report(root: Path, report: Mapping[str, Any]) -> None:
    summary = report["summary"]
    lines = [
        "# R22 V2 Archive Read-only V3 Dry-run",
        "",
        "此报告只对既有 V2 ZIP 做身份/CRC 检查和离线状态分类，不构成 V3 执行，也不重算性能排名。",
        "",
        f"- V2 matrix: `{report['v2_matrix']}`",
        f"- V2 status: `{report['v2_matrix_status']}`",
        f"- V3 design: `{report['v3_design_id']}`",
        f"- Historical V2 cases: {summary['terminal_historical_cases']}/288",
        f"- V2 method units: {summary['evaluable_on_method_units']}/{summary['applicable_on_method_units']} evaluable; {summary['reference_not_evaluable_method_units']} local reference gap",
        f"- V2 aggregate valid-CUT/unique-hit gate: `{summary['v2_aggregate_valid_cut_and_unique_union_gate']}`",
        "- V3 execution: `false`",
        "- Performance ranking/significance: not performed/not claimed",
        "",
        "## Per-case archive checks",
        "",
        "| Case | V2 state | Archive SHA/CRC | Recorded-only classification | Units Evaluable/Applicable | Gap |",
        "|---|---|---|---|---:|---:|",
    ]
    for row in report["historical_archives"]:
        lines.append(
            f"| `{row['case_id']}` | `{row['original_v2_status']}` | `{row['archive_sha256'][:12]}… / {row['archive_crc_status']}` "
            f"| `{row['scenario_status']}` | {row['evaluable_method_units']}/{row['applicable_method_units']} "
            f"| {row['reference_gap_method_units']} |"
        )
    (root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix-manifest", type=Path, default=V2_MATRIX)
    parser.add_argument("--design", type=Path, default=V3_DESIGN)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        output_root = args.output_root.expanduser().resolve()
        if output_root.exists() and any(output_root.iterdir()):
            raise FileExistsError(f"refusing to overwrite offline dry-run output: {output_root}")
        output_root.mkdir(parents=True, exist_ok=True)
        result = audit_archives(args.matrix_manifest, design_path=args.design)
        (output_root / "manifest.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _write_case_csv(output_root / "cases.csv", result["historical_archives"])
        _write_method_csv(output_root / "method_units.csv", result["historical_archives"])
        _write_report(output_root, result)
    except Exception as exc:
        print(f"[audit_r22_v2_archives_for_v3][ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "READ_ONLY_DRY_RUN_COMPLETE", **result["summary"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
