#!/usr/bin/env python3
"""汇总 compact-statistical S+C+N 的 3--5 seed 输出-SCNR 验证。

这个脚本只读取 ``16_run_standard_output_snr_mc.py`` 已经写出的 CSV、JSON
和保留的 XML 审计文件，不读取 raw BIN、不重新拟合 output-SCNR 轴，也不把
任何 evaluation 命中率写回生产配置。每个 seed 的 level_index 是合并键，主横轴
是同一生产物理 CUT 的 CUT-equivalent output SCNR；多 seed 汇总时先把 dB 还原为
CUT 信号/背景功率比，再在有定义的 seed 间求算术均值，避免直接平均 dB 造成
非单调坐标。旧的固定 3x3 支撑总 SCNR 同时保留为 ``support_output_scnr_db``
诊断列。exact CUT 与 ``+/-2`` 单元分辨率事件明确分开，避免把主瓣落到相邻
Doppler 行误解释成 GO 门限命中。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
import sys

sys.path.insert(0, str(SCRIPT_DIR))
from scnr_eval_lib import read_csv, sha256_file, wilson_interval, write_csv  # noqa: E402


ANGLES = (0.0, 30.0, 45.0)
COLORS = {0.0: "#1f77b4", 30.0: "#d98c00", 45.0: "#8c564b"}
LABELS = {0.0: "0°中心", 30.0: "30°中心", 45.0: "45°中心"}
FONT = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
if FONT.is_file():
    font_manager.fontManager.addfont(str(FONT))
    plt.rcParams["font.family"] = "Noto Sans CJK JP"
plt.rcParams["axes.unicode_minus"] = False


def finite(value: object) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def integer(value: object, default: int = 0) -> int:
    number = finite(value)
    return int(round(number)) if math.isfinite(number) else default


def fmt(value: object, digits: int = 3) -> str:
    number = finite(value)
    return f"{number:.{digits}f}" if math.isfinite(number) else "—"


def db_to_ratio(value: object) -> float:
    """把 output-SCNR dB 转为线性功率比；非有限值保持缺失。"""
    number = finite(value)
    return 10.0 ** (number / 10.0) if math.isfinite(number) else float("nan")


def ratio_to_db(value: object) -> float:
    number = finite(value)
    return 10.0 * math.log10(number) if math.isfinite(number) and number > 0.0 else float("nan")


def token(angle: float) -> str:
    return f"angle_{angle:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")


def load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def locate_angle(root: Path, angle: float) -> Path:
    direct = root / token(angle)
    if (direct / "standard_mc_curve_summary.csv").is_file():
        return direct
    matches = [path.parent for path in root.rglob("standard_mc_curve_summary.csv")
               if path.parent.name == token(angle)]
    if len(matches) != 1:
        raise FileNotFoundError(f"{root} 缺少唯一 {angle:g}° 汇总目录: {matches}")
    return matches[0]


def audit_seed(root: Path) -> dict[str, object]:
    """核查 strict/no-prior、四条 pipeline、XML truth 开关和 raw 清理。"""
    root = root.resolve()
    run = load_json(root / "run_manifest.json")
    angle_manifests: list[dict[str, object]] = []
    pipeline_files = sorted(root.rglob("pipeline_manifest.json"))
    active_truth = 0
    truth_paths: list[str] = []
    raw_remaining = sorted(str(path) for path in root.rglob("*.bin") if path.is_file())
    raw_flags_ok = True
    for path in pipeline_files:
        item = load_json(path)
        raw_flags_ok = raw_flags_ok and bool(item.get("raw_removed")) and bool(item.get("algorithm_output_bin_removed"))
        xml_path = Path(str(item.get("pipe_xml", "")))
        if xml_path.is_file():
            xml = xml_path.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"<pc_peak_scene_truth>(.*?)</pc_peak_scene_truth>", xml, flags=re.S)
            if m and m.group(1).strip():
                active_truth += 1
                truth_paths.append(str(xml_path))
            m = re.search(r"<debug_pc_peak>(.*?)</debug_pc_peak>", xml, flags=re.S)
            if m and m.group(1).strip() not in {"0", "0.0", "false", "False"}:
                active_truth += 1
                truth_paths.append(str(xml_path) + ":debug_pc_peak")
        else:
            raw_flags_ok = False
    for angle in ANGLES:
        angle_dir = locate_angle(root, angle)
        item = load_json(angle_dir / "manifest.json")
        angle_manifests.append(item)
    strict_ok = bool(angle_manifests) and all(bool(item.get("strict_online_no_prior")) for item in angle_manifests)
    background_ok = bool(angle_manifests) and all(item.get("background_profile") == "compact_statistical" for item in angle_manifests)
    pfa_ok = bool(angle_manifests) and all(abs(finite(item.get("pfa")) - 1e-6) < 1e-12 for item in angle_manifests)
    minpoints_ok = bool(angle_manifests) and all(integer(item.get("min_points"), -1) == 6 for item in angle_manifests)
    calibration_sanity = [
        {
            "angle_deg": finite(item.get("angle_deg")),
            "pass": bool(item.get("calibration_sanity_pass")),
            "slope": finite(item.get("calibration_slope")),
            "r2": finite(item.get("calibration_r2")),
            "max_abs_bias_db": finite(item.get("calibration_max_abs_bias_db")),
        }
        for item in angle_manifests
    ]
    model_ready = bool(calibration_sanity) and all(bool(item["pass"]) for item in calibration_sanity)
    calibration_failures = [item for item in calibration_sanity if not item["pass"]]
    pipeline_ok = len(pipeline_files) == 12 and raw_flags_ok and not raw_remaining
    status = "pass" if strict_ok and background_ok and pfa_ok and minpoints_ok and pipeline_ok and active_truth == 0 else "fail"
    return {
        "root": str(root), "status": status, "pipeline_count": len(pipeline_files),
        "truth_active_detector_count": active_truth, "diagnostic_truth_path_count": len(truth_paths),
        "diagnostic_truth_paths": truth_paths, "raw_bin_remaining": raw_remaining,
        "strict_online_no_prior": strict_ok, "background_profile_compact_statistical": background_ok,
        "pfa_1e-6": pfa_ok, "min_points_6": minpoints_ok,
        "pipeline_raw_cleanup_flags": raw_flags_ok,
        "calibration_sanity_pass": model_ready,
        "calibration_sanity_by_angle": calibration_sanity,
        "calibration_sanity_failures": calibration_failures,
        "model_ready_for_pooled_axis": model_ready,
        "seed_values": sorted({integer(item.get("seed"), -1) for item in angle_manifests}),
        "angles": [finite(item.get("angle_deg")) for item in angle_manifests],
        "run_manifest": str(root / "run_manifest.json"),
        "source_script": str(run.get("script", "")),
    }


def load_seed(root: Path, audit: dict[str, object]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    rows: list[dict[str, object]] = []
    screens: list[dict[str, object]] = []
    seed_values = audit.get("seed_values", [])
    seed_value = integer(seed_values[0], -1) if isinstance(seed_values, list) and seed_values else -1
    for angle in ANGLES:
        angle_dir = locate_angle(root, angle)
        curve = read_csv(angle_dir / "standard_mc_curve_summary.csv")
        screen_path = angle_dir / "standard_mc_screen_metrics.csv"
        screen = read_csv(screen_path) if screen_path.is_file() else []
        screen_by_level: dict[int, list[dict[str, object]]] = {}
        for item in screen:
            row = dict(item)
            row["angle_deg"] = angle
            row["seed"] = seed_value
            screens.append(row)
            screen_by_level.setdefault(integer(item.get("level_index"), -1), []).append(item)
        for item in curve:
            row = dict(item)
            row["angle_deg"] = angle
            row["seed"] = seed_value
            level = integer(item.get("level_index"), -1)
            groups = screen_by_level.get(level, [])
            support_ratios = [db_to_ratio(group.get("output_scnr_db")) for group in groups]
            support_ratios = [value for value in support_ratios if math.isfinite(value)]
            theory_rows = {
                integer(item.get("level_index"), -1): item
                for item in read_csv(angle_dir / "go_theory_vs_mc.csv")
            }
            cut_db = finite(theory_rows.get(level, {}).get("unit_cut_scnr_db"))
            cut_ratio = db_to_ratio(cut_db)
            # Keep the original dB mean as an audit value.  The primary
            # multi-seed axis is the arithmetic mean of the linear CUT ratios.
            # The level CUT ratio is an already retained paired power estimate,
            # not a hit-rate fit; missing estimates remain undefined.
            row["support_output_scnr_db_logmean"] = finite(item.get("output_scnr_db"))
            row["support_output_scnr_db_powermean"] = ratio_to_db(float(np.mean(support_ratios)) if support_ratios else float("nan"))
            row["support_output_axis_valid_group_count"] = len(support_ratios)
            row["support_output_axis_group_count"] = len(groups)
            row["output_scnr_db_logmean"] = cut_db
            row["output_scnr_db_powermean"] = cut_db if math.isfinite(cut_db) else float("nan")
            row["output_axis_valid_group_count"] = int(math.isfinite(cut_db))
            row["output_axis_group_count"] = len(groups)
            row["output_scnr_db"] = cut_db if math.isfinite(cut_db) else float("nan")
            row["output_scnr_cut_equiv_db"] = cut_db
            row["support_output_scnr_db"] = finite(item.get("output_scnr_db"))
            requested_ratio = db_to_ratio(item.get("requested_output_scnr_db"))
            row["requested_output_scnr_db_power"] = requested_ratio
            rows.append(row)
    if len(rows) != 18:
        raise RuntimeError(f"{root} 应有 3×6=18 曲线行，实际 {len(rows)}")
    return rows, screens


def aggregate(rows: list[dict[str, object]], screens: list[dict[str, object]], seed_count: int) -> list[dict[str, object]]:
    merged: dict[tuple[float, int], dict[str, object]] = {}
    for item in rows:
        angle = finite(item.get("angle_deg")); level = integer(item.get("level_index"), -1)
        if not math.isfinite(angle) or level < 0:
            continue
        key = (angle, level)
        out = merged.setdefault(key, {
            "angle_deg": angle, "level_index": level, "seed_count": 0,
            "requested_values": [], "output_values": [], "output_std_values": [],
            "angle_n": 0, "angle_sum": 0.0, "angle_sumsq": 0.0,
            "theory_iid": [], "theory_empirical": [], "theory_cut_values": [],
            "output_power_values": [], "requested_power_values": [],
            "output_logmean_values": [], "output_valid_group_counts": [], "output_group_counts": [],
            "support_output_power_values": [], "support_output_logmean_values": [],
            "support_output_valid_group_counts": [], "support_output_group_counts": [],
        })
        out["seed_count"] = int(out["seed_count"]) + 1
        out["requested_values"].append(finite(item.get("requested_output_scnr_db")))
        out["output_values"].append(finite(item.get("output_scnr_db")))
        out["output_std_values"].append(finite(item.get("output_scnr_db_std")))
        out["output_power_values"].append(db_to_ratio(item.get("output_scnr_db_powermean")))
        out["requested_power_values"].append(finite(item.get("requested_output_scnr_db_power")))
        out["output_logmean_values"].append(finite(item.get("output_scnr_db_logmean")))
        out["output_valid_group_counts"].append(integer(item.get("output_axis_valid_group_count"), 0))
        out["output_group_counts"].append(integer(item.get("output_axis_group_count"), 0))
        out["support_output_power_values"].append(db_to_ratio(item.get("support_output_scnr_db_powermean")))
        out["support_output_logmean_values"].append(finite(item.get("support_output_scnr_db_logmean")))
        out["support_output_valid_group_counts"].append(integer(item.get("support_output_axis_valid_group_count"), 0))
        out["support_output_group_counts"].append(integer(item.get("support_output_axis_group_count"), 0))
        # 曲线汇总中的 angle_rmse 是该 seed 档位的 pooled RMSE；保留平方和
        # 与样本数可避免把不同 seed 的 RMSE 直接平均。
        n_angle = integer(item.get("angle_matched"), 0)
        if n_angle <= 0:
            # 0/NaN 的档位在 period CSV 中没有匹配角误差，不能填零。
            n_angle = 0
        rmse = finite(item.get("angle_rmse_deg")); bias = finite(item.get("angle_bias_deg"))
        if n_angle and math.isfinite(rmse):
            out["angle_n"] += n_angle
            out["angle_sum"] += (bias * n_angle if math.isfinite(bias) else 0.0)
            out["angle_sumsq"] += rmse * rmse * n_angle
        # go_theory_vs_mc is joined below by a separate map; keep level rows here.
        for field in ("unit_exact", "unit_resolution", "cluster", "target", "track"):
            for suffix in ("hits", "trials"):
                value = finite(item.get(f"{field}_{suffix}"))
                if math.isfinite(value):
                    out[f"{field}_{suffix}"] = float(out.get(f"{field}_{suffix}", 0.0)) + value
    # Preserve each screen's target/track events as authoritative count inputs.
    screen_map: dict[tuple[float, int], list[dict[str, object]]] = {}
    for item in screens:
        key = (finite(item.get("angle_deg")), integer(item.get("level_index"), -1))
        screen_map.setdefault(key, []).append(item)
    result: list[dict[str, object]] = []
    for key, item in merged.items():
        out = dict(item)
        requested = np.asarray([x for x in out.pop("requested_values") if math.isfinite(x)], dtype=float)
        actual = np.asarray([x for x in out.pop("output_values") if math.isfinite(x)], dtype=float)
        out_std = np.asarray([x for x in out.pop("output_std_values") if math.isfinite(x)], dtype=float)
        output_power = np.asarray([x for x in out.pop("output_power_values") if math.isfinite(x)], dtype=float)
        requested_power = np.asarray([x for x in out.pop("requested_power_values") if math.isfinite(x)], dtype=float)
        output_logmean = np.asarray([x for x in out.pop("output_logmean_values") if math.isfinite(x)], dtype=float)
        valid_group_counts = np.asarray(out.pop("output_valid_group_counts"), dtype=int)
        group_counts = np.asarray(out.pop("output_group_counts"), dtype=int)
        support_output_power = np.asarray([x for x in out.pop("support_output_power_values") if math.isfinite(x)], dtype=float)
        support_output_logmean = np.asarray([x for x in out.pop("support_output_logmean_values") if math.isfinite(x)], dtype=float)
        support_valid_group_counts = np.asarray(out.pop("support_output_valid_group_counts"), dtype=int)
        support_group_counts = np.asarray(out.pop("support_output_group_counts"), dtype=int)
        out["requested_output_scnr_db_logmean"] = float(np.mean(requested)) if requested.size else float("nan")
        out["requested_output_scnr_db"] = ratio_to_db(float(np.mean(requested_power))) if requested_power.size else float("nan")
        out["output_scnr_db_logmean"] = float(np.mean(output_logmean)) if output_logmean.size else float("nan")
        out["output_scnr_db"] = ratio_to_db(float(np.mean(output_power))) if output_power.size else float("nan")
        out["output_scnr_cut_equiv_db"] = out["output_scnr_db"]
        out["output_axis_valid_seed_count"] = int(output_power.size)
        out["output_axis_total_seed_count"] = seed_count
        out["output_axis_valid_seed_fraction"] = float(output_power.size / seed_count) if seed_count else float("nan")
        out["output_axis_valid_group_count"] = int(np.sum(valid_group_counts)) if valid_group_counts.size else 0
        out["output_axis_group_count"] = int(np.sum(group_counts)) if group_counts.size else 0
        out["output_scnr_db_std"] = float(np.std(actual, ddof=1)) if actual.size > 1 else 0.0
        out["output_scnr_within_seed_std_mean_db"] = float(np.mean(out_std)) if out_std.size else float("nan")
        out["support_output_scnr_db"] = ratio_to_db(float(np.mean(support_output_power))) if support_output_power.size else float("nan")
        out["support_output_scnr_db_logmean"] = float(np.mean(support_output_logmean)) if support_output_logmean.size else float("nan")
        out["support_output_axis_valid_seed_count"] = int(support_output_power.size)
        out["support_output_axis_valid_group_count"] = int(np.sum(support_valid_group_counts)) if support_valid_group_counts.size else 0
        out["support_output_axis_group_count"] = int(np.sum(support_group_counts)) if support_group_counts.size else 0
        n_angle = integer(out.get("angle_n"), 0)
        out["angle_rmse_deg"] = math.sqrt(float(out.get("angle_sumsq", 0.0)) / n_angle) if n_angle else float("nan")
        out["angle_bias_deg"] = float(out.get("angle_sum", 0.0)) / n_angle if n_angle else float("nan")
        for field in ("unit_exact", "unit_resolution", "cluster", "target", "track"):
            hits = float(out.get(f"{field}_hits", 0.0)); trials = float(out.get(f"{field}_trials", 0.0))
            out[f"{field}_pd"] = hits / trials if trials > 0 else float("nan")
            lo, hi = wilson_interval(int(round(hits)), int(round(trials))) if trials > 0 else (float("nan"), float("nan"))
            out[f"{field}_wilson_low"] = lo; out[f"{field}_wilson_high"] = hi
        # The production target funnel has no independent selector/relocation/
        # match flags in local-test diagnostics.  This is the observable combined
        # post-cluster factor, not three copied estimates.
        cluster_hits = float(out.get("cluster_hits", 0.0)); target_hits = float(out.get("target_hits", 0.0))
        out["postcluster_combined_retention"] = target_hits / cluster_hits if cluster_hits > 0 else float("nan")
        dep = dependency(screen_map.get(key, []))
        out.update(dep)
        result.append(out)
    result.sort(key=lambda x: (float(x["angle_deg"]), int(x["level_index"])))
    if len(result) != 18 or any(integer(x.get("seed_count"), 0) != seed_count for x in result):
        raise RuntimeError(f"合并结果应为 18 行且每行 {seed_count} seed，实际 {len(result)}")
    return result


def dependency(items: list[dict[str, object]]) -> dict[str, object]:
    sequences: list[list[int]] = []
    for item in items:
        sequences.append([integer(item.get(f"screen{i}_hit"), 0) for i in (1, 2, 3)])
    flat = [value for seq in sequences for value in seq]
    p_screen = float(np.mean(flat)) if flat else float("nan")
    pairs = [(seq[i - 1], seq[i]) for seq in sequences for i in (1, 2)]
    prev1 = sum(1 for a, _ in pairs if a == 1); prev0 = sum(1 for a, _ in pairs if a == 0)
    p11 = sum(1 for a, b in pairs if a == 1 and b == 1) / prev1 if prev1 else float("nan")
    p10 = sum(1 for a, b in pairs if a == 0 and b == 1) / prev0 if prev0 else float("nan")
    if len(pairs) >= 2:
        a = np.asarray([x[0] for x in pairs], dtype=float); b = np.asarray([x[1] for x in pairs], dtype=float)
        corr = float(np.corrcoef(a, b)[0, 1]) if np.std(a) > 0 and np.std(b) > 0 else float("nan")
    else:
        corr = float("nan")
    if sequences:
        p12 = float(np.mean([seq[0] * seq[1] for seq in sequences]))
        p13 = float(np.mean([seq[0] * seq[2] for seq in sequences]))
        p23 = float(np.mean([seq[1] * seq[2] for seq in sequences]))
        p123 = float(np.mean([seq[0] * seq[1] * seq[2] for seq in sequences]))
        track_ie = p12 + p13 + p23 - 2.0 * p123
        track_direct = float(np.mean([integer(item.get("track_2of3_hit"), 0) for item in items]))
    else:
        p12 = p13 = p23 = p123 = track_ie = track_direct = float("nan")
    track_ind = 3.0 * p_screen * p_screen - 2.0 * p_screen ** 3 if math.isfinite(p_screen) else float("nan")
    return {"screen_count": len(items), "screen_pd": p_screen, "p_dt1_given_dt0_1": p11,
            "p_dt1_given_dt0_0": p10, "screen_pair_correlation": corr,
            "p12": p12, "p13": p13, "p23": p23, "p123": p123,
            "track_pd_direct": track_direct, "track_pd_inclusion_exclusion": track_ie,
            "track_pd_independent": track_ind}


def theory_map(seed_root: Path) -> dict[tuple[float, int], dict[str, float]]:
    result: dict[tuple[float, int], dict[str, float]] = {}
    for angle in ANGLES:
        path = locate_angle(seed_root, angle) / "go_theory_vs_mc.csv"
        if not path.is_file():
            continue
        for item in read_csv(path):
            key = (angle, integer(item.get("level_index"), -1))
            support_value = finite(item.get("support_output_scnr_db"))
            if not math.isfinite(support_value):
                support_value = finite(item.get("output_scnr_db"))
            result[key] = {"theory_output_scnr_db": finite(item.get("unit_cut_scnr_db")),
                           "theory_support_output_scnr_db": support_value,
                           "theory_unit_pd_iid_go": finite(item.get("unit_pd_iid_go")),
                           "theory_unit_pd_empirical_n_only": finite(item.get("unit_pd_empirical_n_only")),
                           "theory_unit_cut_scnr_db": finite(item.get("unit_cut_scnr_db")),
                           "go_alpha": finite(item.get("alpha"))}
    return result


def add_theory(rows: list[dict[str, object]], seed_roots: list[Path]) -> None:
    maps = [theory_map(root) for root in seed_roots]
    for row in rows:
        key = (float(row["angle_deg"]), integer(row["level_index"], -1))
        values = [mapping[key] for mapping in maps if key in mapping]
        for field in ("theory_output_scnr_db", "theory_unit_pd_iid_go", "theory_unit_pd_empirical_n_only", "theory_unit_cut_scnr_db", "go_alpha"):
            finite_values = [item[field] for item in values if math.isfinite(item[field])]
            row[field] = float(np.mean(finite_values)) if finite_values else float("nan")


def plot(rows: list[dict[str, object]], field: str, ylabel: str, output: Path,
         *, bounds: tuple[float, float] | None = (0.0, 1.05), theory: str | None = None) -> None:
    fig, ax = plt.subplots(figsize=(8.8, 5.4))
    for angle in ANGLES:
        selected = sorted([r for r in rows if abs(float(r["angle_deg"]) - angle) < 1e-6], key=lambda r: float(r["output_scnr_db"]))
        if not selected:
            continue
        x = np.asarray([finite(r.get("output_scnr_db")) for r in selected])
        y = np.asarray([finite(r.get(field)) for r in selected])
        interval_prefix = field[:-3] if field.endswith("_pd") else field
        lo = np.asarray([finite(r.get(f"{interval_prefix}_wilson_low")) for r in selected])
        hi = np.asarray([finite(r.get(f"{interval_prefix}_wilson_high")) for r in selected])
        good = np.isfinite(x) & np.isfinite(y)
        color = COLORS[angle]
        ax.plot(x[good], y[good], "o-", color=color, markerfacecolor="white", label=f"{LABELS[angle]}（{int(selected[0]['seed_count'])} seed）")
        if field != "angle_rmse_deg" and np.any(good):
            low = np.where(np.isfinite(lo[good]), lo[good], y[good]); high = np.where(np.isfinite(hi[good]), hi[good], y[good])
            ax.fill_between(x[good], low, high, color=color, alpha=0.13, linewidth=0)
        if theory:
            ty = np.asarray([finite(r.get(theory)) for r in selected])
            mask = np.isfinite(x) & np.isfinite(ty)
            if np.any(mask):
                ax.plot(x[mask], ty[mask], "--", color=color, alpha=0.65, linewidth=1.3, label=f"{LABELS[angle]}理论")
    ax.set_xlabel("CUT-equivalent 输出 SCNR（dB；生产物理 CUT）")
    ax.set_ylabel(ylabel)
    ax.grid(True, color="#d9dde3", alpha=0.7)
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    if bounds is not None: ax.set_ylim(*bounds)
    ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(output, dpi=220); fig.savefig(output.with_suffix(".pdf")); plt.close(fig)


def table(headers: Iterable[str], data: Iterable[Iterable[object]]) -> str:
    headers = list(headers)
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---:" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(str(x) for x in row) + " |" for row in data)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-root", action="append", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output-pdf", type=Path, required=True)
    parser.add_argument("--output-html", type=Path, required=True)
    args = parser.parse_args()
    if not (3 <= len(args.seed_root) <= 5):
        raise SystemExit("本验证要求 3--5 个独立 seed")
    roots = [path.resolve() for path in args.seed_root]
    audits = [audit_seed(root) for root in roots]
    if any(item["status"] != "pass" for item in audits):
        raise SystemExit("至少一个 seed 未通过 strict/no-prior、raw 清理或 S+C+N 审计；详见 audit")
    seed_values = [tuple(item.get("seed_values", [])) for item in audits]
    if len(set(seed_values)) != len(seed_values):
        raise SystemExit(f"seed 不独立或缺失: {seed_values}")
    all_rows: list[dict[str, object]] = []; all_screens: list[dict[str, object]] = []
    for root, audit in zip(roots, audits):
        rows, screens = load_seed(root, audit); all_rows.extend(rows); all_screens.extend(screens)
    merged = aggregate(all_rows, all_screens, len(roots)); add_theory(merged, roots)
    out = args.output_dir.resolve(); out.mkdir(parents=True, exist_ok=True)
    fields = ["angle_deg", "level_index", "seed_count", "requested_output_scnr_db", "requested_output_scnr_db_logmean",
              "output_scnr_db", "output_scnr_cut_equiv_db", "output_scnr_db_logmean", "output_axis_valid_seed_count", "output_axis_total_seed_count",
              "output_axis_valid_seed_fraction", "output_axis_valid_group_count", "output_axis_group_count", "output_scnr_db_std",
              "support_output_scnr_db", "support_output_scnr_db_logmean", "support_output_axis_valid_seed_count", "support_output_axis_valid_group_count", "support_output_axis_group_count",
              "unit_exact_hits", "unit_exact_trials", "unit_exact_pd", "unit_exact_wilson_low", "unit_exact_wilson_high",
              "unit_resolution_hits", "unit_resolution_trials", "unit_resolution_pd", "unit_resolution_wilson_low", "unit_resolution_wilson_high",
              "cluster_hits", "cluster_trials", "cluster_pd", "cluster_wilson_low", "cluster_wilson_high",
              "target_hits", "target_trials", "target_pd", "target_wilson_low", "target_wilson_high",
              "postcluster_combined_retention", "track_hits", "track_trials", "track_pd", "track_wilson_low", "track_wilson_high",
              "screen_count", "screen_pd", "p_dt1_given_dt0_1", "p_dt1_given_dt0_0", "screen_pair_correlation",
              "p12", "p13", "p23", "p123", "track_pd_direct", "track_pd_inclusion_exclusion", "track_pd_independent",
              "angle_n", "angle_rmse_deg", "angle_bias_deg", "theory_output_scnr_db", "theory_unit_pd_iid_go",
              "theory_unit_pd_empirical_n_only", "theory_unit_cut_scnr_db", "go_alpha"]
    write_csv(out / "scn_multiseed_summary.csv", [{field: row.get(field, "") for field in fields} for row in merged])
    write_csv(out / "scn_information_leakage_audit.csv", audits)
    plot(merged, "unit_exact_pd", "单元级 Pd（exact CUT）", out / "output_scnr_vs_unit_pd_exact_scn.png", theory="theory_unit_pd_empirical_n_only")
    plot(merged, "unit_resolution_pd", "单元级 Pd（±2 分辨率单元）", out / "output_scnr_vs_unit_pd_resolution_scn.png", theory="theory_unit_pd_empirical_n_only")
    plot(merged, "cluster_pd", "聚类事件 Pd", out / "output_scnr_vs_cluster_pd_scn.png")
    plot(merged, "target_pd", "目标级 truth Pd", out / "output_scnr_vs_target_pd_scn.png")
    plot(merged, "track_pd", "三屏选二航迹 Pd", out / "output_scnr_vs_track_pd_scn.png")
    plot(merged, "angle_rmse_deg", "测角 RMSE（deg）", out / "output_scnr_vs_angle_rmse_scn.png", bounds=None)

    summary_rows = []
    for angle in ANGLES:
        selected = [r for r in merged if abs(float(r["angle_deg"]) - angle) < 1e-6]
        crossing = next((float(r["output_scnr_db"]) for r in selected if finite(r.get("target_pd")) >= 0.9), float("nan"))
        rmses = [finite(r.get("angle_rmse_deg")) for r in selected if math.isfinite(finite(r.get("angle_rmse_deg")))]
        summary_rows.append([f"{angle:g}°", len(selected), int(sum(integer(r.get("target_trials")) for r in selected)), fmt(crossing, 2), fmt(np.mean([finite(r.get("target_pd")) for r in selected]), 3), fmt(np.mean(rmses) if rmses else float("nan"), 4)])
    detail_rows = []
    for r in merged:
        detail_rows.append([f"{float(r['angle_deg']):g}°", integer(r.get("level_index")), fmt(r.get("output_scnr_db"), 2), f"{integer(r.get('output_axis_valid_seed_count'))}/{integer(r.get('output_axis_total_seed_count'))}", f"{integer(r.get('unit_exact_hits'))}/{integer(r.get('unit_exact_trials'))}", fmt(r.get("unit_exact_pd")), f"{integer(r.get('unit_resolution_hits'))}/{integer(r.get('unit_resolution_trials'))}", fmt(r.get("unit_resolution_pd")), f"{integer(r.get('cluster_hits'))}/{integer(r.get('cluster_trials'))}", fmt(r.get("cluster_pd")), f"{integer(r.get('target_hits'))}/{integer(r.get('target_trials'))}", fmt(r.get("target_pd")), f"{integer(r.get('track_hits'))}/{integer(r.get('track_trials'))}", fmt(r.get("track_pd")), fmt(r.get("angle_rmse_deg"), 3)])
    theory_detail_rows = []
    for r in merged:
        theory_value = finite(r.get("theory_unit_pd_empirical_n_only"))
        resolution_value = finite(r.get("unit_resolution_pd"))
        theory_detail_rows.append([
            f"{finite(r.get('angle_deg')):g}°", int(r.get("level_index", 0)),
            fmt(r.get("output_scnr_db"), 2), fmt(r.get("theory_unit_cut_scnr_db"), 2),
            fmt(theory_value), fmt(r.get("unit_exact_pd")), fmt(resolution_value),
            fmt(resolution_value - theory_value if math.isfinite(resolution_value) and math.isfinite(theory_value) else float("nan")),
        ])
    calibration_rows = []
    for audit in audits:
        root_name = Path(str(audit["root"])).name
        for item in audit.get("calibration_sanity_by_angle", []):
            calibration_rows.append([
                root_name,
                f"{finite(item.get('angle_deg')):g}°",
                "通过" if bool(item.get("pass")) else "未通过",
                fmt(item.get("slope"), 4),
                fmt(item.get("r2"), 4),
                fmt(item.get("max_abs_bias_db"), 3),
            ])
    axis_deltas = [
        (abs(finite(r.get("output_scnr_db")) - finite(r.get("output_scnr_db_logmean"))), r)
        for r in merged
        if math.isfinite(finite(r.get("output_scnr_db"))) and math.isfinite(finite(r.get("output_scnr_db_logmean")))
    ]
    axis_note = ""
    if axis_deltas:
        _, largest_axis_delta = max(axis_deltas, key=lambda item: item[0])
        axis_note = (
            f"本批功率域汇总相对原始 dB 均值的最大坐标修正为 {fmt(largest_axis_delta.get('output_scnr_db_logmean'), 2)}→"
            f"{fmt(largest_axis_delta.get('output_scnr_db'), 2)} dB（{finite(largest_axis_delta.get('angle_deg')):g}° level "
            f"{integer(largest_axis_delta.get('level_index'))}），仅由功率比聚合顺序造成，不涉及命中率或 detector 阈值。"
        )
    seed_count = len(roots)
    # The campaign intentionally mixes the earlier 2-group seeds with the
    # later 3-group seeds.  Derive the actual retained sample counts from the
    # screen CSVs instead of describing the run with a stale fixed number.
    seed_design: list[dict[str, int]] = []
    for root in roots:
        angle_dir = locate_angle(root, ANGLES[0])
        screen_rows = read_csv(angle_dir / "standard_mc_screen_metrics.csv")
        per_level = [sum(integer(row.get("level_index"), -1) == level for row in screen_rows)
                     for level in range(6)]
        period_count = sum(integer(row.get("period_count"), 0) for row in screen_rows)
        seed_design.append({"groups_per_level": per_level[0] if per_level else 0,
                            "screens_per_level": (per_level[0] * 3) if per_level else 0,
                            "period_count": period_count})
    pooled_groups_per_level = sum(item["groups_per_level"] for item in seed_design)
    pooled_screens_per_level = sum(item["screens_per_level"] for item in seed_design)
    pooled_track_events_per_level = pooled_groups_per_level
    design_counts = sorted({item["groups_per_level"] for item in seed_design})
    design_text = ", ".join(f"{count}个三屏组/档" for count in design_counts)
    period_counts = sorted({item["period_count"] for item in seed_design})
    period_text = "/".join(str(count) for count in period_counts)
    md = out / f"GMTI_S+C+N_{seed_count}seed_输出SCNR验证报告.md"
    sections = [
        f"# GMTI 真实 S+C+N：输出 SCNR—检测概率—测角精度 {seed_count} seed 验证",
        f"本补充报告合并 {seed_count} 次独立 seed 的 compact-statistical S+C+N 生产链结果。每个 seed 使用 0°、30°、45°单波位中心，各 6 个控制档位；本批设计包含 {design_text}，因此 pooled 每角度每档为 {pooled_groups_per_level} 个三屏组、{pooled_screens_per_level} 个 screen truth trial 和 {pooled_track_events_per_level} 个航迹事件。单个 seed 每角度保留 {period_text} 个 period（由其实际 trials-per-level 决定）。正式横轴是同一物理 CUT 上由 S+C+N 与 C+N 配对得到的 CUT-equivalent output SCNR，固定 3×3 支撑总量只作诊断列，`target_snr_db` 仅是控制变量。该规模用于模型验证，不能宣称最终 90% 门限。",
        "## 1. 参数、样本与数据来源\n\n" + table(["项目", "本批取值", "来源/含义"], [
            ["背景", "Rayleigh×lognormal 面杂波 + 热噪声 + 目标；240 scatterers", "`background_profile=compact_statistical`；离散强散射/线散射关闭"],
            ["GO-CFAR", "Pfa=1e-6，guard=4，background=16，alpha约13.4495103", "生产 XML 与 GO meta；四方向最大均值"],
            ["聚类", "min_points=6；3–5 点 +20 dB恢复", "生产规则直接运行 joint hit mask"],
            ["样本", f"{seed_count} seed × 3 角度 × 6 档；每 seed 设计为 {design_text}", f"pooled 每档 {pooled_screens_per_level} 个 unit/target screens、{pooled_track_events_per_level} 个 track events；period 数按 seed 实际配置保留"],
            ["主横轴", "实测 CUT-equivalent output SCNR（生产物理 CUT）", "S+C+N CUT 均值减 C+N CUT P0，再除以 CUT P0；不读取命中结果拟合"],
        ]) + "\n\noutput-SCNR 的每个 seed 曲线来自 `standard_mc_curve_summary.csv` 与同档 `go_theory_vs_mc.csv` 的 CUT 配对列；GO 理论来自同一 seed 的 `go_theory_vs_mc.csv`。多 seed pooled 横轴不再直接平均 dB，而是把各 seed/档位的 CUT 功率比转为线性后求均值再取 10log10；没有有限功率估计的 seed 记为无效并在逐档表中保留有效数，不按命中结果补值。旧的固定 3×3 支撑轴写入 `support_output_scnr_db` 供审计。",
        r"## 2. 为什么同时报告 exact 与 resolution 单元事件\n\n单一 CUT 理论对应固定物理真值单元 $H_{exact}=1\{P_{CUT}>\\alpha M\}$。生产二维 FFT/DBS 后，目标主瓣可能落在相邻 Doppler 行，因此另外定义不改变算法的分辨率事件 $H_{res}=1\{\\exists |\\Delta r|,|\\Delta d|\\le2: H(r+\\Delta d,c+\\Delta r)=1\}$。前者可与 GO $Q_1$ 曲线比较，后者是实际目标分辨率门的操作指标；二者不是同一随机变量，不能互相替代。",
        "## 3. 三层 GO 理论与实测\n\nGO 单元门限是 $T=\\alpha M$，$M=\\max(\\bar P_L,\\bar P_R,\\bar P_T,\\bar P_B)$。IID Gaussian 理论把 CUT 与训练窗归一化后代入 $P_d=Q_1(\\sqrt{2\\gamma},\\sqrt{2\\alpha M})$；真实 C+N 层则对每个离线训练窗的 $M/P_0$ 做经验平均。图中的虚线是 0° seed 的真实 C+N 经验理论参考，彩色实线是各角度的 exact 实测；`theory_unit_cut_scnr_db` 与主横轴相同，旧固定 support SCNR 写在 `support_output_scnr_db` 仅作审计。横轴采用 CUT 功率域 pooled output-SCNR；表中的“有效 seed/总 seed”显示该档有多少个独立背景 realization 给出了有限 CUT 功率估计。理论没有偷偷使用 S+C+N 命中率，也没有把目标重新定位到最高命中格。\n\n![单元 exact](output_scnr_vs_unit_pd_exact_scn.png)\n\n![单元 resolution](output_scnr_vs_unit_pd_resolution_scn.png)\n\nexact 在 0°高档可能下降而 resolution 仍为 1，说明是目标主瓣/production axis 偏移而非 GO 门限变宽；这正是本批把两个事件拆开的原因。" + ("\n\n" + axis_note if axis_note else ""),
        f"### 3.1 逐档理论对照\n\n" + table(["角度", "level", "CUT-equivalent output SCNR", "单 CUT SCNR", "C+N经验理论", "exact实测", "±2-bin实测", "resolution−理论"], theory_detail_rows) + f"\n\n理论列来自每个 seed 的 `go_theory_vs_mc.csv` 后按角度/档位平均；主横轴使用对应 `unit_cut_scnr_db` 的 CUT-equivalent 输出 SCNR，与单 CUT 理论同口径。旧固定 3×3 output SCNR 仅在诊断列保存。±2-bin 事件在 30°和45°的大部分过渡档与 C+N 经验理论接近；0°差异主要由 Doppler 主瓣偏移和本批每档仅 {pooled_screens_per_level} 个 unit 样本造成。",
        "## 4. 从 unit 到 cluster、target、track 的生产漏斗\n\n聚类事件直接由每周期 joint GO hit mask、连通性、min_points=6 及 3–5 点 +20 dB分支计算；目标事件是同帧、有界 Doppler/range 的 production truth-match，并要求 cluster gate 通过的 `target_event`；不把 unit Pd 假设为独立来代替目标级结果。可观测的目标级分解为 $P_{target}=P_{cluster}\\times P(\\text{post-cluster selector/relocation/match}\\mid cluster)$。当前 local-test 审计没有独立 selector、relocation、match 拒绝标志，因此 CSV 只给 `postcluster_combined_retention`，不会把同一最终命中复制成三个条件率。\n\n![cluster](output_scnr_vs_cluster_pd_scn.png)\n\n![target](output_scnr_vs_target_pd_scn.png)\n\n![track](output_scnr_vs_track_pd_scn.png)\n\n![angle](output_scnr_vs_angle_rmse_scn.png)",
        "## 5. 航迹三屏相关性\n\n主航迹事件冻结为同一 truth 轨迹三屏至少命中两屏。对每档直接计数 `track_2of3_hit`；同时从三屏联合 mask 计算 $P_{track}=P_{12}+P_{13}+P_{23}-2P_{123}$，并列出 $P(D_t=1\\mid D_{t-1}=1)$、$P(D_t=1\\mid D_{t-1}=0)$ 和相邻屏相关系数。若屏间独立且边际命中率为 $p$，仅作为 baseline 报告 $3p^2-2p^3$；主值不套该近似。",
        "## 6. 汇总结果\n\n" + table(["角度", "档位数", "target screens", "首个 target Pd>=0.9 (dB)", "平均 target Pd", "平均 RMSE (deg)"], summary_rows) + "\n\n逐档值（所有分子/分母均来自真实 production CSV，未平滑、未按结果删点；SCNR有效seed/总seed用于识别坐标缺失）：\n\n" + table(["角度", "level", "output SCNR(dB)", "SCNR有效seed/总seed", "exact", "Pd", "resolution", "Pd", "cluster", "Pd", "target", "Pd", "track", "Pd", "RMSE"], detail_rows),
        "## 7. 信息泄露与清理审计\n\n" + table(["seed 根目录", "status", "pipeline 数", "在线 truth", "诊断 truth", "剩余 raw BIN"], [[Path(str(a["root"])).name, a["status"], a["pipeline_count"], a["truth_active_detector_count"], a["diagnostic_truth_path_count"], len(a["raw_bin_remaining"])] for a in audits]) + "\n\n审计规则：每个 seed 必须有 12 条 pipeline（reference S-only、N-only、S+N、S+C+N 对应的四条链×3角度），`strict_online_no_prior=true`，生产 XML 的 `pc_peak_scene_truth` 为空且 `debug_pc_peak=0`，GO/TrackManager 审计成功后 `raw_removed=true`、`algorithm_output_bin_removed=true` 且根目录不再有 BIN。truth 只用于离线固定支撑、命中评分、truth-match、RMSE 和三屏标签，不进入 detector。\n\n输出轴校准另行按 seed/角度审计如下；`model_ready_for_pooled_axis` 要求该 seed 的三个角度均通过，未通过只说明该背景 realization 不适合直接作为稳定 pooled 轴，不删除样本：\n\n" + table(["seed", "角度", "calibration sanity", "slope", "R²", "最大偏差(dB)"], calibration_rows),
        "## 7.1 最短复现入口\n\n每个 seed 使用同一命令模板，仅替换 `--seed`、`--output-root` 和 trials-per-level；本批旧 seed（20261031–20261033）使用 2，新 seed（20261034–20261035）使用 3（GPU 运行）：\n\n```bash\nCUDA_VISIBLE_DEVICES=0 python3 -u scripts/gmti_scnr_eval/16_run_standard_output_snr_mc.py \\\n  --output-root outputs/gmti_scnr_eval/scn_validation_20260830/seed_<SEED>_formal \\\n  --angles 0,30,45 --seed <SEED> \\\n  --input-snr-grid=-40,-35,-30,-25,-20,-15 --trials-per-level <2或3> \\\n  --theory-samples 10000 --build-dir build --min-points 6 \\\n  --small-min-points 3 --small-peak-db 20.0 --strict-online-no-prior \\\n  --background-profile compact_statistical\n```\n\n汇总命令见 `37_build_scn_multiseed_validation.py`；它只读取保留 CSV/JSON/XML，不重新打开 BIN。",
        f"## 8. 限制与下一步\n\n本批 {seed_count} seed、pooled 每档 {pooled_screens_per_level} 个 screen truth trial 的 Wilson 区间仍较宽；审计表中的 calibration sanity 按 seed/角度逐项列出，部分 seed/角度未通过时，只表示该背景 realization 的 output→CUT 线性残差需单独解释，不能删除该 seed，也不能把 pooled output-SCNR 轴直接当作稳定标尺。`model_ready_for_pooled_axis=false` 只表示该根不满足“已校准稳定轴”的附加条件；它仍保留在本次 pooled 统计，不按命中率删除，也不影响其严格无先验、清理和链路审计结果。面积杂波采用紧凑统计模型，尚未覆盖全场景强散射/线散射。目标 selector/relocation/match 尚未在 local-test 中分开导出。本报告不把当前结果写成最终门限。",
    ]
    # Section 2 is a raw Python string so LaTeX braces/backslashes are not
    # interpreted by Python; restore its literal Markdown line breaks before
    # handing the document to pandoc.
    md.write_text("\n\n".join(sections).replace("\\n", "\n").replace("\\\\", "\\") + "\n", encoding="utf-8")
    pdf = args.output_pdf.resolve(); html = args.output_html.resolve(); pdf.parent.mkdir(parents=True, exist_ok=True); html.parent.mkdir(parents=True, exist_ok=True)
    pandoc = shutil.which("pandoc"); xelatex = shutil.which("xelatex")
    if pandoc and xelatex:
        result = subprocess.run([pandoc, str(md), "--resource-path", str(out), "--pdf-engine", xelatex, "-V", "CJKmainfont=Noto Sans CJK SC", "--metadata", f"title=GMTI S+C+N {seed_count}seed 输出SCNR验证", "-o", str(pdf)], cwd=ROOT, check=False)
        if result.returncode:
            raise SystemExit(f"PDF 生成失败，退出码={result.returncode}")
    if pandoc:
        result = subprocess.run([pandoc, str(md), "--standalone", "--resource-path", str(out), "--self-contained", "--mathml", "--metadata", f"title=GMTI S+C+N {seed_count}seed 输出SCNR验证", "-o", str(html)], cwd=ROOT, check=False)
        if result.returncode:
            raise SystemExit(f"HTML 生成失败，退出码={result.returncode}")
    manifest = {"status": "pass", "seed_count": len(roots), "angles": list(ANGLES), "levels": 6,
                "roots": [str(x) for x in roots], "audits": audits, "summary_csv": str(out / "scn_multiseed_summary.csv"),
                "model_ready_for_pooled_axis": all(bool(a.get("model_ready_for_pooled_axis")) for a in audits),
                "calibration_sanity_failures": [a for a in audits if not bool(a.get("model_ready_for_pooled_axis"))],
                "background_profile": "compact_statistical", "min_points": 6, "pfa": 1e-6,
                "output_axis": "measured S+C+N physical CUT increment over C+N CUT P0 (CUT-equivalent output SCNR); fixed 3x3 support retained as diagnostic",
                "output_axis_aggregation": "arithmetic mean of finite linear CUT power ratios across seed; original dB value retained as output_scnr_db_logmean",
                "output_axis_invalid_values": "non-finite per-seed CUT estimates remain excluded from the axis and are counted in output_axis_valid_seed_count; no hit-rate imputation",
                "theory_axis_note": "GO theory and plots use the retained unit_cut_scnr_db as the primary output axis; support output SCNR is diagnostic",
                "target_factor_note": "selector/relocation/match not separately exposed; combined post-cluster retention only",
                "raw_bin_remaining": sum(len(a["raw_bin_remaining"]) for a in audits),
                "reproduction_command": "python3 -u scripts/gmti_scnr_eval/16_run_standard_output_snr_mc.py --angles 0,30,45 --input-snr-grid=-40,-35,-30,-25,-20,-15 --trials-per-level 2 --theory-samples 10000 --min-points 6 --small-min-points 3 --small-peak-db 20.0 --strict-online-no-prior --background-profile compact_statistical"}
    (out / "scn_multiseed_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PASS] S+C+N 汇总 CSV: {out / 'scn_multiseed_summary.csv'}")
    print(f"[PASS] S+C+N 报告 PDF: {pdf}")
    print(f"[PASS] S+C+N 报告 HTML: {html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
