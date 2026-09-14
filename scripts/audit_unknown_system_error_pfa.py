#!/usr/bin/env python3
"""Audit empirical Pfa and its CUT denominator on controlled H0 scenes.

The script keeps the production GO threshold and configured Pfa unchanged. It
generates four target-free controls, runs the real Stage2 simulator and
``GMTI_core`` on CUDA, retains the production CFAR maps, and reports both the
full valid-CUT denominator and the historical dynamic-band denominator. The
calibrated control is made by applying the known-error correction to the
geometry-error raw file; that path is an evaluator reference, not a blind
estimator input.
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
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_model_mismatch_audit as production_audit  # noqa: E402
import run_unknown_system_error_end_to_end as e2e  # noqa: E402
from unknown_geometry_correction import apply_unknown_geometry_correction  # noqa: E402


TEMPLATE = ROOT / "configs/research/unknown_system_error_end_to_end_pilot.json"
SIMULATOR = ROOT / "build/simulate_stage2_statistical"
GMTI_CORE = ROOT / "build/GMTI_core"
GEOMETRY_ERROR_M = 0.0025
DEFAULT_SEED = 7701


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.integer, int)):
        return int(value)
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(value), ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def _write_rows(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(_json_safe(rows))


def _run_logged_env(
    command: Sequence[str], log_path: Path, env: Mapping[str, str]
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        log.write("$ " + " ".join(str(item) for item in command) + "\n")
        log.write("GMTI_CFAR_DUMP_BEAM=" + env.get("GMTI_CFAR_DUMP_BEAM", "") + "\n")
        log.flush()
        completed = subprocess.run(
            [str(item) for item in command],
            cwd=ROOT,
            env=dict(env),
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
        log.write(f"exit_code={completed.returncode}\n")
    return int(completed.returncode)


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", "--git-dir=.git-real", "--work-tree=.", *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unavailable"


def _probe_gpu() -> dict[str, object]:
    completed = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=name,pstate,temperature.gpu,power.draw,power.limit,clocks.sm,utilization.gpu,memory.used,memory.total",
            "--format=csv,noheader",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return {"returncode": completed.returncode, "output": completed.stdout}


def _probe_disk() -> dict[str, object]:
    completed = subprocess.run(
        ["df", "-h", str(ROOT), "/tmp"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return {"returncode": completed.returncode, "output": completed.stdout}


def _set_geometry_truth(config: dict[str, object], delta_m: float) -> None:
    geometry = config.get("channel_geometry")
    if not isinstance(geometry, dict):
        raise ValueError("channel_geometry must be an object")
    for channel_number in range(1, 5):
        channel = geometry.get(f"channel_{channel_number}")
        if not isinstance(channel, dict):
            raise ValueError(f"channel_{channel_number} geometry is missing")
        reported = channel.get("reported")
        true = channel.get("true")
        if not isinstance(reported, dict) or not isinstance(true, dict):
            raise ValueError(f"channel_{channel_number} reported/true geometry is missing")
        true.clear()
        true.update(reported)
        if channel_number in (2, 4):
            true["x_m"] = float(reported["x_m"]) + delta_m


def _make_control_config(
    template: Mapping[str, object],
    label: str,
    output_dir: Path,
    seed: int,
    area_enabled: bool,
    geometry_delta_m: float,
) -> dict[str, object]:
    config = copy.deepcopy(dict(template))
    config["case_id"] = f"pfa_{label}"
    config["output_dir"] = str(output_dir.resolve())
    config["truth_output"] = False
    config.setdefault("random", {})["random_seed"] = int(seed)  # type: ignore[union-attr]
    scene = config.get("scene")
    if not isinstance(scene, dict):
        raise ValueError("scene must be an object")
    scene["signal_only"] = False
    # With no enabled target, ``auto`` selects the continuous-area
    # precompressed path.  The formal C+N partner was frozen from a target
    # run and therefore used raw-LFM clutter; pin the clutter controls to that
    # same source domain so their Pfa comparison is meaningful.
    scene["output_signal_domain"] = "raw_lfm" if area_enabled else "auto"
    area = scene.get("area_clutter")
    if not isinstance(area, dict):
        raise ValueError("scene.area_clutter must be an object")
    area["enabled"] = bool(area_enabled)
    area["model"] = "beam_center_clutter"
    area["scatterer_count"] = 0
    area["mean_power"] = 1.0
    area["texture_sigma"] = 0.0
    area["temporal_correlation_rho"] = 1.0
    thermal = scene.get("thermal_noise")
    if not isinstance(thermal, dict):
        raise ValueError("scene.thermal_noise must be an object")
    thermal["enabled"] = True
    thermal["noise_power"] = 0.01
    thermal["include_target_only"] = False
    targets = config.get("targets")
    if not isinstance(targets, list):
        raise ValueError("targets must be an array")
    for target in targets:
        if isinstance(target, dict):
            target["enabled"] = False
    _set_geometry_truth(config, geometry_delta_m)
    return config


def _write_config(path: Path, config: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(config), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_period_file(case_dir: Path) -> Path:
    return e2e._find_period_file(case_dir)


def _patch_xml_mode(xml_path: Path, mode: str) -> None:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    parameter = root.find(".//GMTI_parameter")
    if parameter is None:
        raise RuntimeError(f"XML has no GMTI_parameter: {xml_path}")
    element = parameter.find("csi_detection_band_mode")
    if element is None:
        element = ET.SubElement(parameter, "csi_detection_band_mode")
    element.text = mode
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)


def _run_stage2_control(
    label: str,
    config: Mapping[str, object],
    control_dir: Path,
    simulator: Path,
) -> dict[str, object]:
    config_path = control_dir / "stage2.run.json"
    _write_config(config_path, config)
    log_path = control_dir / "simulate_stage2.log"
    rc = e2e._run_logged(
        [str(simulator), "--config", str(config_path)], log_path
    )
    row: dict[str, object] = {
        "label": label,
        "stage2_exit_code": rc,
        "stage2_config": str(config_path),
        "stage2_log": str(log_path),
        "status": "stage2_failed" if rc else "stage2_completed",
    }
    if rc == 0:
        stage2_dir = control_dir / "stage2"
        raw = _read_period_file(stage2_dir)
        xml = stage2_dir / "config/temp_config_stage2_period_0000.xml"
        row.update({
            "raw_path": str(raw),
            "raw_sha256": _sha256(raw),
            "raw_size_bytes": raw.stat().st_size,
            "stage2_xml": str(xml),
        })
    return row


def _run_core(
    label: str,
    raw_path: Path,
    source_xml: Path,
    control_dir: Path,
    core: Path,
    mode: str,
) -> dict[str, object]:
    production_root = control_dir / f"production_{mode}"
    production_root.mkdir(parents=True, exist_ok=True)
    xml_path = production_root / "config.xml"
    shutil.copy2(source_xml, xml_path)
    e2e._set_xml_path(xml_path, raw_path, production_root)
    production_audit.patch_production_xml(xml_path)
    _patch_xml_mode(xml_path, mode)
    log_path = production_root / "gmticore.log"
    env = os.environ.copy()
    # The one-beam Stage2 file is consumed by GMTI_core as processing beam 1;
    # period/file indices are zero-based elsewhere, but this diagnostic
    # selector follows the core's emitted beam id.
    env["GMTI_CFAR_DUMP_BEAM"] = "1"
    rc = _run_logged_env(
        [str(core), str(xml_path), "--runtime-mode=debug", "--runtime-diagnostics=on"],
        log_path,
        env,
    )
    row: dict[str, object] = {
        "label": label,
        "cfar_mode": mode,
        "gmticore_exit_code": rc,
        "production_root": str(production_root),
        "config_xml": str(xml_path),
        "core_log": str(log_path),
        "status": "core_failed" if rc else "core_completed",
    }
    if rc == 0:
        row.update(_audit_core_output(production_root))
    return row


def _read_key_value(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _float_or_none(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _read_cfar_summary(log_path: Path) -> dict[str, object]:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    lines = re.findall(r"\[CFAR\]\[SUMMARY\].*", text)
    result: dict[str, object] = {
        "summary_found": bool(lines),
        "summary_line": lines[-1] if lines else None,
    }
    if lines:
        for key, raw in re.findall(r"([A-Za-z][A-Za-z0-9_]*)=([-+0-9.eE]+)", lines[-1]):
            value = _float_or_none(raw)
            if value is not None and value.is_integer():
                result[key] = int(value)
            else:
                result[key] = value
    return result


def _read_split_summary(log_path: Path) -> dict[str, object]:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    lines = re.findall(r"\[CFAR\]\[SPLIT\].*", text)
    result: dict[str, object] = {"split_summary_found": bool(lines)}
    if lines:
        result["split_summary_line"] = lines[-1]
        for key, raw in re.findall(r"([A-Za-z][A-Za-z0-9_]*)=([-+0-9.eE]+)", lines[-1]):
            value = _float_or_none(raw)
            result[key] = int(value) if value is not None and value.is_integer() else value
    return result


def _find_csi_manifest(production_root: Path) -> Path:
    return production_audit.find_single_manifest(production_root)


def _active_manifest_row(manifest_path: Path) -> dict[str, str]:
    with manifest_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise RuntimeError(f"empty CSI manifest: {manifest_path}")
    return next(
        (row for row in rows if row.get("roi_name") == "active_support_unmasked"),
        rows[0],
    )


def _runtime_config(production_root: Path) -> dict[str, object]:
    paths = sorted(production_root.rglob("runtime_config_dump.json"))
    if not paths:
        return {}
    value = json.loads(paths[0].read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _directional_go_means(power: np.ndarray, guard: int, background: int) -> tuple[np.ndarray, ...]:
    """Reconstruct the four GO training means used by cfar_detect_kernel."""

    height, width = power.shape
    radius = guard + background
    if radius <= 0 or 2 * radius + 1 > height:
        raise ValueError("invalid GO geometry for directional means")
    prefix_x = np.concatenate(
        [np.zeros((height, 1), dtype=np.float64), np.cumsum(power, axis=1, dtype=np.float64)],
        axis=1,
    )
    left_row = np.zeros_like(power, dtype=np.float64)
    right_row = np.zeros_like(power, dtype=np.float64)
    valid_cols = np.arange(radius, width - radius)
    left_row[:, valid_cols] = (
        prefix_x[:, valid_cols - guard]
        - prefix_x[:, valid_cols - radius]
    )
    right_row[:, valid_cols] = (
        prefix_x[:, valid_cols + radius + 1]
        - prefix_x[:, valid_cols + guard + 1]
    )

    def circular_row_sum(values: np.ndarray) -> np.ndarray:
        extended = np.concatenate([values[-radius:], values, values[:radius]], axis=0)
        prefix = np.concatenate(
            [np.zeros((1, width), dtype=np.float64), np.cumsum(extended, axis=0, dtype=np.float64)],
            axis=0,
        )
        row_index = np.arange(height)
        return prefix[row_index + 2 * radius + 1] - prefix[row_index]

    left = circular_row_sum(left_row) / float((2 * radius + 1) * background)
    right = circular_row_sum(right_row) / float((2 * radius + 1) * background)

    horizontal = np.zeros_like(power, dtype=np.float64)
    horizontal[:, valid_cols] = (
        prefix_x[:, valid_cols + radius + 1]
        - prefix_x[:, valid_cols - radius]
    )
    row_extended = np.concatenate([horizontal[-radius:], horizontal, horizontal[:radius]], axis=0)
    prefix_y = np.concatenate(
        [np.zeros((1, width), dtype=np.float64), np.cumsum(row_extended, axis=0, dtype=np.float64)],
        axis=0,
    )
    row_index = np.arange(height)
    top = (
        prefix_y[row_index + background]
        - prefix_y[row_index]
    ) / float(background * (2 * radius + 1))
    bottom = (
        prefix_y[row_index + radius + guard + 1 + background]
        - prefix_y[row_index + radius + guard + 1]
    ) / float(background * (2 * radius + 1))
    return left, right, top, bottom


def _quantiles(values: np.ndarray) -> dict[str, float | None]:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return {"p01": None, "p50": None, "p90": None, "p95": None, "p99": None}
    q = np.percentile(finite, [1, 50, 90, 95, 99])
    return {key: float(value) for key, value in zip(("p01", "p50", "p90", "p95", "p99"), q)}


def _valid_masks(
    height: int,
    width: int,
    guard: int,
    background: int,
    circular: bool,
    az_st: int | None,
    az_ed: int | None,
) -> dict[str, np.ndarray]:
    radius = guard + background
    rows = np.arange(height)[:, None]
    cols = np.arange(width)[None, :]
    if circular:
        row_valid = np.ones((height, 1), dtype=bool)
    else:
        row_valid = ((rows >= radius) & (rows < height - radius))
    col_valid = (cols >= radius) & (cols < width - radius)
    full = row_valid & col_valid
    dynamic = np.zeros((height, 1), dtype=bool)
    if az_st is not None and az_ed is not None:
        dynamic = (rows >= az_st) & (rows <= az_ed)
    dynamic &= row_valid
    return {"full": full, "dynamic": dynamic & col_valid, "outside_dynamic": full & ~(dynamic & col_valid)}


def _map_stats(
    hits: np.ndarray,
    power: np.ndarray,
    threshold: np.ndarray,
    masks: Mapping[str, np.ndarray],
    alpha: float | None,
    guard: int,
    background: int,
) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, mask in masks.items():
        selected_hits = hits[mask]
        selected_power = power[mask]
        selected_threshold = threshold[mask]
        hit_mask = selected_hits > 0.0
        result[name] = {
            "valid_cut_cells": int(np.count_nonzero(mask)),
            "hit_cells_from_map": int(np.count_nonzero(hit_mask)),
            "empirical_pfa": (
                float(np.count_nonzero(hit_mask) / np.count_nonzero(mask))
                if np.count_nonzero(mask) else None
            ),
            "power_quantiles": _quantiles(selected_power),
            "threshold_quantiles": _quantiles(selected_threshold),
            "hit_power_quantiles": _quantiles(selected_power[hit_mask]),
            "power_over_threshold_quantiles": _quantiles(
                selected_power / np.maximum(selected_threshold, np.finfo(np.float32).tiny)
            ),
        }
    if alpha is not None and alpha > 0.0:
        left, right, top, bottom = _directional_go_means(power.astype(np.float64), guard, background)
        directional = np.stack([left, right, top, bottom], axis=0)
        valid = masks["full"]
        max_mean = np.max(directional, axis=0)
        min_mean = np.min(directional, axis=0)
        median_mean = np.median(directional, axis=0)
        result["go_training"] = {
            "directional_mean_quantiles": {
                name: _quantiles(directional[index][valid])
                for index, name in enumerate(("left", "right", "top", "bottom"))
            },
            "go_max_mean_quantiles": _quantiles(max_mean[valid]),
            "go_max_to_min_ratio_quantiles": _quantiles(
                max_mean[valid] / np.maximum(min_mean[valid], np.finfo(np.float64).tiny)
            ),
            "go_max_to_median_ratio_quantiles": _quantiles(
                max_mean[valid] / np.maximum(median_mean[valid], np.finfo(np.float64).tiny)
            ),
            "threshold_reconstruction_residual_quantiles": _quantiles(
                threshold[valid].astype(np.float64) - alpha * max_mean[valid]
            ),
            "configured_go_alpha": alpha,
            "training_distribution_definition": "four directional GO ring means; threshold=alpha*max(four means)",
        }
    return result


def _audit_core_output(production_root: Path) -> dict[str, object]:
    log_path = production_root / "gmticore.log"
    manifest_path = _find_csi_manifest(production_root)
    active = _active_manifest_row(manifest_path)
    runtime = _runtime_config(production_root)
    file_layout = runtime.get("file_layout", {})
    detection = runtime.get("detection", {})
    scan = runtime.get("scan", {})
    channel_and_gmti = runtime.get("channel_and_gmti", {})
    if not isinstance(file_layout, Mapping):
        file_layout = {}
    if not isinstance(detection, Mapping):
        detection = {}
    if not isinstance(scan, Mapping):
        scan = {}
    if not isinstance(channel_and_gmti, Mapping):
        channel_and_gmti = {}

    metas = sorted(production_root.rglob("cfar_*_meta.txt"))
    if not metas:
        return {
            "status": "completed_without_cfar_maps",
            "cfar_summary": _read_cfar_summary(log_path),
            "manifest": str(manifest_path),
            "runtime_config": str(next(iter(production_root.rglob("runtime_config_dump.json")), "")),
        }
    meta_path = metas[0]
    meta = _read_key_value(meta_path)
    try:
        height = int(meta["rows"])
        width = int(meta["cols"])
    except (KeyError, ValueError) as exc:
        raise RuntimeError(f"invalid CFAR meta dimensions: {meta_path}") from exc
    stem = meta_path.name[: -len("_meta.txt")]
    debug_dir = meta_path.parent
    map_paths = {
        "hits": debug_dir / f"{stem}_hits.f32",
        "power": debug_dir / f"{stem}_power.f32",
        "threshold": debug_dir / f"{stem}_threshold.f32",
    }
    arrays: dict[str, np.ndarray] = {}
    for name, path in map_paths.items():
        if not path.is_file():
            raise RuntimeError(f"CFAR map is missing: {path}")
        values = np.fromfile(path, dtype="<f4")
        if values.size != height * width:
            raise RuntimeError(f"CFAR map has {values.size} values, expected {height * width}: {path}")
        arrays[name] = values.reshape(height, width)

    guard = int(float(detection.get("cfar_guard_cells", 4)))
    background = int(float(detection.get("cfar_background_cells", 16)))
    # Current runtime diagnostics stores these detector fields in
    # channel_and_gmti; keep the default only for older dumps.
    circular = bool(channel_and_gmti.get("cfar_doppler_circular", True))
    az_st = _float_or_none(active.get("az_st"))
    az_ed = _float_or_none(active.get("az_ed"))
    az_start = int(az_st) if az_st is not None else None
    az_end = int(az_ed) if az_ed is not None else None
    masks = _valid_masks(height, width, guard, background, circular, az_start, az_end)
    alpha = _float_or_none(meta.get("cfar_alpha"))
    map_stats = _map_stats(
        arrays["hits"], arrays["power"], arrays["threshold"], masks,
        alpha, guard, background,
    )
    cfar = _read_cfar_summary(log_path)
    split = _read_split_summary(log_path)
    mode = str(channel_and_gmti.get("csi_detection_band_mode", "dynamic"))
    radius = guard + background
    branch_evaluations = None
    if mode == "split" and az_start is not None and az_end is not None:
        boundary_guard = int(channel_and_gmti.get("csi_split_boundary_guard_rows", 4))
        in_start = max(0, az_start - boundary_guard)
        in_end = min(height - 1, az_end + boundary_guard)
        out_start = min(height - 1, az_start + boundary_guard + 1)
        out_end = max(0, az_end - boundary_guard - 1)
        in_rows = max(0, in_end - in_start + 1)
        out_rows = height - max(0, out_end - out_start + 1)
        cols = max(0, width - 2 * radius)
        branch_evaluations = {
            "in_branch_cut_rows": [in_start, in_end],
            "out_branch_excluded_rows": [out_start, out_end],
            "in_branch_evaluation_cells": in_rows * cols,
            "out_branch_evaluation_cells": out_rows * cols,
            "branch_evaluation_cells_total": (in_rows + out_rows) * cols,
            "unique_effective_valid_cut_cells": int(np.count_nonzero(masks["full"])),
        }

    result: dict[str, object] = {
        "status": "completed_with_cfar_maps",
        "manifest": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "runtime_config": str(next(iter(production_root.rglob("runtime_config_dump.json")), "")),
        "cfar_meta": str(meta_path),
        "cfar_map_paths": {name: str(path) for name, path in map_paths.items()},
        "matrix_shape": [height, width],
        "cfar_mode": mode,
        "configured_pfa": _float_or_none(meta.get("cfar_pfa_configured")),
        "go_alpha": alpha,
        "cfar_type": meta.get("cfar_type"),
        "guard_cells": guard,
        "background_cells": background,
        "cfar_radius_cells": radius,
        "doppler_circular": circular,
        "dynamic_band": {"az_st": az_start, "az_ed": az_end},
        "cfar_summary": cfar,
        "split_summary": split,
        "branch_evaluations": branch_evaluations,
        "denominator_definition": {
            "full_valid_CUTs": "all rows when circular=true, otherwise rows [R,H-1-R], with range columns [R,W-1-R], R=guard+background",
            "dynamic_band_valid_CUTs": "same range-valid columns restricted to CSI active azimuth rows; this is the historical denominator, not the kernel cut mask in dynamic mode",
            "dynamic_mode_kernel_cut_band": "cut_band_mode=0; all full valid CUT rows are tested, while dynamic band only selects CSI versus channel-2 detector input",
            "split_mode_kernel_cut_band": "two branch evaluations with an overlap; unique effective map rows are reported separately from branch evaluation cells",
        },
        "map_stats": map_stats,
        "dimension_provenance": {
            "runtime_file_layout": dict(file_layout),
            "runtime_scan": dict(scan),
            "active_csi_manifest_row": active,
        },
    }
    return result


def _make_calibrated_raw(
    geometry_error_raw: Path,
    destination: Path,
    metadata: Mapping[str, object],
) -> dict[str, object]:
    return apply_unknown_geometry_correction(
        geometry_error_raw,
        destination,
        metadata,
        GEOMETRY_ERROR_M,
    )


def run_audit(
    output_root: Path,
    simulator: Path = SIMULATOR,
    core: Path = GMTI_CORE,
    seed: int = DEFAULT_SEED,
) -> dict[str, object]:
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"refusing to overwrite non-empty output: {output_root}")
    if not TEMPLATE.is_file():
        raise RuntimeError(f"missing template: {TEMPLATE}")
    if not simulator.is_file() or not core.is_file():
        raise RuntimeError(f"missing executable(s): {simulator}, {core}")
    output_root.mkdir(parents=True, exist_ok=True)
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))

    controls = (
        ("H0_pure_noise", False, 0.0),
        ("H1_stationary_clutter", True, 0.0),
        ("H2_geometry_error_clutter", True, GEOMETRY_ERROR_M),
    )
    stage2_rows: dict[str, dict[str, object]] = {}
    # Keep the random realization paired across H0/H1/H2.  The control
    # difference should then identify the effect of clutter/geometry rather
    # than silently mixing it with a seed change.
    for label, area_enabled, delta_m in controls:
        control_dir = output_root / "inputs" / label
        config = _make_control_config(
            template, label, control_dir / "stage2", seed, area_enabled, delta_m
        )
        stage2_rows[label] = _run_stage2_control(label, config, control_dir, simulator)
        if stage2_rows[label].get("stage2_exit_code") != 0:
            continue

    if stage2_rows["H2_geometry_error_clutter"].get("stage2_exit_code") == 0:
        h2_raw = Path(stage2_rows["H2_geometry_error_clutter"]["raw_path"])
        h2_xml = Path(stage2_rows["H2_geometry_error_clutter"]["stage2_xml"])
        resolved = json.loads(
            (output_root / "inputs/H2_geometry_error_clutter/stage2/scenario_resolved.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = e2e.matrix.estimator_metadata(resolved)
        calibrated_dir = output_root / "inputs/H3_calibrated_geometry_error_clutter"
        calibrated_raw = calibrated_dir / "corrected.bin"
        calibration_result = _make_calibrated_raw(h2_raw, calibrated_raw, metadata)
        stage2_rows["H3_calibrated_geometry_error_clutter"] = {
            "label": "H3_calibrated_geometry_error_clutter",
            "stage2_exit_code": 0,
            "raw_path": str(calibrated_raw),
            "raw_sha256": _sha256(calibrated_raw),
            "raw_size_bytes": calibrated_raw.stat().st_size,
            "source_raw_path": str(h2_raw),
            "source_raw_sha256": _sha256(h2_raw),
            "known_correction_delta_m": GEOMETRY_ERROR_M,
            "correction_result": calibration_result,
            "stage2_xml": str(h2_xml),
            "status": "correction_completed",
        }

    core_rows: list[dict[str, object]] = []
    for label in ("H0_pure_noise", "H1_stationary_clutter", "H2_geometry_error_clutter"):
        row = stage2_rows[label]
        if row.get("stage2_exit_code") != 0:
            continue
        raw = Path(row["raw_path"])
        xml = Path(row["stage2_xml"])
        core_rows.append(
            _run_core(label, raw, xml, output_root / "controls" / label, core, "dynamic")
        )
    calibrated = stage2_rows.get("H3_calibrated_geometry_error_clutter")
    if calibrated and calibrated.get("stage2_exit_code") == 0:
        core_rows.append(
            _run_core(
                "H3_calibrated_geometry_error_clutter",
                Path(calibrated["raw_path"]),
                Path(calibrated["stage2_xml"]),
                output_root / "controls/H3_calibrated_geometry_error_clutter",
                core,
                "dynamic",
            )
        )
    h2 = stage2_rows["H2_geometry_error_clutter"]
    if h2.get("stage2_exit_code") == 0:
        core_rows.append(
            _run_core(
                "H2_geometry_error_clutter",
                Path(h2["raw_path"]),
                Path(h2["stage2_xml"]),
                output_root / "controls/H2_geometry_error_clutter",
                core,
                "split",
            )
        )

    summary_rows: list[dict[str, object]] = []
    for row in core_rows:
        audit = row.get("map_stats", {})
        full = audit.get("full", {}) if isinstance(audit, Mapping) else {}
        dynamic = audit.get("dynamic", {}) if isinstance(audit, Mapping) else {}
        summary_rows.append({
            "label": row.get("label"),
            "cfar_mode": row.get("cfar_mode"),
            "status": row.get("status"),
            "gmticore_exit_code": row.get("gmticore_exit_code"),
            "configured_pfa": row.get("configured_pfa"),
            "full_valid_CUTs": full.get("valid_cut_cells") if isinstance(full, Mapping) else None,
            "full_hit_cells_from_map": full.get("hit_cells_from_map") if isinstance(full, Mapping) else None,
            "full_empirical_pfa": full.get("empirical_pfa") if isinstance(full, Mapping) else None,
            "dynamic_band_valid_CUTs": dynamic.get("valid_cut_cells") if isinstance(dynamic, Mapping) else None,
            "dynamic_band_hit_cells_from_map": dynamic.get("hit_cells_from_map") if isinstance(dynamic, Mapping) else None,
            "dynamic_band_empirical_pfa": dynamic.get("empirical_pfa") if isinstance(dynamic, Mapping) else None,
            "log_hit_cells": row.get("cfar_summary", {}).get("hit_cells") if isinstance(row.get("cfar_summary"), Mapping) else None,
            "log_clusters": row.get("cfar_summary", {}).get("clusters") if isinstance(row.get("cfar_summary"), Mapping) else None,
            "production_root": row.get("production_root"),
        })

    manifest = {
        "schema": "unknown_system_error_pfa_audit_v1",
        "status": "completed" if core_rows and all(row.get("status", "").startswith("completed") for row in core_rows) else "completed_with_failures",
        "ai_training": False,
        "threshold_tuning": False,
        "known_correction_used_only_for_evaluation": True,
        "geometry_error_m": GEOMETRY_ERROR_M,
        "working_directory": str(ROOT),
        "source_commit": _git("rev-parse", "HEAD"),
        "tracked_worktree_status": _git("status", "--short", "--untracked-files=no"),
        "python": sys.version,
        "platform": platform.platform(),
        "simulator": str(simulator.resolve()),
        "simulator_sha256": _sha256(simulator),
        "gmticore": str(core.resolve()),
        "gmticore_sha256": _sha256(core),
        "template": str(TEMPLATE.resolve()),
        "template_sha256": _sha256(TEMPLATE),
        "gpu_probe": _probe_gpu(),
        "disk_probe": _probe_disk(),
        "controls": stage2_rows,
        "core_runs": core_rows,
        "denominator_conclusion": {
            "production_dynamic_mode_tests_full_valid_CUTs": True,
            "historical_dynamic_band_ratio_is_not_the_kernel_test_denominator": True,
            "reason": "cfar_detect_kernel receives cut_band_mode=0 in dynamic mode; band_st/band_ed only choose detector source",
        },
        "limitations": [
            "Controls are one-beam, target-free, stationary clutter/noise cases; they explain this audit's relative Pfa, not a universal operational Pfa.",
            "Cluster counts remain a post-CFAR connected-component result and are not substituted for CUT-level Pfa.",
            "The calibrated control uses the evaluator-known 2.5 mm correction and is not an online blind result.",
        ],
    }
    _write_rows(output_root / "pfa_summary.csv", summary_rows)
    _write_json(output_root / "manifest.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "outputs/unknown_system_error_pfa_audit_20260914_v1",
    )
    parser.add_argument("--simulator", type=Path, default=SIMULATOR)
    parser.add_argument("--gmticore", type=Path, default=GMTI_CORE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)
    manifest = run_audit(
        args.output_root.resolve(),
        simulator=args.simulator.resolve(),
        core=args.gmticore.resolve(),
        seed=args.seed,
    )
    print(json.dumps({
        "status": manifest["status"],
        "output_root": str(args.output_root.resolve()),
        "control_count": len(manifest["controls"]),
        "core_run_count": len(manifest["core_runs"]),
    }, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
