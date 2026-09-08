#!/usr/bin/env python3
"""Run the reproducible Stage2 -> production GMTI azimuth study.

Every trial has a resolved JSON/XML, logs and algorithm result directory.  A
small ideal-case audit can retain every Stage2 packet; larger Monte Carlo runs
reuse one packet directory by default so hundreds of transient 50 MiB inputs
do not become permanent workspace garbage.
"""

import argparse
import csv
import json
import math
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import numpy as np

os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/gmti_matplotlib")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "configs/stage2/azimuth_localization_paired_smoke.json"
MATCH_CONFIG = ROOT / "configs/eval/match_config.json"
SIM = ROOT / "build/simulate_stage2_statistical"
CORE = ROOT / "build/GMTI_core"
EVAL = ROOT / "scripts/run_p4_truth_eval.py"


def finite(value, default=math.nan):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def db_ratio(num, den):
    return 10.0 * math.log10(num / den) if num > 0.0 and den > 0.0 else math.nan


def command(cmd, log_path):
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "$ " + " ".join(str(x) for x in cmd) + "\n\n" + proc.stdout + "\n" + proc.stderr,
        encoding="utf-8")
    if proc.returncode:
        raise RuntimeError(f"exit={proc.returncode}: {' '.join(str(x) for x in cmd)}")


def set_xml(root, key, value):
    parent = root.find(".//GMTI_parameter")
    if parent is None:
        raise RuntimeError("generated XML has no GMTI_parameter")
    node = parent.find(key)
    if node is None:
        node = ET.SubElement(parent, key)
    node.text = str(value)


def write_case_config(spec, run_path, work_dir):
    cfg = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    cfg["case_id"] = spec["case_id"]
    cfg["output_dir"] = str(work_dir)
    # The FPGA-to-CPU new-protocol payload remains four-channel for every
    # trial.  ``channels`` denotes the number of physical phase centres, not
    # the packet width: 2 means A/A/B/B and direct pairwise summation; 4
    # means four physical centres and compensated pairwise fusion.
    cfg["waveform"]["new_protocol_channel_count"] = 4
    cfg["scan"]["scan_min_deg"] = spec.get("scan_angle_deg", 0.0)
    cfg["scan"]["beam_count"] = 1
    cfg["random"]["random_seed"] = spec["seed"]
    cfg["random"]["four_channel_phase_center_mode"] = spec["phase_center_mode"]
    use_clutter = spec["clutter_scale"] > 0.0
    cfg["scene"]["mode"] = "full" if use_clutter else "target_only"
    cfg["scene"]["clutter_amplitude_scale"] = spec["clutter_scale"]
    if use_clutter:
        # Current Stage2 discrete Rayleigh/log-normal area model; retain its
        # physical channel propagation and vary target-to-local-background
        # level through the existing target ``snr_db`` control.
        cfg["scene"]["area_clutter"] = {
            "enabled": True,
            "model": "rayleigh_lognormal_texture",
            "scatterer_count": int(spec.get("clutter_scatterer_count", 300)),
            "mean_power": 1.0,
            "texture_sigma": 0.4,
            "spatial_cell_m": 30.0,
            "azimuth_subcell_count": 9,
        }
    cfg["scene"]["thermal_noise"] = {
        "enabled": spec["noise_power"] > 0.0,
        "noise_power": spec["noise_power"],
        "include_target_only": True,
    }
    target = cfg["targets"][0]
    target["init"]["beam_id"] = 1
    target["init"]["expected_bin"] = spec.get("expected_bin", 2200)
    target["init"]["azimuth_offset_deg"] = spec.get("azimuth_offset_deg", 0.0)
    target["motion"]["ve_mps"] = spec.get("ve_mps", -3.0)
    target["motion"]["vn_mps"] = spec.get("vn_mps", 2.0)
    target["amplitude"] = {"type": "snr_db", "snr_db": spec["snr_db"]}
    run_path.parent.mkdir(parents=True, exist_ok=True)
    # The project JSON reader is intentionally lightweight and does not
    # decode ``\\uXXXX`` escapes.  Keep the non-ASCII workspace path literal.
    run_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_specs(groups, noise_seeds, other_seeds, phase_centers=(2, 4),
                clutter_scatterer_count=300):
    specs = []
    def token(value):
        text = f"{value:g}"
        return text.replace("-", "m").replace(".", "p")

    def add(group, channels, seeds, **kw):
        paired = bool(kw.pop("paired", False))
        # Keep every experimental control explicitly represented in every
        # record.  Besides making the CSV self-describing, this prevents
        # heterogeneous str/int values from reaching the summary sort key
        # after a resumed run.
        kw.setdefault("scan_angle_deg", 0.0)
        kw.setdefault("azimuth_offset_deg", 0.0)
        kw.setdefault("expected_bin", 2200)
        kw.setdefault("ve_mps", -3.0)
        kw.setdefault("vn_mps", 2.0)
        kw.setdefault("clutter_scatterer_count", clutter_scatterer_count)
        for seed_offset in range(seeds):
            seed = 20260812 + seed_offset
            label = (
                f"{group}_pc{channels}_snr{token(kw.get('snr_db', 0.0))}"
                f"_n{token(kw.get('noise_power', 0.0))}"
                f"_cl{token(kw.get('clutter_scale', 0.0))}"
                f"_cs{kw.get('clutter_scatterer_count')}"
                f"_sc{token(kw.get('scan_angle_deg', 0.0))}"
                f"_off{token(kw.get('azimuth_offset_deg', 0.0))}"
                f"_bin{kw.get('expected_bin', 2200)}"
                f"_ve{token(kw.get('ve_mps', -3.0))}"
                f"_vn{token(kw.get('vn_mps', 2.0))}_s{seed}")
            spec = dict(group=group, channels=channels, seed=seed,
                        phase_center_count=channels,
                        case_id=label, phase_center_mode=("paired_same_phase_center"
                        if paired else "physical"),
                        **kw)
            specs.append(spec)

    if "ideal" in groups:
        for ch in phase_centers:
            add("ideal", ch, 1, paired=(ch == 2), snr_db=45.0,
                noise_power=0.0, clutter_scale=0.0)
    if "noise" in groups:
        for snr in (30.0, 20.0, 15.0, 10.0, 5.0, 0.0):
            for ch in phase_centers:
                add("noise", ch, noise_seeds, paired=(ch == 2), snr_db=snr,
                    noise_power=1.0e-4, clutter_scale=0.0)
    if "clutter" in groups:
        for scr in (30.0, 20.0, 10.0):
            for ch in phase_centers:
                add("clutter", ch, other_seeds, paired=(ch == 2), snr_db=scr,
                    noise_power=0.0, clutter_scale=1.0)
    if "mixed" in groups:
        for snr in (25.0, 15.0, 5.0):
            for ch in phase_centers:
                add("mixed", ch, other_seeds, paired=(ch == 2), snr_db=snr,
                    noise_power=1.0e-4, clutter_scale=1.0)
    if "scan" in groups:
        for angle in (-40.0, -20.0, 0.0, 20.0, 40.0):
            for ch in phase_centers:
                add("scan", ch, other_seeds // 2, paired=(ch == 2), snr_db=20.0,
                    noise_power=1.0e-4, clutter_scale=0.0, scan_angle_deg=angle)
    if "offset" in groups:
        for offset in (0.0, 0.57, 1.14, 1.90):
            for ch in phase_centers:
                add("offset", ch, other_seeds // 2, paired=(ch == 2), snr_db=20.0,
                    noise_power=1.0e-4, clutter_scale=0.0, azimuth_offset_deg=offset)
    if "range" in groups:
        for expected_bin in (1000, 2200, 3400):
            for ch in phase_centers:
                add("range", ch, other_seeds // 2, paired=(ch == 2), snr_db=20.0,
                    noise_power=1.0e-4, clutter_scale=0.0, expected_bin=expected_bin)
    if "velocity" in groups:
        for ve, vn in ((-3.0, 2.0), (0.0, 2.0), (3.0, 2.0)):
            for ch in phase_centers:
                add("velocity", ch, other_seeds // 2, paired=(ch == 2), snr_db=20.0,
                    noise_power=1.0e-4, clutter_scale=0.0, ve_mps=ve, vn_mps=vn)
    return specs


def latest_csv(path):
    choices = list(path.glob("csi_metrics/*/csi_metric_tap_summary.csv"))
    return max(choices, key=lambda p: p.stat().st_mtime) if choices else None


def read_rows(path):
    if not path or not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def trial_result(spec, work_dir, output_dir):
    out = output_dir
    report_dir = out / "reports"
    det_metrics = read_rows(report_dir / "detection_metrics.csv")
    matches = read_rows(report_dir / "truth_match_results.csv")
    detections = read_rows(out / "detection_results.csv")
    truths = read_rows(work_dir / "truth/truth_targets_by_beam.csv")
    metric = det_metrics[0] if det_metrics else {}
    result = dict(spec)
    result.update({
        "input_protocol_channel_count": 4,
        "fusion_input_channel_count": 4,
        "algorithm_variant": (
            "2pc_direct_sum" if spec["channels"] == 2
            else "4pc_phase_compensated_fusion"),
        "detected_count": finite(metric.get("detection_count"), 0.0),
        "matched_count": finite(metric.get("matched_count"), 0.0),
        "false_alarm_count": finite(metric.get("false_alarm_count"), 0.0),
        "missed_count": finite(metric.get("missed_truth_count"), 1.0),
        "pd": finite(metric.get("recall"), 0.0),
    })
    selected = {}
    truth = {}
    if matches:
        match = matches[0]
        idx = int(float(match["item_index"]))
        if 0 <= idx < len(detections):
            selected = detections[idx]
        truth_idx = int(float(match["truth_index"]))
        if 0 <= truth_idx < len(truths):
            truth = truths[truth_idx]
    if not truth:
        # A missed target has no conditional localization error.  Retain the
        # first truth only for static scenario metadata such as range.
        truth = truths[0] if truths else {}
    for key in (
        "angle_error_deg", "azimuth_error_m", "range_error_m", "position_error_m",
        "af_used_hz", "truth_af_geometry_hz", "af_total_hz", "truth_af_total_hz",
        "phase_rad", "ctdr_raw_phase_rad", "truth_ctdr_phase_rad",
        "loc_jacobian_daf_dphase_hz_per_rad",
        "loc_jacobian_daf_doppler_hz_per_hz",
        "loc_jacobian_crossrange_m_per_phase_rad", "lambda_m", "power",
        "range_m", "truth_angle_deg", "estimated_angle_deg",
    ):
        result[key] = finite(selected.get(key))
    result["truth_range_m"] = finite(truth.get("range_m"))
    result["truth_e"] = finite(truth.get("e_mid"))
    result["truth_n"] = finite(truth.get("n_mid"))
    # Stage2 truth CSV is the source of truth for these fields.  The
    # diagnostic writer uses legacy ``truth_*`` names, whereas Stage2 uses
    # ``*_truth`` names; prefer the latter when it is present rather than
    # silently carrying a NaN into the Jacobian comparison.
    truth_geo = finite(truth.get("af_geometry_truth_hz"))
    truth_total = finite(truth.get("af_total_truth_hz"))
    truth_phase = finite(truth.get("phi_total_truth_rad"))
    if math.isfinite(truth_geo):
        result["truth_af_geometry_hz"] = truth_geo
    if math.isfinite(truth_total):
        result["truth_af_total_hz"] = truth_total
    if math.isfinite(truth_phase):
        result["truth_ctdr_phase_rad"] = truth_phase
    result["channel_mode_actual"] = selected.get("channel_mode", "")
    result["phase_compensation_actual"] = selected.get("four_channel_phase_compensation_enable", "")
    result["loc_used_mode"] = selected.get("loc_used_mode", "")
    result["fgeo_error_hz"] = result["af_used_hz"] - result["truth_af_geometry_hz"]
    phase_delta = result["ctdr_raw_phase_rad"] - result["truth_ctdr_phase_rad"]
    result["ctdr_phase_error_rad"] = math.atan2(math.sin(phase_delta), math.cos(phase_delta))

    # The diagnostic writer has a pulse-level truth association for phase
    # auditing.  A detection, however, is a beam-level result.  Recompute all
    # direction-dependent errors from the Hungarian-matched beam-midpoint
    # truth and the platform state actually consumed by the production
    # localizer.  This prevents target motion during an aperture from being
    # mistaken for a period/seed-dependent localization error.
    pe = finite(selected.get("platform_e"))
    pn = finite(selected.get("platform_n"))
    te = result["truth_e"]
    tn = result["truth_n"]
    ee = finite(selected.get("new_e"))
    en = finite(selected.get("new_n"))
    if all(math.isfinite(v) for v in (pe, pn, te, tn, ee, en)):
        de, dn = te - pe, tn - pn
        rng = math.hypot(de, dn)
        if rng > 0.0:
            xe, xn = ee - te, en - tn
            truth_angle = math.degrees(math.atan2(dn, de))
            estimate_angle = math.degrees(math.atan2(en - pn, ee - pe))
            result["truth_angle_deg"] = truth_angle
            result["estimated_angle_deg"] = estimate_angle
            result["angle_error_deg"] = math.degrees(math.atan2(
                math.sin(math.radians(estimate_angle - truth_angle)),
                math.cos(math.radians(estimate_angle - truth_angle))))
            ue, un = de / rng, dn / rng
            result["azimuth_error_m"] = -xe * un + xn * ue
            result["range_error_m"] = xe * ue + xn * un
            result["position_error_m"] = math.hypot(xe, xn)
    csi = latest_csv(out)
    summary = read_rows(csi)[-1] if csi else {}
    result["processed_csi_background_power"] = finite(summary.get("after_mean_power"))
    result["processed_csi_target_to_background_db"] = db_ratio(
        result["power"], result["processed_csi_background_power"])
    result["phase_model_coherence"] = finite(summary.get("phase_model_weighted_coherence"))
    result["phase_model_circular_rmse_rad"] = finite(
        summary.get("phase_model_weighted_circular_rmse_rad"))
    return result


def write_csv(path, rows):
    fields = sorted({key for row in rows for key in row}) if rows else []
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def stat(values):
    a = np.asarray([v for v in values if math.isfinite(v)], dtype=float)
    if not len(a):
        return dict(n=0, bias=math.nan, std=math.nan, rmse=math.nan, p95=math.nan)
    return dict(n=int(len(a)), bias=float(np.mean(a)),
                std=float(np.std(a, ddof=1)) if len(a) > 1 else 0.0,
                rmse=float(np.sqrt(np.mean(a * a))),
                p95=float(np.percentile(np.abs(a), 95.0)))


def summarize(rows):
    keys = ("group", "channels", "snr_db", "clutter_scale", "noise_power",
            "scan_angle_deg", "azimuth_offset_deg", "expected_bin", "ve_mps", "vn_mps")
    buckets = defaultdict(list)
    for row in rows:
        # ``--resume`` reloads completed CSV rows as text whereas new rows
        # still carry numeric values.  Canonicalize the grouping controls so
        # one physical condition cannot split into e.g. ``20`` and ``20.0``.
        ident = (
            row.get("group", ""),
            int(finite(row.get("channels"), 0.0)),
            finite(row.get("snr_db")),
            finite(row.get("clutter_scale")),
            finite(row.get("noise_power")),
            finite(row.get("scan_angle_deg")),
            finite(row.get("azimuth_offset_deg")),
            int(finite(row.get("expected_bin"), 0.0)),
            finite(row.get("ve_mps")),
            finite(row.get("vn_mps")),
        )
        buckets[ident].append(row)
    out = []
    for ident, members in sorted(buckets.items(), key=lambda item: tuple(str(x) for x in item[0])):
        rec = dict(zip(keys, ident))
        rec["trial_count"] = len(members)
        rec["matched_count"] = sum(finite(x.get("matched_count"), 0.0) for x in members)
        rec["missed_count"] = sum(finite(x.get("missed_count"), 0.0) for x in members)
        rec["false_alarm_count"] = sum(finite(x.get("false_alarm_count"), 0.0) for x in members)
        rec["pd"] = rec["matched_count"] / len(members) if members else math.nan
        matched = [x for x in members if finite(x.get("matched_count"), 0.0) > 0.0]
        rec["truth_range_mean_m"] = stat(
            [finite(x.get("truth_range_m")) for x in matched])["bias"]
        for label, field in (("angle", "angle_error_deg"), ("azimuth", "azimuth_error_m"),
                             ("range", "range_error_m"), ("position", "position_error_m"),
                             ("fgeo", "fgeo_error_hz"), ("phase", "ctdr_phase_error_rad")):
            for name, value in stat([finite(x.get(field)) for x in matched]).items():
                rec[f"{label}_{name}"] = value
        phase_std = rec["phase_std"]
        jx = np.asarray([finite(x.get("loc_jacobian_crossrange_m_per_phase_rad")) for x in matched])
        jx = jx[np.isfinite(jx)]
        rec["theory_azimuth_std_m"] = float(np.median(np.abs(jx)) * phase_std) if len(jx) and math.isfinite(phase_std) else math.nan
        jf = np.asarray([finite(x.get("loc_jacobian_daf_dphase_hz_per_rad")) for x in matched])
        jf = jf[np.isfinite(jf)]
        rec["theory_fgeo_std_hz"] = float(np.median(np.abs(jf)) * phase_std) if len(jf) and math.isfinite(phase_std) else math.nan
        rec["processed_csi_target_to_background_db_mean"] = stat(
            [finite(x.get("processed_csi_target_to_background_db")) for x in matched])["bias"]
        out.append(rec)
    return out


def plot_series(summary, group, xkey, ykey, ylabel, name, results_dir):
    use = [r for r in summary if r["group"] == group and math.isfinite(finite(r.get(xkey))) and math.isfinite(finite(r.get(ykey)))]
    if not use:
        return
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    for ch in (2, 4):
        part = sorted((r for r in use if int(float(r["channels"])) == ch), key=lambda r: finite(r[xkey]))
        if part:
            ax.plot([finite(r[xkey]) for r in part], [finite(r[ykey]) for r in part], "o-", label=f"{ch}pc")
    ax.set_xlabel(xkey)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=.3)
    if ax.lines:
        ax.legend()
    fig.tight_layout()
    fig.savefig(results_dir / "figures" / f"{name}.png", dpi=160)
    plt.close(fig)


def make_figures(rows, summary, results_dir):
    figdir = results_dir / "figures"
    figdir.mkdir(parents=True, exist_ok=True)
    plot_series(summary, "noise", "snr_db", "fgeo_std", "f_geo Std (Hz)", "01_intermediate_std_vs_snr", results_dir)
    plot_series(summary, "noise", "snr_db", "angle_rmse", "angle RMSE (deg)", "02_angle_rmse_vs_snr", results_dir)
    plot_series(summary, "noise", "snr_db", "azimuth_rmse", "azimuth RMSE (m)", "03_azimuth_rmse_vs_snr", results_dir)
    plot_series(summary, "noise", "snr_db", "theory_azimuth_std_m", "Jacobian theory azimuth Std (m)", "04_theory_std_vs_snr", results_dir)
    plot_series(summary, "noise", "snr_db", "pd", "Pd", "05_pd_vs_snr", results_dir)
    plot_series(summary, "scan", "scan_angle_deg", "azimuth_rmse", "azimuth RMSE (m)", "06_azimuth_rmse_vs_scan", results_dir)
    plot_series(summary, "offset", "azimuth_offset_deg", "azimuth_rmse", "azimuth RMSE (m)", "07_azimuth_rmse_vs_offset", results_dir)
    plot_series(summary, "range", "truth_range_mean_m", "azimuth_rmse", "azimuth RMSE (m)", "08_azimuth_rmse_vs_range", results_dir)
    plot_series(summary, "mixed", "snr_db", "azimuth_p95", "azimuth |error| P95 (m)", "09_azimuth_p95_vs_scnr_proxy", results_dir)
    use = [r for r in rows if r.get("group") in ("noise", "mixed") and finite(r.get("matched_count"), 0) > 0]
    if use:
        fig, ax = plt.subplots(figsize=(6.4, 4.2))
        for ch in (2, 4):
            vals = np.sort(np.abs([finite(r.get("azimuth_error_m")) for r in use if r["channels"] == ch and math.isfinite(finite(r.get("azimuth_error_m")))]))
            if len(vals):
                ax.plot(vals, np.arange(1, len(vals) + 1) / len(vals), label=f"{ch}pc")
        ax.set_xlabel("absolute azimuth error (m)")
        ax.set_ylabel("empirical CDF")
        ax.grid(True, alpha=.3)
        if ax.lines:
            ax.legend()
        fig.tight_layout()
        fig.savefig(figdir / "10_azimuth_absolute_error_cdf.png", dpi=160)
        plt.close(fig)


def write_summary_md(path, rows, summary, errors):
    ideal = [r for r in summary if r["group"] == "ideal"]
    with path.open("w", encoding="utf-8") as f:
        f.write("# 方位定位专题 Monte Carlo 运行摘要\n\n")
        f.write("- 执行链：`simulate_stage2_statistical -> GMTI_core --runtime-mode debug -> run_p4_truth_eval.py`。\n")
        f.write("- 两组均为 4 路协议数据和 4 路融合；2pc=A/A/B/B 直接相加，4pc=四物理相位中心、补偿后融合。\n")
        f.write("- 匹配：复用工程一对一 Hungarian matcher；未匹配 trial 纳入 Pd/miss，不纳入条件误差。\n")
        f.write("- 理论列：用生产 CTDR 定位函数中心差分 Jacobian 与实测 truth 相位残差标准差的一阶传播；它不是额外的替代定位器。\n")
        f.write("- processed_csi_target_to_background_db 是 CFAR 目标功率除以生产 CSI 全支撑 after-power 均值的实际输出代理，不能与输入 SNR/SCR 混称。\n\n")
        f.write("## 理想闭环\n\n")
        for r in ideal:
            f.write(f"- {r['channels']}pc: Pd={r['pd']:.3g}, azimuth RMSE={r['azimuth_rmse']:.6g} m, angle RMSE={r['angle_rmse']:.6g} deg, false alarms={r['false_alarm_count']:.0f}.\n")
        f.write("\n## 聚合结果\n\n")
        f.write("| group | phase centers | control | trials | Pd | angle RMSE deg | az RMSE m | az P95 m | theory az Std m | false alarms |\n|---|---:|---|---:|---:|---:|---:|---:|---:|---:|\n")
        for r in summary:
            control = f"snr={r['snr_db']}; scan={r['scan_angle_deg']}; off={r['azimuth_offset_deg']}; bin={r['expected_bin']}"
            f.write("| {group} | {channels} | {control} | {trial_count} | {pd:.3g} | {angle_rmse:.4g} | {azimuth_rmse:.4g} | {azimuth_p95:.4g} | {theory_azimuth_std_m:.4g} | {false_alarm_count:.0f} |\n".format(control=control, **r))
        if errors:
            f.write("\n## 失败 trial\n\n")
            for item in errors:
                f.write(f"- {item}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default="outputs/azimuth_localization_study")
    parser.add_argument("--work-dir", default="outputs/azimuth_localization_study_work")
    parser.add_argument("--groups", nargs="+", default=["ideal", "noise", "clutter", "mixed", "scan", "offset", "range", "velocity"])
    parser.add_argument("--noise-seeds", type=int, default=20)
    parser.add_argument("--other-seeds", type=int, default=8)
    parser.add_argument("--phase-centers", nargs="+", type=int,
                        choices=(2, 4), default=[2, 4],
                        help="Run only the selected physical phase-centre models; packet input remains 4ch.")
    parser.add_argument("--retain-stage2-inputs", action="store_true",
                        help="Keep one independent raw Stage2 packet directory per trial (for small audit runs only).")
    parser.add_argument("--clutter-scatterer-count", type=int, default=300,
                        help="Discrete area-clutter scatterer count; record it in every resolved Stage2 JSON.")
    parser.add_argument("--limit", type=int, default=0, help="Run only the first N trials (smoke/debug)")
    parser.add_argument("--resume", action="store_true", help="Skip trial IDs already present in trial_results.csv")
    args = parser.parse_args()
    unknown = set(args.groups) - {"ideal", "noise", "clutter", "mixed", "scan", "offset", "range", "velocity"}
    if unknown:
        parser.error(f"unknown groups: {sorted(unknown)}")
    results_dir = (ROOT / args.results_dir).resolve()
    work_dir = (ROOT / args.work_dir).resolve()
    artifacts = results_dir / "artifacts"
    results_dir.mkdir(parents=True, exist_ok=True)
    # Stage2 creates child directories but deliberately does not create a
    # missing output root, so establish the reusable work root explicitly.
    work_dir.mkdir(parents=True, exist_ok=True)
    (artifacts / "configs").mkdir(parents=True, exist_ok=True)
    (artifacts / "xml").mkdir(parents=True, exist_ok=True)
    (artifacts / "logs").mkdir(parents=True, exist_ok=True)
    trial_csv = results_dir / "trial_results.csv"
    if trial_csv.exists() and not args.resume:
        parser.error(
            f"{trial_csv} already exists; use --resume or choose a new --results-dir "
            "to preserve completed Monte Carlo evidence")
    rows = read_rows(trial_csv) if args.resume else []
    done = {r.get("case_id") for r in rows}
    specs = build_specs(args.groups, args.noise_seeds, args.other_seeds,
                        tuple(args.phase_centers), args.clutter_scatterer_count)
    if args.limit:
        specs = specs[:args.limit]
    errors = []
    for trial_index, spec in enumerate(specs, 1):
        if spec["case_id"] in done:
            continue
        print(f"[{trial_index}/{len(specs)}] {spec['case_id']}", flush=True)
        case_work_dir = (
            work_dir / "stage2_cases" / spec["case_id"]
            if args.retain_stage2_inputs else work_dir / "stage2_shared")
        run_json = case_work_dir / "run_config.json"
        try:
            write_case_config(spec, run_json, case_work_dir)
            command([str(SIM), "--run-config", str(run_json)], artifacts / "logs" / f"{spec['case_id']}_stage2.log")
            xml_path = case_work_dir / "config/temp_config_stage2_period_0000.xml"
            tree = ET.parse(xml_path)
            xml_root = tree.getroot()
            # Isolate every algorithm result directory.  Each run gets its own
            # output directory, so incremental GMTI numbering remains unique
            # across arbitrarily many Monte Carlo trials.
            result_dir = work_dir / "algorithm_result" / spec["case_id"]
            set_xml(xml_root, "result_add", result_dir)
            set_xml(xml_root, "csi_metrics_enable", 1)
            set_xml(xml_root, "csi_metrics_dump_power_maps", 0)
            set_xml(xml_root, "debug_pc_peak", 0)
            set_xml(xml_root, "stage2_case_id", spec["case_id"])
            # Both arms receive and fuse all four physical receiver streams.
            # They differ only in whether Ch1/Ch3 and Ch2/Ch4 are distinct
            # phase centres requiring the production compensation factors.
            set_xml(xml_root, "enable_four_channel_fusion", 1)
            set_xml(
                xml_root,
                "four_channel_phase_compensation_enable",
                int(spec["phase_center_mode"] != "paired_same_phase_center"),
            )
            tree.write(xml_path, encoding="utf-8", xml_declaration=True)
            command([str(CORE), str(xml_path), "--runtime-mode", "debug"], artifacts / "logs" / f"{spec['case_id']}_gmti.log")
            out_dir = result_dir
            command([sys.executable, str(EVAL), "--output-dir", str(out_dir), "--truth-dir", str(case_work_dir / "truth"), "--match-config", str(MATCH_CONFIG)], artifacts / "logs" / f"{spec['case_id']}_eval.log")
            shutil.copy2(run_json, artifacts / "configs" / f"{spec['case_id']}.json")
            shutil.copy2(xml_path, artifacts / "xml" / f"{spec['case_id']}.xml")
            rows.append(trial_result(spec, case_work_dir, out_dir))
            write_csv(trial_csv, rows)
        except Exception as exc:  # retain the log and proceed to remaining independent trials
            errors.append(f"{spec['case_id']}: {exc}")
            print(errors[-1], file=sys.stderr, flush=True)
    summary = summarize(rows)
    write_csv(results_dir / "summary_results.csv", summary)
    make_figures(rows, summary, results_dir)
    write_summary_md(results_dir / "study_summary.md", rows, summary, errors)
    print(f"completed={len(rows)} failed={len(errors)} results={results_dir}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
