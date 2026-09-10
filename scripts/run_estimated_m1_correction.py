#!/usr/bin/env python3
"""Replay an M1 case using delays estimated from measured F1/F2 only.

The estimator summary is the only source of the correction value.  This
runner deliberately does not read the injected scenario delay; it applies an
independent zero-padded FFT phase-ramp correction to raw channel 2 and reruns
the production GMTI binary.  It keeps only compact JSON/CSV summaries after a
successful replay.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_mechanism_aware_oracle import (  # noqa: E402
    finite,
    inverse_channel_impairment_frequency_domain,
    run_logged,
    single_manifest,
    summary_metrics,
    patch_xml,
)


def read_estimates(path: Path, selected: set[str] | None = None) -> dict[str, float]:
    data = json.loads(path.resolve().read_text(encoding="utf-8"))
    output: dict[str, float] = {}
    for method, values in data.get("methods", {}).items():
        if selected and method not in selected:
            continue
        value = finite(values.get("delta_tau_ns"))
        if math.isfinite(value):
            output[method] = value
    if not output:
        raise SystemExit(f"estimator summary has no finite delay estimates: {path}")
    return output


def write_summary(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "method", "estimated_delay_ns", "status", "gmticore_exit_code",
        "current_cancellation_db", "estimated_cancellation_db",
        "headroom_gain_db_vs_current", "current_coherence", "estimated_coherence",
        "current_phase_rmse_rad", "estimated_phase_rmse_rad", "manifest",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True,
                        help="production M1 variant directory with stage2 raw/config")
    parser.add_argument("--estimator-summary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--method", dest="methods", action="append",
                        choices=("D1_ordinary_LS", "D2_weighted_LS", "D3_Huber_weighted_LS"),
                        help="只重放指定估计方法；可重复传入，默认全部")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    args = parser.parse_args()

    case = args.case.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    xml_path = case / "stage2/config/temp_config_stage2_period_0000.xml"
    if not xml_path.is_file():
        raise SystemExit(f"missing production XML: {xml_path}")
    input_path = Path(ET.parse(xml_path).getroot().findtext(".//GMTI_data_new") or "")
    if not input_path.is_file():
        raise SystemExit(f"missing production raw input: {input_path}")
    estimates = read_estimates(args.estimator_summary, set(args.methods or []))
    source_manifest = single_manifest(case)
    current = summary_metrics(source_manifest)
    rows: list[dict[str, Any]] = []
    for method, delay_ns in estimates.items():
        method_dir = output / method
        result_dir = method_dir / "stage2/algorithm_result/period_0000"
        corrected_path = method_dir / "stage2/data/estimated_corrected_period_0000.bin"
        transform = inverse_channel_impairment_frequency_domain(
            input_path, corrected_path, xml_path, delay_ns, 0.0)
        oracle_xml = method_dir / "stage2/config/estimated_config.xml"
        patch_xml(xml_path, corrected_path, result_dir, oracle_xml)
        log_path = method_dir / "gmticore.log"
        return_code = run_logged(
            [str(args.build_dir.resolve() / "GMTI_core"), str(oracle_xml),
             "--runtime-mode=debug", "--runtime-diagnostics=on"], log_path)
        row: dict[str, Any] = {
            "method": method,
            "estimated_delay_ns": delay_ns,
            "status": "gmticore_failed" if return_code else "pending",
            "gmticore_exit_code": return_code,
            "current_cancellation_db": current["cancellation_db"],
            "manifest": None,
            "transform": transform,
        }
        if return_code == 0:
            manifest = single_manifest(method_dir)
            estimated = summary_metrics(manifest)
            row.update({
                "status": "pass",
                "estimated_cancellation_db": estimated["cancellation_db"],
                "headroom_gain_db_vs_current": estimated["cancellation_db"] - current["cancellation_db"],
                "current_coherence": current["coherence"],
                "estimated_coherence": estimated["coherence"],
                "current_phase_rmse_rad": current["phase_rmse_rad"],
                "estimated_phase_rmse_rad": estimated["phase_rmse_rad"],
                "manifest": str(manifest.resolve()),
            })
        rows.append(row)
        # The corrected raw input is reproducible from the source raw and the
        # compact estimator summary; remove it after the production replay.
        if corrected_path.is_file():
            corrected_path.unlink()

    compact_rows = [{key: value for key, value in row.items() if key != "transform"}
                    for row in rows]
    write_summary(output / "estimated_m1_correction_summary.csv", compact_rows)
    (output / "estimated_m1_correction_manifest.json").write_text(
        json.dumps({
            "schema_version": 1,
            "ai_training": False,
            "truth_used_in_estimator": False,
            "case": str(case),
            "source_estimator_summary": str(args.estimator_summary.resolve()),
            "correction": "zero-padded FFT frequency-domain phase ramp",
            "rows": compact_rows,
        }, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "case": str(case),
                      "method_count": len(rows),
                      "failed": sum(row["status"] != "pass" for row in rows)},
                     ensure_ascii=False))
    return 0 if all(row["status"] == "pass" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
