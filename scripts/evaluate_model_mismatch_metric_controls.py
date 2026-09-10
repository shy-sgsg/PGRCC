#!/usr/bin/env python3
"""Evaluate production target-only and negative controls.

The control runner deliberately keeps the two controls separate from the
positive C+N challenge runs.  This evaluator consumes only those production
artifacts:

* ``target_only``: target ROI power retention, defined as
  ``10log10(after_power / before_power)`` on the same target-only ROI.
* ``negative_control``: empirical CFAR-cell Pfa, defined as production
  ``hit_cells / valid_cfar_test_cells`` with the denominator reconstructed from
  the runtime geometry and the CUDA CFAR kernel's valid-cell rules.

It never uses the diagnostic ``target_*_truth`` fields in detection rows and
never trains AI.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def find_manifest(control_dir: Path) -> Path:
    manifests = sorted(
        (control_dir / "stage2/algorithm_result/period_0000/csi_metrics").glob(
            "*/csi_roi_manifest.csv"
        )
    )
    if len(manifests) != 1:
        raise RuntimeError(f"CSI manifest 数量不是 1：{control_dir} -> {manifests}")
    return manifests[0]


def resolve_array(manifest_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_file():
        return path
    candidate = manifest_dir / path
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(value)


def latest_runtime_config(control_dir: Path) -> Path:
    path = control_dir / "stage2/algorithm_result/period_0000/runtime_config_dump.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def parse_cfar_summary(control_dir: Path) -> dict[str, Any]:
    log_path = control_dir / "gmticore.log"
    if not log_path.is_file():
        raise FileNotFoundError(log_path)
    text = log_path.read_text(encoding="utf-8", errors="replace")
    matches = re.findall(
        r"\[CFAR\]\[SUMMARY\].*?hit_cells=(\d+).*?clusters=(\d+)"
        r".*?selected=(\d+).*?min_points=([0-9eE+\-.]+)"
        r".*?pf=([0-9eE+\-.]+)",
        text,
    )
    if not matches:
        raise RuntimeError(f"未找到 CFAR summary：{log_path}")
    hit_cells, clusters, selected, min_points, configured_pf = matches[-1]
    return {
        "log": str(log_path.resolve()),
        "hit_cells": int(hit_cells),
        "clusters": int(clusters),
        "selected": int(selected),
        "min_points": float(min_points),
        "configured_pf": float(configured_pf),
    }


def valid_row_indices(height: int, guard: int, background: int,
                      circular: bool, exclude_start: int,
                      exclude_end: int) -> list[int]:
    radius = max(0, guard) + max(1, background)
    first = 0 if circular else radius
    last = height - 1 if circular else height - 1 - radius
    if last < first:
        return []
    return [
        row for row in range(first, last + 1)
        if not (
            exclude_start >= 0
            and exclude_start <= row <= exclude_end
        )
    ]


def parse_dynamic_band(log_path: Path) -> tuple[int, int] | None:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    matches = re.findall(
        r"\[CFAR\]\[CSI-BAND\].*?dynamic=\[(\d+),(\d+)\]",
        text,
    )
    if not matches:
        return None
    start, end = matches[-1]
    return int(start), int(end)


def count_cfar_test_cells(control_dir: Path, manifest: dict[str, str],
                          config: dict[str, Any], log_path: Path) -> dict[str, Any]:
    """Reconstruct the cell denominator used by the production CUDA CFAR.

    The kernel first rejects Doppler/range border cells, then applies row
    exclusions and (only for split calls) the cut-band predicate.  Dynamic,
    full, and union calls use the default cut mode 0 in the current production
    path.  Split is counted as the sum of its two branch evaluations.
    """
    rows = as_int(manifest.get("rows"))
    cols = as_int(manifest.get("cols"))
    detection = config.get("detection", {})
    channel = config.get("channel_and_gmti", {})
    guard = as_int(channel.get("cfar_guard_cells", detection.get("cfar_guard_cells")))
    background = as_int(channel.get("cfar_background_cells", detection.get("cfar_background_cells")), 1)
    circular = bool(channel.get("cfar_doppler_circular", True))
    exclude_start = as_int(channel.get("cfar_exclude_row_start"), -1)
    exclude_end = as_int(channel.get("cfar_exclude_row_end"), -1)
    range_cells = max(cols - 2 * (max(0, guard) + max(1, background)), 0)
    base_rows = valid_row_indices(
        rows, guard, background, circular, exclude_start, exclude_end
    )
    mode = str(channel.get("csi_detection_band_mode", "dynamic"))
    branch_rows: list[int]
    branch_count = 1
    if mode == "split":
        band = parse_dynamic_band(log_path)
        if band is None:
            raise RuntimeError("split CFAR 缺少 dynamic band 日志，无法计算测试分母")
        dynamic_start, dynamic_end = band
        boundary_guard = as_int(channel.get("csi_split_boundary_guard_rows"), 0)
        in_start = max(0, dynamic_start - boundary_guard)
        in_end = min(rows - 1, dynamic_end + boundary_guard)
        out_start = min(rows - 1, dynamic_start + boundary_guard + 1)
        out_end = max(0, dynamic_end - boundary_guard - 1)
        in_rows = [row for row in base_rows if in_start <= row <= in_end]
        out_rows = [row for row in base_rows if not (out_start <= row <= out_end)]
        branch_rows = in_rows + out_rows
        branch_count = 2
    elif mode in {"dynamic", "full", "union"}:
        branch_rows = base_rows
    else:
        raise RuntimeError(f"未知 csi_detection_band_mode：{mode}")
    test_cells = len(branch_rows) * range_cells
    return {
        "rows": rows,
        "cols": cols,
        "guard_cells": guard,
        "background_cells": background,
        "doppler_circular": circular,
        "exclude_row_start": exclude_start,
        "exclude_row_end": exclude_end,
        "csi_detection_band_mode": mode,
        "branch_count": branch_count,
        "valid_doppler_rows": len(branch_rows),
        "valid_range_cells_per_row": range_cells,
        "valid_cfar_test_cells": test_cells,
        "denominator_definition": (
            "CUDA cfar_detect_kernel valid geometry cells after row exclusions; "
            "dynamic/full/union use cut_band_mode=0; split sums in/out branch evaluations"
        ),
    }


def target_centers(control_dir: Path, manifest: dict[str, str],
                   before: np.ndarray) -> list[dict[str, Any]]:
    truth_path = control_dir / "stage2/truth/truth_targets_by_beam.csv"
    truths = [
        row for row in read_csv(truth_path)
        if str(row.get("visible", "1")).strip().lower() in {"1", "true", "yes"}
        and as_int(row.get("beam_id"), -1) == as_int(manifest.get("beam_id"), -1)
    ]
    axis = np.load(resolve_array(Path(manifest["before_power_path"]).parent,
                                 manifest["fa_axis_path"])).reshape(-1).astype(np.float64)
    if axis.size < 2:
        raise RuntimeError("target-only fa_axis 少于两个点")
    step = float(np.median(np.diff(axis)))
    if not step > 0.0 or not np.allclose(np.diff(axis), step, rtol=1e-5, atol=1e-6):
        raise RuntimeError("target-only Doppler axis 非严格均匀")
    prf = step * axis.size
    axis_center = 0.5 * (float(axis[0]) + float(axis[-1]))
    centers: list[dict[str, Any]] = []
    for truth in truths:
        af = as_float(truth.get("af_total_truth_hz"))
        if not math.isfinite(af):
            continue
        wrapped = af + round((axis_center - af) / prf) * prf
        row = int(np.argmin(np.abs(axis - wrapped)))
        col = as_int(truth.get("expected_bin", truth.get("range_bin")), -1)
        if not (0 <= row < before.shape[0] and 0 <= col < before.shape[1]):
            continue
        centers.append({
            "target_id": truth.get("target_id", ""),
            "row": row,
            "col": col,
            "truth_af_hz": af,
            "axis_af_hz": float(axis[row]),
            "wrap_order": int(round((wrapped - af) / prf)),
        })
    if not centers:
        raise RuntimeError(f"target-only 没有可映射 visible target：{truth_path}")
    return centers


def evaluate_target_only(control_dir: Path, manifest: dict[str, str]) -> dict[str, Any]:
    manifest_dir = Path(manifest["before_power_path"]).parent
    before = np.load(resolve_array(manifest_dir, manifest["before_power_path"])).astype(np.float64)
    after = np.load(resolve_array(manifest_dir, manifest["after_power_path"])).astype(np.float64)
    if before.shape != after.shape:
        raise RuntimeError("target-only before/after shape mismatch")
    half_rows = as_int(manifest.get("target_half_doppler_bins"), 0)
    half_cols = as_int(manifest.get("target_half_range_bins"), 0)
    per_target: list[dict[str, Any]] = []
    for center in target_centers(control_dir, manifest, before):
        r0 = max(0, center["row"] - half_rows)
        r1 = min(before.shape[0], center["row"] + half_rows + 1)
        c0 = max(0, center["col"] - half_cols)
        c1 = min(before.shape[1], center["col"] + half_cols + 1)
        before_roi = before[r0:r1, c0:c1]
        after_roi = after[r0:r1, c0:c1]
        finite = np.isfinite(before_roi) & np.isfinite(after_roi) & (before_roi >= 0.0) & (after_roi >= 0.0)
        before_power = float(np.mean(before_roi[finite])) if np.any(finite) else math.nan
        after_power = float(np.mean(after_roi[finite])) if np.any(finite) else math.nan
        if not (math.isfinite(before_power) and before_power > 0.0 and math.isfinite(after_power) and after_power >= 0.0):
            loss = math.nan
            status = "non_positive_or_nonfinite_power"
        elif after_power == 0.0:
            loss = -math.inf
            status = "zero_after_power"
        else:
            loss = 10.0 * math.log10(after_power / before_power)
            status = "measured"
        per_target.append({
            **center,
            "roi_row_start": r0,
            "roi_row_end": r1 - 1,
            "roi_col_start": c0,
            "roi_col_end": c1 - 1,
            "roi_cells": int(np.count_nonzero(finite)),
            "target_power_before": before_power,
            "target_power_after": after_power,
            "target_loss_dB": loss,
            "status": status,
        })
    finite_losses = [item["target_loss_dB"] for item in per_target
                     if math.isfinite(item["target_loss_dB"])]
    return {
        "mode": "target_only",
        "status": "measured" if finite_losses else "unavailable",
        "target_count": len(per_target),
        "target_loss_dB": float(np.mean(finite_losses)) if finite_losses else math.nan,
        "definition": "10log10(mean target-only after ROI power / mean target-only before ROI power); negative means target power loss",
        "source_truth": str((control_dir / "stage2/truth/truth_targets_by_beam.csv").resolve()),
        "source_manifest": str(find_manifest(control_dir).resolve()),
        "targets": per_target,
    }


def evaluate_negative_control(control_dir: Path, manifest: dict[str, str],
                              config: dict[str, Any]) -> dict[str, Any]:
    cfar = parse_cfar_summary(control_dir)
    geometry = count_cfar_test_cells(
        control_dir, manifest, config, Path(cfar["log"])
    )
    denominator = int(geometry["valid_cfar_test_cells"])
    pfa = cfar["hit_cells"] / denominator if denominator > 0 else math.nan
    return {
        "mode": "negative_control",
        "status": "measured" if math.isfinite(pfa) else "unavailable",
        "Pfa": pfa,
        "hit_cells": cfar["hit_cells"],
        "clusters": cfar["clusters"],
        "selected": cfar["selected"],
        "configured_pf": cfar["configured_pf"],
        "cfar_geometry": geometry,
        "definition": "negative-control empirical Pfa = production CFAR hit cells / valid CFAR test cells; configured pf is reported separately",
        "source_log": cfar["log"],
        "source_manifest": str(find_manifest(control_dir).resolve()),
    }


def evaluate_control(control_dir: Path) -> dict[str, Any]:
    summary_path = control_dir / "control_summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(summary_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("status") != "pass":
        raise RuntimeError(f"control 未通过，拒绝计算指标：{summary}")
    manifest_path = find_manifest(control_dir)
    manifest_rows = read_csv(manifest_path)
    if len(manifest_rows) != 1:
        raise RuntimeError(f"CSI manifest 行数不是 1：{manifest_path}")
    manifest = manifest_rows[0]
    config = json.loads(latest_runtime_config(control_dir).read_text(encoding="utf-8"))
    mode = summary.get("mode")
    if mode == "target_only":
        metrics = evaluate_target_only(control_dir, manifest)
    elif mode == "negative_control":
        metrics = evaluate_negative_control(control_dir, manifest, config)
    else:
        raise RuntimeError(f"未知控制模式：{mode}")
    result = {
        "schema_version": 1,
        "control_dir": str(control_dir.resolve()),
        "source_variant": summary.get("source_variant", ""),
        "mode": mode,
        "metrics": metrics,
    }
    (control_dir / "control_metrics.json").write_text(
        json.dumps(json_safe(result), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def discover_control_dirs(root: Path) -> list[Path]:
    return sorted(path.parent for path in root.rglob("control_summary.json"))


def json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-dir", type=Path,
                        help="单个 target_only 或 negative_control 目录")
    parser.add_argument("--root", type=Path,
                        default=ROOT / "outputs/ai_csi_model_mismatch_metric_controls",
                        help="控制输出根目录；默认评估其中所有 pass control")
    args = parser.parse_args()
    dirs = [args.control_dir.resolve()] if args.control_dir else discover_control_dirs(args.root.resolve())
    if not dirs:
        raise SystemExit("没有找到 control_summary.json")
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    for control_dir in dirs:
        try:
            results.append(evaluate_control(control_dir))
        except Exception as exc:  # noqa: BLE001 - report every failed control
            failures.append(f"{control_dir}: {exc}")
    if args.control_dir:
        if failures:
            raise SystemExit(failures[0])
        print(json.dumps(json_safe(results[0]), ensure_ascii=False, indent=2))
        return 0
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    aggregate = {
        "schema_version": 1,
        "ai_training": False,
        "control_count": len(results),
        "failed_count": len(failures),
        "failures": failures,
        "results": results,
    }
    (root / "metrics_manifest.json").write_text(
        json.dumps(json_safe(aggregate), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "root": str(root),
        "evaluated": len(results),
        "failed": len(failures),
    }, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
