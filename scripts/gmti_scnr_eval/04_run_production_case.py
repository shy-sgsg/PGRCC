#!/usr/bin/env python3
"""生成并运行一例 Stage2→正式 GMTI GO-CFAR 链路。

该脚本是后续批量试验的低层入口。输入场景只包含分离的点目标；原始 BIN
可在诊断提取成功后删除，避免为每个 seed 累积数 GB 的回波。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from scnr_eval_lib import ensure_dir, sha256_file


ROOT = Path(__file__).resolve().parents[2]


def command(argv: list[str], cwd: Path, log: Path, env: dict[str, str] | None = None) -> None:
    ensure_dir(log.parent)
    start = time.monotonic()
    with log.open("w", encoding="utf-8") as stream:
        stream.write("$ " + " ".join(argv) + "\n\n")
        result = subprocess.run(argv, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                                text=True, env=env, check=False)
        stream.write(f"\n[exit_code]={result.returncode}\n[elapsed_sec]={time.monotonic() - start:.3f}\n")
    if result.returncode:
        raise RuntimeError(f"命令失败（退出码{result.returncode}）：{' '.join(argv)}；见 {log}")


def set_xml(root: ET.Element, tag: str, value: object) -> None:
    parent = root.find("GMTI_parameter")
    if parent is None:
        raise RuntimeError("Stage2 生成 XML 缺少 GMTI_parameter")
    node = parent.find(tag)
    if node is None:
        node = ET.SubElement(parent, tag)
    node.text = str(value)


def patch_production_xml(xml: Path, scenario_dir: Path, beam_count: int,
                         scan_min_deg: float, scan_step_deg: float,
                         pfa: float, cfar_guard: int, cfar_background: int,
                         min_points: int, csi_split_small_cluster_min_points: int,
                         csi_split_small_cluster_peak_over_median_db: float,
                         csi_metrics_enable: bool, csi_metrics_dump_power_maps: bool,
                         csi_metrics_beam_id: int,
                         doppler_center_override_hz: float | None,
                         p38_csi_override_k_rad_per_hz: float | None,
                         p38_csi_override_b_rad: float | None,
                         paired_raw_range_phase_override_f32: Path | None,
                         paired_csi_range_phase_override_f32: Path | None) -> Path:
    tree = ET.parse(xml)
    root = tree.getroot()
    result_dir = scenario_dir / "algorithm_result" / "period_0000"
    raw = scenario_dir / "data" / "stage2_statistical_newprotocol_period_0000.bin"
    # 这些值冻结当前正式电子扫描的 GO-CFAR 路径；不采用 Stage2 模板中较旧的 pf/窗口。
    updates: dict[str, object] = {
        "result_add": result_dir,
        "GMTI_data_new": raw,
        "test": 0,
        "wavepos_st": 1,
        "wavepos_ed": beam_count,
        "wavepos_skip": 1,
        "new_protocol_file_first_beam": 1,
        "new_protocol_file_scan_beam_count": beam_count,
        "new_protocol_file_period_index": 0,
        "scan_min_deg": scan_min_deg,
        "scan_max_deg": scan_min_deg + (beam_count - 1) * scan_step_deg,
        "scan_step_deg": scan_step_deg,
        "pf": pfa,
        "cfar_guard_cells": cfar_guard,
        "cfar_background_cells": cfar_background,
        "cfar_type": "GO",
        "cfar_doppler_circular": "true",
        "dynamic_cfar_enable": "false",
        "csi_detection_band_mode": "split",
        "csi_split_in_band_cfar_type": "GO",
        "csi_split_boundary_guard_rows": 4,
        "csi_split_merge_doppler_bins": 2,
        "csi_split_merge_range_bins": 2,
        "min_points": min_points,
        "csi_split_small_cluster_enable": "true",
        "csi_split_small_cluster_min_points": csi_split_small_cluster_min_points,
        "csi_split_small_cluster_peak_over_median_db": csi_split_small_cluster_peak_over_median_db,
        "csi_split_out_of_band_phase_filter_enable": "false",
        "detection_results_csv_dump": 1,
        "csi_metrics_enable": str(csi_metrics_enable).lower(),
        "csi_metrics_dump_power_maps": str(csi_metrics_dump_power_maps).lower(),
        "csi_metrics_beam_id": csi_metrics_beam_id,
        "p38_diagnostics_dump": 0,
        "pc_peak_scene_truth": scenario_dir / "truth" / "scene_truth.csv",
        "track_debug_dump": 1,
        "track_debug_dump_level": 1,
    }
    for tag, value in updates.items():
        set_xml(root, tag, value)
    if doppler_center_override_hz is not None:
        set_xml(root, "doppler_center_override_hz", doppler_center_override_hz)
    if p38_csi_override_k_rad_per_hz is not None:
        set_xml(root, "p38_csi_override_k_rad_per_hz", p38_csi_override_k_rad_per_hz)
        set_xml(root, "p38_csi_override_b_rad", p38_csi_override_b_rad)
    if paired_raw_range_phase_override_f32 is not None:
        set_xml(root, "paired_raw_range_phase_override_f32",
                paired_raw_range_phase_override_f32.resolve())
        set_xml(root, "paired_csi_range_phase_override_f32",
                paired_csi_range_phase_override_f32.resolve())
    ensure_dir(result_dir)
    tree.write(xml, encoding="utf-8", xml_declaration=True)
    return xml


def write_manifest(scenario_dir: Path, scenario: Path, xml: Path, raw: Path,
                   args: argparse.Namespace, raw_removed: bool, raw_sha256: str | None,
                   diagnostic_beams: str) -> None:
    payload = {
        "scenario": str(scenario), "scenario_sha256": sha256_file(scenario),
        "production_xml": str(xml), "production_xml_sha256": sha256_file(xml),
        "raw_path": str(raw), "raw_sha256_before_cleanup": raw_sha256,
        "raw_removed": raw_removed, "cfar": {"type": "GO", "pfa": args.pfa,
        "guard_half_width": args.cfar_guard, "background_thickness": args.cfar_background},
        "csi_metrics": {"enabled": args.csi_metrics_enable,
                        "dump_power_maps": args.csi_metrics_dump_power_maps,
                        "beam_id": args.csi_metrics_beam_id},
        "doppler_center_override_hz": args.doppler_center_override_hz,
        "p38_csi_override": {
            "k_rad_per_hz": args.p38_csi_override_k_rad_per_hz,
            "b_rad": args.p38_csi_override_b_rad},
        "paired_range_phase_override": {
            "raw_f32": str(args.paired_raw_range_phase_override_f32)
                if args.paired_raw_range_phase_override_f32 else None,
            "csi_f32": str(args.paired_csi_range_phase_override_f32)
                if args.paired_csi_range_phase_override_f32 else None},
        "cfar_diagnostic_beams": diagnostic_beams,
        "commands": {"stage2": str(Path(args.build_dir) / "simulate_stage2_statistical"),
                     "gmti_core": str(Path(args.build_dir) / "GMTI_core")},
    }
    (scenario_dir / "run_manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                                      encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, required=True, help="Stage2 run JSON")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--diagnostic-beams", default="",
                        help="逗号分隔的 CFAR 全图诊断波位；为空时不导出")
    parser.add_argument("--diagnostic-beam", type=int, default=None,
                        help="兼容旧调用：唯一写 CFAR 全图诊断的波位；-1 关闭")
    parser.add_argument("--pfa", type=float, default=1e-6)
    parser.add_argument("--cfar-guard", type=int, default=4)
    parser.add_argument("--cfar-background", type=int, default=16)
    parser.add_argument("--min-points", type=int, default=6,
                        help="常规 GO 连通簇最小单元数")
    parser.add_argument("--csi-split-small-cluster-min-points", type=int, default=3,
                        help="split 小簇恢复的最小单元数；须不大于 --min-points")
    parser.add_argument("--csi-split-small-cluster-peak-over-median-db", type=float, default=20.0,
                        help="split 模式 5~(min_points-1) 小簇恢复所需的局部峰值裕量；默认与 Config 一致")
    parser.add_argument("--csi-metrics-enable", action="store_true",
                        help="开启只读 CSI 指标 tap；仅专项标定使用，默认关闭")
    parser.add_argument("--csi-metrics-dump-power-maps", action="store_true",
                        help="在 CSI tap 已开启时导出通道/对消功率图；默认关闭")
    parser.add_argument("--csi-metrics-beam-id", type=int, default=-1,
                        help="CSI tap 波位 ID；-1 表示全部，单波位标定可保持默认")
    parser.add_argument("--doppler-center-override-hz", type=float, default=None,
                        help="仅配对标定：冻结为已审计的 wrapped Doppler 中心；省略时保持生产估计")
    parser.add_argument("--p38-csi-override-k-rad-per-hz", type=float, default=None,
                        help="仅配对标定：冻结最终 CSI P38 slope；须与 intercept 成对给出")
    parser.add_argument("--p38-csi-override-b-rad", type=float, default=None,
                        help="仅配对标定：冻结最终 CSI P38 intercept；须与 slope 成对给出")
    parser.add_argument("--paired-raw-range-phase-override-f32", type=Path, default=None,
                        help="仅配对标定：冻结第一段距离相位校正的 float32 参考文件")
    parser.add_argument("--paired-csi-range-phase-override-f32", type=Path, default=None,
                        help="仅配对标定：冻结 CSI 段距离相位校正的 float32 参考文件")
    parser.add_argument("--delete-raw-after-success", action="store_true",
                        help="显式删除本次成功处理的原始 BIN；默认保留，避免意外删除诊断现场")
    args = parser.parse_args()
    scenario = args.scenario.resolve()
    build = args.build_dir.resolve()
    if args.csi_metrics_dump_power_maps and not args.csi_metrics_enable:
        raise SystemExit("--csi-metrics-dump-power-maps 需要同时给出 --csi-metrics-enable")
    if args.pfa <= 0.0 or args.pfa >= 1.0:
        raise SystemExit("--pfa 必须在 (0,1) 内")
    if args.min_points < 1 or not (1 <= args.csi_split_small_cluster_min_points <= args.min_points):
        raise SystemExit("小簇最小单元数必须在 [1, --min-points] 内")
    if args.csi_split_small_cluster_peak_over_median_db < 0.0:
        raise SystemExit("--csi-split-small-cluster-peak-over-median-db 不能为负")
    if args.doppler_center_override_hz is not None and not math.isfinite(args.doppler_center_override_hz):
        raise SystemExit("--doppler-center-override-hz 必须为有限数")
    if ((args.p38_csi_override_k_rad_per_hz is None) !=
            (args.p38_csi_override_b_rad is None)):
        raise SystemExit("两个 --p38-csi-override-* 参数必须成对给出")
    if (args.p38_csi_override_k_rad_per_hz is not None and
            (not math.isfinite(args.p38_csi_override_k_rad_per_hz) or
             not math.isfinite(args.p38_csi_override_b_rad))):
        raise SystemExit("--p38-csi-override-* 必须为有限数")
    if ((args.paired_raw_range_phase_override_f32 is None) !=
            (args.paired_csi_range_phase_override_f32 is None)):
        raise SystemExit("两个 --paired-*-range-phase-override-f32 参数必须成对给出")
    if args.paired_raw_range_phase_override_f32 is not None:
        args.paired_raw_range_phase_override_f32 = args.paired_raw_range_phase_override_f32.resolve()
        args.paired_csi_range_phase_override_f32 = args.paired_csi_range_phase_override_f32.resolve()
        if (not args.paired_raw_range_phase_override_f32.is_file() or
                not args.paired_csi_range_phase_override_f32.is_file()):
            raise SystemExit("指定的配对距离相位参考文件不存在")
    if not scenario.is_file():
        raise SystemExit(f"找不到场景：{scenario}")
    if not (build / "simulate_stage2_statistical").is_file() or not (build / "GMTI_core").is_file():
        raise SystemExit("找不到已构建的 simulate_stage2_statistical/GMTI_core")
    spec = json.loads(scenario.read_text(encoding="utf-8"))
    scenario_dir = Path(spec["output_dir"])
    scenario_dir = scenario_dir if scenario_dir.is_absolute() else ROOT / scenario_dir
    beam_count = int(spec["scan"]["beam_count"])
    scan_min = float(spec["scan"]["scan_min_deg"])
    scan_step = float(spec["scan"]["scan_step_deg"])
    command([str(build / "simulate_stage2_statistical"), "--config", str(scenario)], ROOT,
            scenario_dir / "logs" / "simulate_stage2.log")
    xml = scenario_dir / "config" / "temp_config_stage2_newsystem.xml"
    if not xml.is_file():
        raise RuntimeError(f"Stage2 未生成 XML：{xml}")
    patch_production_xml(xml, scenario_dir, beam_count, scan_min, scan_step,
                         args.pfa, args.cfar_guard, args.cfar_background,
                         args.min_points, args.csi_split_small_cluster_min_points,
                         args.csi_split_small_cluster_peak_over_median_db,
                         args.csi_metrics_enable, args.csi_metrics_dump_power_maps,
                         args.csi_metrics_beam_id, args.doppler_center_override_hz,
                         args.p38_csi_override_k_rad_per_hz,
                         args.p38_csi_override_b_rad,
                         args.paired_raw_range_phase_override_f32,
                         args.paired_csi_range_phase_override_f32)
    environment = os.environ.copy()
    diagnostic_beams = args.diagnostic_beams.strip()
    if not diagnostic_beams and args.diagnostic_beam is not None and args.diagnostic_beam >= 0:
        diagnostic_beams = str(args.diagnostic_beam)
    if diagnostic_beams:
        environment["GMTI_CFAR_DUMP_BEAM"] = diagnostic_beams
    command([str(build / "GMTI_core"), str(xml), "--runtime-mode=debug", "--runtime-diagnostics=on"], ROOT,
            scenario_dir / "logs" / "gmti_core.log", environment)
    raw = scenario_dir / "data" / "stage2_statistical_newprotocol_period_0000.bin"
    raw_sha256 = sha256_file(raw) if raw.exists() else None
    write_manifest(scenario_dir, scenario, xml, raw, args, raw_removed=False,
                   raw_sha256=raw_sha256, diagnostic_beams=diagnostic_beams)
    if args.delete_raw_after_success and raw.exists():
        raw.unlink()
        # 保留清单及其 SHA，而不是保留无法复现实验结论所必需之外的大型回波。
        write_manifest(scenario_dir, scenario, xml, raw, args, raw_removed=True,
                       raw_sha256=raw_sha256, diagnostic_beams=diagnostic_beams)
    print(f"[PASS] 正式 GO-CFAR 链路完成：{scenario_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
