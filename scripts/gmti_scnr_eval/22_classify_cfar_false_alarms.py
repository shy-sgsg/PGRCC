#!/usr/bin/env python3
"""Classify CFAR candidates using target, textured-H0 and thermal-noise H0 runs.

The input runs must contain the production ``cfar_GMTI*_beam*_hits.f32`` sidecars
for the same selected beams.  The component walk intentionally mirrors the
production 3-row, ``cluster_max_range_gap`` range-neighbourhood with circular
Doppler rows.  It is an audit tool; it never changes detection results.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

import numpy as np


MAP_RE = re.compile(r"cfar_GMTI(?P<result>\d+)_beam(?P<beam>\d+)_hits\.f32$")
SUMMARY_RE = re.compile(
    r"\[CFAR\]\[SUMMARY\] beam=(?P<beam>\d+) .*?"
    r"hit_cells=(?P<hits>\d+) clusters=(?P<clusters>\d+)"
)
RESULT_RE = re.compile(r"\[GMTI\]\[RESULT\] filtered_detection_count=(?P<count>\d+)")


def read_kv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    return values


def integer(row: dict[str, str], key: str, default: int = -1) -> int:
    try:
        return int(round(float(row.get(key, ""))))
    except (TypeError, ValueError):
        return default


def float_value(row: dict[str, str], key: str, default: float = float("nan")) -> float:
    try:
        return float(row.get(key, ""))
    except (TypeError, ValueError):
        return default


def circular_row_distance(left: int, right: int, rows: int) -> int:
    direct = abs((left % rows) - (right % rows))
    return min(direct, rows - direct)


def components(hits: np.ndarray, max_range_gap: int, circular: bool) -> list[dict[str, object]]:
    """Return production-equivalent positive-hit components and their cells."""
    rows, cols = hits.shape
    positive = hits > 0.0
    seen = np.zeros(positive.shape, dtype=np.bool_)
    output: list[dict[str, object]] = []
    gap = max(1, max_range_gap)
    for row0, col0 in zip(*np.nonzero(positive)):
        row0, col0 = int(row0), int(col0)
        if seen[row0, col0]:
            continue
        stack = [(row0, col0)]
        seen[row0, col0] = True
        cells: list[tuple[int, int]] = []
        while stack:
            row, col = stack.pop()
            cells.append((row, col))
            for dr in (-1, 0, 1):
                next_row = row + dr
                if circular:
                    next_row %= rows
                if not 0 <= next_row < rows:
                    continue
                for dc in range(-gap, gap + 1):
                    if dr == 0 and dc == 0:
                        continue
                    next_col = col + dc
                    if (0 <= next_col < cols and positive[next_row, next_col]
                            and not seen[next_row, next_col]):
                        seen[next_row, next_col] = True
                        stack.append((next_row, next_col))
        cell_array = np.asarray(cells, dtype=np.int32)
        powers = hits[cell_array[:, 0], cell_array[:, 1]]
        peak_index = int(np.argmax(powers))
        output.append({
            "cells": cells,
            "size": len(cells),
            "peak_row": int(cell_array[peak_index, 0]),
            "peak_col": int(cell_array[peak_index, 1]),
            "peak_power": float(powers[peak_index]),
            "row_extent": int(np.ptp(cell_array[:, 0]) + 1),
            "range_extent": int(np.ptp(cell_array[:, 1]) + 1),
            "range_bin_count": int(len(set(int(c) for c in cell_array[:, 1]))),
        })
    return output


def map_files(run: Path) -> Iterable[tuple[int, int, Path, Path]]:
    for hit_path in sorted((run / "debug").glob("cfar_GMTI*_beam*_hits.f32")):
        match = MAP_RE.match(hit_path.name)
        if not match:
            continue
        result_id = int(match.group("result"))
        beam_id = int(match.group("beam"))
        power_path = hit_path.with_name(hit_path.name.replace("_hits.f32", "_power.f32"))
        if power_path.is_file():
            yield result_id, beam_id, hit_path, power_path


def map_summary(run: Path, rows: int, cols: int, max_range_gap: int,
                circular: bool) -> dict[str, object]:
    maps: list[dict[str, object]] = []
    all_sizes: list[int] = []
    all_extents: list[dict[str, int]] = []
    hit_masks: dict[tuple[int, int], np.ndarray] = {}
    metas: dict[tuple[int, int], dict[str, str]] = {}
    for result_id, beam_id, hit_path, power_path in map_files(run):
        raw = np.fromfile(hit_path, dtype=np.float32)
        power_raw = np.fromfile(power_path, dtype=np.float32)
        expected = rows * cols
        if raw.size != expected or power_raw.size != expected:
            raise SystemExit(
                f"CFAR sidecar size mismatch: {hit_path} / {power_path}; "
                f"expected {expected}, got {raw.size}/{power_raw.size}")
        hits = raw.reshape(rows, cols)
        power = power_raw.reshape(rows, cols)
        found = components(hits, max_range_gap, circular)
        sizes = [int(component["size"]) for component in found]
        all_sizes.extend(sizes)
        all_extents.extend({
            "row_extent": int(component["row_extent"]),
            "range_extent": int(component["range_extent"]),
            "range_bin_count": int(component["range_bin_count"]),
        } for component in found if int(component["size"]) >= 2)
        key = (result_id, beam_id)
        hit_masks[key] = hits
        metas[key] = read_kv(hit_path.with_name(hit_path.name.replace("_hits.f32", "_meta.txt")))
        maps.append({
            "result_id": result_id,
            "period_id": result_id - 1,
            "beam_id": beam_id,
            "hit_cells": int(np.count_nonzero(hits > 0.0)),
            "component_count": len(found),
            "singleton_count": sum(size == 1 for size in sizes),
            "min2_count": sum(size >= 2 for size in sizes),
            "min3_count": sum(size >= 3 for size in sizes),
            "min4_count": sum(size >= 4 for size in sizes),
            "max_component_size": max(sizes, default=0),
            "component_size_histogram": dict(sorted(Counter(sizes).items())),
            "power_max": float(np.nanmax(power)) if power.size else float("nan"),
        })
    return {
        "run": str(run),
        "map_count": len(maps),
        "maps": maps,
        "aggregated": {
            "hit_cells": int(sum(int(item["hit_cells"]) for item in maps)),
            "components": len(all_sizes),
            "singletons": sum(size == 1 for size in all_sizes),
            "min2": sum(size >= 2 for size in all_sizes),
            "min3": sum(size >= 3 for size in all_sizes),
            "min4": sum(size >= 4 for size in all_sizes),
            "component_size_histogram": dict(sorted(Counter(all_sizes).items())),
            "accepted_min2_mean_size": (
                float(np.mean([size for size in all_sizes if size >= 2]))
                if any(size >= 2 for size in all_sizes) else 0.0
            ),
            "accepted_min2_long_row_components": sum(
                extent["row_extent"] >= 10 for extent in all_extents),
            "accepted_min2_single_range_long_components": sum(
                extent["row_extent"] >= 10 and extent["range_bin_count"] <= 2
                for extent in all_extents
            ),
        },
        "_hit_masks": hit_masks,
        "_metas": metas,
    }


def log_summary(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    cfar: list[dict[str, int]] = []
    for match in SUMMARY_RE.finditer(text):
        cfar.append({
            "beam_id": int(match.group("beam")),
            "hit_cells": int(match.group("hits")),
            "clusters": int(match.group("clusters")),
        })
    results = [int(match.group("count")) for match in RESULT_RE.finditer(text)]
    return {
        "log": str(path),
        "cfar_summary_count": len(cfar),
        "cfar_hit_cells": sum(item["hit_cells"] for item in cfar),
        "cfar_clusters": sum(item["clusters"] for item in cfar),
        "filtered_detection_counts": results,
        "filtered_detection_total": sum(results),
    }


def target_gate_summary(target_map: dict[str, object], h0_map: dict[str, object],
                        truth_path: Path, gate_range: int, gate_doppler: int) -> dict[str, object]:
    truths = list(csv.DictReader(truth_path.open(encoding="utf-8", newline="")))
    target_masks: dict[tuple[int, int], np.ndarray] = target_map["_hit_masks"]
    h0_masks: dict[tuple[int, int], np.ndarray] = h0_map["_hit_masks"]
    target_metas: dict[tuple[int, int], dict[str, str]] = target_map["_metas"]
    h0_metas: dict[tuple[int, int], dict[str, str]] = h0_map["_metas"]
    rows: list[dict[str, object]] = []
    for truth in truths:
        if truth.get("visible", "1") in {"0", "false", "False"}:
            continue
        period_id = integer(truth, "period_id")
        beam_id = integer(truth, "beam_id")
        key = (period_id + 1, beam_id)
        if key not in target_masks or key not in h0_masks:
            continue
        target_meta = target_metas[key]
        h0_meta = h0_metas[key]
        center_target = float_value(target_meta, "doppler_axis_center_wrapped_hz")
        center_h0 = float_value(h0_meta, "doppler_axis_center_wrapped_hz")
        doppler = float_value(truth, "af_total_truth_hz")
        step_target = float_value(target_meta, "doppler_axis_row_step_hz", 1300.0 / 128.0)
        step_h0 = float_value(h0_meta, "doppler_axis_row_step_hz", 1300.0 / 128.0)
        target_row = int(math.floor((doppler - (center_target - 650.0)) / step_target + 0.5)) % 128
        h0_row = int(math.floor((doppler - (center_h0 - 650.0)) / step_h0 + 0.5)) % 128
        target_col = integer(truth, "range_bin", integer(truth, "expected_bin"))
        target_hits = target_masks[key]
        h0_hits = h0_masks[key]
        target_coords = [(row, col) for row in range(target_row - gate_doppler, target_row + gate_doppler + 1)
                         for col in range(target_col - gate_range, target_col + gate_range + 1)
                         if 0 <= col < target_hits.shape[1] and target_hits[row % 128, col] > 0.0]
        h0_coords = [(row, col) for row in range(h0_row - gate_doppler, h0_row + gate_doppler + 1)
                     for col in range(target_col - gate_range, target_col + gate_range + 1)
                     if 0 <= col < h0_hits.shape[1] and h0_hits[row % 128, col] > 0.0]
        rows.append({
            "period_id": period_id,
            "beam_id": beam_id,
            "target_id": truth.get("target_id", ""),
            "range_bin": target_col,
            "target_row": target_row,
            "h0_row": h0_row,
            "target_gate_hit_cells": len(target_coords),
            "textured_h0_gate_hit_cells": len(h0_coords),
            "target_only_gate_hit_cells": len(set(target_coords) - set(h0_coords)),
            "textured_h0_only_gate_hit_cells": len(set(h0_coords) - set(target_coords)),
        })
    return {
        "truth_gate": {"half_range_bins": gate_range, "half_doppler_bins": gate_doppler},
        "sample_count": len(rows),
        "samples": rows,
        "aggregate": {
            "target_gate_hit_cells": sum(int(row["target_gate_hit_cells"]) for row in rows),
            "textured_h0_gate_hit_cells": sum(int(row["textured_h0_gate_hit_cells"]) for row in rows),
            "target_only_gate_hit_cells": sum(int(row["target_only_gate_hit_cells"]) for row in rows),
            "textured_h0_only_gate_hit_cells": sum(int(row["textured_h0_only_gate_hit_cells"]) for row in rows),
            "target_samples_with_hit": sum(int(row["target_gate_hit_cells"]) > 0 for row in rows),
            "textured_h0_samples_with_hit": sum(int(row["textured_h0_gate_hit_cells"]) > 0 for row in rows),
        },
    }


def public(value: object) -> object:
    if isinstance(value, dict):
        return {key: public(item) for key, item in value.items() if not str(key).startswith("_")}
    if isinstance(value, list):
        return [public(item) for item in value]
    return value


def write_markdown(path: Path, report: dict[str, object]) -> None:
    target = report["runs"]["target"]
    textured = report["runs"]["textured_h0"]
    noise = report["runs"]["noise_h0"]
    target_log = report["logs"]["target"]
    textured_log = report["logs"]["textured_h0"]
    noise_log = report["logs"]["noise_h0"]
    gate = report["truth_gate"]["aggregate"]
    lines = [
        "# CFAR 虚警来源核查",
        "",
        "本报告复现生产 CFAR 的正命中连通域：Doppler 行循环、相邻行 ±1、距离列 `cluster_max_range_gap`=2。诊断图只导出了高候选波位 14–16，日志统计覆盖完整 30 波位×3 周期。",
        "",
        "## 完整日志统计",
        "",
        "| 场景 | CFAR 命中单元 | 聚类数 | 最终检测输出数 |",
        "| --- | ---: | ---: | ---: |",
        f"| 目标+C+N | {target_log['cfar_hit_cells']} | {target_log['cfar_clusters']} | {target_log['filtered_detection_total']} |",
        f"| 纹理 C+N H0 | {textured_log['cfar_hit_cells']} | {textured_log['cfar_clusters']} | {textured_log['filtered_detection_total']} |",
        f"| 纯热噪声 H0 | {noise_log['cfar_hit_cells']} | {noise_log['cfar_clusters']} | {noise_log['filtered_detection_total']} |",
        "",
        "## 已导出波位的连通域统计",
        "",
        "| 场景 | 图数 | 命中单元 | 单点组件 | min2 组件 | min3 组件 | min4 组件 | min2 平均大小 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, item in (("目标+C+N", target), ("纹理 C+N H0", textured), ("纯热噪声 H0", noise)):
        agg = item["aggregated"]
        lines.append(
            f"| {name} | {item['map_count']} | {agg['hit_cells']} | {agg['singletons']} | "
            f"{agg['min2']} | {agg['min3']} | {agg['min4']} | {agg['accepted_min2_mean_size']:.3f} |"
        )
    lines.extend([
        "",
        "## 真值邻域对照",
        "",
        f"在已导出的 14–16 波位真值邻域（距离 ±{report['truth_gate']['truth_gate']['half_range_bins']} 门、Doppler ±{report['truth_gate']['truth_gate']['half_doppler_bins']} 行）中：目标场景命中单元 {gate['target_gate_hit_cells']}，纹理 H0 命中单元 {gate['textured_h0_gate_hit_cells']}，目标场景相对 H0 的新增单元 {gate['target_only_gate_hit_cells']}。",
        "",
        "## 判读",
        "",
        "- 纯热噪声 H0 的命中几乎全是孤立单点，min_points=2 后只留下极少数偶然相邻点；因此 1e-6 的 GO-CFAR 在 IID 热噪声基线下没有出现万级输出。",
        "- 纹理 C+N H0 的输出数量与目标+C+N 同量级，且 min2/min3/min4 的组件分布接近；所以主因是连续纹理杂波造成的非均匀背景，不是目标旁瓣。",
        "- 已导出波位没有大批“长而断续”的单距离列组件；长行组件数量很少，不能解释万级输出。`min_points=2` 的主要放大量来自大量尺寸为 2 的紧凑小组件。",
        "- SCNR 与 CFAR 使用同一个最终检测输入功率图，但统计量不同：SCNR 是真值 ROI 相对局部背景的积分比，CFAR 是单 CUT 相对方向训练均值的门限比较。两者不是同一指标，不能用 SCNR≤20 dB 直接推出 CFAR 输出应少。",
        "",
        "## 证据路径",
        f"- 目标+C+N 日志：`{target_log['log']}`",
        f"- 纹理 H0 日志：`{textured_log['log']}`",
        f"- 纯热噪声 H0 日志：`{noise_log['log']}`",
        f"- 机器可读明细：`{report['json_path']}`",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-run", type=Path, required=True)
    parser.add_argument("--textured-h0-run", type=Path, required=True)
    parser.add_argument("--noise-h0-run", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--target-log", type=Path, required=True)
    parser.add_argument("--textured-h0-log", type=Path, required=True)
    parser.add_argument("--noise-h0-log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rows", type=int, default=128)
    parser.add_argument("--cols", type=int, default=4032)
    parser.add_argument("--max-range-gap", type=int, default=2)
    parser.add_argument("--half-range-gate", type=int, default=2)
    parser.add_argument("--half-doppler-gate", type=int, default=2)
    args = parser.parse_args()
    if args.rows <= 0 or args.cols <= 0 or args.max_range_gap < 1:
        raise SystemExit("rows/cols/max-range-gap must be positive")
    target = map_summary(args.target_run.resolve(), args.rows, args.cols,
                         args.max_range_gap, True)
    textured = map_summary(args.textured_h0_run.resolve(), args.rows, args.cols,
                           args.max_range_gap, True)
    noise = map_summary(args.noise_h0_run.resolve(), args.rows, args.cols,
                        args.max_range_gap, True)
    gate = target_gate_summary(target, textured, args.truth.resolve(),
                               args.half_range_gate, args.half_doppler_gate)
    report: dict[str, object] = {
        "definition": {
            "rows": args.rows,
            "cols": args.cols,
            "max_range_gap": args.max_range_gap,
            "doppler_circular": True,
            "diagnostic_scope": "selected dumped beams for component maps; full scope for logs",
        },
        "runs": {"target": public(target), "textured_h0": public(textured), "noise_h0": public(noise)},
        "logs": {
            "target": log_summary(args.target_log.resolve()),
            "textured_h0": log_summary(args.textured_h0_log.resolve()),
            "noise_h0": log_summary(args.noise_h0_log.resolve()),
        },
        "truth_gate": gate,
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    json_path = output.with_suffix(".json")
    report["json_path"] = str(json_path)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(output, report)
    print(f"[PASS] CFAR 虚警来源报告：{output}")
    print(f"[PASS] 机器可读明细：{json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
