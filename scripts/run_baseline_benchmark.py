#!/usr/bin/env python3
"""Run the first GMTI clutter-suppression baseline benchmark.

The benchmark deliberately separates two comparison groups:

* F1/F2 strict: Current legacy CSI, phase-only CSI/DPCA-class cancellation,
  and row-complex-LS/Wiener-class cancellation all consume the same two
  equivalent CSI channels and the same backend CFAR.
* Academic reference: JDL reduced-dimension STAP, MNEC, and a subaperture-
  averaged MNEC implementation use the original four-channel space-time data.

Weights and covariance matrices are estimated from the paired C+N background
only. Target truth is used only for evaluation labels/ROIs and the MDV proxy
sweep. Raw generated BINs are removed after each case unless --keep-data is
specified.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

os.environ.setdefault("MPLCONFIGDIR", "/tmp/gmti_baseline_matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import run_ai_csi_oracle as oracle


F12_METHODS = [
    "Current legacy CSI",
    "Phase-only CSI (DPCA-class)",
    "Row complex LS / Wiener",
]
ACADEMIC_METHODS = [
    "JDL-3x4 reduced STAP",
    "MNEC",
    "SA-MNEC",
]
METHODS = F12_METHODS + ACADEMIC_METHODS
JDL_OFFSETS = (-1, 0, 1)
METHOD_GROUPS = {
    **{name: "F1/F2 strict" for name in F12_METHODS},
    **{name: "4-channel academic reference" for name in ACADEMIC_METHODS},
}


def relative_repo_path(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace(os.sep, "/")


def db(value: float) -> float:
    return float(10.0 * math.log10(max(float(value), 1.0e-300)))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def capture_command(command: List[str]) -> Dict[str, object]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "output": completed.stdout[-4000:],
    }


def reproducibility_provenance(command_line: List[str]) -> Dict[str, object]:
    git_dir = ROOT / ".git-real"
    commit = capture_command(["git", f"--git-dir={git_dir}", "rev-parse", "HEAD"])
    status = capture_command(
        [
            "git",
            f"--git-dir={git_dir}",
            "--work-tree=.",
            "status",
            "--short",
            "--untracked-files=all",
        ]
    )
    executable = ROOT / "build/simulate_stage2_statistical"
    build_type = "unknown"
    cache = ROOT / "build/CMakeCache.txt"
    if cache.exists():
        for line in cache.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("CMAKE_BUILD_TYPE:STRING="):
                build_type = line.split("=", 1)[1]
                break
    return {
        "working_directory": str(ROOT),
        "command": command_line,
        "source_commit": commit["output"].strip(),
        "worktree_status": status["output"],
        "worktree_dirty": bool(status["output"].strip()),
        "simulator_executable": relative_repo_path(executable),
        "simulator_executable_bytes": executable.stat().st_size if executable.exists() else None,
        "simulator_executable_sha256": sha256_file(executable) if executable.exists() else None,
        "cmake_build_type": build_type,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "matplotlib": matplotlib.__version__,
        "platform": platform.platform(),
        "gpu_probe": capture_command(["nvidia-smi"]),
    }


def write_csv(path: Path, rows: Iterable[Dict[str, object]]) -> None:
    rows = list(rows)
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: List[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def load_suite(path: Path) -> Tuple[Path, Dict[str, object]]:
    suite = json.loads(path.read_text(encoding="utf-8"))
    base = ROOT / str(suite["base_config"])
    if not base.exists():
        raise FileNotFoundError(f"base Stage2 config not found: {base}")
    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("baseline suite must contain a non-empty cases list")
    return base, suite


def apply_case_config(
    base_config: Dict[str, object],
    case: Dict[str, object],
    output_dir: Path,
    background_dir: Path,
    target_enabled: bool,
    paired: bool,
) -> Dict[str, object]:
    config = copy.deepcopy(base_config)
    case_id = str(case["case_id"])
    config["case_id"] = case_id if target_enabled else f"{case_id}_background"
    config["output_dir"] = relative_repo_path(output_dir)
    config["paired_background_output_dir"] = (
        relative_repo_path(background_dir) if paired and target_enabled else ""
    )
    config["truth_output"] = bool(target_enabled)
    random_cfg = config.setdefault("random", {})
    random_cfg["random_seed"] = int(case["seed"])
    scene_cfg = config.setdefault("scene", {})
    area_cfg = scene_cfg.setdefault("area_clutter", {})
    area_cfg["texture_sigma"] = float(case["texture_sigma"])
    area_cfg["azimuth_subcell_count"] = int(case["azimuth_subcell_count"])
    targets = config.setdefault("targets", [])
    if not targets:
        raise ValueError(f"base config has no target for {case_id}")
    target_cfg = targets[0]
    target_cfg["target_id"] = str(case["target_id"])
    target_cfg["enabled"] = bool(target_enabled)
    target_cfg.setdefault("motion", {})
    target_cfg["motion"]["ve_mps"] = float(case["motion"]["ve_mps"])
    target_cfg["motion"]["vn_mps"] = float(case["motion"]["vn_mps"])
    target_cfg.setdefault("amplitude", {})
    target_cfg["amplitude"]["snr_db"] = float(case["target_snr_db"])
    impairment_cfg = config.setdefault("channel_impairments", {})
    impairment_cfg.update(copy.deepcopy(case.get("impairments", {})))
    if not impairment_cfg.get("enabled", False):
        for key in (
            "channel_amp_mismatch_db",
            "channel_fixed_phase_mismatch_deg",
            "channel_phase_jitter_std_deg",
            "channel_drop_probability",
        ):
            impairment_cfg[key] = 0.0
    return config


def write_json(path: Path, value: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_simulator(config_path: Path) -> None:
    executable = ROOT / "build/simulate_stage2_statistical"
    if not executable.exists():
        raise FileNotFoundError(
            f"{executable} not found; build it with cmake --build build --target simulate_stage2_statistical -j2"
        )
    completed = subprocess.run(
        [str(executable), "--config", str(config_path)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        tail = completed.stdout[-4000:]
        raise RuntimeError(
            f"Stage2 simulation failed for {config_path} (exit {completed.returncode})\n{tail}"
        )


def prepare_case_data(
    base_config_path: Path,
    suite: Dict[str, object],
    case: Dict[str, object],
    keep_data: bool,
) -> Dict[str, Path | bool]:
    base_config = json.loads(base_config_path.read_text(encoding="utf-8"))
    case_root = ROOT / str(suite["output_root"]) / str(case["case_id"])
    target_dir = case_root / "target"
    background_dir = case_root / "background"
    target_bin = target_dir / "data/stage2_statistical_newprotocol_period_0000.bin"
    background_bin = background_dir / "data/stage2_statistical_newprotocol_period_0000.bin"
    target_xml = target_dir / "config/temp_config_stage2_period_0000.xml"
    truth = target_dir / "truth/truth_targets_by_beam.csv"
    generated_dir = ROOT / "outputs/ai_csi_baseline/generated_configs"
    target_config_path = generated_dir / f"{case['case_id']}_target.json"
    background_config_path = generated_dir / f"{case['case_id']}_background.json"
    paired = bool(case.get("use_paired_background", False))
    # Stage2's native makeDirs creates only one directory level at a time;
    # materialize the controlled case root before invoking the executable.
    case_root.mkdir(parents=True, exist_ok=True)

    try:
        if not (target_bin.exists() and background_bin.exists() and target_xml.exists() and truth.exists()):
            target_config = apply_case_config(
                base_config, case, target_dir, background_dir, True, paired
            )
            write_json(target_config_path, target_config)
            if not target_bin.exists() or not background_bin.exists() or not target_xml.exists():
                run_simulator(target_config_path)
            if not paired and not background_bin.exists():
                background_config = apply_case_config(
                    base_config, case, background_dir, background_dir, False, False
                )
                write_json(background_config_path, background_config)
                run_simulator(background_config_path)

        if not (target_bin.exists() and background_bin.exists() and target_xml.exists() and truth.exists()):
            raise RuntimeError(f"incomplete generated data for {case['case_id']}")
    except BaseException:
        if not keep_data and case_root.exists():
            cleanup_case({"case_root": case_root, "keep_data": False})
        raise

    return {
        "case_root": case_root,
        "target_bin": target_bin,
        "background_bin": background_bin,
        "target_xml": target_xml,
        "truth": truth,
        "generated_dir": generated_dir,
        "keep_data": keep_data,
    }


def cleanup_case(paths: Dict[str, Path | bool]) -> None:
    if bool(paths["keep_data"]):
        return
    case_root = Path(paths["case_root"])
    if case_root == ROOT or ROOT not in case_root.parents:
        raise RuntimeError(f"refusing to clean unexpected case path: {case_root}")
    if case_root.exists():
        shutil.rmtree(case_root)


def channel_impairment_stats(
    background_bin: Path,
    target_bin: Path,
    case_root: Path,
    total_pulses: int,
) -> Tuple[int, int, float]:
    truth_paths = [
        case_root / "background/truth/channel_impairment_truth.csv",
        case_root / "target/truth/channel_impairment_truth.csv",
    ]
    rows: List[Dict[str, str]] = []
    for path in truth_paths:
        if path.exists():
            with path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            break
    if not rows:
        return 0, total_pulses, 1.0
    dropped = sum(int(float(row.get("channel_dropped", 0.0))) for row in rows)
    total = len(rows)
    valid = total - dropped
    return dropped, valid, valid / max(1, total)


def rd4_from_compressed(compressed: np.ndarray, fa_ctr: float, p: oracle.Params) -> np.ndarray:
    preflip = np.where(np.arange(p.pulse_num)[:, None, None] % 2 == 0, 1.0, -1.0)
    spectrum = np.fft.fft(compressed * preflip, axis=0)
    k = oracle.dbs_shift_index(fa_ctr, p)
    if k:
        spectrum = np.roll(spectrum, -k, axis=0)
    return spectrum


@dataclass
class CaseData:
    case: Dict[str, object]
    p: oracle.Params
    truth_row: int
    truth_col: int
    target_id: str
    target_fd: float
    fa_ctr: float
    axis: np.ndarray
    current_support: np.ndarray
    alignment: oracle.Alignment
    background_alignment: oracle.Alignment
    raw_bg_compressed: np.ndarray
    raw_bg_rd: np.ndarray
    raw_target_rd: np.ndarray
    dropped_samples: int
    valid_samples: int
    valid_sample_fraction: float
    target_bin_bytes: int
    background_bin_bytes: int
    target_sha256: str
    background_sha256: str


def load_case_data(case: Dict[str, object], paths: Dict[str, Path | bool]) -> CaseData:
    p = oracle.Params.from_xml(Path(paths["target_xml"]))
    truth_row, truth_col, target_id, target_fd = oracle.read_truth(Path(paths["truth"]))
    bg_raw, bg_header = oracle.read_raw(Path(paths["background_bin"]), p)
    target_raw, target_header = oracle.read_raw(Path(paths["target_bin"]), p)
    if bg_raw.shape != target_raw.shape:
        raise RuntimeError(f"paired shape mismatch for {case['case_id']}: {bg_raw.shape} vs {target_raw.shape}")
    bg_fused, residual = oracle.fuse_four_channels(bg_raw, p, bg_header)
    target_fused, _ = oracle.fuse_four_channels(target_raw, p, target_header, residual)
    bg_compressed = oracle.pulse_compress(bg_fused, p)
    target_compressed = oracle.pulse_compress(target_fused, p)
    bg_compressed[:, :, 1] *= p.calib_coef
    target_compressed[:, :, 1] *= p.calib_coef
    fa_ctr = oracle.estimate_fa_ctr(bg_compressed[:, :, 0], p)
    axis = oracle.current_axis(fa_ctr, p)
    az_st, az_ed, _, _ = oracle.support_indices(axis, fa_ctr, p)
    current_support = np.zeros(p.pulse_num, dtype=bool)
    current_support[az_st : az_ed + 1] = True
    shift_current = float(round((0.5 * p.d_channel_m) / 60.0 * p.prf_hz))
    alignment = oracle.make_alignment(
        "baseline_current",
        shift_current,
        bg_compressed,
        target_compressed,
        fa_ctr,
        axis,
        current_support,
        p,
        estimation_source="target",
    )
    background_alignment = oracle.make_alignment(
        "baseline_background_only",
        shift_current,
        bg_compressed,
        target_compressed,
        fa_ctr,
        axis,
        current_support,
        p,
        estimation_source="background",
    )
    raw_bg_compressed = oracle.pulse_compress(bg_raw, p)
    raw_target_compressed = oracle.pulse_compress(target_raw, p)
    raw_bg_rd = rd4_from_compressed(raw_bg_compressed, fa_ctr, p)
    raw_target_rd = rd4_from_compressed(raw_target_compressed, fa_ctr, p)
    dropped, valid, valid_fraction = channel_impairment_stats(
        Path(paths["background_bin"]),
        Path(paths["target_bin"]),
        Path(paths["case_root"]),
        p.pulse_num,
    )
    target_path = Path(paths["target_bin"])
    background_path = Path(paths["background_bin"])
    target_sha256 = sha256_file(target_path)
    background_sha256 = sha256_file(background_path)
    del bg_raw, target_raw, bg_fused, target_fused, target_compressed, raw_target_compressed
    return CaseData(
        case=case,
        p=p,
        truth_row=truth_row,
        truth_col=truth_col,
        target_id=target_id,
        target_fd=target_fd,
        fa_ctr=fa_ctr,
        axis=axis,
        current_support=current_support,
        alignment=alignment,
        background_alignment=background_alignment,
        raw_bg_compressed=raw_bg_compressed,
        raw_bg_rd=raw_bg_rd,
        raw_target_rd=raw_target_rd,
        dropped_samples=dropped,
        valid_samples=valid,
        valid_sample_fraction=valid_fraction,
        target_bin_bytes=target_path.stat().st_size,
        background_bin_bytes=background_path.stat().st_size,
        target_sha256=target_sha256,
        background_sha256=background_sha256,
    )


def phase_only_cancel(
    f1: np.ndarray, f2: np.ndarray, phase_rows: np.ndarray, support: np.ndarray
) -> np.ndarray:
    out = f1.copy()
    for row in np.flatnonzero(support):
        out[row] = f1[row] - np.exp(1j * phase_rows[row]) * f2[row]
    return out


def dynamic_detector(
    output: np.ndarray, original: np.ndarray, support: np.ndarray
) -> np.ndarray:
    detector = original.copy()
    detector[support] = output[support]
    return detector


def mnec_weight_from_cov(
    covariance: np.ndarray,
    steering: np.ndarray,
    rank_mode: str = "adaptive",
) -> Tuple[np.ndarray, int]:
    dim = covariance.shape[0]
    covariance = 0.5 * (covariance + covariance.conj().T)
    trace = float(np.trace(covariance).real)
    loading = max(trace / max(dim, 1) * 1.0e-2, 1.0e-12)
    covariance = covariance + loading * np.eye(dim, dtype=np.complex128)
    values, vectors = np.linalg.eigh(covariance)
    floor = max(float(np.median(np.maximum(values.real, 0.0))), 1.0e-12)
    if rank_mode == "adaptive":
        rank = int(np.count_nonzero(values > 3.0 * floor))
        rank = max(1, min(dim - 1, rank))
    else:
        rank = max(1, min(dim - 1, int(rank_mode)))
    clutter = vectors[:, -rank:]
    projector = np.eye(dim, dtype=np.complex128) - clutter @ clutter.conj().T
    projected = projector @ steering
    denominator = np.vdot(steering, projected)
    if abs(denominator) <= 1.0e-10:
        return steering / max(np.linalg.norm(steering), 1.0e-12), rank
    return projected / denominator, rank


def train_jdl_weights(bg_rd: np.ndarray, p: oracle.Params) -> np.ndarray:
    rows, cols, channels = bg_rd.shape
    train_cols = np.arange(max(0, p.rg_st), min(cols, p.rg_ed + 1))
    offsets = JDL_OFFSETS
    dim = channels * len(offsets)
    steering = np.zeros(dim, dtype=np.complex128)
    steering[channels + 0] = 1.0
    weights = np.zeros((rows, dim), dtype=np.complex128)
    for row in range(rows):
        snapshots = np.concatenate(
            [bg_rd[(row + offset) % rows, train_cols, :] for offset in offsets], axis=1
        )
        covariance = snapshots.conj().T @ snapshots / max(1, snapshots.shape[0])
        weights[row], _ = mnec_weight_from_cov(covariance, steering, rank_mode="adaptive")
    return weights


def train_mnec_weights(bg_rd: np.ndarray, p: oracle.Params) -> Tuple[np.ndarray, np.ndarray]:
    rows, cols, channels = bg_rd.shape
    train_cols = np.arange(max(0, p.rg_st), min(cols, p.rg_ed + 1))
    steering = np.ones(channels, dtype=np.complex128) / math.sqrt(channels)
    weights = np.zeros((rows, channels), dtype=np.complex128)
    ranks = np.zeros(rows, dtype=np.int32)
    for row in range(rows):
        snapshots = bg_rd[row, train_cols, :]
        covariance = snapshots.conj().T @ snapshots / max(1, snapshots.shape[0])
        weights[row], ranks[row] = mnec_weight_from_cov(covariance, steering)
    return weights, ranks


def train_sa_mnec_weights(
    bg_compressed: np.ndarray,
    bg_rd: np.ndarray,
    axis: np.ndarray,
    fa_ctr: float,
    p: oracle.Params,
    subapertures: int = 4,
) -> Tuple[np.ndarray, np.ndarray]:
    pulses, cols, channels = bg_compressed.shape
    train_cols = np.arange(max(0, p.rg_st), min(cols, p.rg_ed + 1))
    scale = np.sqrt(np.mean(np.abs(bg_rd[:, train_cols, :]) ** 2, axis=(0, 1)))
    scale = np.maximum(scale, 1.0e-12)
    normalized = bg_compressed / scale[None, None, :]
    segments = [segment for segment in np.array_split(np.arange(pulses), subapertures) if len(segment)]
    covariance_sum = np.zeros((pulses, channels, channels), dtype=np.complex128)
    frequency = axis - fa_ctr
    for segment in segments:
        phase = np.exp(
            -2j * math.pi * frequency[:, None] * segment[None, :] / p.prf_hz
        )
        snapshots = np.einsum(
            "mp,prc->mrc", phase, normalized[segment], optimize=True
        ) / float(len(segment))
        for row in range(pulses):
            x = snapshots[row, train_cols, :]
            covariance = x.conj().T @ x / max(1, x.shape[0])
            trace = float(np.trace(covariance).real)
            if trace > 1.0e-12:
                covariance_sum[row] += covariance / (trace / channels)
    covariance_sum /= max(1, len(segments))
    steering = np.ones(channels, dtype=np.complex128) / math.sqrt(channels)
    weights = np.zeros((pulses, channels), dtype=np.complex128)
    ranks = np.zeros(pulses, dtype=np.int32)
    for row in range(pulses):
        weights[row], ranks[row] = mnec_weight_from_cov(covariance_sum[row], steering)
    return weights, scale


def apply_row_weights(rd: np.ndarray, weights: np.ndarray) -> np.ndarray:
    return np.einsum("mc,mrc->mr", weights.conj(), rd, optimize=True)


def apply_jdl_weights(rd: np.ndarray, weights: np.ndarray) -> np.ndarray:
    features = np.concatenate(
        [np.roll(rd, -offset, axis=0) for offset in JDL_OFFSETS], axis=2
    )
    return np.einsum("mc,mrc->mr", weights.conj(), features, optimize=True)


@dataclass
class MethodOutput:
    method: str
    group: str
    bg: np.ndarray
    target: np.ndarray
    detector_bg: np.ndarray
    detector_target: np.ndarray
    input_bg: np.ndarray
    input_target: np.ndarray
    input_power: np.ndarray
    runtime_ms: float
    details: Dict[str, object]
    model: Dict[str, object]


def run_method(data: CaseData, method: str) -> MethodOutput:
    started = time.perf_counter()
    a = data.background_alignment if method == "Row complex LS / Wiener" else data.alignment
    model: Dict[str, object] = {}
    if method in F12_METHODS:
        if method == "Current legacy CSI":
            bg = oracle.current_cancel(
                a.f1_bg, a.f2_bg, np.asarray(a.p38["phase_rows"]), data.current_support
            )
            target = oracle.current_cancel(
                a.f1_target, a.f2_target, np.asarray(a.p38["phase_rows"]), data.current_support
            )
        elif method == "Phase-only CSI (DPCA-class)":
            phase = np.asarray(a.p38["phase_rows"])
            bg = phase_only_cancel(a.f1_bg, a.f2_bg, phase, data.current_support)
            target = phase_only_cancel(a.f1_target, a.f2_target, phase, data.current_support)
        else:
            bg = oracle.direct_weight_cancel(
                a.f1_bg, a.f2_bg, a.alpha, data.current_support
            )
            target = oracle.direct_weight_cancel(
                a.f1_target, a.f2_target, a.alpha, data.current_support
            )
        detector_bg = dynamic_detector(bg, a.f2_bg, data.current_support)
        detector_target = dynamic_detector(target, a.f2_target, data.current_support)
        input_bg = a.f1_bg
        input_target = a.f1_target
        input_power = np.minimum(np.abs(a.f1_bg), np.abs(a.f2_bg)) ** 2
        details = {
            "input_interface": "F1/F2 equivalent CSI channels",
            "weight_source": "Current measured P38" if method != "Row complex LS / Wiener" else "paired C+N background",
            "support": "current dynamic support",
        }
    else:
        if method == "JDL-3x4 reduced STAP":
            weights = train_jdl_weights(data.raw_bg_rd, data.p)
            model["weights"] = weights
            bg = apply_jdl_weights(data.raw_bg_rd, weights)
            target = apply_jdl_weights(data.raw_target_rd, weights)
            details = {
                "input_interface": "original four-channel space-time data",
                "feature_dimension": 12,
                "temporal_offsets": "-1,0,+1 Doppler rows",
                "covariance_training": "fixed full range training region from C+N",
            }
            input_bg = data.raw_bg_rd[:, :, 0]
            input_target = data.raw_target_rd[:, :, 0]
            input_power = np.abs(input_bg) ** 2
        elif method == "MNEC":
            weights, ranks = train_mnec_weights(data.raw_bg_rd, data.p)
            model["weights"] = weights
            bg = apply_row_weights(data.raw_bg_rd, weights)
            target = apply_row_weights(data.raw_target_rd, weights)
            details = {
                "input_interface": "original four-channel space-time data",
                "adaptive_clutter_rank_min": int(np.min(ranks)),
                "adaptive_clutter_rank_max": int(np.max(ranks)),
                "covariance_training": "fixed full range training region from C+N",
            }
            input_bg = data.raw_bg_rd[:, :, 0]
            input_target = data.raw_target_rd[:, :, 0]
            input_power = np.abs(input_bg) ** 2
        else:
            weights, scale = train_sa_mnec_weights(
                data.raw_bg_compressed,
                data.raw_bg_rd,
                data.axis,
                data.fa_ctr,
                data.p,
            )
            model["weights"] = weights
            model["scale"] = scale
            bg_norm = data.raw_bg_rd / scale[None, None, :]
            target_norm = data.raw_target_rd / scale[None, None, :]
            bg = apply_row_weights(bg_norm, weights)
            target = apply_row_weights(target_norm, weights)
            details = {
                "input_interface": "original four-channel space-time data",
                "subaperture_count": 4,
                "covariance_training": "subaperture-averaged C+N covariance with per-channel normalization",
                "weight_source": "paired C+N background",
            }
            input_bg = bg_norm[:, :, 0]
            input_target = target_norm[:, :, 0]
            input_power = np.abs(input_bg) ** 2
        detector_bg = bg
        detector_target = target
    runtime_ms = 1000.0 * (time.perf_counter() - started)
    return MethodOutput(
        method=method,
        group=METHOD_GROUPS[method],
        bg=bg,
        target=target,
        detector_bg=detector_bg,
        detector_target=detector_target,
        input_bg=input_bg,
        input_target=input_target,
        input_power=input_power,
        runtime_ms=runtime_ms,
        details=details,
        model=model,
    )


def background_cfar(output: np.ndarray, p: oracle.Params) -> Dict[str, float]:
    rows, cols = output.shape
    radius = p.cfar_guard + p.cfar_background
    if cols <= 2 * radius:
        return {
            "false_alarm_count": math.nan,
            "background_false_alarm_count": math.nan,
            "background_Pfa": math.nan,
            "cfar_test_cells": 0.0,
        }
    # A truth point outside the matrix makes oracle.go_cfar retain all CFAR hits.
    result = oracle.go_cfar(output, rows + 100, cols + 100, p)
    test_cells = float(rows * (cols - 2 * radius))
    count = float(result["false_alarm_count"])
    return {
        "false_alarm_count": count,
        "background_false_alarm_count": count,
        "background_Pfa": count / max(test_cells, 1.0),
        "cfar_test_cells": test_cells,
        "cfar_alpha": float(result["cfar_alpha"]),
    }


def output_metrics(data: CaseData, result: MethodOutput) -> Dict[str, float]:
    rows, cols = result.bg.shape
    col_start = max(0, min(cols, data.p.rg_st))
    col_stop = max(col_start + 1, min(cols, data.p.rg_ed + 1))
    fixed_bg_power = result.input_power[:, col_start:col_stop]
    residual_power = np.abs(result.bg[:, col_start:col_stop]) ** 2
    input_median = float(np.median(fixed_bg_power))
    residual_p95 = float(np.percentile(residual_power, 95.0))
    residual_p99 = float(np.percentile(residual_power, 99.0))
    high_threshold = input_median * 10.0 ** (15.0 / 10.0)
    r0 = max(0, data.truth_row - 2)
    r1 = min(rows, data.truth_row + 3)
    c0 = max(0, data.truth_col - 2)
    c1 = min(cols, data.truth_col + 3)
    roi = (slice(r0, r1), slice(c0, c1))
    input_clutter_roi = float(np.mean(result.input_power[roi]))
    output_clutter_roi = float(np.mean(np.abs(result.bg[roi]) ** 2))
    signal_before = result.input_target[roi] - result.input_bg[roi]
    signal_after = result.target[roi] - result.bg[roi]
    signal_power_before = float(np.mean(np.abs(signal_before) ** 2))
    signal_power_after = float(np.mean(np.abs(signal_after) ** 2))
    input_scnr = db(signal_power_before / max(input_clutter_roi, 1.0e-300))
    output_scnr = db(signal_power_after / max(output_clutter_roi, 1.0e-300))
    target_cfar = oracle.go_cfar(
        result.detector_target, data.truth_row, data.truth_col, data.p
    )
    cfar_bg = background_cfar(result.detector_bg, data.p)
    high_count = int(np.count_nonzero(residual_power > high_threshold))
    fixed_cells = float(residual_power.size)
    return {
        "CA_dB": db(np.mean(fixed_bg_power) / max(np.mean(residual_power), 1.0e-300)),
        "CSR_dB": db(np.mean(fixed_bg_power) / max(np.mean(residual_power), 1.0e-300)),
        "input_SCNR_dB": input_scnr,
        "output_SCNR_dB": output_scnr,
        "SCNR_improvement_dB": output_scnr - input_scnr,
        "target_loss_dB": db(signal_power_after / max(signal_power_before, 1.0e-300)),
        "background_residual_p95_power": residual_p95,
        "background_residual_p99_power": residual_p99,
        "background_residual_p95_dB_over_input_median": db(residual_p95 / max(input_median, 1.0e-300)),
        "background_residual_p99_dB_over_input_median": db(residual_p99 / max(input_median, 1.0e-300)),
        "high_energy_residual_points": float(high_count),
        "high_energy_residual_fraction": high_count / max(fixed_cells, 1.0),
        "high_energy_threshold_dB_over_input_median": 15.0,
        "background_power_before_roi": input_clutter_roi,
        "background_power_after_roi": output_clutter_roi,
        "target_signal_power_before": signal_power_before,
        "target_signal_power_after": signal_power_after,
        "Pd": float(target_cfar["Pd"]),
        "target_cfar_candidate_cells": float(target_cfar["cfar_candidate_cells"]),
        "target_peak_power": float(target_cfar["target_peak_power"]),
        **cfar_bg,
    }


def apply_model(
    data: CaseData,
    method: str,
    model: Dict[str, object],
    target_shift_rows: int = 0,
) -> Tuple[np.ndarray, np.ndarray]:
    if method in F12_METHODS:
        a = data.background_alignment if method == "Row complex LS / Wiener" else data.alignment
        signal_f1 = a.f1_target - a.f1_bg
        signal_f2 = a.f2_target - a.f2_bg
        target_f1 = a.f1_bg + np.roll(signal_f1, target_shift_rows, axis=0)
        target_f2 = a.f2_bg + np.roll(signal_f2, target_shift_rows, axis=0)
        bg_f1 = a.f1_bg
        bg_f2 = a.f2_bg
        if method == "Current legacy CSI":
            phase = np.asarray(a.p38["phase_rows"])
            bg = oracle.current_cancel(bg_f1, bg_f2, phase, data.current_support)
            target = oracle.current_cancel(target_f1, target_f2, phase, data.current_support)
        elif method == "Phase-only CSI (DPCA-class)":
            phase = np.asarray(a.p38["phase_rows"])
            bg = phase_only_cancel(bg_f1, bg_f2, phase, data.current_support)
            target = phase_only_cancel(target_f1, target_f2, phase, data.current_support)
        else:
            bg = oracle.direct_weight_cancel(bg_f1, bg_f2, a.alpha, data.current_support)
            target = oracle.direct_weight_cancel(target_f1, target_f2, a.alpha, data.current_support)
        detector = dynamic_detector(target, target_f2, data.current_support)
        return target, detector
    signal = data.raw_target_rd - data.raw_bg_rd
    target_rd = data.raw_bg_rd + np.roll(signal, target_shift_rows, axis=0)
    bg_rd = data.raw_bg_rd
    if method == "SA-MNEC":
        scale = np.asarray(model["scale"])
        bg_rd = bg_rd / scale[None, None, :]
        target_rd = target_rd / scale[None, None, :]
    weights = np.asarray(model["weights"])
    target = (
        apply_jdl_weights(target_rd, weights)
        if method == "JDL-3x4 reduced STAP"
        else apply_row_weights(target_rd, weights)
    )
    return target, target


def mdv_proxy(data: CaseData, method: str, model: Dict[str, object]) -> Dict[str, float]:
    # Local row-shift sweep around the clutter centre.  This is a reproducible
    # detection threshold proxy, not a full operational MDV curve.
    center = int(np.argmin(np.abs(data.axis - data.fa_ctr)))
    offsets = (0, 1, 2, 4, 6, 8, 12, 16, 24, 32)
    candidate_rows = {max(0, min(data.p.pulse_num - 1, center + offset)) for offset in offsets}
    candidate_rows.update(max(0, min(data.p.pulse_num - 1, center - offset)) for offset in offsets)
    ordered = sorted(candidate_rows, key=lambda row: abs(float(data.axis[row] - data.fa_ctr)))
    detected: List[Tuple[float, int]] = []
    for row in ordered:
        shift = int(row - data.truth_row)
        if method in F12_METHODS:
            target, detector = apply_model(data, method, model, shift)
        else:
            signal = data.raw_target_rd - data.raw_bg_rd
            target_rd = data.raw_bg_rd + np.roll(signal, shift, axis=0)
            if method == "SA-MNEC":
                scale = np.asarray(model["scale"])
                target_rd = target_rd / scale[None, None, :]
            if method == "JDL-3x4 reduced STAP":
                target = apply_jdl_weights(target_rd, np.asarray(model["weights"]))
            else:
                target = apply_row_weights(target_rd, np.asarray(model["weights"]))
            detector = target
        del target
        pd = oracle.go_cfar(detector, row, data.truth_col, data.p)["Pd"]
        if pd >= 0.5:
            velocity = abs(float(data.axis[row] - data.fa_ctr)) * (oracle.C0 / data.p.fc_hz) / 2.0
            detected.append((velocity, row))
    if not detected:
        return {"MDV_mps": math.nan, "MDV_detected_row": math.nan, "MDV_sweep_points": float(len(ordered))}
    velocity, row = min(detected, key=lambda item: item[0])
    return {"MDV_mps": velocity, "MDV_detected_row": float(row), "MDV_sweep_points": float(len(ordered))}


def baseline_row(data: CaseData, output: MethodOutput, mdv: Dict[str, float]) -> Dict[str, object]:
    row: Dict[str, object] = {
        "case_id": str(data.case["case_id"]),
        "scenario_label": str(data.case["label"]),
        "method": output.method,
        "comparison_group": output.group,
        "target_id": data.target_id,
        "target_snr_config_dB": float(data.case["target_snr_db"]),
        "target_fd_truth_hz": data.target_fd,
        "target_row_truth": data.truth_row,
        "target_range_bin_truth": data.truth_col,
        "fa_ctr_hz": data.fa_ctr,
        "dynamic_support_row_start": int(np.flatnonzero(data.current_support)[0]),
        "dynamic_support_row_end": int(np.flatnonzero(data.current_support)[-1]),
        "target_support_edge_signed_rows": min(
            data.truth_row - int(np.flatnonzero(data.current_support)[0]),
            int(np.flatnonzero(data.current_support)[-1]) - data.truth_row,
        ),
        "target_clutter_center_offset_hz": data.target_fd - data.fa_ctr,
        "target_motion_ve_mps": float(data.case["motion"]["ve_mps"]),
        "target_motion_vn_mps": float(data.case["motion"]["vn_mps"]),
        "channel_dropped_pulses": data.dropped_samples,
        "channel_valid_pulses": data.valid_samples,
        "channel_valid_fraction": data.valid_sample_fraction,
        "target_bin_bytes": data.target_bin_bytes,
        "background_bin_bytes": data.background_bin_bytes,
        "target_input_sha256": data.target_sha256,
        "background_input_sha256": data.background_sha256,
        "runtime_ms": output.runtime_ms,
        **output_metrics(data, output),
        **mdv,
    }
    return row


def heatmap(
    rows: List[Dict[str, object]],
    key: str,
    filename: str,
    title: str,
    colorbar_label: str,
    log10: bool = False,
) -> None:
    cases = list(dict.fromkeys(str(row["case_id"]) for row in rows))
    labels = {str(row["case_id"]): str(row["scenario_label"]) for row in rows}
    matrix = np.full((len(cases), len(METHODS)), np.nan, dtype=float)
    for row in rows:
        i = cases.index(str(row["case_id"]))
        j = METHODS.index(str(row["method"]))
        value = float(row[key])
        matrix[i, j] = math.log10(max(value, 1.0e-12)) if log10 else value
    fig, ax = plt.subplots(figsize=(13, 7))
    image = ax.imshow(matrix, aspect="auto", cmap="viridis")
    ax.set_xticks(np.arange(len(METHODS)), METHODS, rotation=35, ha="right")
    ax.set_yticks(np.arange(len(cases)), [labels[case] for case in cases])
    ax.set_title(title, pad=22)
    fig.text(
        0.5,
        0.94,
        "7 fixed single-period scenarios × 6 methods; fixed all-Doppler evaluation range",
        ha="center",
        va="top",
        fontsize=9,
        color="#555555",
    )
    ax.set_xlabel("Method")
    ax.set_ylabel("Scenario")
    fig.colorbar(image, ax=ax, label=colorbar_label)
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            if np.isfinite(matrix[i, j]):
                ax.text(j, i, f"{matrix[i, j]:.2f}", ha="center", va="center", fontsize=7, color="white")
    fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.92])
    fig.savefig(OUT / filename, dpi=150)
    plt.close(fig)


def plot_detection_runtime(rows: List[Dict[str, object]]) -> None:
    cases = list(dict.fromkeys(str(row["case_id"]) for row in rows))
    labels = {str(row["case_id"]): str(row["scenario_label"]) for row in rows}
    keys = ["background_Pfa", "Pd", "MDV_mps", "runtime_ms"]
    titles = ["Background Pfa", "Pd", "MDV proxy (m/s)", "Runtime (ms)"]
    fig, axes = plt.subplots(2, 2, figsize=(15, 9))
    for axis, key, title in zip(axes.flat, keys, titles):
        matrix = np.full((len(cases), len(METHODS)), np.nan, dtype=float)
        for row in rows:
            i = cases.index(str(row["case_id"]))
            j = METHODS.index(str(row["method"]))
            matrix[i, j] = float(row[key])
        if key in {"background_Pfa", "runtime_ms"}:
            matrix = np.log10(np.maximum(matrix, 1.0e-12))
            label = "log10(value)"
        else:
            label = key
        image = axis.imshow(matrix, aspect="auto", cmap="magma")
        axis.set_xticks(np.arange(len(METHODS)), METHODS, rotation=35, ha="right")
        axis.set_yticks(np.arange(len(cases)), [labels[case] for case in cases])
        axis.set_title(title)
        fig.colorbar(image, ax=axis, label=label)
    fig.suptitle("Detection, MDV proxy and runtime", y=0.995)
    fig.text(
        0.5,
        0.965,
        "7 fixed single-period scenarios; background Pfa uses pure C+N and runtime is CPU milliseconds",
        ha="center",
        va="top",
        fontsize=9,
        color="#555555",
    )
    fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.945])
    fig.savefig(OUT / "baseline_detection_runtime.png", dpi=150)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        type=Path,
        default=Path("configs/research/ai_csi_baseline_suite.json"),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("outputs/ai_csi_baseline"),
    )
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="retain generated per-case BINs and transient configs for debugging",
    )
    args = parser.parse_args()
    global OUT
    OUT = ROOT / args.out if not args.out.is_absolute() else args.out
    OUT.mkdir(parents=True, exist_ok=True)
    Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)
    suite_path = ROOT / args.suite if not args.suite.is_absolute() else args.suite
    base_config_path, suite = load_suite(suite_path)
    all_rows: List[Dict[str, object]] = []
    scenario_rows: List[Dict[str, object]] = []
    generated_dirs: set[Path] = set()
    for case in suite["cases"]:
        paths = prepare_case_data(base_config_path, suite, case, args.keep_data)
        generated_dirs.add(Path(paths["generated_dir"]))
        try:
            data = load_case_data(case, paths)
            scenario_rows.append(
                {
                    "case_id": case["case_id"],
                    "scenario_label": case["label"],
                    "target_id": data.target_id,
                    "target_fd_truth_hz": data.target_fd,
                    "target_row_truth": data.truth_row,
                    "fa_ctr_hz": data.fa_ctr,
                    "dynamic_support_row_start": int(np.flatnonzero(data.current_support)[0]),
                    "dynamic_support_row_end": int(np.flatnonzero(data.current_support)[-1]),
                    "target_support_edge_signed_rows": min(
                        data.truth_row - int(np.flatnonzero(data.current_support)[0]),
                        int(np.flatnonzero(data.current_support)[-1]) - data.truth_row,
                    ),
                    "target_clutter_center_offset_hz": data.target_fd - data.fa_ctr,
                    "channel_dropped_pulses": data.dropped_samples,
                    "channel_valid_pulses": data.valid_samples,
                    "channel_valid_fraction": data.valid_sample_fraction,
                    "target_bin_path": relative_repo_path(Path(paths["target_bin"])),
                    "background_bin_path": relative_repo_path(Path(paths["background_bin"])),
                    "target_bin_bytes": data.target_bin_bytes,
                    "background_bin_bytes": data.background_bin_bytes,
                    "target_input_sha256": data.target_sha256,
                    "background_input_sha256": data.background_sha256,
                    "target_snr_config_dB": case["target_snr_db"],
                }
            )
            for method in METHODS:
                output = run_method(data, method)
                mdv = mdv_proxy(data, method, output.model)
                all_rows.append(baseline_row(data, output, mdv))
                del output
            del data
        finally:
            cleanup_case(paths)
    if not args.keep_data:
        for directory in generated_dirs:
            if directory == ROOT or ROOT not in directory.parents:
                raise RuntimeError(f"refusing to clean unexpected generated config path: {directory}")
            if directory.exists():
                shutil.rmtree(directory)

    write_csv(OUT / "baseline_summary.csv", all_rows)
    write_csv(OUT / "baseline_scenario_summary.csv", scenario_rows)
    heatmap(
        all_rows,
        "SCNR_improvement_dB",
        "baseline_scnr_improvement.png",
        "Baseline SCNR improvement",
        "SCNR improvement (dB)",
    )
    heatmap(
        all_rows,
        "background_residual_p99_dB_over_input_median",
        "baseline_residual_p99.png",
        "Fixed-region background residual p99",
        "p99 / input median (dB)",
    )
    heatmap(
        all_rows,
        "target_loss_dB",
        "baseline_target_loss.png",
        "Target loss",
        "target loss (dB)",
    )
    plot_detection_runtime(all_rows)
    manifest = {
        "script": "scripts/run_baseline_benchmark.py",
        "suite": relative_repo_path(suite_path),
        "suite_definition": suite,
        "reproducibility": reproducibility_provenance(
            [sys.executable, *sys.argv]
        ),
        "comparison_groups": {
            "F1/F2 strict": "Current, phase-only CSI/DPCA-class, and row complex LS/Wiener use the same F1/F2 interface and dynamic backend CFAR.",
            "4-channel academic reference": "JDL, MNEC, and SA-MNEC use original four-channel space-time data and are not mixed into the strict F1/F2 claim.",
        },
        "methods": {
            "Current legacy CSI": "source legacy_min_magnitude CSI",
            "Phase-only CSI (DPCA-class)": "F1 - exp(j*phase_current) F2 inside current dynamic support; no amplitude equalization",
            "Row complex LS / Wiener": "background-only per-Doppler row alpha = sum(F1 conj(F2))/sum(|F2|^2)",
            "JDL-3x4 reduced STAP": "4 channels x 3 adjacent Doppler rows, diagonally loaded covariance, unit-gain reduced-dimension beamformer",
            "MNEC": "adaptive dominant-eigenspace projector with minimum-norm unit-gain steering response",
            "SA-MNEC": "four subaperture covariance estimates, trace normalization/per-channel normalization, then MNEC projector",
            "ADMM-ANM-STAP": "not implemented in first round; optional and not required to establish the requested trend",
            "DU-ANM-STAP": "not implemented",
            "neural_network": "not trained",
        },
        "oracle_weight_policy": "No target truth is used to construct a background-trained weight. Row-LS/Wiener, JDL, MNEC and SA-MNEC weights and their input calibration are computed from C+N only, then fixed for target evaluation.",
        "current_observation_policy": "Current legacy/phase-only CSI retain the existing target-observation P38 calibration path; this is not an Oracle weight. Background-trained LS/Wiener, JDL, MNEC and SA-MNEC weights use C+N only.",
        "metrics": {
            "high_energy_region": "fixed all-Doppler rows and configured range columns rg_st..rg_ed",
            "high_energy_threshold": "15 dB over the per-scenario fixed-region C+N input median",
            "background_fa": "pure C+N background with the same CPU GO-CFAR geometry; Pfa = hit cells / valid CFAR test cells",
            "mdv": "local row-shift target sweep around clutter centre; minimum detected Doppler separation converted by lambda/2; a proxy, not an operational full sweep",
            "runtime": "CPU method runtime for one paired case, excluding simulator generation and MDV sweep",
        },
        "sa_mnec_reference": {
            "title": "Subaperture Averaged Minimum Norm Eigen-Canceler for Airborne Radar Clutter Suppression",
            "doi": "10.1109/LGRS.2026.3664295",
            "url": "https://doi.org/10.1109/LGRS.2026.3664295",
            "basis": "public abstract: subaperture averaged CCM combined with MNEC for nonhomogeneous clutter, channel amplitude/phase errors and sample-limited conditions",
        },
        "raw_data_policy": "Generated BINs and transient matrices are removed by default after each case; only compact CSV/PNG/JSON summaries are retained.",
        "scenario_inputs": scenario_rows,
        "gpu_production_run": False,
        "evidence_boundary": "CPU offline baseline replay; no CUDA production claim because the current environment has no communicable NVIDIA driver.",
    }
    (OUT / "baseline_benchmark_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"cases": len(scenario_rows), "methods": len(METHODS), "output": str(OUT)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
