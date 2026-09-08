#!/usr/bin/env python3
"""以共同 Doppler/P38/CSI 参考运行隔离 S-only、C+N、S+C+N 配对标定。

S-only 保留与另两组完全相同的 Stage2 杂波/噪声包作目标幅度参考，但由
``scene.signal_only`` 在注入前清空 wire payload；因此三个 variant 的目标信号
严格相同，而 C+N/S+C+N 仍使用同一统计杂波与热噪声实现。C+N 只运行一次；每个
目标独立运行其 S-only/S+C+N 对，避免同一原始 LFM 中多目标旁瓣互相污染。随后将
C+N 审计出的 wrapped Doppler 中心、CSI P38 与两段距离相位校正冻结到另两组；不要求
S-only 通过最终检测/聚类筛选，也绝不按 S+C+N 的峰值重选位置。
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from scnr_eval_lib import ensure_dir, read_csv, sha256_file, write_csv


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]


def number(row: dict[str, str], key: str, default: float = float("nan")) -> float:
    try:
        value = row.get(key, "")
        return float(value) if value not in {"", None} else default
    except ValueError:
        return default


def integer(row: dict[str, str], key: str, default: int = -1) -> int:
    value = number(row, key, float(default))
    return int(round(value)) if math.isfinite(value) else default


def db_ratio(numerator: float, denominator: float) -> float:
    return 10.0 * math.log10(numerator / denominator) if numerator > 0.0 and denominator > 0.0 else float("nan")


def command(argv: list[str], log: Path) -> None:
    ensure_dir(log.parent)
    with log.open("w", encoding="utf-8") as stream:
        stream.write("$ " + " ".join(argv) + "\n\n")
        result = subprocess.run(argv, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                text=True, check=False)
        stream.write(f"\n[exit_code]={result.returncode}\n")
    if result.returncode:
        raise RuntimeError(f"配对标定命令失败：{' '.join(argv)}；见 {log}")


def write_variant(base: dict, variant_dir: Path, name: str, target_enabled: bool,
                  selected_target_id: str | None = None) -> Path:
    document = copy.deepcopy(base)
    stage2_dir = variant_dir / "stage2"
    document["case_id"] = f"{base.get('case_id', 'go_scnr')}_{name}"
    document["output_dir"] = str(stage2_dir)
    scene = document.setdefault("scene", {})
    # Stage2 在该标记下先冻结完整 C+N 包以计算目标幅度，随后只清空其 payload。
    # 不改 scene.mode，确保三组共享同一统计杂波/热噪声及 packet-addressed 随机性。
    scene["signal_only"] = name == "s_only"
    for target in document.get("targets", []):
        target["enabled"] = target_enabled and (
            selected_target_id is None or target.get("target_id") == selected_target_id)
    ensure_dir(variant_dir)
    scenario = variant_dir / "scenario.json"
    scenario.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return scenario


def run_variant(name: str, scenario: Path, out: Path, args: argparse.Namespace,
                doppler_center_override_hz: float | None = None,
                p38_csi_override: tuple[float, float] | None = None,
                range_phase_override: tuple[Path, Path] | None = None) -> dict:
    argv = [
        sys.executable, str(SCRIPT_DIR / "04_run_production_case.py"),
        "--scenario", str(scenario), "--build-dir", str(args.build_dir),
        "--diagnostic-beams", args.diagnostic_beams,
        "--pfa", str(args.pfa), "--cfar-guard", str(args.cfar_guard),
        "--cfar-background", str(args.cfar_background),
        "--csi-metrics-enable", "--csi-metrics-dump-power-maps",
    ]
    if doppler_center_override_hz is not None:
        argv.extend(["--doppler-center-override-hz", repr(doppler_center_override_hz)])
    if p38_csi_override is not None:
        argv.extend(["--p38-csi-override-k-rad-per-hz", repr(p38_csi_override[0]),
                     "--p38-csi-override-b-rad", repr(p38_csi_override[1])])
    if range_phase_override is not None:
        argv.extend(["--paired-raw-range-phase-override-f32", str(range_phase_override[0]),
                     "--paired-csi-range-phase-override-f32", str(range_phase_override[1])])
    if args.delete_raw_after_success:
        argv.append("--delete-raw-after-success")
    command(argv, out / "logs" / f"run_{name}.log")
    spec = json.loads(scenario.read_text(encoding="utf-8"))
    stage2 = Path(spec["output_dir"])
    stage2 = stage2 if stage2.is_absolute() else ROOT / stage2
    return {"name": name, "scenario": scenario, "stage2": stage2,
            "algorithm": stage2 / "algorithm_result" / "period_0000"}


def cfar_doppler_center(run: dict) -> float:
    """Read the C+N wrapped center that controls the paired production operator."""
    values: list[float] = []
    for meta in sorted((run["algorithm"] / "debug").glob("cfar_*_meta.txt")):
        for line in meta.read_text(encoding="utf-8", errors="replace").splitlines():
            key, separator, value = line.partition("=")
            if key == "doppler_axis_center_wrapped_hz" and separator:
                try:
                    parsed = float(value)
                except ValueError:
                    continue
                if math.isfinite(parsed):
                    values.append(parsed)
    if not values:
        raise RuntimeError(f"{run['name']} 缺少 CFAR Doppler 中心审计 sidecar")
    if max(values) - min(values) > 1.0e-6:
        raise RuntimeError(f"{run['name']} 的 CFAR Doppler 中心不唯一：{values}")
    return values[0]


def csi_p38_fit(run: dict) -> tuple[float, float]:
    """Read the C+N final-CSI P38 fit persisted by the production tap."""
    rows = metrics_rows(run)
    values = [(number(row, "p38_k_rad_per_hz"), number(row, "p38_b_rad"))
              for row in rows]
    values = [(k, b) for k, b in values if math.isfinite(k) and math.isfinite(b)]
    if len(values) != 1:
        raise RuntimeError(f"{run['name']} 缺少唯一的 CSI P38 参考参数：{values}")
    return values[0]


def range_phase_reference(run: dict) -> tuple[Path, Path]:
    """Return the exact C+N range-phase vectors emitted by the production path."""
    debug = run["algorithm"] / "debug"
    raw = sorted(debug.glob("paired_range_phase_raw_period*_beam*.f32"))
    csi = sorted(debug.glob("paired_range_phase_csi_period*_beam*.f32"))
    if len(raw) != 1 or len(csi) != 1:
        raise RuntimeError(
            f"{run['name']} 缺少唯一的 C+N 距离相位参考：raw={raw}, csi={csi}")
    if raw[0].stat().st_size == 0 or raw[0].stat().st_size != csi[0].stat().st_size:
        raise RuntimeError(f"{run['name']} 的 C+N 距离相位参考长度非法")
    return raw[0].resolve(), csi[0].resolve()


def metrics_rows(run: dict) -> list[dict[str, str]]:
    manifests = sorted((run["algorithm"] / "csi_metrics").rglob("csi_roi_manifest.csv"))
    if len(manifests) != 1:
        raise RuntimeError(f"{run['name']} 应恰有一个 csi_roi_manifest.csv，实际为 {manifests}")
    rows = list(read_csv(manifests[0]))
    if not rows:
        raise RuntimeError(f"{run['name']} 的 CSI 指标清单为空：{manifests[0]}")
    return rows


def resolve_map(row: dict[str, str], field: str, manifest: Path) -> Path:
    value = row.get(field, "")
    if not value:
        raise RuntimeError(f"CSI 清单缺少 {field}；请使用含通道功率图的新构建")
    path = Path(value)
    return path if path.is_absolute() else manifest.parent / path


def cfar_power_for_beam(run: dict, beam_id: int, shape: tuple[int, int]) -> np.ndarray:
    """Read the exact stitched CFAR input power surface used by production.

    In split-band mode the in-band cells use CSI while the out-of-band cells
    use channel-2.  The CSI metrics tap is intentionally CSI-only, so it is
    not a valid unit-Pd surface for an out-of-band truth cell.  The debug
    CFAR power map is the post-branch stitched surface passed to the real
    GO-CFAR kernel and therefore owns the detection-SCNR definition.
    """
    debug = run["algorithm"] / "debug"
    candidates = sorted(debug.glob(f"cfar_GMTI*_beam{beam_id:03d}_power.f32"))
    if not candidates:
        candidates = sorted(debug.glob(f"cfar_beam{beam_id:03d}_power.f32"))
    if len(candidates) != 1:
        raise RuntimeError(f"{run['name']} 缺少唯一 production CFAR 功率图 beam={beam_id}: {candidates}")
    raw = np.fromfile(candidates[0], dtype=np.float32)
    expected = shape[0] * shape[1]
    if raw.size != expected:
        raise RuntimeError(f"CFAR 功率图长度不匹配：{candidates[0]} ({raw.size}!={expected})")
    return raw.reshape(shape)


def cfar_complex_for_beam(run: dict, beam_id: int,
                          shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Read the audited complex input of the production stitched CFAR branch."""
    debug = run["algorithm"] / "debug"
    real_paths = sorted(debug.glob(f"cfar_GMTI*_beam{beam_id:03d}_complex_real.f32"))
    imag_paths = sorted(debug.glob(f"cfar_GMTI*_beam{beam_id:03d}_complex_imag.f32"))
    if not real_paths:
        real_paths = sorted(debug.glob(f"cfar_beam{beam_id:03d}_complex_real.f32"))
        imag_paths = sorted(debug.glob(f"cfar_beam{beam_id:03d}_complex_imag.f32"))
    if len(real_paths) != 1 or len(imag_paths) != 1:
        raise RuntimeError(
            f"{run['name']} 缺少唯一 production CFAR 复数输入图 beam={beam_id}: "
            f"real={real_paths}, imag={imag_paths}")
    real = np.fromfile(real_paths[0], dtype=np.float32)
    imag = np.fromfile(imag_paths[0], dtype=np.float32)
    expected = shape[0] * shape[1]
    if real.size != expected or imag.size != expected:
        raise RuntimeError(f"CFAR 复数输入图长度不匹配：{real_paths[0]}, {imag_paths[0]}")
    return real.reshape(shape), imag.reshape(shape)


def map_for_beam(run: dict, beam_id: int) -> dict[str, np.ndarray]:
    manifests = sorted((run["algorithm"] / "csi_metrics").rglob("csi_roi_manifest.csv"))
    manifest = manifests[0]
    rows = list(read_csv(manifest))
    matches = [row for row in rows if integer(row, "beam_id") == beam_id]
    # 单波位标定中 processOnePeriod 的内部 beam ID 可因调用层而取 0/1；
    # 只在清单确实仅有一个波位时安全地回退。
    if not matches and len(rows) == 1:
        matches = rows
    if len(matches) != 1:
        raise RuntimeError(f"{run['name']} 找不到唯一 beam={beam_id} 的 CSI 功率图：{manifest}")
    row = matches[0]
    result = {
        "after": np.load(resolve_map(row, "after_power_path", manifest)),
        "after_real": np.load(resolve_map(row, "after_real_path", manifest)),
        "after_imag": np.load(resolve_map(row, "after_imag_path", manifest)),
        "channel1": np.load(resolve_map(row, "channel1_power_path", manifest)),
        "channel2": np.load(resolve_map(row, "channel2_power_path", manifest)),
        "ctdr_channel1_real": np.load(resolve_map(row, "ctdr_channel1_real_path", manifest)),
        "ctdr_channel1_imag": np.load(resolve_map(row, "ctdr_channel1_imag_path", manifest)),
        "ctdr_channel2_real": np.load(resolve_map(row, "ctdr_channel2_real_path", manifest)),
        "ctdr_channel2_imag": np.load(resolve_map(row, "ctdr_channel2_imag_path", manifest)),
        "fa_axis_hz": np.load(resolve_map(row, "fa_axis_path", manifest)),
    }
    result["cfar_power"] = cfar_power_for_beam(run, beam_id, result["after"].shape)
    cfar_real, cfar_imag = cfar_complex_for_beam(run, beam_id, result["after"].shape)
    result["cfar_complex_real"] = cfar_real
    result["cfar_complex_imag"] = cfar_imag
    return result


def truth_locations(run: dict) -> list[dict[str, object]]:
    truth_path = run["stage2"] / "truth" / "truth_targets_by_beam.csv"
    if not truth_path.is_file():
        raise RuntimeError("S-only 缺少 truth_targets_by_beam.csv")
    locations: list[dict[str, object]] = []
    for truth in read_csv(truth_path):
        if truth.get("visible", "1") in {"0", "false", "False"}:
            continue
        target_id = truth.get("target_id", "")
        expected = integer(truth, "range_bin", integer(truth, "expected_bin"))
        truth_doppler = number(truth, "af_total_truth_hz")
        if not target_id or expected < 0 or not math.isfinite(truth_doppler):
            raise RuntimeError(f"S-only truth 缺少 target/range/Doppler：{truth}")
        locations.append({
            "target_id": target_id, "beam_id": integer(truth, "beam_id"),
            "truth_angle_deg": number(truth, "azimuth_deg"),
            "configured_target_snr_db": number(truth, "snr_db"),
            "truth_range_bin": expected, "truth_doppler_hz": truth_doppler,
        })
    return locations


def physical_doppler_row(fa_axis_hz: np.ndarray, truth_doppler_hz: float,
                         prf_hz: float) -> int:
    """Return the production row nearest the physical truth Doppler modulo PRF."""
    if fa_axis_hz.ndim != 1 or fa_axis_hz.size == 0 or not math.isfinite(truth_doppler_hz) or prf_hz <= 0.0:
        raise RuntimeError("无效的 production fa_axis 或 physical truth Doppler")
    circular_difference = np.remainder(fa_axis_hz.astype(np.float64) - truth_doppler_hz + 0.5 * prf_hz,
                                        prf_hz) - 0.5 * prf_hz
    return int(np.argmin(np.abs(circular_difference)))


def go_directional_background(power: np.ndarray, row: int, col: int,
                              guard: int, background: int) -> dict[str, float]:
    """Return the exact four production GO training-strip means at one CUT.

    The production detector uses the maximum of the four *directional* means,
    not the C+N value that happens to be at the CUT.  The latter is a single
    exponential-like draw and made the former calibration non-monotonic.  This
    routine mirrors ``cfar_detect_kernel``: Doppler wraps and range does not.
    """
    if power.ndim != 2 or guard < 0 or background <= 0:
        raise RuntimeError("GO 训练窗输入或参数非法")
    rows, cols = power.shape
    outer = guard + background
    if not (0 <= row < rows and outer <= col < cols - outer):
        raise RuntimeError(f"truth CUT ({row},{col}) 不具备完整的非环绕 GO 训练窗")

    def block(row_indices: np.ndarray, col_start: int, col_stop: int) -> float:
        # np.ix_ avoids inadvertently pairing row/column index arrays.  Row
        # wrapping follows the production Doppler-axis circular-CFAR setting.
        values = power[np.ix_(row_indices % rows, np.arange(col_start, col_stop))]
        if values.size == 0 or not np.all(np.isfinite(values)):
            raise RuntimeError("GO 训练窗出现空值或非有限功率")
        return float(np.mean(values, dtype=np.float64))

    full_rows = np.arange(row - outer, row + outer + 1)
    left = block(full_rows, col - outer, col - guard)
    right = block(full_rows, col + guard + 1, col + outer + 1)
    top = block(np.arange(row - outer, row - guard), col - outer, col + outer + 1)
    bottom = block(np.arange(row + guard + 1, row + outer + 1), col - outer, col + outer + 1)
    reference = max(left, right, top, bottom)
    return {
        "go_left_mean_power": left,
        "go_right_mean_power": right,
        "go_top_mean_power": top,
        "go_bottom_mean_power": bottom,
        "go_reference_power": reference,
    }


def go_hits_at_truth(run: dict, beam: int, row: int, col: int,
                     shape: tuple[int, int]) -> dict[str, int]:
    """Read the auditable production GO hit map at exact and resolution-cell CUT.

    The exact CUT is the cleanest comparison with the cell-Pd model.  The
    two-cell resolution event is retained separately for target-level joining;
    it does not replace the exact event in the theoretical comparison.
    """
    candidates = sorted((run["algorithm"] / "debug").glob(f"cfar_GMTI*_beam{beam:03d}_hits.f32"))
    if len(candidates) != 1:
        raise RuntimeError(f"{run['name']} 缺少唯一 GO hit 图 beam={beam}: {candidates}")
    raw = np.fromfile(candidates[0], dtype=np.float32)
    if raw.size != shape[0] * shape[1]:
        raise RuntimeError(f"GO hit 图长度不匹配：{candidates[0]}")
    hits = raw.reshape(shape)
    exact = int(hits[row, col] > 0.0)
    resolved = 0
    for delta_row in range(-2, 3):
        for delta_col in range(-2, 3):
            candidate_col = col + delta_col
            if 0 <= candidate_col < shape[1] and hits[(row + delta_row) % shape[0], candidate_col] > 0.0:
                resolved = 1
                break
        if resolved:
            break
    return {"unit_go_cfar_exact_cut_hit": exact, "unit_go_cfar_resolution_hit": resolved}


def signal_template_peak(signal_power: np.ndarray, physical_row: int, physical_col: int,
                         row_half_width: int = 4, col_half_width: int = 3) -> tuple[int, int, int, int]:
    """Locate the deterministic target-template main-lobe from S-only CFAR input.

    ``row_truth`` is a continuous-Doppler bin projection, while the actual
    FFT peak can fall in an adjacent bin.  We therefore use the S-only,
    no-background template to pick its maximum within a fixed resolution
    stencil.  This is fixed before S+C+N is inspected; it is not final-output
    peak picking and cannot select a clutter/noise fluctuation.
    """
    if signal_power.ndim != 2:
        raise RuntimeError("S-only production CFAR 功率图不是二维矩阵")
    rows, cols = signal_power.shape
    if not (0 <= physical_row < rows and 0 <= physical_col < cols):
        raise RuntimeError("物理 truth CUT 越出 S-only CFAR 图")
    if physical_col - col_half_width < 0 or physical_col + col_half_width >= cols:
        raise RuntimeError("truth CUT 附近无法容纳固定信号模板窗")
    row_indices = (np.arange(physical_row - row_half_width, physical_row + row_half_width + 1) % rows)
    col_indices = np.arange(physical_col - col_half_width, physical_col + col_half_width + 1)
    window = signal_power[np.ix_(row_indices, col_indices)]
    if not np.all(np.isfinite(window)):
        raise RuntimeError("S-only 信号模板窗包含非有限功率")
    flat = int(np.argmax(window))
    row_index, col_index = np.unravel_index(flat, window.shape)
    peak_row, peak_col = int(row_indices[row_index]), int(col_indices[col_index])
    if row_index in {0, window.shape[0] - 1} or col_index in {0, window.shape[1] - 1}:
        raise RuntimeError("S-only 主瓣落在固定模板窗边界；拒绝静默扩大或偏置选点")
    row_offset = peak_row - physical_row
    if row_offset > rows // 2:
        row_offset -= rows
    elif row_offset < -rows // 2:
        row_offset += rows
    return peak_row, peak_col, row_offset, peak_col - physical_col


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-scenario", type=Path, required=True,
                        help="由 04_make_compact_scenario.py 生成的单周期紧凑统计场景")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--diagnostic-beams", default="1")
    parser.add_argument("--pfa", type=float, default=1e-7)
    parser.add_argument("--cfar-guard", type=int, default=4)
    parser.add_argument("--cfar-background", type=int, default=16)
    parser.add_argument("--replicas-per-scnr", type=int, default=0,
                        help="每个角度/注入档参与隔离配对的副本数；0 表示场景中的全部副本")
    parser.add_argument("--delete-raw-after-success", action="store_true",
                        help="仅在已获授权时删除三个成功 variant 的 raw BIN")
    args = parser.parse_args()
    scenario = args.base_scenario.resolve()
    if not scenario.is_file():
        raise SystemExit(f"找不到 base scenario：{scenario}")
    if not (args.build_dir / "simulate_stage2_statistical").is_file() or not (args.build_dir / "GMTI_core").is_file():
        raise SystemExit("找不到已构建的 simulate_stage2_statistical/GMTI_core")
    base = json.loads(scenario.read_text(encoding="utf-8"))
    if int(base.get("random", {}).get("period_count", 0)) != 1:
        raise SystemExit("配对输出 SCNR 标定要求单周期 base scenario")
    if args.replicas_per_scnr < 0:
        raise SystemExit("--replicas-per-scnr 不能为负")
    out = ensure_dir(args.output_dir.resolve())
    # 同一原始 LFM 内的多个目标在脉压后仍有旁瓣和相干叠加；即便 CUT 相隔 80
    # 个 bin，也不能把它们同时启用后各自当作独立 S-only 样本。C+N 只需跑一次，
    # 每个目标则单独跑 S-only/S+C+N，得到真正隔离的配对输出量纲。
    c_plus_n_scenario = write_variant(
        base, out / "variants" / "c_plus_n", "c_plus_n", False)
    c_plus_n_run = run_variant("c_plus_n", c_plus_n_scenario, out, args)
    reference_center_hz = cfar_doppler_center(c_plus_n_run)
    reference_csi_p38 = csi_p38_fit(c_plus_n_run)
    reference_range_phase = range_phase_reference(c_plus_n_run)
    selected_targets: list[dict] = []
    selected_per_group: dict[tuple[int, float], int] = defaultdict(int)
    for target in base.get("targets", []):
        if not target.get("enabled", True):
            continue
        target_id = str(target.get("target_id", ""))
        init = target.get("init", {})
        amplitude = target.get("amplitude", {})
        beam = int(init.get("beam_id", -1))
        snr = float(amplitude.get("snr_db", float("nan")))
        if not target_id or beam < 1 or not math.isfinite(snr):
            raise SystemExit(f"base scenario target 字段不完整：{target}")
        key = (beam, snr)
        if args.replicas_per_scnr and selected_per_group[key] >= args.replicas_per_scnr:
            continue
        selected_per_group[key] += 1
        selected_targets.append(target)
    if not selected_targets:
        raise SystemExit("没有可用于隔离配对标定的 target")
    prf_hz = float(base["waveform"]["prf_hz"])
    c_plus_n_maps: dict[int, dict[str, np.ndarray]] = {}
    isolated_variants: list[dict[str, str]] = []
    records: list[dict[str, object]] = []
    for target in selected_targets:
        target_id = str(target["target_id"])
        target_dir = out / "variants" / "targets" / target_id
        s_only_scenario = write_variant(base, target_dir / "s_only", "s_only", True, target_id)
        combined_scenario = write_variant(
            base, target_dir / "s_plus_c_plus_n", "s_plus_c_plus_n", True, target_id)
        s_only_run = run_variant(f"s_only_{target_id}", s_only_scenario, out, args,
                                 reference_center_hz, reference_csi_p38, reference_range_phase)
        combined_run = run_variant(f"s_plus_c_plus_n_{target_id}", combined_scenario, out, args,
                                   reference_center_hz, reference_csi_p38, reference_range_phase)
        locations = truth_locations(s_only_run)
        if len(locations) != 1 or locations[0]["target_id"] != target_id:
            raise RuntimeError(f"{target_id} 的隔离 S-only truth 不唯一：{locations}")
        location = locations[0]
        beam = int(location["beam_id"])
        col = int(location["truth_range_bin"])
        truth_doppler_hz = float(location["truth_doppler_hz"])
        c_plus_n_maps.setdefault(beam, map_for_beam(c_plus_n_run, beam))
        maps = {"c_plus_n": c_plus_n_maps[beam],
                "s_only": map_for_beam(s_only_run, beam),
                "s_plus_c_plus_n": map_for_beam(combined_run, beam)}
        reference_axis = maps["c_plus_n"]["fa_axis_hz"]
        for name, matrix_set in maps.items():
            axis = matrix_set["fa_axis_hz"]
            if axis.shape != reference_axis.shape or not np.allclose(axis, reference_axis,
                                                                       rtol=0.0, atol=1.0e-5):
                raise RuntimeError(
                    f"{name} 与 C+N 的 production Doppler 轴不一致；拒绝生成伪配对 SCNR")
        physical_rows: dict[str, int] = {}
        for name, matrix_set in maps.items():
            row = physical_doppler_row(matrix_set["fa_axis_hz"], truth_doppler_hz, prf_hz)
            physical_rows[name] = row
        template_row, template_col, template_row_offset, template_col_offset = signal_template_peak(
            maps["s_only"]["cfar_power"], physical_rows["s_only"], col)
        samples: dict[str, float] = {}
        for name, matrix_set in maps.items():
            for channel, matrix in matrix_set.items():
                if channel == "fa_axis_hz":
                    continue
                if matrix.ndim != 2 or not (0 <= template_row < matrix.shape[0] and
                                             0 <= template_col < matrix.shape[1]):
                    raise RuntimeError(
                        f"{name} 的 {channel} 图与 S-only 信号模板单元 "
                        f"({template_row},{template_col}) 不兼容")
                samples[f"{name}_{channel}_power"] = float(matrix[template_row, template_col])
        # 以 C+N 的 *GO 训练窗 directional-max* 为背景标尺。不能使用 C+N
        # 在 CUT 的单个功率样本：它不是检测器门限的分母，也会随 range 随机起伏。
        go_references = {
            channel: go_directional_background(maps["c_plus_n"][channel], template_row, template_col,
                                               args.cfar_guard, args.cfar_background)
            for channel in ("cfar_power", "channel1", "channel2")
        }
        gamma1 = (samples["s_only_channel1_power"] /
                  go_references["channel1"]["go_reference_power"])
        gamma2 = (samples["s_only_channel2_power"] /
                  go_references["channel2"]["go_reference_power"])
        # 相位差方差为 1/(2γ1)+1/(2γ2)，故等效 γφ 是两路 γ 的调和平均。
        gamma_phi = (2.0 * gamma1 * gamma2 / (gamma1 + gamma2)
                     if gamma1 > 0.0 and gamma2 > 0.0 else float("nan"))
        signal_cfar = samples["s_only_cfar_power_power"]
        background_cfar = go_references["cfar_power"]["go_reference_power"]
        s_plus_cn_after = complex(samples["s_plus_c_plus_n_after_real_power"],
                                  samples["s_plus_c_plus_n_after_imag_power"])
        cn_after = complex(samples["c_plus_n_after_real_power"],
                           samples["c_plus_n_after_imag_power"])
        incremental_csi_signal_power = abs(s_plus_cn_after - cn_after) ** 2
        s_plus_cn_cfar = complex(samples["s_plus_c_plus_n_cfar_complex_real_power"],
                                  samples["s_plus_c_plus_n_cfar_complex_imag_power"])
        cn_cfar = complex(samples["c_plus_n_cfar_complex_real_power"],
                          samples["c_plus_n_cfar_complex_imag_power"])
        incremental_cfar_signal_power = abs(s_plus_cn_cfar - cn_cfar) ** 2
        hit_events = go_hits_at_truth(combined_run, beam, template_row, template_col,
                                      maps["s_plus_c_plus_n"]["after"].shape)
        record = dict(location)
        record["calibration_seed"] = int(base.get("random", {}).get("random_seed", -1))
        record["isolation_mode"] = "single_target_per_S-only_and_S+C+N_run"
        record.update({f"{name}_physical_doppler_row": row for name, row in physical_rows.items()})
        record.update({
            "signal_template_row": template_row,
            "signal_template_range_bin": template_col,
            "signal_template_row_offset_from_physical_truth": template_row_offset,
            "signal_template_range_offset_from_physical_truth": template_col_offset,
        })
        record.update(samples)
        for channel, values in go_references.items():
            record.update({f"c_plus_n_{channel}_{key}": value for key, value in values.items()})
        record.update(hit_events)
        record.update({
            # 主横轴为生产拼接 CFAR 复输入的 paired complex difference。CSI 和
            # channel-2 均可能经过数据自适应的前端；只有 |z_S+C+N-z_C+N|² 才是
            # 同一实际 CFAR CUT 的非中心信号功率。S-only 仅定位模板主瓣，不能
            # 代替该实测复差。
            "output_scnr_det_out_db": db_ratio(incremental_cfar_signal_power, background_cfar),
            "effective_output_scnr_det_out_db": db_ratio(incremental_cfar_signal_power, background_cfar),
            "output_scnr_det_out_cut_db": db_ratio(incremental_cfar_signal_power,
                                                     samples["c_plus_n_cfar_power_power"]),
            "s_only_production_cfar_branch_scnr_db": db_ratio(signal_cfar, background_cfar),
            "s_plus_c_plus_n_incremental_cfar_signal_power": incremental_cfar_signal_power,
            "contextual_incremental_output_scnr_db": db_ratio(incremental_csi_signal_power, background_cfar),
            "s_plus_c_plus_n_incremental_signal_power": incremental_csi_signal_power,
            "output_scnr_phase_ch1_db": db_ratio(
                samples["s_only_channel1_power"], go_references["channel1"]["go_reference_power"]),
            "output_scnr_phase_ch2_db": db_ratio(
                samples["s_only_channel2_power"], go_references["channel2"]["go_reference_power"]),
            "output_scnr_phase_eff_linear": gamma_phi,
            "output_scnr_phase_eff_db": 10.0 * math.log10(gamma_phi) if gamma_phi > 0.0 else float("nan"),
            "phase_sigma_ideal_rad": 1.0 / math.sqrt(gamma_phi) if gamma_phi > 0.0 else float("nan"),
            "s_only_to_contextual_incremental_csi_power_ratio": (
                signal_cfar / incremental_cfar_signal_power if incremental_cfar_signal_power > 0.0 else float("nan")),
            "sample_location_definition": "physical truth Doppler is first projected through the frozen C+N axis; S-only production-CFAR branch then selects its main-lobe maximum in a fixed ±4 Doppler × ±3 range template stencil. The resulting template CUT is copied unchanged to C+N/S+C+N; no S+C+N/final-output peak selection",
            "background_definition": "C+N is the same packet-addressed Stage2 clutter+thermal-noise realization as S+C+N; primary denominator is the exact production GO directional-max training mean around the fixed S-only template CUT (Doppler circular, range non-wrapping), not the C+N CUT value",
        })
        records.append(record)
        isolated_variants.append({
            "target_id": target_id,
            "s_only_scenario": str(s_only_scenario),
            "s_plus_c_plus_n_scenario": str(combined_scenario),
            "s_only_stage2": str(s_only_run["stage2"]),
            "s_plus_c_plus_n_stage2": str(combined_run["stage2"]),
        })
    write_csv(out / "go_paired_output_scnr_samples.csv", records)
    grouped: dict[tuple[float, float], list[dict[str, object]]] = defaultdict(list)
    for record in records:
        # beam geometry at 0° can be represented as tiny signed round-off values
        # (for example -1e-19).  It is one configured test angle, not distinct bins.
        grouped[(round(float(record["truth_angle_deg"]), 6),
                 float(record["configured_target_snr_db"]))].append(record)
    summary: list[dict[str, object]] = []
    for (angle, injection), rows in sorted(grouped.items()):
        def median(key: str) -> float:
            values = [float(row[key]) for row in rows if math.isfinite(float(row[key]))]
            return float(np.median(values)) if values else float("nan")
        summary.append({
            "truth_angle_deg": angle, "configured_target_snr_db": injection,
            "sample_count": len(rows),
            "output_scnr_det_out_db_median": median("output_scnr_det_out_db"),
            "effective_output_scnr_det_out_db_median": median("effective_output_scnr_det_out_db"),
            "output_scnr_det_out_cut_db_median": median("output_scnr_det_out_cut_db"),
            "output_scnr_phase_ch1_db_median": median("output_scnr_phase_ch1_db"),
            "output_scnr_phase_ch2_db_median": median("output_scnr_phase_ch2_db"),
            "output_scnr_phase_eff_db_median": median("output_scnr_phase_eff_db"),
            "phase_sigma_ideal_rad_median": median("phase_sigma_ideal_rad"),
            "contextual_incremental_output_scnr_db_median": median("contextual_incremental_output_scnr_db"),
            "s_only_to_contextual_incremental_csi_power_ratio_median": median("s_only_to_contextual_incremental_csi_power_ratio"),
            "scnr_definition": "10log10(|z_S+C+N-z_C+N|² / max(mean(left),mean(right),mean(top),mean(bottom))_C+N) at a fixed S-only main-lobe template CUT; z is the exact stitched production CFAR branch input",
            "effective_scnr_definition": "same as output_scnr_det_out_db; S-only CFAR-branch power and CSI-only complex residual are retained only as diagnostics",
        })
    write_csv(out / "go_paired_output_scnr_summary.csv", summary)
    provenance = {
        "base_scenario": str(scenario), "base_scenario_sha256": sha256_file(scenario),
        "cfar": {"type": "GO", "pfa": args.pfa, "guard_half_width": args.cfar_guard,
                 "background_thickness": args.cfar_background},
        "c_plus_n": {"scenario": str(c_plus_n_scenario),
                       "scenario_sha256": sha256_file(c_plus_n_scenario),
                       "stage2": str(c_plus_n_run["stage2"])},
        "isolated_target_variants": isolated_variants,
        "replicas_per_scnr_requested": args.replicas_per_scnr,
        "randomness": "C+N and every isolated target pair retain the same random section; Stage2 thermal noise is packet-address seeded",
        "common_doppler_center_hz": reference_center_hz,
        "common_csi_p38_k_rad_per_hz": reference_csi_p38[0],
        "common_csi_p38_b_rad": reference_csi_p38[1],
        "common_raw_range_phase_reference_f32": str(reference_range_phase[0]),
        "common_csi_range_phase_reference_f32": str(reference_range_phase[1]),
        "location_rule": "physical truth Doppler is mapped through one C+N-reference production axis shared by all variants; fixed truth range column; no final-output or peak selection",
        "signal_only_semantics": "S-only calculates its sole target amplitude from the same generated C+N packet, then clears only the emitted payload before target injection",
        "raw_removed": bool(args.delete_raw_after_success),
    }
    (out / "go_paired_output_scnr_provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PASS] 已完成 {len(records)} 个固定单元的 GO 输出 SCNR 配对标定：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
