#!/usr/bin/env python3
"""汇总四项主指标的 output-SCNR 交点和联合门限。"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
import sys
sys.path.insert(0, str(SCRIPT_DIR))
from scnr_eval_lib import (ensure_dir, fixed_pd, read_csv, remap_target_theory_rows,
                           write_csv)  # noqa: E402


def number(value: object) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def crossing(x: list[float], y: list[float], target: float, direction: str) -> float:
    pairs = sorted((a, b) for a, b in zip(x, y) if math.isfinite(a) and math.isfinite(b))
    if not pairs:
        return float("nan")
    for index, (x1, y1) in enumerate(pairs):
        hit = y1 <= target if direction == "below" else y1 >= target
        if not hit:
            continue
        if index == 0:
            return x1
        x0, y0 = pairs[index - 1]
        return x0 if y1 == y0 else x0 + (target - y0) * (x1 - x0) / (y1 - y0)
    return float("nan")


def theory_cross(path: Path, x_field: str, y_field: str, target: float, direction: str) -> float:
    rows = read_csv(path) if path.is_file() else []
    x = [number(row.get(x_field)) for row in rows]
    y = [number(row.get(y_field)) for row in rows]
    return crossing(x, y, target, direction)


def fixed_unit_cross() -> float:
    x = np.linspace(-10.0, 40.0, 2001)
    y = fixed_pd(10.0 ** (x / 10.0), 1.0e-6)
    return crossing(x.tolist(), y.tolist(), 0.9, "above")


def mapped_angle_cross(angle: float, calibration_dir: Path) -> float:
    path = ROOT / "outputs/gmti_scnr_eval/angle_theory" / f"angle_theory_{int(round(angle))}deg.csv"
    # The phase/Jacobian model is driven by the centre CUT SCNR.  The angle
    # theory generator now stores the inverse, independently calibrated
    # centre-CUT -> fixed-support output-SCNR coordinate explicitly.  Use that
    # field directly; the older support-output transfer has the opposite
    # semantic (target amplitude -> 3x3 support power) and must not be reused
    # for this crossing.
    mapped_cross = theory_cross(path, "output_scnr_db_mapped",
                                "production_jacobian_rmse_theta_deg", 0.2, "below")
    if math.isfinite(mapped_cross):
        return mapped_cross
    # Legacy archives do not contain the mapped coordinate.  Returning the
    # phase-effective crossing is preferable to applying a physically wrong
    # transfer; callers/reporting label this value as unavailable on the
    # formal output-SCNR axis.
    return theory_cross(path, "scnr_phase_eff_db",
                        "production_jacobian_rmse_theta_deg", 0.2, "below")


def go_theory_path(run_dir: Path, angle: float) -> Path:
    """Locate per-angle GO theory in either a merged run or a chunk manifest."""
    name = f"angle_{angle:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")
    candidates = [run_dir / "runs" / name / name / "go_theory_vs_mc.csv"]
    manifest_path = run_dir / "run_manifest.json"
    if manifest_path.is_file():
        try:
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
            candidates.extend(Path(str(chunk)) / "runs" / name / name / "go_theory_vs_mc.csv"
                              for chunk in document.get("chunks", []))
        except (OSError, ValueError, TypeError):
            pass
    return next((path for path in candidates if path.is_file()), candidates[0])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--metrics-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--target-theory-dir", type=Path,
                        default=ROOT / "docs/GMTI_输出SCNR_目标级minpoints理论_20260826")
    args = parser.parse_args()
    fixed_cross = fixed_unit_cross()
    run_dir = args.run_dir.resolve()
    calibration_candidates = []
    run_manifest_path = run_dir / "run_manifest.json"
    if run_manifest_path.is_file():
        try:
            run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
            external = run_manifest.get("calibration_dir")
            if external:
                calibration_candidates.append(Path(str(external)))
        except (OSError, ValueError, TypeError):
            pass
    calibration_candidates.extend([run_dir.parent / "calibration", run_dir.parent.parent / "calibration"])
    calibration_dir = next((path for path in calibration_candidates if (path / "calibration_mapping.csv").is_file()), calibration_candidates[0])
    metrics_dir = (args.metrics_dir or run_dir).resolve()
    output_dir = ensure_dir((args.output_dir or run_dir / "joint_threshold").resolve())
    summary = read_csv(metrics_dir / "mc_curve_summary.csv")
    rows: list[dict[str, object]] = []
    for angle in sorted({number(row.get("angle_deg")) for row in summary}):
        angle_rows = [row for row in summary if abs(number(row.get("angle_deg")) - angle) < 1e-6]
        x = [number(row.get("output_scnr_db", row.get("desired_scnr_out_db"))) for row in angle_rows]
        target = crossing(x, [number(row.get("angle_rmse_deg")) for row in angle_rows], 0.2, "below")
        unit = crossing(x, [number(row.get("unit_pd")) for row in angle_rows], 0.9, "above")
        target_pd = crossing(x, [number(row.get("target_pd")) for row in angle_rows], 0.9, "above")
        track = crossing(x, [number(row.get("track_pd")) for row in angle_rows], 0.9, "above")
        theory_angle = mapped_angle_cross(angle, calibration_dir)
        group = {0.0: "0deg_center", 30.0: "30deg_center", 45.0: "45deg_center"}.get(angle)
        theory_rows = [row for row in read_csv(args.target_theory_dir.resolve() / "target_pd_minpoints_theory.csv")
                       if row.get("angle_group") == group and int(round(number(row.get("min_points")))) == 6]
        theory_rows = remap_target_theory_rows(theory_rows, args.target_theory_dir.resolve(), angle)
        tx = [number(row.get("output_scnr_db")) for row in theory_rows]
        tu = theory_cross(go_theory_path(run_dir, angle), "output_scnr_db", "unit_pd_empirical_n_only", 0.9, "above")
        tt = crossing(tx, [number(row.get("pd_target_poisson_binomial_profile")) for row in theory_rows], 0.9, "above")
        tr = crossing(tx, [3.0 * number(row.get("pd_target_poisson_binomial_profile")) ** 2 - 2.0 * number(row.get("pd_target_poisson_binomial_profile")) ** 3 for row in theory_rows], 0.9, "above")
        target_axis_offset_db = number(theory_rows[0].get("target_theory_axis_offset_db")) if theory_rows else float("nan")
        joint_mc = max((value for value in (target, unit, target_pd, track) if math.isfinite(value)), default=float("nan"))
        joint_theory = max((value for value in (theory_angle, tu, tt, tr) if math.isfinite(value)), default=float("nan"))
        rows.append({"angle_deg": angle, "mc_angle_rmse_0p2_db": target,
                     "mc_unit_pd_0p9_db": unit, "mc_target_pd_0p9_db": target_pd,
                     "mc_track_pd_0p9_db": track, "mc_joint_max_db": joint_mc,
                     "theory_angle_rmse_0p2_db": theory_angle, "theory_unit_pd_0p9_db": tu,
                     "theory_target_pd_0p9_db": tt, "theory_track_pd_0p9_db": tr,
                     "target_theory_axis_offset_db": target_axis_offset_db,
                     "theory_fixed_threshold_unit_pd_0p9_db": fixed_cross,
                     "theory_joint_max_db": joint_theory})
    write_csv(output_dir / "joint_threshold_summary.csv", rows)
    (output_dir / "joint_threshold_summary.json").write_text(json.dumps({
        "definition": "joint max is the largest finite output-SCNR crossing among angle RMSE<=0.2, unit/target/track Pd>=0.9",
        "rows": rows,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# output SCNR 联合门限", "", "联合门限是四项主指标交点中最大的有限值；缺失交点不补值。", "",
             "| 角度 | MC测角RMSE≤0.2° | MC unit Pd≥0.9 | MC target Pd≥0.9 | MC track Pd≥0.9 | MC联合最大 |", "|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        lines.append("| {angle_deg:.0f} | {mc_angle_rmse_0p2_db:.2f} | {mc_unit_pd_0p9_db:.2f} | {mc_target_pd_0p9_db:.2f} | {mc_track_pd_0p9_db:.2f} | {mc_joint_max_db:.2f} |".format(**{k: (v if math.isfinite(number(v)) else float("nan")) for k, v in row.items()}))
    (output_dir / "joint_threshold_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[PASS] 联合门限摘要已生成：{output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
