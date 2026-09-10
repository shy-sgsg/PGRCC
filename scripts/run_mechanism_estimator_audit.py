#!/usr/bin/env python3
"""Run observable M1/M2 estimators and build mechanism features."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def find_manifest(case_dir: Path) -> Path:
    paths = sorted(case_dir.rglob("csi_roi_manifest.csv"))
    if len(paths) != 1:
        raise RuntimeError(f"expected one csi_roi_manifest.csv under {case_dir}, found {paths}")
    return paths[0]


def run(command: list[str]) -> None:
    completed = subprocess.run(command, cwd=ROOT, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"estimator failed ({completed.returncode}): {' '.join(command)}\n{completed.stdout}")


def evaluation_truth(case: Path, mechanism: str) -> tuple[str, float] | None:
    """Read injected truth only to label post-hoc error, never to fit the estimator."""
    scenario_path = case / "scenario.json"
    if not scenario_path.is_file():
        return None
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    impairments = scenario.get("channel_impairments", {})
    if mechanism == "M1" and "channel_time_delay_ns" in impairments:
        return "--truth-delay-ns", float(impairments["channel_time_delay_ns"])
    if mechanism == "M2" and "per_pulse_phase_drift_deg" in impairments:
        # The measured cross spectrum is angle(F1*conj(F2)); a positive phase
        # injected into channel 2 therefore appears with a negative slope.
        return "--truth-slope-deg-per-pulse", -float(impairments["per_pulse_phase_drift_deg"])
    return None


def xml_float(case: Path, tag: str, default: float) -> float:
    xml_path = case / "stage2/config/temp_config_stage2_period_0000.xml"
    if not xml_path.is_file():
        return default
    text = ET.parse(xml_path).getroot().findtext(f".//{tag}")
    if not text:
        return default
    value = float(text)
    return value if value > 1.0e6 else value * 1.0e6 if tag == "fs" else value


def raw_input(case: Path) -> Path:
    paths = sorted((case / "stage2/data").glob("*period_0000.bin"))
    if len(paths) != 1:
        raise RuntimeError(f"expected one production raw period under {case}, found {paths}")
    return paths[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m1-case", type=Path, action="append", default=[])
    parser.add_argument("--m2-case", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bandwidth-mhz", type=float, default=50.0)
    parser.add_argument("--chirp-duration-us", type=float, default=130.0)
    parser.add_argument("--prf-hz", type=float, default=1300.0)
    parser.add_argument("--input-mode", choices=("raw", "manifest"), default="raw",
                        help="raw uses the pre-Doppler production channel samples; manifest keeps the CSI inverse-Doppler diagnostic")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    summaries: list[Path] = []
    records: list[dict[str, Any]] = []
    for mechanism, cases in (("M1", args.m1_case), ("M2", args.m2_case)):
        for index, case in enumerate(cases, 1):
            case = case.resolve()
            manifest = find_manifest(case) if args.input_mode == "manifest" else None
            case_output = output / f"{mechanism}_{index:03d}"
            case_output.mkdir(parents=True, exist_ok=True)
            if mechanism == "M1":
                command = [sys.executable, str(ROOT / "scripts/estimate_channel_delay.py"),
                           "--output-dir", str(case_output)]
                if args.input_mode == "raw":
                    command.extend([
                        "--raw-bin", str(raw_input(case)),
                        "--pulse-len", str(int(xml_float(case, "pulse_len", 11840))),
                        "--channel-count", str(int(xml_float(case, "new_protocol_channel_count", 4))),
                        "--fs-hz", str(xml_float(case, "fs", 60.0e6)),
                    ])
                else:
                    command.extend(["--manifest", str(manifest),
                                    "--bandwidth-mhz", str(args.bandwidth_mhz),
                                    "--chirp-duration-us", str(args.chirp_duration_us)])
                summary = case_output / "channel_delay_summary.json"
            else:
                command = [sys.executable, str(ROOT / "scripts/estimate_temporal_phase.py"),
                           "--output-dir", str(case_output), "--prf-hz", str(args.prf_hz)]
                if args.input_mode == "raw":
                    command.extend([
                        "--raw-bin", str(raw_input(case)),
                        "--pulse-len", str(int(xml_float(case, "pulse_len", 11840))),
                        "--channel-count", str(int(xml_float(case, "new_protocol_channel_count", 4))),
                        "--fs-hz", str(xml_float(case, "fs", 60.0e6)),
                    ])
                else:
                    command.extend(["--manifest", str(manifest)])
                summary = case_output / "temporal_phase_summary.json"
            truth = evaluation_truth(case, mechanism)
            if truth is not None:
                command.extend([truth[0], str(truth[1])])
            run(command)
            summaries.append(summary)
            records.append({"mechanism": mechanism, "case": str(case),
                            "manifest": str(manifest), "summary": str(summary),
                            "input_mode": args.input_mode,
                            "truth_passed_for_evaluation_only": truth is not None,
                            "m2_cross_spectrum_truth_sign": "negative_of_channel_2_injected_phase" if mechanism == "M2" else None})
    feature_table = output / "mechanism_feature_table.csv"
    if summaries:
        command = [sys.executable, str(ROOT / "scripts/build_mechanism_feature_table.py")]
        for summary in summaries:
            command.extend(["--summary", str(summary)])
        command.extend(["--output", str(feature_table)])
        run(command)
    audit = {
        "schema_version": 1,
        "ai_training": False,
        "truth_used_in_estimators": False,
        "records": records,
        "feature_table": str(feature_table) if summaries else None,
    }
    (output / "mechanism_estimator_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "case_count": len(records),
                      "feature_table": str(feature_table) if summaries else None}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
