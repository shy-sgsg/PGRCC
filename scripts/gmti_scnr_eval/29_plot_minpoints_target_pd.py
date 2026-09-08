#!/usr/bin/env python3
"""汇总 min_points=2/3/6 的真实目标级 Pd，并分别绘图。

每个输入目录必须是 ``17_run_center_angle_monte_carlo.py`` 的输出，或已经
合并的正式 min_points=6 目录。脚本只做 CSV 汇总和可视化，不重新拟合
任何概率模型；每条实测曲线都保留真实 hit/trial 分母和 Wilson 区间。
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
import sys

sys.path.insert(0, str(SCRIPT_DIR))
from scnr_eval_lib import ensure_dir, read_csv, wilson_interval, write_csv  # noqa: E402


COLORS = {0.0: "#1f77b4", 30.0: "#d98c00", 45.0: "#8c564b"}
LABELS = {0.0: "0°中心", 30.0: "30°中心", 45.0: "45°中心"}
FONT = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
if FONT.is_file():
    font_manager.fontManager.addfont(str(FONT))
    plt.rcParams["font.family"] = "Noto Sans CJK JP"
plt.rcParams["axes.unicode_minus"] = False


def finite(value: object) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def angle_token(angle: float) -> str:
    return f"angle_{angle:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")


def infer_angle_from_path(path: Path) -> float:
    """Infer the angle only from the audited per-angle output directory.

    ``16_run_standard_output_snr_mc.py`` writes one curve per angle and does
    not duplicate the angle column in that per-angle CSV.  The parent
    directory is an explicit run label (``angle_p0p000`` etc.), not a truth
    value; using it here only restores the grouping metadata for plotting.
    """
    for part in reversed(path.parts):
        if not part.startswith("angle_"):
            continue
        token = part[len("angle_") :]
        try:
            sign = -1.0 if token.startswith("m") else 1.0
            body = token[1:] if token[:1] in {"m", "p"} else token
            return sign * float(body.replace("p", "."))
        except ValueError:
            continue
    return float("nan")


def find_curve(root: Path) -> Path:
    return find_curves(root)[0]


def find_curves(root: Path) -> list[Path]:
    """Return the authoritative aggregate, or all explicit per-angle CSVs."""
    direct = [root / "mc_curve_summary.csv", root / "standard_mc_curve_summary.csv"]
    for candidate in direct:
        if candidate.is_file():
            return [candidate]
    angle_paths = [
        root / "runs" / angle_token(angle) / angle_token(angle) / "standard_mc_curve_summary.csv"
        for angle in (0.0, 30.0, 45.0)
    ]
    existing = [path for path in angle_paths if path.is_file()]
    if existing:
        return existing
    matches = sorted(root.rglob("standard_mc_curve_summary.csv")) + sorted(root.rglob("mc_curve_summary.csv"))
    if matches:
        # Prefer explicit angle-labelled files and keep one file per angle;
        # this avoids silently plotting only the first angle in a nested run.
        labelled = [path for path in matches if math.isfinite(infer_angle_from_path(path))]
        if labelled:
            unique: dict[float, Path] = {}
            for path in sorted(labelled, key=lambda item: (len(item.relative_to(root).parts), str(item))):
                unique.setdefault(round(infer_angle_from_path(path), 6), path)
            return list(unique.values())
        matches.sort(key=lambda path: (len(path.relative_to(root).parts), str(path)))
        return [matches[0]]
    raise FileNotFoundError(f"{root} 下没有 mc_curve_summary.csv/standard_mc_curve_summary.csv")


def manifest_info(root: Path) -> dict[str, object]:
    for path in (root / "run_manifest.json", root / "manifest.json"):
        if path.is_file():
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                return document if isinstance(document, dict) else {}
            except (OSError, ValueError, TypeError):
                return {}
    return {}


def normalize(root: Path, minimum: int) -> list[dict[str, object]]:
    paths = find_curves(root)
    info = manifest_info(root)
    if "strict_online_no_prior" not in info:
        for candidate in paths:
            nested = manifest_info(candidate.parent)
            if nested.get("strict_online_no_prior") is True:
                info["strict_online_no_prior"] = True
                break
    result: list[dict[str, object]] = []
    for path in paths:
        inferred_angle = infer_angle_from_path(path)
        for row in read_csv(path):
            angle = finite(row.get("angle_deg"))
            if not math.isfinite(angle):
                angle = inferred_angle
            x = finite(row.get("output_scnr_db"))
            if not math.isfinite(x):
                x = finite(row.get("desired_scnr_out_db", row.get("requested_output_scnr_db")))
            hits = finite(row.get("target_hits"))
            trials = finite(row.get("target_trials"))
            pd = finite(row.get("target_pd"))
            if not math.isfinite(pd) and math.isfinite(hits) and math.isfinite(trials) and trials > 0:
                pd = hits / trials
            if not (math.isfinite(angle) and math.isfinite(x) and math.isfinite(pd)
                    and math.isfinite(hits) and math.isfinite(trials) and trials > 0):
                continue
            low = finite(row.get("target_wilson_low"))
            high = finite(row.get("target_wilson_high"))
            if not (math.isfinite(low) and math.isfinite(high)):
                low, high = wilson_interval(int(round(hits)), int(round(trials)))
            result.append({
                "min_points": minimum,
                "angle_deg": angle,
                "output_scnr_db": x,
                "target_hits": int(round(hits)),
                "target_trials": int(round(trials)),
                "target_pd": pd,
                "target_wilson_low": low,
                "target_wilson_high": high,
                "source_root": str(root.resolve()),
                "source_curve": str(path.resolve()),
                "background": info.get("background", ""),
                "seed": info.get("base_seed", info.get("seed", "")),
                "trials_per_level": info.get("trials_per_level", ""),
                "small_min_points": info.get("small_min_points", ""),
                "small_peak_db": info.get("small_peak_db", ""),
                "strict_online_no_prior": bool(info.get("strict_online_no_prior", False)),
            })
    if not result:
        raise RuntimeError(f"{path} 没有有效目标级 Pd 行")
    return result


def plot_one(rows: list[dict[str, object]], minimum: int, output: Path) -> None:
    fig, ax = plt.subplots(figsize=(8.2, 5.2))
    for angle in sorted({float(row["angle_deg"]) for row in rows}):
        selected = sorted((row for row in rows if abs(float(row["angle_deg"]) - angle) < 1e-6),
                          key=lambda row: float(row["output_scnr_db"]))
        x = np.asarray([float(row["output_scnr_db"]) for row in selected])
        y = np.asarray([float(row["target_pd"]) for row in selected])
        lo = np.asarray([float(row["target_wilson_low"]) for row in selected])
        hi = np.asarray([float(row["target_wilson_high"]) for row in selected])
        color = COLORS.get(angle, "#333333")
        label = f"实测 MC {LABELS.get(angle, f'{angle:g}°')}（n={int(sum(int(r['target_trials']) for r in selected))}）"
        ax.plot(x, y, "o-", color=color, markerfacecolor="white", label=label)
        ax.fill_between(x, lo, hi, color=color, alpha=0.12, linewidth=0)
    ax.axhline(0.9, color="#555555", linestyle=":", label="Pd=0.9")
    ax.set_xlabel("输出 SCNR（dB；固定 S+N 支撑增量 / N-only P0）")
    ax.set_ylabel("目标级最终 truth Pd")
    ax.set_title(f"目标级 Pd：min_points={minimum}（独立分图）")
    ax.set_ylim(0.0, 1.03)
    ax.grid(True, color="#d9dde3", alpha=0.7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output, dpi=200)
    fig.savefig(output.with_suffix(".pdf"))
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min2-dir", type=Path, required=True)
    parser.add_argument("--min3-dir", type=Path, required=True)
    parser.add_argument("--min6-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = ensure_dir(args.output_dir.resolve())
    sources = {2: args.min2_dir.resolve(), 3: args.min3_dir.resolve(), 6: args.min6_dir.resolve()}
    all_rows: list[dict[str, object]] = []
    manifest_rows: list[dict[str, object]] = []
    for minimum, root in sources.items():
        rows = normalize(root, minimum)
        all_rows.extend(rows)
        manifest_rows.append({
            "min_points": minimum,
            "root": str(root),
            "curve": str(find_curve(root).resolve()),
            "angle_count": len({float(row["angle_deg"]) for row in rows}),
            "row_count": len(rows),
            "screen_trial_count": int(sum(int(row["target_trials"]) for row in rows)),
            "background": rows[0].get("background", ""),
            "trials_per_level": rows[0].get("trials_per_level", ""),
            "seed": rows[0].get("seed", ""),
            "small_min_points": rows[0].get("small_min_points", ""),
            "small_peak_db": rows[0].get("small_peak_db", ""),
            "strict_online_no_prior": rows[0].get("strict_online_no_prior", False),
        })
        plot_one(rows, minimum, output / f"target_pd_minpoints_{minimum}.png")
    all_rows.sort(key=lambda row: (int(row["min_points"]), float(row["angle_deg"]), float(row["output_scnr_db"])))
    write_csv(output / "target_pd_minpoints_summary.csv", all_rows)
    (output / "target_pd_minpoints_manifest.json").write_text(
        json.dumps({"sources": manifest_rows, "status": "pass"}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# min_points=2/3/6 目标级实测对照",
        "",
        "三种 min_points 各自单独运行真实 CUDA 生产链（或复用已审计的 pooled min6），" \
        "横轴均为固定 3×3 支撑 output SCNR；每个图只画一个 min_points，避免把三条曲线挤在一张图。",
        "",
        "| min_points | 曲线 CSV | PNG | PDF |",
        "|---:|---|---|---|",
    ]
    for minimum in (2, 3, 6):
        lines.append(f"| {minimum} | target_pd_minpoints_summary.csv | target_pd_minpoints_{minimum}.png | target_pd_minpoints_{minimum}.pdf |")
    lines.extend([
        "",
        "表中 `target_hits/target_trials` 是真实 truth-match 计数；阴影为 Wilson 95% 区间。",
        "min_points=2 的运行将 `small_min_points=2` 显式写入配置以通过生产校验；min_points=3 使用 `small_min_points=3`；min_points=6 继承生产的 3–5 点、+20 dB 小簇恢复规则。`strict_online_no_prior=true` 表示 N-only 结果没有回写生产 XML。",
    ])
    (output / "target_pd_minpoints_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[PASS] min_points=2/3/6 分图及汇总已写入 {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
