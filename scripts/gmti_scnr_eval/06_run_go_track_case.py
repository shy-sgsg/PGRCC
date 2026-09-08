#!/usr/bin/env python3
"""运行 Stage2 三周期相扫与生产 TrackManager，并保留最小 GO-CFAR 审计证据。

单个 seed 的生命周期是：生成连续 3 个 raw BIN → ``GMTI_pipe_core`` 本地三
周期回放 → 保留每周期检测 CSV、TrackManager 审计和所选波位的 GO 功率图。
原始 BIN 默认不删除；只有调用者显式给出清理开关时才会删除。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from scnr_eval_lib import ensure_dir, sha256_file


ROOT = Path(__file__).resolve().parents[2]


def run_command(argv: list[str], cwd: Path, log: Path, env: dict[str, str] | None = None) -> None:
    ensure_dir(log.parent)
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as stream:
        stream.write("$ " + " ".join(argv) + "\n\n")
        result = subprocess.run(argv, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                                text=True, env=env, check=False)
        stream.write(f"\n[exit_code]={result.returncode}\n[elapsed_sec]={time.monotonic() - started:.3f}\n")
    if result.returncode:
        raise RuntimeError(f"命令失败（退出码 {result.returncode}）：{' '.join(argv)}；见 {log}")


def set_xml(root: ET.Element, tag: str, value: object) -> None:
    parent = root.find("GMTI_parameter")
    if parent is None:
        raise RuntimeError("Stage2 生成 XML 缺少 GMTI_parameter")
    node = parent.find(tag)
    if node is None:
        node = ET.SubElement(parent, tag)
    node.text = str(value)


def patch_pipe_xml(source: Path, destination: Path, stage2: Path, spec: dict,
                   result_dir: Path, args: argparse.Namespace) -> None:
    tree = ET.parse(source)
    root = tree.getroot()
    scan = spec["scan"]
    waveform = spec["waveform"]
    random = spec["random"]
    updates: dict[str, object] = {
        "result_add": result_dir,
        "GMTI_data_new": stage2 / "data" / "stage2_statistical_newprotocol_period_0000.bin",
        "test": 0,
        "wavepos_st": 1,
        "wavepos_ed": int(scan["beam_count"]),
        "wavepos_skip": 1,
        "new_protocol_file_first_beam": 1,
        "new_protocol_file_scan_beam_count": int(scan["beam_count"]),
        "new_protocol_file_period_index": 0,
        "stage2_period_id": int(random["period_start"]),
        "scan_min_deg": float(scan["scan_min_deg"]),
        "scan_max_deg": float(scan["scan_min_deg"]) +
                        (int(scan["beam_count"]) - 1) * float(scan["scan_step_deg"]),
        "scan_step_deg": float(scan["scan_step_deg"]),
        "pf": args.pfa,
        "cfar_guard_cells": args.cfar_guard,
        "cfar_background_cells": args.cfar_background,
        "cfar_type": "GO",
        "cfar_doppler_circular": "true",
        "dynamic_cfar_enable": "false",
        "csi_detection_band_mode": "split",
        "csi_split_in_band_cfar_type": "GO",
        "csi_split_boundary_guard_rows": 4,
        "csi_split_merge_doppler_bins": 2,
        "csi_split_merge_range_bins": 2,
        "min_points": args.min_points,
        "csi_split_small_cluster_enable": "true",
        "csi_split_small_cluster_min_points": args.csi_split_small_cluster_min_points,
        "csi_split_small_cluster_peak_over_median_db": args.csi_split_small_cluster_peak_over_median_db,
        "csi_split_out_of_band_phase_filter_enable": "false",
        "detection_results_csv_dump": 1,
        "csi_metrics_enable": "false",
        "csi_metrics_dump_power_maps": "false",
        "p38_diagnostics_dump": 0,
        "track_debug_dump": 1,
        "track_debug_dump_level": 1,
        "track_confirm_window": 3,
        "track_confirm_hits": 2,
        "pc_peak_scene_truth": stage2 / "truth" / "scene_truth.csv",
        "pulse_num": int(waveform["pulse_num"]),
    }
    for tag, value in updates.items():
        set_xml(root, tag, value)
    ensure_dir(destination.parent)
    tree.write(destination, encoding="utf-8", xml_declaration=True)


def check_period_snapshots(result_dir: Path, period_start: int, period_count: int) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for index in range(period_count):
        result_id = index + 1
        path = result_dir / f"detection_results_GMTI{result_id:02d}.csv"
        if not path.is_file():
            raise RuntimeError(f"缺少 PIPE 周期快照：{path}")
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        expected = period_start + index
        period_ids = sorted({int(float(row["period_id"])) for row in rows if row.get("period_id", "")})
        if rows and expected in period_ids:
            period_binding = "writer_absolute_period_id"
        elif rows and period_ids == [period_start]:
            # GMTI_pipe_core 从首周期 XML 读取 stage2_period_id，因此多文件 local-test
            # 回放会在 GMTI02/03 中重复写 0。结果编号与 --local-test N=raw[N-1]
            # 才是本专项的周期绑定；manifest 同时保存该 raw 的 SHA-256。
            period_binding = "result_id_to_input_raw_legacy_writer_period_id"
        elif rows:
            raise RuntimeError(
                f"{path} 的 period_id={period_ids}，应包含绝对 Stage2 周期 {expected}；"
                "拒绝将错误 truth 周期用于 GO-Pd 统计")
        else:
            period_binding = "empty_detection_snapshot"
        records.append({"result_id": result_id, "expected_period_id": expected,
                        "detection_csv": str(path), "detection_rows": len(rows),
                        "reported_period_ids": period_ids, "truth_period_binding": period_binding})
    return records


def raw_files_complete(stage2: Path, raw_files: list[Path]) -> bool:
    """校验 Stage2 已完整封口的三周期 raw，可安全跳过重复生成。"""
    manifest = stage2 / "data" / "period_files.csv"
    if not manifest.is_file() or any(not path.is_file() for path in raw_files):
        return False
    with manifest.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != len(raw_files):
        return False
    expected_by_name = {Path(row.get("file", "")).name: int(row.get("file_bytes", "-1")) for row in rows}
    return all(expected_by_name.get(path.name) == path.stat().st_size for path in raw_files)


def pipe_output_complete(result_dir: Path, period_count: int, log: Path) -> bool:
    snapshots = [result_dir / f"detection_results_GMTI{index + 1:02d}.csv"
                 for index in range(period_count)]
    if any(not path.is_file() for path in snapshots):
        return False
    if not list((result_dir / "track_debug").rglob("track_output_payloads.csv")):
        return False
    return log.is_file() and "[exit_code]=0" in log.read_text(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--diagnostic-beams", default="",
                        help="逗号分隔的目标波位；仅这些波位导出 GO 功率图")
    parser.add_argument("--pfa", type=float, default=1e-6)
    parser.add_argument("--cfar-guard", type=int, default=4)
    parser.add_argument("--cfar-background", type=int, default=16)
    parser.add_argument("--min-points", type=int, default=6,
                        help="常规 GO 连通簇最小单元数")
    parser.add_argument("--csi-split-small-cluster-min-points", type=int, default=3,
                        help="split 小簇恢复的最小单元数；须不大于 --min-points")
    parser.add_argument("--csi-split-small-cluster-peak-over-median-db", type=float, default=20.0,
                        help="split 模式 5~(min_points-1) 小簇恢复所需的局部峰值裕量；默认与 Config 一致")
    parser.add_argument("--delete-raw-after-success", action="store_true",
                        help="显式删除本次成功处理的连续 raw BIN；默认保留")
    parser.add_argument("--resume-complete-output", action="store_true",
                        help="仅当 Stage2 raw 已封口且 PIPE 已成功时跳过重跑，补写审计 manifest")
    args = parser.parse_args()

    if args.pfa <= 0.0 or args.pfa >= 1.0:
        raise SystemExit("--pfa 必须在 (0,1) 内")
    if args.min_points < 1 or not (1 <= args.csi_split_small_cluster_min_points <= args.min_points):
        raise SystemExit("小簇最小单元数必须在 [1, --min-points] 内")
    if args.csi_split_small_cluster_peak_over_median_db < 0.0:
        raise SystemExit("--csi-split-small-cluster-peak-over-median-db 不能为负")

    scenario = args.scenario.resolve()
    build = args.build_dir.resolve()
    if not scenario.is_file():
        raise SystemExit(f"找不到场景：{scenario}")
    for executable in ("simulate_stage2_statistical", "GMTI_pipe_core"):
        if not (build / executable).is_file():
            raise SystemExit(f"找不到已构建的 {executable}")
    spec = json.loads(scenario.read_text(encoding="utf-8"))
    random = spec["random"]
    period_start = int(random["period_start"])
    period_count = int(random["period_count"])
    if period_count != 3:
        raise SystemExit("航迹级 Pd 只接受连续 3 周期输入（track_confirm_window=3, hits=2）")
    stage2 = Path(spec["output_dir"])
    stage2 = stage2 if stage2.is_absolute() else ROOT / stage2
    result_dir = stage2 / "algorithm_result" / "pipe_3period"
    raw_files = [stage2 / "data" / f"stage2_statistical_newprotocol_period_{period:04d}.bin"
                 for period in range(period_start, period_start + period_count)]

    stage2_log = stage2 / "logs" / "simulate_stage2.log"
    # TrackManager debug directories are append-only by run timestamp.  A
    # blind rerun into the same result directory would therefore leave two
    # equally plausible association audits and make later attribution
    # ambiguous.  ``--resume-complete-output`` is the only safe way to reuse
    # a sealed PIPE result; otherwise require a new case directory.
    if not args.resume_complete_output and result_dir.exists() and any(result_dir.iterdir()):
        raise RuntimeError(
            f"{result_dir} 已包含 PIPE/TrackManager 产物；拒绝覆盖并混合审计。"
            "请改用新的 case 输出目录，或在确认完整后使用 --resume-complete-output。")
    if args.resume_complete_output and raw_files_complete(stage2, raw_files):
        print("[RESUME] Stage2 三周期 raw 已完整，跳过重生成")
    else:
        run_command([str(build / "simulate_stage2_statistical"), "--config", str(scenario)], ROOT, stage2_log)
    missing = [str(path) for path in raw_files if not path.is_file()]
    if missing:
        raise RuntimeError("Stage2 未生成连续 raw 文件：" + ", ".join(missing))
    source_xml = stage2 / "config" / f"temp_config_stage2_period_{period_start:04d}.xml"
    if not source_xml.is_file():
        raise RuntimeError(f"Stage2 未生成首周期 XML：{source_xml}")
    pipe_xml = stage2 / "config" / "gmti_scnr_go_pipe_3period.xml"
    patch_pipe_xml(source_xml, pipe_xml, stage2, spec, result_dir, args)

    environment = os.environ.copy()
    if args.diagnostic_beams.strip():
        environment["GMTI_CFAR_DUMP_BEAM"] = args.diagnostic_beams.strip()
    echo_specs = [f"{index + 1}={path}" for index, path in enumerate(raw_files)]
    pipe_log = stage2 / "logs" / "gmti_pipe_core_3period.log"
    if args.resume_complete_output and pipe_output_complete(result_dir, period_count, pipe_log):
        print("[RESUME] PIPE 三帧结果与 TrackManager 输出已完整，跳过重处理")
    else:
        run_command([
            str(build / "GMTI_pipe_core"), "--config", str(pipe_xml), "--result-dir", str(result_dir),
            "--track-debug-dir", str(result_dir / "track_debug"), "--track-debug-dump", "on",
            "--runtime-mode=debug", "--runtime-diagnostics=on", "--local-test", *echo_specs,
        ], ROOT, pipe_log, environment)

    snapshots = check_period_snapshots(result_dir, period_start, period_count)
    track_payloads = sorted(str(path) for path in (result_dir / "track_debug").rglob("track_output_payloads.csv"))
    if not track_payloads:
        raise RuntimeError("TrackManager 未写出 track_output_payloads.csv")
    raw_hashes = {str(path): sha256_file(path) for path in raw_files}
    manifest = {
        "scenario": str(scenario), "scenario_sha256": sha256_file(scenario),
        "pipe_xml": str(pipe_xml), "pipe_xml_sha256": sha256_file(pipe_xml),
        "cfar": {"type": "GO", "pfa": args.pfa, "guard_half_width": args.cfar_guard,
                 "background_thickness": args.cfar_background},
        "diagnostic_beams": args.diagnostic_beams.strip(),
        "period_start": period_start, "period_count": period_count,
        "raw_sha256_before_cleanup": raw_hashes, "snapshots": snapshots,
        "result_dir": str(result_dir),
        "track_output_payloads": track_payloads,
        "commands": {"stage2": str(build / "simulate_stage2_statistical"),
                     "pipe": str(build / "GMTI_pipe_core")},
        "raw_removed": False,
    }
    manifest_path = stage2 / "go_track_run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.delete_raw_after_success:
        for path in raw_files:
            path.unlink()
        manifest["raw_removed"] = True
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PASS] 生产三周期 GO-CFAR/TrackManager 链路完成：{stage2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
