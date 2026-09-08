#!/usr/bin/env python3
"""从生产 GO-CFAR 的最小诊断文件提取每目标 CFAR/相位/角度统计量。

只读取一个被 ``GMTI_CFAR_DUMP_BEAM`` 选中的 2 MiB 功率图；输出完成后可
删除该 F32 图。不会默认保存整幅 CSI 矩阵。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from scnr_eval_lib import GoCfarGeometry, ensure_dir, read_csv, write_csv


def number(row: dict[str, str], key: str, default: float = float("nan")) -> float:
    try:
        value = row.get(key, "")
        return float(value) if value not in {"", None} else default
    except ValueError:
        return default


def integer(row: dict[str, str], key: str, default: int = -1) -> int:
    value = number(row, key, float(default))
    return int(round(value)) if math.isfinite(value) else default


def wrap(value: float) -> float:
    return (value + math.pi) % (2.0 * math.pi) - math.pi


def wrap_degrees(value: float) -> float:
    return (value + 180.0) % 360.0 - 180.0


def production_truth_angle_deg(truth: dict[str, str]) -> float:
    """Truth angle in the production localization convention.

    The generated geometry uses command angle 0° on the negative-E line of
    sight, so writeresults reports it as -180° (30° -> -150°, 45° -> -135°).
    Keep this convention when a local-test detection lacks writeresults'
    truth-labelled angle fields; the error is invariant under the common
    180° offset, while using ``azimuth_deg`` directly would create a false
    180° error in the fallback path.
    """
    command = number(truth, "theta_cmd_deg")
    return wrap_degrees(command - 180.0) if math.isfinite(command) else float("nan")


def circular_row_distance(left: int, right: int, rows: int) -> int:
    if left < 0 or right < 0 or rows <= 0:
        return rows
    direct = abs((left % rows) - (right % rows))
    return min(direct, rows - direct)


def read_key_value_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    return values


def truth_row_on_cfar_axis(truth: dict[str, str], meta: dict[str, str],
                           pulse_num: int, prf_hz: float) -> tuple[int, str]:
    """Map truth to the row used by the production packet/truth matcher.

    The production CFAR sidecar is authoritative whenever it is present:
    ``row_truth`` is the packet-addressed/raw FFT row (possibly negative),
    whereas the dumped map may have been re-centred by the run-time Doppler
    axis.  Reusing the raw row for a centre beam therefore scores a different
    physical cell whenever the observed centre is non-zero.  Project the
    physical truth Doppler through the actual sidecar for both centre and
    off-centre beams; only legacy diagnostics with no usable sidecar fall back
    to the circular packet row.  Keep the source label in every row so an
    audit can distinguish the two cases.
    """
    rows = integer(meta, "rows", pulse_num)
    raw = integer(truth, "row_truth")
    center = number(meta, "doppler_axis_center_wrapped_hz")
    truth_doppler = number(truth, "af_total_truth_hz")
    step = number(meta, "doppler_axis_row_step_hz")
    if not math.isfinite(step) and rows > 0 and prf_hz > 0.0:
        step = prf_hz / rows
    if rows > 0 and math.isfinite(center) and math.isfinite(truth_doppler) and step > 0.0:
        row = math.floor((truth_doppler - (center - 0.5 * prf_hz)) / step + 0.5)
        return int(row) % rows, "production_cfar_axis_meta"
    return (raw % rows if rows > 0 and raw != -1 else raw), "legacy_simulator_row_truth"


def cfar_window(power: np.ndarray, row: int, col: int, geometry: GoCfarGeometry,
                alpha: float) -> dict[str, float | int]:
    rows, cols = power.shape
    g, b, outer = geometry.guard_half_width, geometry.background_thickness, geometry.outer_half_width
    if not 0 <= col < cols or col - outer < 0 or col + outer >= cols:
        return {"cfar_window_valid": 0, "cfar_train_mean": float("nan"), "cfar_threshold": float("nan"),
                "cut_power": float("nan"), "scnr_det_out_db": float("nan")}
    at = lambda r, c: float(power[r % rows, c])
    left = np.asarray([at(r, c) for r in range(row - outer, row + outer + 1)
                       for c in range(col - outer, col - g)], dtype=np.float64)
    right = np.asarray([at(r, c) for r in range(row - outer, row + outer + 1)
                        for c in range(col + g + 1, col + outer + 1)], dtype=np.float64)
    top = np.asarray([at(r, c) for r in range(row - outer, row - g)
                      for c in range(col - outer, col + outer + 1)], dtype=np.float64)
    bottom = np.asarray([at(r, c) for r in range(row + g + 1, row + outer + 1)
                         for c in range(col - outer, col + outer + 1)], dtype=np.float64)
    means = [float(np.mean(part)) for part in (left, right, top, bottom)]
    noise = max(means)
    cut = float(power[row % rows, col])
    excess = cut - noise
    return {"cfar_window_valid": 1, "cfar_train_count": geometry.directional_strip_cells,
            "cfar_train_mean": noise, "cfar_left_mean": means[0], "cfar_right_mean": means[1],
            "cfar_top_mean": means[2], "cfar_bottom_mean": means[3],
            "cfar_threshold": alpha * noise, "cut_power": cut,
            "scnr_det_out_db": 10.0 * math.log10(excess / noise) if excess > 0.0 and noise > 0.0 else float("nan")}


def connected_go_hit_component_size(hits: np.ndarray, row: int, col: int,
                                    max_range_gap: int, doppler_circular: bool) -> int:
    """复现 GPU 聚类的 3×(2*gap+1) 邻域，返回 CFAR hit 连通分量单元数。

    这是 phase/strong-small 二次筛选之前的生产 GO hit component 大小；最终检测
    已通过这些筛选，故该数可审计其输入连通分量，但不会伪装成一个独立的聚类器。
    """
    rows, cols = hits.shape
    if not (0 <= row < rows and 0 <= col < cols) or hits[row, col] <= 0.0:
        return 0
    gap = max(1, max_range_gap)
    todo = [(row, col)]
    seen = {(row, col)}
    while todo:
        current_row, current_col = todo.pop()
        for delta_row in (-1, 0, 1):
            neighbor_row = current_row + delta_row
            if doppler_circular:
                neighbor_row %= rows
            if not 0 <= neighbor_row < rows:
                continue
            for delta_col in range(-gap, gap + 1):
                if delta_row == 0 and delta_col == 0:
                    continue
                neighbor_col = current_col + delta_col
                if not 0 <= neighbor_col < cols or hits[neighbor_row, neighbor_col] <= 0.0:
                    continue
                key = (neighbor_row, neighbor_col)
                if key not in seen:
                    seen.add(key)
                    todo.append(key)
    return len(seen)


def truth_resolution_go_hit(hits: np.ndarray, row: int, col: int,
                            range_tolerance: int = 2,
                            doppler_tolerance: int = 2) -> dict[str, int]:
    """Return whether GO-CFAR fired in the truth resolution cell.

    A physical Doppler is projected onto a finite FFT grid and the production
    detection/NMS path permits a two-bin displacement.  The exact projected
    CUT remains an auditable diagnostic, while this small range/Doppler gate
    is the operational unit-level event used for Pd: it cannot turn a remote
    clutter peak into a target hit, but it does not falsely penalise a target
    whose main-lobe lands on the adjacent FFT sample.
    """
    rows, cols = hits.shape
    if not (0 <= col < cols) or rows <= 0:
        return {"hit": 0, "row_offset": -1, "range_offset": -1}
    candidates: list[tuple[int, int, int, int, int]] = []
    for dr in range(-doppler_tolerance, doppler_tolerance + 1):
        candidate_row = (row + dr) % rows
        for dc in range(-range_tolerance, range_tolerance + 1):
            candidate_col = col + dc
            if 0 <= candidate_col < cols and hits[candidate_row, candidate_col] > 0.0:
                # Prefer the closest location, then preserve a deterministic
                # row/range order for reproducible diagnostics.
                candidates.append((abs(dr) + abs(dc), abs(dr), abs(dc), dr, dc))
    if not candidates:
        return {"hit": 0, "row_offset": -1, "range_offset": -1}
    _, _, _, row_offset, range_offset = min(candidates)
    return {"hit": 1, "row_offset": row_offset, "range_offset": range_offset}


def config_values(xml: Path) -> dict[str, str]:
    root = ET.parse(xml).getroot()
    return {node.tag: node.text.strip() for node in root.iter() if node.text and node.text.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage2-dir", type=Path, required=True)
    parser.add_argument("--algorithm-dir", type=Path, default=None,
                        help="生产结果目录；默认 stage2/algorithm_result/period_0000")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--diagnostic-beams", default="",
                        help="逗号分隔的已导出 CFAR 功率图波位")
    parser.add_argument("--diagnostic-beam", type=int, default=None,
                        help="兼容旧调用：单一已导出 CFAR 功率图波位")
    parser.add_argument("--diagnostic-result-id", type=int, default=0,
                        help="PIPE 三周期回放的 GMTI 结果编号；0 自动兼容单周期旧命名")
    parser.add_argument("--period-id", type=int, default=None,
                        help="只统计指定绝对 Stage2 周期的 truth（PIPE 单周期快照必填）")
    parser.add_argument("--detection-csv", type=Path, default=None,
                        help="指定检测 CSV；默认 algorithm_result/period_0000/detection_results.csv")
    parser.add_argument("--production-xml", type=Path, default=None,
                        help="实际处理本次检测 CSV 的 GMTI XML；PIPE 三帧回放必须显式给出")
    parser.add_argument("--cleanup-cfar-binary", action="store_true")
    args = parser.parse_args()
    stage2 = args.stage2_dir.resolve()
    out = ensure_dir(args.output_dir.resolve())
    algorithm = args.algorithm_dir.resolve() if args.algorithm_dir else stage2 / "algorithm_result" / "period_0000"
    truth_path = stage2 / "truth" / "truth_targets_by_beam.csv"
    det_path = args.detection_csv.resolve() if args.detection_csv else algorithm / "detection_results.csv"
    xml = args.production_xml.resolve() if args.production_xml else \
        stage2 / "config" / "temp_config_stage2_newsystem.xml"
    if not truth_path.is_file() or not det_path.is_file() or not xml.is_file():
        raise SystemExit("缺少 truth_targets_by_beam.csv、detection_results.csv 或生产 XML")
    cfg = config_values(xml)
    geometry = GoCfarGeometry(int(cfg["cfar_guard_cells"]), int(cfg["cfar_background_cells"]))
    pfa = float(cfg["pf"])
    # 旧诊断没有 alpha sidecar 时保持历史 CA 公式，避免把旧运行误标成
    # 新的 GO 校准门限；新运行从生产 sidecar 读取实际使用的 alpha。
    alpha = geometry.alpha(pfa)
    alpha_source = "legacy_ca_ring_formula_no_production_meta"
    pulse_num, range_bins = int(cfg["pulse_num"]), int(cfg["rg_len"])
    beam_text = args.diagnostic_beams.strip()
    if not beam_text and args.diagnostic_beam is not None and args.diagnostic_beam >= 0:
        beam_text = str(args.diagnostic_beam)
    diagnostic_beams = sorted({int(token.strip()) for token in beam_text.split(",") if token.strip()})
    power_by_beam: dict[int, np.ndarray] = {}
    hits_by_beam: dict[int, np.ndarray] = {}
    power_paths: dict[int, Path] = {}
    hit_paths: dict[int, Path] = {}
    axis_meta_by_beam: dict[int, dict[str, str]] = {}
    for beam in diagnostic_beams:
        debug_dir = algorithm / "debug"
        if args.diagnostic_result_id > 0:
            stem = f"cfar_GMTI{args.diagnostic_result_id:02d}_beam{beam:03d}"
            legacy_stem = f"cfar_beam{beam:03d}"
            power_path = debug_dir / f"{stem}_power.f32"
            hit_path = debug_dir / f"{stem}_hits.f32"
            if not power_path.is_file():
                power_path = debug_dir / f"{legacy_stem}_power.f32"
                hit_path = debug_dir / f"{legacy_stem}_hits.f32"
        else:
            power_path = debug_dir / f"cfar_beam{beam:03d}_power.f32"
            hit_path = debug_dir / f"cfar_beam{beam:03d}_hits.f32"
            if not power_path.is_file():
                candidates = sorted(debug_dir.glob(f"cfar_GMTI*_beam{beam:03d}_power.f32"))
                if len(candidates) == 1:
                    power_path = candidates[0]
                    hit_path = power_path.with_name(
                        power_path.name.removesuffix("_power.f32") + "_hits.f32")
        if not power_path.is_file():
            continue
        raw = np.fromfile(power_path, dtype=np.float32)
        if raw.size != pulse_num * range_bins:
            raise SystemExit(f"CFAR 功率图长度错误：{power_path}: {raw.size} != {pulse_num}*{range_bins}")
        power_by_beam[beam] = raw.reshape(pulse_num, range_bins)
        if hit_path.is_file():
            raw_hits = np.fromfile(hit_path, dtype=np.float32)
            if raw_hits.size != pulse_num * range_bins:
                raise SystemExit(f"CFAR hit 图长度错误：{hit_path}: {raw_hits.size} != {pulse_num}*{range_bins}")
            hits_by_beam[beam] = raw_hits.reshape(pulse_num, range_bins)
        power_paths[beam] = power_path
        hit_paths[beam] = hit_path
        meta_path = power_path.with_name(power_path.name.removesuffix("_power.f32") + "_meta.txt")
        axis_meta_by_beam[beam] = read_key_value_file(meta_path)
    production_alphas = [number(meta, "cfar_alpha") for meta in axis_meta_by_beam.values()]
    production_alphas = [value for value in production_alphas if math.isfinite(value) and value > 0.0]
    if production_alphas:
        if max(production_alphas) - min(production_alphas) > 1e-9:
            raise RuntimeError("同一 production GO 诊断出现不一致的 CFAR alpha")
        alpha = production_alphas[0]
        alpha_source = "production_cfar_meta"
    truths = [row for row in read_csv(truth_path) if row.get("visible", "1") not in {"0", "false", "False"}]
    if args.period_id is not None:
        truths = [row for row in truths if integer(row, "period_id") == args.period_id]
    detections = read_csv(det_path)
    by_target: dict[str, list[dict[str, str]]] = {}
    for detection in detections:
        target_id = detection.get("target_id", "")
        if target_id:
            by_target.setdefault(target_id, []).append(detection)
    target_rows: list[dict] = []
    cfar_rows: list[dict] = []
    angle_rows: list[dict] = []
    for truth in truths:
        target_id = truth.get("target_id", "")
        candidates = by_target.get(target_id, [])
        match_source = "writeresults_target_id_beam_range"
        # ``detection_results_GMTI*.csv`` 通常由生产 writeresults.cpp 写入
        # target_id；但 local-test 多文件回放时，packet 的 period_id 可能
        # 被重置为 0，导致 writer 无法把当前帧 detection 标上 truth ID。
        # 这种情况下不能把“已有的同波束、同 range、同 Doppler 分辨单元
        # 的生产 detection”误记成漏检。只在 target_id 为空/没有匹配时
        # 使用一个有界的几何回退，并把来源显式写入审计字段；这不会改变
        # PIPE 协议载荷，也不会把远处 clutter 峰变成目标。
        expected = integer(truth, "range_bin", integer(truth, "expected_bin"))
        truth_beam = integer(truth, "beam_id")
        truth_row_raw = integer(truth, "row_truth")
        truth_row, truth_row_source = truth_row_on_cfar_axis(
            truth, axis_meta_by_beam.get(truth_beam, {}), pulse_num, float(cfg["PRF"]))
        # A target_id alone is not sufficient evidence for this frame: local
        # test/writeresults may carry a stale ID from a neighbouring packet.
        # Keep the same bounded Doppler gate used by the fallback path so a
        # remote Doppler peak cannot become a truth match (and hence a target
        # Pd hit) merely because its range/ID happens to agree.
        candidates = [row for row in candidates
                      if integer(row, "beam_id") == truth_beam and
                      abs(integer(row, "range_bin") - expected) <= 2 and
                      circular_row_distance(integer(row, "row"), truth_row, pulse_num) <= 2]
        if not candidates:
            fallback = [row for row in detections
                        if integer(row, "beam_id") == truth_beam
                        and abs(integer(row, "range_bin") - expected) <= 2
                        and circular_row_distance(integer(row, "row"), truth_row, pulse_num) <= 2]
            if fallback:
                candidates = fallback
                match_source = "writeresults_beam_range_doppler_fallback"
        detection = min(candidates, key=lambda row: (
            abs(integer(row, "range_bin") - expected),
            circular_row_distance(integer(row, "row"), truth_row, pulse_num))) if candidates else None
        matched = detection is not None
        truth_angle_fallback = production_truth_angle_deg(truth)
        record = {
            "case_id": truth.get("case_id", ""), "seed": cfg.get("stage2_random_seed", ""),
            "period_id": integer(truth, "period_id"), "beam_id": truth_beam,
            # 检测 Pd/标定按命令波位归组；目标的实际方位会随其运动在连续三帧间
            # 小幅漂移，不能让这种漂移拆分同一波位/SCNR 的统计试验。真实方位仍
            # 单独保留，并继续用作测角误差的 truth。
            "target_id": target_id, "truth_angle_deg": number(truth, "theta_cmd_deg"),
            "physical_truth_angle_deg": number(truth, "azimuth_deg"),
            "truth_range_bin": expected, "truth_doppler": number(truth, "af_total_truth_hz"),
            "truth_cell_doppler_row": truth_row, "truth_simulator_doppler_row": truth_row_raw,
            "truth_cell_doppler_row_source": truth_row_source, "truth_cell_range_col": expected,
            "production_detection_row_offset": (circular_row_distance(integer(detection, "row"), truth_row, pulse_num)
                                                  if detection is not None else float("nan")),
            "production_detection_match_source": match_source if detection is not None else "none",
            "configured_target_snr_db": number(truth, "snr_db"), "final_output": int(matched),
            "truth_matched": int(matched), "cfar_type": "GO", "cfar_alpha": alpha,
            "cfar_alpha_source": alpha_source,
            "scnr_phase_ch1_db": float("nan"), "scnr_phase_ch2_db": float("nan"),
            "scnr_phase_eff_db": float("nan"),
            "phase_truth_rad": number(truth, "phi_total_truth_rad"),
            "phase_est_rad": number(detection, "ctdr_raw_phase_rad") if detection else float("nan"),
            "p38_k": number(detection, "p38_k") if detection else float("nan"),
            "p38_beta": number(detection, "p38_b") if detection else float("nan"),
            # 正常情况下使用 writeresults 已计算的 truth/estimate；local-test
            # 回退匹配没有 truth 指针时，仍从生产 localization 字段
            # theta_used_deg 复原估计角，并用同一生产坐标约定的 truth 角。
            "angle_truth_deg": number(detection, "truth_angle_deg", truth_angle_fallback) if detection else truth_angle_fallback,
            "angle_est_deg": (number(detection, "estimated_angle_deg",
                                      number(detection, "theta_used_deg"))
                              if detection else float("nan")),
            "angle_error_deg": number(detection, "angle_error_deg") if detection else float("nan"),
            "cluster_size": float("nan"),
            "cluster_size_semantics": "unavailable_without_selected_beam_go_hit_map",
            "unit_cell_window_valid": 0,
            # exact_cut 保留“一个指定 FFT 采样点”的严格证据；而 unit_go_cfar_hit
            # 是生产 target matching 所用的物理分辨单元事件（range/Doppler 各 ±2）。
            "unit_go_cfar_exact_cut_hit": float("nan"),
            "unit_go_cfar_hit": float("nan"),
            "unit_go_cfar_hit_row_offset": float("nan"),
            "unit_go_cfar_hit_range_offset": float("nan"),
            "unit_go_hit_component_size": float("nan"),
            "unit_cell_semantics": (
                "physical truth Doppler mapped through the production CFAR axis sidecar; "
                "unit_go_cfar_hit is a ±2 Doppler/range-bin truth resolution-cell event before "
                "clustering, target_select, relocation, and truth matching; exact_cut is retained separately"),
        }
        if matched:
            if not math.isfinite(record["angle_error_deg"]):
                if math.isfinite(record["angle_truth_deg"]) and math.isfinite(record["angle_est_deg"]):
                    record["angle_error_deg"] = wrap_degrees(
                        record["angle_est_deg"] - record["angle_truth_deg"])
            record.update({"range_bin": integer(detection, "range_bin"), "doppler_row": integer(detection, "row"),
                           "detection_power": number(detection, "power")})
            if math.isfinite(record["phase_est_rad"]) and math.isfinite(record["phase_truth_rad"]):
                record["phase_error_rad"] = wrap(record["phase_est_rad"] - record["phase_truth_rad"])
            else:
                record["phase_error_rad"] = float("nan")
            power = power_by_beam.get(truth_beam)
            if power is not None:
                record.update(cfar_window(power, integer(detection, "row"), integer(detection, "col"), geometry, alpha))
                hits = hits_by_beam.get(truth_beam)
                if hits is not None:
                    component_size = connected_go_hit_component_size(
                        hits, integer(detection, "row"), integer(detection, "col"),
                        int(cfg.get("cluster_max_range_gap", "2")),
                        cfg.get("cfar_doppler_circular", "true").strip().lower() in {"1", "true", "yes"})
                    record["cluster_size"] = component_size
                    record["cluster_size_semantics"] = (
                        "production_GO_hit_connected_component_before_phase_and_strong_small_postfilters")
            else:
                record.update({"cfar_window_valid": 0, "cfar_train_count": geometry.directional_strip_cells,
                               "cfar_train_mean": float("nan"), "cfar_threshold": float("nan"),
                               "cut_power": float("nan"), "scnr_det_out_db": float("nan")})
        else:
            record.update({"range_bin": float("nan"), "doppler_row": float("nan"), "detection_power": float("nan"),
                           "phase_error_rad": float("nan"), "cfar_window_valid": 0,
                           "cfar_train_count": geometry.directional_strip_cells, "cfar_train_mean": float("nan"),
                           "cfar_threshold": float("nan"), "cut_power": float("nan"), "scnr_det_out_db": float("nan")})

        # 单元级指标固定在物理真值映射到本次生产 CFAR 轴的 row/range，不以最后
        # 输出 detection 的局部峰替换。它因而能覆盖“最终无输出”的真实目标单元，
        # 与目标级 Pd/航迹级 Pd 保持不同层次。
        truth_power = power_by_beam.get(truth_beam)
        if truth_power is not None and truth_row >= 0:
            unit = cfar_window(truth_power, truth_row, expected, geometry, alpha)
            record.update({"unit_" + key: value for key, value in unit.items()})
            record["unit_cell_window_valid"] = int(unit["cfar_window_valid"])
            truth_hits = hits_by_beam.get(truth_beam)
            if truth_hits is not None and 0 <= expected < truth_hits.shape[1]:
                record["unit_go_cfar_exact_cut_hit"] = int(
                    truth_hits[truth_row % truth_hits.shape[0], expected] > 0.0)
                resolution_hit = truth_resolution_go_hit(truth_hits, truth_row, expected)
                record["unit_go_cfar_hit"] = resolution_hit["hit"]
                record["unit_go_cfar_hit_row_offset"] = resolution_hit["row_offset"]
                record["unit_go_cfar_hit_range_offset"] = resolution_hit["range_offset"]
                if resolution_hit["hit"]:
                    component_row = (truth_row + resolution_hit["row_offset"]) % truth_hits.shape[0]
                    component_col = expected + resolution_hit["range_offset"]
                    record["unit_go_hit_component_size"] = connected_go_hit_component_size(
                        truth_hits, component_row, component_col,
                        int(cfg.get("cluster_max_range_gap", "2")),
                        cfg.get("cfar_doppler_circular", "true").strip().lower()
                        in {"1", "true", "yes"})
        elif truth_power is not None:
            record["unit_cell_semantics"] = "truth Doppler unavailable; no specified GO unit result"
        target_rows.append(record)
        cfar_rows.append({key: record.get(key) for key in (
            "case_id", "seed", "period_id", "beam_id", "target_id", "configured_target_snr_db", "cfar_type",
            "cfar_alpha", "cfar_alpha_source", "cfar_window_valid", "cfar_train_count", "cfar_train_mean", "cfar_left_mean",
            "cfar_right_mean", "cfar_top_mean", "cfar_bottom_mean", "cfar_threshold", "cut_power",
            "scnr_det_out_db", "cluster_size", "cluster_size_semantics", "final_output", "truth_matched",
            "truth_cell_doppler_row", "truth_simulator_doppler_row", "truth_cell_doppler_row_source",
            "truth_cell_range_col", "unit_cell_window_valid", "unit_go_cfar_exact_cut_hit",
            "unit_go_cfar_hit", "unit_go_cfar_hit_row_offset", "unit_go_cfar_hit_range_offset",
            "unit_go_hit_component_size",
            "unit_cfar_train_count", "unit_cfar_train_mean", "unit_cfar_left_mean", "unit_cfar_right_mean",
            "unit_cfar_top_mean", "unit_cfar_bottom_mean", "unit_cfar_threshold", "unit_cut_power",
            "unit_scnr_det_out_db", "unit_cell_semantics")})
        angle_rows.append({key: record.get(key) for key in (
            "case_id", "seed", "period_id", "beam_id", "target_id", "configured_target_snr_db",
            "phase_truth_rad", "phase_est_rad", "phase_error_rad", "p38_k", "p38_beta",
            "angle_truth_deg", "angle_est_deg", "angle_error_deg", "final_output", "truth_matched")})
    write_csv(out / "target_diagnostics.csv", target_rows)
    write_csv(out / "cfar_window_diagnostics.csv", cfar_rows)
    write_csv(out / "angle_diagnostics.csv", angle_rows)
    (out / "diagnostic_provenance.json").write_text(json.dumps({
        "stage2_dir": str(stage2), "production_xml": str(xml), "cfar_type": "GO", "configured_pfa": pfa,
        "guard_half_width": geometry.guard_half_width, "background_thickness": geometry.background_thickness,
        "alpha_ring_cell_count": geometry.code_total_background_cells, "alpha": alpha,
        "power_maps": {str(beam): str(path) for beam, path in power_paths.items()},
        "cfar_axis_meta": {str(beam): axis_meta_by_beam.get(beam, {}) for beam in power_paths},
        "detection_csv": str(det_path), "truth_period_id": args.period_id,
        "diagnostic_result_id": args.diagnostic_result_id,
        "phase_scnr_fields": "暂不可从当前生产输出逐目标直接测得；由跨 seed phase_error 方差在测角 sweep 汇总计算",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.cleanup_cfar_binary:
        for path in [*power_paths.values(), *hit_paths.values()]:
            if path.exists():
                path.unlink()
    print(f"[PASS] 提取 {len(target_rows)} 个目标的 GO-CFAR 最小诊断：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
