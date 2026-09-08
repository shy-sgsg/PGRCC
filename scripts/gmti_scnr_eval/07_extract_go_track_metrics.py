#!/usr/bin/env python3
"""汇总一个正式三周期 GO-CFAR seed 的目标级与航迹级审计指标。"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from scnr_eval_lib import ensure_dir, read_csv, write_csv


SCRIPT_DIR = Path(__file__).resolve().parent


def integer(row: dict[str, str], key: str, default: int = -1) -> int:
    try:
        return int(float(row.get(key, "")))
    except (TypeError, ValueError):
        return default


def parse_result_id(row: dict[str, str], key: str = "result_id", default: int = -1) -> int:
    """Parse both TrackManager's integer frame ID and GMTI02-style IDs."""
    value = str(row.get(key, "")).strip()
    try:
        return int(float(value))
    except (TypeError, ValueError):
        match = re.fullmatch(r"GMTI0*([0-9]+)", value, flags=re.IGNORECASE)
        return int(match.group(1)) if match else default


def number(row: dict[str, str], key: str) -> float:
    try:
        return float(row.get(key, ""))
    except (TypeError, ValueError):
        return float("nan")


def paired_scnr_map(paths: list[Path]) -> dict[tuple[float, float], dict[str, str]]:
    result: dict[tuple[float, float], dict[str, str]] = {}
    for path in paths:
        for row in read_csv(path):
            angle = number(row, "truth_angle_deg")
            injection = number(row, "configured_target_snr_db")
            if not (angle == angle and injection == injection):
                continue
            key = (round(angle, 8), round(injection, 8))
            if key in result:
                raise RuntimeError(f"配对 SCNR 标定重复 angle/SCNR：{angle:g}°/{injection:g} dB")
            result[key] = row
    return result


def run_extractor(stage2: Path, algorithm: Path, result_id: int, period_id: int,
                  diagnostic_beams: str, production_xml: Path, destination: Path) -> None:
    command = [
        sys.executable, str(SCRIPT_DIR / "05_extract_go_diagnostics.py"),
        "--stage2-dir", str(stage2), "--algorithm-dir", str(algorithm),
        "--output-dir", str(destination), "--diagnostic-result-id", str(result_id),
        "--period-id", str(period_id), "--detection-csv",
        str(algorithm / f"detection_results_GMTI{result_id:02d}.csv"),
        "--production-xml", str(production_xml),
    ]
    if diagnostic_beams:
        command.extend(["--diagnostic-beams", diagnostic_beams])
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, check=False)
    (destination / "extract.log").write_text(result.stdout, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"周期 {period_id} GO 诊断提取失败；见 {destination / 'extract.log'}")


def remove_cfar_binaries(result_dir: Path, diagnostic_beams: str) -> list[dict[str, object]]:
    """在全 seed GO/TrackManager 指标写入后回收已经转换为 CSV 的 F32 图。"""
    beams = sorted({int(token.strip()) for token in diagnostic_beams.split(",") if token.strip()})
    removed: list[dict[str, object]] = []
    debug_dirs = [result_dir / "debug", *sorted(result_dir.glob("period_*/debug"))]
    for debug_dir in debug_dirs:
        if not debug_dir.is_dir():
            continue
        for beam in beams:
            for path in sorted(debug_dir.glob(f"cfar_*_beam{beam:03d}_*.f32")):
                if path.is_file():
                    size = path.stat().st_size
                    path.unlink()
                    removed.append({"path": str(path), "bytes": size})
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage2-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--diagnostic-beams", default="",
                        help="与 06_run_go_track_case.py 一致的逗号分隔波位")
    parser.add_argument("--paired-output-scnr-summary", type=Path, action="append", default=[],
                        help="可重复给出已合并的正式配对标定 summary；向逐目标诊断附加 SCNR_det/phase 映射")
    parser.add_argument("--cleanup-cfar-binaries", action="store_true",
                        help="仅在本 seed 全部 GO/TrackManager 指标写入成功后回收已转换的 F32 图")
    args = parser.parse_args()
    stage2 = args.stage2_dir.resolve()
    out = ensure_dir(args.output_dir.resolve())
    manifest_path = stage2 / "go_track_run_manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"缺少三周期运行清单：{manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    result_dir = Path(manifest.get("result_dir", stage2 / "algorithm_result" / "pipe_3period"))
    if not result_dir.is_dir():
        raise SystemExit(f"缺少 PIPE 结果目录：{result_dir}")
    production_xml = Path(manifest.get("pipe_xml", ""))
    if not production_xml.is_file():
        raise SystemExit(f"缺少实际 PIPE XML：{production_xml}")
    period_start = int(manifest["period_start"])
    period_count = int(manifest["period_count"])
    if period_count != 3:
        raise SystemExit("航迹级汇总只接受完整连续三周期运行")
    diagnostic_beams = args.diagnostic_beams.strip() or str(manifest.get("diagnostic_beams", ""))
    calibration = paired_scnr_map([path.resolve() for path in args.paired_output_scnr_summary])

    target_rows: list[dict] = []
    for index in range(period_count):
        result_id = index + 1
        period_id = period_start + index
        destination = ensure_dir(out / "per_period" / f"period_{period_id:04d}")
        run_extractor(stage2, result_dir, result_id, period_id, diagnostic_beams, production_xml, destination)
        rows = read_csv(destination / "target_diagnostics.csv")
        for row in rows:
            row["result_id"] = str(result_id)
            row["source_period_metrics_dir"] = str(destination)
        target_rows.extend(rows)
    for row in target_rows:
        key = (round(number(row, "truth_angle_deg"), 8), round(number(row, "configured_target_snr_db"), 8))
        calibrated = calibration.get(key)
        if calibrated is None:
            row.update({
                "scnr_det_out_db_calibrated": "", "scnr_phase_ch1_db_calibrated": "",
                "scnr_phase_ch2_db_calibrated": "", "scnr_phase_eff_db_calibrated": "",
                "scnr_calibration_sample_count": "", "scnr_calibration_source": "unavailable",
            })
        else:
            row.update({
                "scnr_det_out_db_calibrated": calibrated.get("output_scnr_det_out_db_median", ""),
                "scnr_phase_ch1_db_calibrated": calibrated.get("output_scnr_phase_ch1_db_median", ""),
                "scnr_phase_ch2_db_calibrated": calibrated.get("output_scnr_phase_ch2_db_median", ""),
                "scnr_phase_eff_db_calibrated": calibrated.get("output_scnr_phase_eff_db_median", ""),
                "scnr_calibration_sample_count": calibrated.get("sample_count", ""),
                "scnr_calibration_source": "paired_S-only_C+N_median_same_angle_input_scnr",
            })
    write_csv(out / "target_diagnostics_all_periods.csv", target_rows)

    # TrackManager's matched_det_index is an index into the current GMTI
    # result packet, not an arbitrary CSV identifier.  The detection CSV is
    # emitted from that same packet order.  Verify the one-to-one contract
    # explicitly before using it for target/track attribution; never silently
    # join matched_det_index to a differently ordered det_id field.
    detection_target_by_key: dict[tuple[int, int], str] = {}
    detection_binding_rows: list[dict[str, object]] = []
    track_debug_paths = [Path(path).parent for path in manifest.get("track_output_payloads", [])]
    if not track_debug_paths:
        track_debug_paths = list((result_dir / "track_debug").rglob("track_output_payloads.csv"))
        track_debug_paths = [path.parent for path in track_debug_paths]
    if len(track_debug_paths) != 1:
        raise RuntimeError(f"应恰有一个 TrackManager 审计目录，实际为 {track_debug_paths}")
    track_debug = track_debug_paths[0]
    track_detections = read_csv(track_debug / "track_detections.csv")
    for index in range(period_count):
        result_id = index + 1
        snapshot = result_dir / f"detection_results_GMTI{result_id:02d}.csv"
        snapshot_rows = read_csv(snapshot)
        frame_track_rows = [row for row in track_detections if parse_result_id(row) == result_id]
        frame_track_rows.sort(key=lambda row: integer(row, "det_index"))
        expected_indexes = list(range(len(snapshot_rows)))
        actual_indexes = [integer(row, "det_index") for row in frame_track_rows]
        if len(frame_track_rows) != len(snapshot_rows) or actual_indexes != expected_indexes:
            raise RuntimeError(
                f"结果 {result_id} 的 TrackManager 输入与 detection CSV 不再是一一对应："
                f"track={len(frame_track_rows)}, csv={len(snapshot_rows)}, indexes={actual_indexes}")
        for det_index, row in enumerate(snapshot_rows):
            det_id = integer(row, "det_id")
            if det_id != det_index:
                raise RuntimeError(
                    f"结果 {result_id} 的 detection_results.csv det_id={det_id} 不等于封包顺序 {det_index}；"
                    "拒绝把 TrackManager 内部索引归因给目标")
            target_id = row.get("target_id", "")
            if target_id:
                detection_target_by_key[(result_id, det_index)] = target_id
            detection_binding_rows.append({
                "result_id": result_id, "matched_det_index": det_index,
                "detection_csv_det_id": det_id, "target_id": target_id,
                "binding": "TrackManager_current_packet_order_equals_detection_csv_row_order",
            })
    write_csv(out / "track_detection_index_binding.csv", detection_binding_rows)

    states = read_csv(track_debug / "track_states.csv")
    payloads = read_csv(track_debug / "track_output_payloads.csv")
    confirmed_matched = {
        (parse_result_id(row), integer(row, "matched_det_index"))
        for row in states
        if row.get("state") == "Confirmed" and integer(row, "matched_this_frame") == 1
        and integer(row, "matched_det_index") >= 0
    }
    protocol_target_keys: set[tuple[int, int]] = set()
    rejected_payloads: list[dict] = []
    for row in payloads:
        key = (parse_result_id(row), integer(row, "matched_det_index"))
        valid_source = row.get("resolved_source") == "measurement" and \
            row.get("position_source") == "matched_detection"
        if valid_source and key in confirmed_matched:
            protocol_target_keys.add(key)
        else:
            rejected_payloads.append({**row, "audit_reason": "not_confirmed_current_measurement"})
    write_csv(out / "track_payload_rejected_or_nonmeasurement.csv", rejected_payloads)

    per_target: dict[str, dict[str, object]] = {}
    for row in target_rows:
        target_id = row.get("target_id", "")
        if not target_id:
            continue
        summary = per_target.setdefault(target_id, {
            "case_id": row.get("case_id", ""), "seed": row.get("seed", ""),
            "target_id": target_id, "truth_angle_deg": row.get("truth_angle_deg", ""),
            "configured_target_snr_db": row.get("configured_target_snr_db", ""),
            "frames": {},
        })
        frame_id = parse_result_id(row)
        summary["frames"][frame_id] = integer(row, "final_output", 0)

    sequence_rows: list[dict] = []
    for target_id, summary in sorted(per_target.items()):
        frames: dict[int, int] = summary["frames"]  # type: ignore[assignment]
        hit_count = sum(int(frames.get(index + 1, 0)) for index in range(period_count))
        payload_result_ids = sorted(result_id for (result_id, det_id) in protocol_target_keys
                                    if detection_target_by_key.get((result_id, det_id)) == target_id)
        row = {key: value for key, value in summary.items() if key != "frames"}
        row.update({
            "frame1_target_output": int(frames.get(1, 0)),
            "frame2_target_output": int(frames.get(2, 0)),
            "frame3_target_output": int(frames.get(3, 0)),
            "target_hits_in_3_frames": hit_count,
            "two_of_three_target_hits": int(hit_count >= 2),
            "trackmanager_confirmed_matched_protocol_output": int(bool(payload_result_ids)),
            "protocol_output_result_ids": ";".join(map(str, payload_result_ids)),
            "track_definition": "TrackManager Confirmed + matched_this_frame + payload measurement source",
        })
        sequence_rows.append(row)
    write_csv(out / "track_sequence_audit.csv", sequence_rows)
    provenance = {
        "stage2_dir": str(stage2), "result_dir": str(result_dir),
        "track_debug_dir": str(track_debug), "period_start": period_start,
        "period_count": period_count, "cfar_type": "GO",
        "protocol_validation": "仅计 Confirmed + matched_this_frame，且载荷 resolved_source=measurement / position_source=matched_detection",
        "track_detection_index_binding": "matched_det_index is verified against TrackManager input order and detection CSV row order; GMTI02 payload IDs are normalized to integer result IDs",
        "target_count": len(sequence_rows), "confirmed_matched_keys": len(confirmed_matched),
        "accepted_protocol_payload_keys": len(protocol_target_keys),
        "paired_output_scnr_summaries": [str(path.resolve()) for path in args.paired_output_scnr_summary],
    }
    if args.cleanup_cfar_binaries:
        provenance["removed_cfar_binaries"] = remove_cfar_binaries(result_dir, diagnostic_beams)
    (out / "track_metric_provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PASS] 汇总 {len(target_rows)} 条 GO 目标帧记录与 {len(sequence_rows)} 条三帧航迹审计：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
