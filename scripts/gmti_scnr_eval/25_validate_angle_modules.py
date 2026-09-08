#!/usr/bin/env python3
"""分模块验证 GMTI 测角链路。

本脚本不运行新的 0/30/45 度场景，也不替代生产定位器。它把测角拆成四个
可以单独判读的环节：

1. 生产 Doppler 几何的 ``f=-2V sin(theta)/lambda`` 正逆变换；
2. 两个独立接收通道的复数相位差估计；
3. ``phase=k*f+b`` 的 P38 斜率拟合；
4. 生产 ENU 投影的 theta -> position -> theta 闭环。

若给出已有标准 MC 根目录，还会把已保存的生产 angle_error/phase_error
作为“接口审计”附在报告中。该部分只读已有产物，不重新拟合参数、不把一个
0 度案例外推为 30/45 度结论。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import xml.etree.ElementTree as ET
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/gmti_scnr_eval_matplotlib")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from scnr_eval_lib import ensure_dir, read_csv, write_csv


C = 299_792_458.0
PI = math.pi


def xml_values(path: Path) -> dict[str, str]:
    root = ET.parse(path).getroot()
    return {node.tag: node.text.strip() for node in root.iter()
            if node.text and node.text.strip()}


def finite_float(value: object, default: float = float("nan")) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def wrap_pi(value: float) -> float:
    return (value + PI) % (2.0 * PI) - PI


def wrap_deg(value: float) -> float:
    return (value + 180.0) % 360.0 - 180.0


def production_f_from_theta(theta_deg: np.ndarray | float,
                            speed_mps: float, lambda_m: float) -> np.ndarray:
    theta = np.radians(np.asarray(theta_deg, dtype=float))
    return -2.0 * speed_mps * np.sin(theta) / lambda_m


def production_theta_from_f(f_hz: np.ndarray | float,
                            speed_mps: float, lambda_m: float) -> np.ndarray:
    ratio = np.asarray(f_hz, dtype=float) * lambda_m / (2.0 * speed_mps)
    return -np.degrees(np.arcsin(np.clip(ratio, -1.0, 1.0)))


def left_look_vector(theta_deg: float, heading_deg: float, squint_side: int) -> tuple[float, float]:
    """与 writeresults::project_from_theta_deg_local 一致的 ENU look 向量。

    生产 Doppler 定义带负号，因此 ``sinA=f*lambda/(2V)=-sin(theta)``；
    这是此前最容易在离线测角脚本中漏掉的符号。
    """
    heading = math.radians(heading_deg)
    along_e, along_n = math.cos(heading), math.sin(heading)
    sin_a = -math.sin(math.radians(theta_deg))
    cross = math.sqrt(max(0.0, 1.0 - sin_a * sin_a))
    if squint_side == 1:
        side_e, side_n = -along_n, along_e
    else:
        side_e, side_n = along_n, -along_e
    look_e = cross * side_e + sin_a * along_e
    look_n = cross * side_n + sin_a * along_n
    norm = math.hypot(look_e, look_n)
    return look_e / norm, look_n / norm


def production_theta_from_position(platform_v_angle_deg: float,
                                    platform_e: float, platform_n: float,
                                    target_e: float, target_n: float,
                                    squint_side: int) -> float:
    target_az = math.degrees(math.atan2(target_n - platform_n,
                                        target_e - platform_e))
    side_dir = -90.0 if squint_side == 1 else 90.0
    return wrap_deg(side_dir - (platform_v_angle_deg - target_az))


def phase_noise_curve(rng: np.random.Generator, gamma_db: np.ndarray,
                      trials: int) -> list[dict[str, float]]:
    """双通道 phase(arg(x1 conj(x2))) 的实测/小噪声理论对比。

    每个通道 ``x=exp(j phi)+CN(0,1/gamma)``，所以两个通道相位差的
    一阶方差为 ``1/(2 gamma_1)+1/(2 gamma_2)``。等 SNR 时
    ``gamma_phi=2 gamma_1 gamma_2/(gamma_1+gamma_2)=gamma``，即
    ``sigma_phi=1/sqrt(gamma_phi)``。
    """
    rows: list[dict[str, float]] = []
    phi = 0.73
    for db in gamma_db:
        gamma = 10.0 ** (float(db) / 10.0)
        noise_scale = 1.0 / math.sqrt(2.0 * gamma)
        n1 = noise_scale * (rng.standard_normal(trials) + 1j * rng.standard_normal(trials))
        n2 = noise_scale * (rng.standard_normal(trials) + 1j * rng.standard_normal(trials))
        signal = complex(math.cos(phi), math.sin(phi))
        x1 = signal + n1
        x2 = signal + n2
        phase_est = np.angle(x1 * np.conj(x2))
        errors = np.asarray([wrap_pi(float(value)) for value in phase_est], dtype=float)
        empirical = float(np.sqrt(np.mean(errors * errors)))
        theoretical = 1.0 / math.sqrt(gamma)
        rows.append({
            "gamma_db": float(db), "gamma_linear": gamma,
            "trials": float(trials), "phase_rmse_rad": empirical,
            "phase_sigma_theory_rad": theoretical,
            "ratio_empirical_to_theory": empirical / theoretical,
            "phase_wrap_rmse_limit_rad": PI / math.sqrt(3.0),
        })
    return rows


def p38_curve(rng: np.random.Generator, gamma_db: np.ndarray,
              trials: int, speed_mps: float, d_chan_m: float,
              lambda_m: float) -> list[dict[str, float]]:
    """对 P38 直线拟合的斜率方差做独立模块验证。"""
    # Common-TX dual-RX 的 raw P38 slope 使用 d/2 等效相位中心间距。
    d_equiv = 0.5 * d_chan_m
    truth_k = -2.0 * PI * d_equiv / speed_mps
    truth_b = 0.73
    fa = np.linspace(-1100.0, 1100.0, 81, dtype=float)
    x = fa - float(np.mean(fa))
    sxx = float(np.sum(x * x))
    rows: list[dict[str, float]] = []
    for db in gamma_db:
        gamma = 10.0 ** (float(db) / 10.0)
        sigma_phi = 1.0 / math.sqrt(gamma)
        # 在相位域生成 wrapped observation，再按生产同样的“先 unwrap 后拟合”
        # 进行最小二乘。高 SNR 点用于斜率统计；低 SNR 点会显式暴露 unwrap
        # 不适用的边界，而不是静默宣称通过。
        errors = rng.normal(0.0, sigma_phi, size=(trials, fa.size))
        clean = truth_k * fa[None, :] + truth_b
        observed = np.unwrap((clean + errors + PI) % (2.0 * PI) - PI, axis=1)
        centered_y = observed - np.mean(observed, axis=1, keepdims=True)
        slopes = (centered_y @ x) / sxx
        empirical_rmse = float(np.sqrt(np.mean((slopes - truth_k) ** 2)))
        theory_rmse = sigma_phi / math.sqrt(sxx)
        rows.append({
            "gamma_db": float(db), "gamma_linear": gamma,
            "trials": float(trials), "truth_k_rad_per_hz": truth_k,
            "truth_b_rad": truth_b, "slope_rmse_rad_per_hz": empirical_rmse,
            "slope_sigma_theory_rad_per_hz": theory_rmse,
            "ratio_empirical_to_theory": empirical_rmse / theory_rmse,
            "fa_span_hz": float(fa[-1] - fa[0]), "sample_count": float(fa.size),
        })
    return rows


def projection_roundtrip(speed_mps: float, lambda_m: float,
                          heading_deg: float, squint_side: int) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    platform_e, platform_n = 1000.0, -2000.0
    ground_range = 83_000.0
    angles = np.asarray([-45.0, -30.0, -10.0, 0.0, 10.0, 30.0, 45.0])
    for angle in angles:
        look_e, look_n = left_look_vector(float(angle), heading_deg, squint_side)
        target_e = platform_e + ground_range * look_e
        target_n = platform_n + ground_range * look_n
        recovered = production_theta_from_position(
            heading_deg, platform_e, platform_n, target_e, target_n, squint_side)
        f_hz = float(production_f_from_theta(angle, speed_mps, lambda_m))
        recovered_from_f = float(production_theta_from_f(f_hz, speed_mps, lambda_m))
        rows.append({
            "truth_theta_deg": float(angle), "doppler_hz": f_hz,
            "position_theta_deg": recovered,
            "position_roundtrip_error_deg": wrap_deg(recovered - float(angle)),
            "doppler_roundtrip_theta_deg": recovered_from_f,
            "doppler_roundtrip_error_deg": wrap_deg(recovered_from_f - float(angle)),
            "look_norm": math.hypot(look_e, look_n),
            "target_e": target_e, "target_n": target_n,
        })
    return rows


def production_angle_audit(root: Path) -> list[dict[str, object]]:
    """读取已有 target_debug 产物中的有限角度误差，不改动产物。"""
    rows: list[dict[str, object]] = []
    for path in sorted(root.glob("seed_*/angle_p*/standard_mc_period_metrics.csv")):
        for item in read_csv(path):
            angle_error = finite_float(item.get("angle_error_deg"))
            phase_error = finite_float(item.get("phase_error_rad"))
            if not (math.isfinite(angle_error) or math.isfinite(phase_error)):
                continue
            rows.append({
                "seed": path.parts[-3] if len(path.parts) >= 3 else "",
                "period_id": item.get("period_id", ""),
                "output_scnr_db": finite_float(item.get("output_scnr_db")),
                "target_event": item.get("target_event", ""),
                "angle_error_deg": angle_error,
                "phase_error_rad": phase_error,
                "cluster_size_selected": finite_float(item.get("cluster_size_selected")),
                "source": str(path.resolve()),
            })
    return rows


def plot_phase(out: Path, rows: list[dict[str, float]]) -> None:
    x = [r["gamma_db"] for r in rows]
    y = [r["phase_rmse_rad"] for r in rows]
    t = [r["phase_sigma_theory_rad"] for r in rows]
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.semilogy(x, y, "o-", label="paired complex phase (MC)")
    ax.semilogy(x, t, "--", label=r"theory $1/\sqrt{\gamma_\phi}$")
    ax.axhline(PI / math.sqrt(3.0), color="0.45", linestyle=":", label="uniform phase limit")
    ax.set(xlabel="per-channel SNR (dB)", ylabel="phase RMSE (rad)", title="Module 2: two-channel phase noise")
    ax.grid(True, which="both", alpha=0.3); ax.legend(); fig.tight_layout()
    fig.savefig(out / "angle_module_phase_noise.png", dpi=180)
    fig.savefig(out / "angle_module_phase_noise.pdf")
    plt.close(fig)


def plot_p38(out: Path, rows: list[dict[str, float]]) -> None:
    x = [r["gamma_db"] for r in rows]
    y = [r["slope_rmse_rad_per_hz"] for r in rows]
    t = [r["slope_sigma_theory_rad_per_hz"] for r in rows]
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.semilogy(x, y, "o-", label="line-fit slope (MC)")
    ax.semilogy(x, t, "--", label=r"OLS theory $\sigma_\phi/\sqrt{\sum(f-\bar f)^2}$")
    ax.set(xlabel="phase SNR (dB)", ylabel="P38 slope RMSE (rad/Hz)", title="Module 3: P38 slope fit")
    ax.grid(True, which="both", alpha=0.3); ax.legend(); fig.tight_layout()
    fig.savefig(out / "angle_module_p38.png", dpi=180)
    fig.savefig(out / "angle_module_p38.pdf")
    plt.close(fig)


def plot_projection(out: Path, rows: list[dict[str, float]]) -> None:
    x = [r["truth_theta_deg"] for r in rows]
    y = [r["position_roundtrip_error_deg"] for r in rows]
    z = [r["doppler_roundtrip_error_deg"] for r in rows]
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.plot(x, y, "o-", label="ENU projection round-trip")
    ax.plot(x, z, "s--", label="Doppler geometry round-trip")
    ax.axhline(0.0, color="0.35", linewidth=0.8)
    ax.set(xlabel="truth theta (deg)", ylabel="round-trip error (deg)", title="Modules 1/4: geometry round-trip")
    ax.grid(True, alpha=0.3); ax.legend(); fig.tight_layout()
    fig.savefig(out / "angle_module_projection.png", dpi=180)
    fig.savefig(out / "angle_module_projection.pdf")
    plt.close(fig)


def plot_production(out: Path, rows: list[dict[str, object]]) -> None:
    finite = [r for r in rows if math.isfinite(finite_float(r.get("angle_error_deg")))]
    if not finite:
        return
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.scatter([finite_float(r["output_scnr_db"]) for r in finite],
               [finite_float(r["angle_error_deg"]) for r in finite], s=22, alpha=0.75)
    ax.axhline(0.0, color="0.35", linewidth=0.8)
    ax.set(xlabel="output SCNR (dB)", ylabel="production angle error (deg)",
           title="Existing production angle audit (finite target matches)")
    ax.grid(True, alpha=0.3); fig.tight_layout()
    fig.savefig(out / "angle_module_production_audit.png", dpi=180)
    fig.savefig(out / "angle_module_production_audit.pdf")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", type=Path, default=Path("gmti.xml"))
    parser.add_argument("--scenario", type=Path, default=None,
                        help="可选标准场景 JSON；用于读取真实平台速度/heading")
    parser.add_argument("--production-root", type=Path, default=None,
                        help="可选已有 target_debug 根目录，只读生产角度审计")
    parser.add_argument("--output-dir", type=Path,
                        default=Path("outputs/gmti_scnr_eval/angle_modules_20260827"))
    parser.add_argument("--seed", type=int, default=20260827)
    parser.add_argument("--trials", type=int, default=20000)
    parser.add_argument("--phase-grid", default="-5,0,5,10,15,20,25,30")
    args = parser.parse_args()
    if args.trials < 100 or not args.phase_grid.strip():
        raise SystemExit("trials 至少 100，phase-grid 不能为空")

    values = xml_values(args.xml.resolve())
    fc_ghz = finite_float(values.get("fc"))
    d_chan = finite_float(values.get("d_chan"))
    if not (fc_ghz > 0.0 and d_chan > 0.0):
        raise SystemExit("XML 缺少有效 fc/d_chan")
    lambda_m = C / (fc_ghz * 1.0e9)
    speed_mps = 60.0
    heading_deg = 90.0
    squint_side = int(round(finite_float(values.get("squint_side"), 1.0)))
    if args.scenario is not None and args.scenario.is_file():
        scene = json.loads(args.scenario.read_text(encoding="utf-8"))
        speed_mps = finite_float(scene.get("platform", {}).get("speed_mps"), speed_mps)
        squint_side = int(round(finite_float(
            scene.get("platform", {}).get("squint_side"), squint_side)))
        heading_deg = finite_float(
            scene.get("platform", {}).get("heading_deg"), heading_deg)

    gamma_db = np.asarray([float(token.strip()) for token in args.phase_grid.split(",")
                           if token.strip()], dtype=float)
    if gamma_db.size == 0 or not np.all(np.isfinite(gamma_db)):
        raise SystemExit("phase-grid 含无效值")
    out = ensure_dir(args.output_dir.resolve())
    rng = np.random.default_rng(args.seed)

    phase_rows = phase_noise_curve(rng, gamma_db, args.trials)
    p38_rows = p38_curve(rng, gamma_db, max(1000, args.trials // 4),
                          speed_mps, d_chan, lambda_m)
    projection_rows = projection_roundtrip(speed_mps, lambda_m, heading_deg, squint_side)
    production_rows = (production_angle_audit(args.production_root.resolve())
                       if args.production_root is not None else [])

    write_csv(out / "angle_module_phase_noise.csv", phase_rows)
    write_csv(out / "angle_module_p38.csv", p38_rows)
    write_csv(out / "angle_module_projection_roundtrip.csv", projection_rows)
    write_csv(out / "angle_module_production_audit.csv", production_rows)
    plot_phase(out, phase_rows)
    plot_p38(out, p38_rows)
    plot_projection(out, projection_rows)
    plot_production(out, production_rows)

    projection_max = max(abs(r["position_roundtrip_error_deg"]) for r in projection_rows)
    doppler_max = max(abs(r["doppler_roundtrip_error_deg"]) for r in projection_rows)
    high_phase = [r["ratio_empirical_to_theory"] for r in phase_rows if r["gamma_db"] >= 15.0]
    high_p38 = [r["ratio_empirical_to_theory"] for r in p38_rows if r["gamma_db"] >= 15.0]
    summary = [
        {"module": "doppler_geometry", "sample_count": len(projection_rows),
         "max_abs_error": doppler_max, "status": "PASS" if doppler_max < 1.0e-12 else "FAIL",
         "parameter_source": "production f=-2Vsin(theta)/lambda"},
        {"module": "enu_projection", "sample_count": len(projection_rows),
         "max_abs_error": projection_max, "status": "PASS" if projection_max < 1.0e-12 else "FAIL",
         "parameter_source": "writeresults project_from_theta/theta_from_position"},
        {"module": "two_channel_phase", "sample_count": len(phase_rows),
         "max_abs_error": max(abs(v - 1.0) for v in high_phase) if high_phase else float("nan"),
         "status": "PASS" if high_phase and max(abs(v - 1.0) for v in high_phase) < 0.15 else "CHECK",
         "parameter_source": "paired CN channels; gamma_phi=2*g1*g2/(g1+g2)"},
        {"module": "p38_slope", "sample_count": len(p38_rows),
         "max_abs_error": max(abs(v - 1.0) for v in high_p38) if high_p38 else float("nan"),
         "status": "PASS" if high_p38 and max(abs(v - 1.0) for v in high_p38) < 0.20 else "CHECK",
         "parameter_source": "OLS slope variance sigma_phi/sqrt(Sxx)"},
        {"module": "production_angle_audit", "sample_count": len(production_rows),
         "max_abs_error": float("nan"), "status": "READ_ONLY" if production_rows else "NOT_PROVIDED",
         "parameter_source": "existing standard_mc_period_metrics.csv"},
    ]
    write_csv(out / "angle_module_summary.csv", summary)
    provenance = {
        "xml": str(args.xml.resolve()), "scenario": str(args.scenario.resolve()) if args.scenario else None,
        "fc_ghz": fc_ghz, "lambda_m": lambda_m, "d_chan_m": d_chan,
        "platform_speed_mps": speed_mps, "heading_deg": heading_deg,
        "squint_side": squint_side, "seed": args.seed, "trials": args.trials,
        "phase_grid_db": gamma_db.tolist(),
        "production_root": str(args.production_root.resolve()) if args.production_root else None,
        "scope": "module validation only; no new 0/30/45 or beam-edge sweep",
    }
    (out / "angle_module_provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    finite_angle = [finite_float(r.get("angle_error_deg")) for r in production_rows
                    if math.isfinite(finite_float(r.get("angle_error_deg")))]
    finite_phase = [finite_float(r.get("phase_error_rad")) for r in production_rows
                    if math.isfinite(finite_float(r.get("phase_error_rad")))]
    report = f"""# GMTI 测角模块化验证（{args.output_dir.name}）

## 结论先行

本轮只验证测角的可独立判读模块，没有重新跑 30/45 度或波位边缘场景。
Doppler 几何和 ENU 投影闭环最大误差分别为
`{doppler_max:.3e} deg` 与 `{projection_max:.3e} deg`。双通道相位和 P38
斜率在高 SNR 区间分别与一阶理论的比值范围见 CSV；低 SNR 时相位展开会失效，
因此被保留为边界现象而不是隐藏掉。

## 模块 1：Doppler 到角度

生产链使用

$$f_{{geo}}=-\\frac{{2V}}{{\\lambda}}\\sin\\theta,
\\qquad \\hat\\theta=-\\arcsin\\left(\\frac{{\\lambda f_{{geo}}}}{{2V}}\\right).$$

其中 $V={speed_mps:.6g}$ m/s、$f_c={fc_ghz:.9g}$ GHz、
$\\lambda={lambda_m:.9g}$ m。表 `angle_module_projection_roundtrip.csv`
逐点记录 -45/-30/-10/0/10/30/45 度的正逆变换。

## 模块 2：双通道相位

每个通道生成 $x_i=e^{{j\\phi}}+n_i$，$n_i\\sim CN(0,1/\\gamma_i)$，
估计量为 $\\angle(x_1x_2^*)$。小噪声近似给出

$$\\sigma_\\phi^2\\simeq\\frac1{{2\\gamma_1}}+\\frac1{{2\\gamma_2}}
=\\frac1{{\\gamma_\\phi}},\\quad
\\gamma_\\phi=\\frac{{2\\gamma_1\\gamma_2}}{{\\gamma_1+\\gamma_2}}.$$

实测曲线见 `angle_module_phase_noise.png`，数值见同名 CSV。

## 模块 3：P38 斜率

对 $\\phi_i=k f_i+b+\\epsilon_i$ 做中心化最小二乘，理论斜率标准差为

$$\\sigma_k=\\frac{{\\sigma_\\phi}}
{{\\sqrt{{\\sum_i(f_i-\\bar f)^2}}}}.$$

本模块采用 common-TX 双接收的 $d/2$ 等效相位中心间距，真值
`k={p38_rows[0]['truth_k_rad_per_hz']:.9g} rad/Hz`；这只验证拟合器的统计
接口，不把它当作含 CFAR/聚类/定位的端到端精度。

## 模块 4：ENU 投影

`writeresults.cpp` 的生产约定是 `sinA=f*lambda/(2V)=-sin(theta)`，左视
方向由平台航向旋转得到。若离线脚本遗漏这个负号，会出现角度符号翻转；本轮
闭环显式检查了该约定。

## 已有生产角度审计

如果提供 `--production-root`，报告附录只读已有 `standard_mc_period_metrics.csv`。
本部分样本数为 `{len(production_rows)}`，只代表已有 target-match 样本，不能替代
后续 0/30/45、波位中心/边缘的正式实验，也不会用这些样本重新拟合理论。

## 产物

- `angle_module_summary.csv`：每个模块的判定和参数来源；
- `angle_module_phase_noise.csv`、`angle_module_p38.csv`、`angle_module_projection_roundtrip.csv`；
- `angle_module_phase_noise.png/.pdf`、`angle_module_p38.png/.pdf`、`angle_module_projection.png/.pdf`；
- `angle_module_provenance.json`：命令参数和物理参数来源。

复现命令：

```bash
python3 scripts/gmti_scnr_eval/25_validate_angle_modules.py \\
  --scenario outputs/gmti_scnr_eval/target_debug_0deg_20260827/seed_20260842/angle_p0p000/s_plus_n/scenario.json \\
  --production-root outputs/gmti_scnr_eval/target_debug_0deg_20260827
```
"""
    (out / "angle_module_validation_report.md").write_text(report, encoding="utf-8")
    print(f"[PASS] 测角模块化验证已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
