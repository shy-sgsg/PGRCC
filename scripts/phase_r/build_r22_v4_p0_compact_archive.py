#!/usr/bin/env python3
"""Build a stand-alone evidence ZIP from the retained R22-V4 P0 archive."""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / (
    "outputs/r22_v4_p0_seed1937003_20261123/archives/engineering/"
    "engineering__seed_1937003__vr_2d5__snr_20__delay_4ns__targets_20__periods_10__attempt_001.zip"
)
EVIDENCE_DIR = ROOT / "docs/research/phase_r/R22/evidence/v4_final_freeze_readiness"
DEFAULT_DESTINATION = ROOT / (
    "docs/research/phase_r/R22/evidence/v4_final_freeze_readiness/"
    "p0_target_present_plus4ns_compact.zip"
)
EXPECTED_SOURCE_SHA256 = "0974e1dfd1467cdd2bdced856e8c673d40c8966a53ab05261d0c5db2d99c4b02"
EXPECTED_SOURCE_MANIFEST_SHA256 = "4f96e4f1fb1cf7db1ebe2d1d07c79a8537b47fccac1efa3f4fbd7c7a0e4036a0"
ADDITIONAL_REANALYSIS_MEMBERS = (
    "evaluation/doppler_opportunity_audit.csv",
    "evaluation/h0_false_alarm_metrics.csv",
    "evaluation/h0_task_metrics.csv",
    "evaluation/parameter_metrics.csv",
    "evaluation/target_associations.csv",
    "evaluation/target_level_metrics.csv",
    "evaluation/task_metrics.csv",
    "evaluation/track_level_metrics.csv",
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--evidence-dir", type=Path, default=EVIDENCE_DIR)
    parser.add_argument("--allow-noncanonical-source", action="store_true")
    args = parser.parse_args()
    source = args.source.expanduser().resolve()
    destination = args.destination.expanduser().resolve()
    evidence = args.evidence_dir.expanduser().resolve()
    if not args.allow_noncanonical_source and source != DEFAULT_SOURCE.resolve():
        parser.error("source override requires --allow-noncanonical-source")
    if source == destination:
        parser.error("compact destination must differ from the retained source ZIP")

    sys.path.insert(0, str(ROOT))
    from scripts.phase_r.r22_v4_p0_compact_archive import build_p0_compact_archive

    result = build_p0_compact_archive(
        source,
        destination,
        expected_source_sha256=EXPECTED_SOURCE_SHA256,
        expected_source_manifest_sha256=EXPECTED_SOURCE_MANIFEST_SHA256,
        additional_reanalysis_members=ADDITIONAL_REANALYSIS_MEMBERS,
    )
    compact_manifest_name = "P0_COMPACT_MANIFEST.json"
    with zipfile.ZipFile(destination, "r") as archive:
        compact_manifest_bytes = archive.read(compact_manifest_name)
    manifest_path = evidence / "compact_archive_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(compact_manifest_bytes)
    record = {
        **result,
        "archive_path": destination.relative_to(ROOT).as_posix()
        if destination.is_relative_to(ROOT) else str(destination),
        "source_path_recorded_in_compact": False,
        "source_path_recorded_in_evidence": source.relative_to(ROOT).as_posix()
        if source.is_relative_to(ROOT) else str(source),
        "compact_manifest_evidence_path": manifest_path.relative_to(ROOT).as_posix()
        if manifest_path.is_relative_to(ROOT) else str(manifest_path),
        "stage2_cuda_production_started": False,
    }
    _write_json(evidence / "compact_archive_generation.json", record)
    print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
