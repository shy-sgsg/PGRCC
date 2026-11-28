#!/usr/bin/env python3
"""Recompute core P0 science outputs from the compact ZIP only and compare parity."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COMPACT = ROOT / (
    "docs/research/phase_r/R22/evidence/v4_final_freeze_readiness/"
    "p0_target_present_plus4ns_compact.zip"
)
DEFAULT_REFERENCE = ROOT / "docs/research/phase_r/R22/evidence/v4_confirmatory_freeze_readiness"
DEFAULT_OUTPUT = ROOT / "docs/research/phase_r/R22/evidence/v4_final_freeze_readiness"
CORE_TABLES = (
    "p0_method_summary.csv",
    "p0_recomputed_parity.csv",
    "p0_pairwise_common_position_rmse.csv",
    "p0_coverage_recovery.csv",
    "p0_per_period_metrics.csv",
    "p0_per_target_metrics.csv",
    "p0_track_summary.csv",
    "p0_track_payload_lineage_audit.csv",
    "p0_target_track_lineage.csv",
    "p0_cfar_geometry_summary.csv",
    "p0_target_response_summary.csv",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _csv_row_count(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return max(0, sum(1 for _ in csv.reader(stream)) - 1)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["table", "parity", "compact_sha256", "reference_sha256", "compact_rows", "reference_rows"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compact-archive", type=Path, default=DEFAULT_COMPACT)
    parser.add_argument("--reference-dir", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--temp-dir", type=Path)
    args = parser.parse_args()
    compact_path = args.compact_archive.expanduser().resolve()
    reference_dir = args.reference_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not compact_path.is_file():
        parser.error(f"compact ZIP is missing: {compact_path}")
    sys.path.insert(0, str(ROOT))
    from scripts.phase_r.r22_v4_p0_compact_archive import verify_p0_compact_archive
    from scripts.phase_r import analyze_r22_v4_p0_freeze_readiness as analysis

    start = time.monotonic()
    archive_validation = verify_p0_compact_archive(compact_path)
    with zipfile.ZipFile(compact_path, "r") as archive:
        compact_manifest = json.loads(archive.read("P0_COMPACT_MANIFEST.json"))
        case_root = str(compact_manifest["case_root_member_prefix"])
        case_manifests = [name for name in archive.namelist() if name.endswith("/case_manifest.json")]
        if case_manifests != [f"{case_root}/case_manifest.json"]:
            raise ValueError("compact member inventory and case-root manifest disagree")
        with tempfile.TemporaryDirectory(
            prefix="r22-v4-compact-reanalysis-",
            dir=str(args.temp_dir.expanduser().resolve()) if args.temp_dir else None,
        ) as temporary:
            scratch = Path(temporary)
            task_result = analysis.analyze_task_metrics(archive, case_root, scratch)
            associations = analysis.zip_csv(archive, "evaluation/target_associations.csv")
            track_audit = analysis.audit_track_payloads(archive, associations, scratch)
            cfar = analysis.analyze_cfar_geometry(archive, scratch)
            response = analysis.analyze_target_response(archive, case_root, scratch)
            _ = task_result, track_audit, cfar, response
            parity_rows: list[dict[str, Any]] = []
            for table in CORE_TABLES:
                compact_table = scratch / table
                reference_table = reference_dir / table
                if not compact_table.is_file():
                    parity_rows.append({
                        "table": table, "parity": "MISSING_COMPACT_OUTPUT",
                        "compact_sha256": "", "reference_sha256": _sha256(reference_table)
                        if reference_table.is_file() else "",
                        "compact_rows": "", "reference_rows": _csv_row_count(reference_table)
                        if reference_table.is_file() else "",
                    })
                elif not reference_table.is_file():
                    parity_rows.append({
                        "table": table, "parity": "MISSING_REFERENCE_OUTPUT",
                        "compact_sha256": _sha256(compact_table), "reference_sha256": "",
                        "compact_rows": _csv_row_count(compact_table), "reference_rows": "",
                    })
                else:
                    compact_sha, reference_sha = _sha256(compact_table), _sha256(reference_table)
                    parity_rows.append({
                        "table": table,
                        "parity": "PASS" if compact_sha == reference_sha else "MISMATCH",
                        "compact_sha256": compact_sha,
                        "reference_sha256": reference_sha,
                        "compact_rows": _csv_row_count(compact_table),
                        "reference_rows": _csv_row_count(reference_table),
                    })
            output_dir.mkdir(parents=True, exist_ok=True)
            parity_path = output_dir / "compact_archive_parity.csv"
            _write_csv(parity_path, parity_rows)
            all_pass = len(parity_rows) == len(CORE_TABLES) and all(
                row["parity"] == "PASS" for row in parity_rows
            )
            validation = {
                **archive_validation,
                "core_reanalysis_table_count": len(CORE_TABLES),
                "core_reanalysis_parity": "PASS" if all_pass else "FAIL",
                "core_tables": [row["table"] for row in parity_rows],
                "parity_csv": parity_path.relative_to(ROOT).as_posix()
                if parity_path.is_relative_to(ROOT) else str(parity_path),
                "parity_rows": parity_rows,
                "reanalysis_input_policy": "the compact ZIP was the only P0 experiment-data input",
                "full_source_zip_opened_by_reanalysis": False,
                "stage2_production_cuda_started": False,
                "reanalysis_wall_seconds": time.monotonic() - start,
            }
            validation_path = output_dir / "compact_archive_validation.json"
            validation_path.write_text(
                json.dumps(validation, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            print(json.dumps({
                "status": "PASS" if all_pass else "FAIL",
                "archive_sha256": archive_validation["archive_sha256"],
                "core_tables": len(CORE_TABLES),
                "parity": {row["table"]: row["parity"] for row in parity_rows},
                "validation_path": str(validation_path),
                "elapsed_seconds": validation["reanalysis_wall_seconds"],
            }, ensure_ascii=False, sort_keys=True))
            return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
