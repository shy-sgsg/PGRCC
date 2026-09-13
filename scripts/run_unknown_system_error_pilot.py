#!/usr/bin/env python3
"""Run the compact deterministic four-condition unknown-error pilot.

The pilot is intentionally a raw-IQ observability experiment.  It exercises
the production Stage2 packet generator and the four-channel baseline-error
injector, then applies deterministic known/estimated phase corrections.  It
does not run the production CSI/STAP/CFAR chain and therefore cannot be used
as an end-to-end detection or tracking benchmark.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import platform
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = ROOT / "configs/research/unknown_system_error_baseline_pilot.json"
SIMULATOR = ROOT / "build/simulate_stage2_statistical"
ANALYZER_PATH = ROOT / "scripts/analyze_four_channel_observables.py"
BASELINE_ERROR_M = 0.01
REQUIRED_CONDITIONS = (
    "ideal_on",
    "current_unknown",
    "known_correction",
    "estimated_correction",
)


def _load_analyzer():
    spec = importlib.util.spec_from_file_location("four_channel_observables", ANALYZER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {ANALYZER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OBS = _load_analyzer()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    command = ["git", "--git-dir=.git-real", "--work-tree=.", *args]
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return result.stdout.strip()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def validate_estimator_inputs(paths: Iterable[Path | str]) -> None:
    """Reject truth/target-truth files from the estimated-error estimator."""

    for path in paths:
        normalized = str(path).lower()
        if "truth" in normalized:
            raise ValueError(f"estimated-error estimator cannot read truth path: {path}")


def validate_condition_set(condition_ids: Iterable[str]) -> None:
    present = set(condition_ids)
    missing = [condition for condition in REQUIRED_CONDITIONS if condition not in present]
    if missing:
        raise ValueError("pilot is missing required conditions: " + ", ".join(missing))


def compute_recovery_metrics(
    current_value: float,
    known_value: float,
    estimated_value: float,
    metric_direction: str = "lower_is_better",
    zero_tolerance: float = 1.0e-12,
) -> Dict[str, object]:
    """Return signed Known-Current and Estimated-Current recovery values."""

    if metric_direction == "lower_is_better":
        recoverable = float(current_value) - float(known_value)
        actual_recovered = float(current_value) - float(estimated_value)
    elif metric_direction == "higher_is_better":
        recoverable = float(known_value) - float(current_value)
        actual_recovered = float(estimated_value) - float(current_value)
    else:
        raise ValueError("metric_direction must be lower_is_better or higher_is_better")
    ratio = (
        None
        if abs(recoverable) <= zero_tolerance
        else actual_recovered / recoverable
    )
    return {
        "recoverable_space_known_minus_current": recoverable,
        "actual_recovered_estimated_minus_current": actual_recovered,
        "recovery_ratio": ratio,
    }


def _find_period_file(case_dir: Path) -> Path:
    manifest = case_dir / "data/period_files.csv"
    if not manifest.is_file():
        raise RuntimeError(f"stage2 period manifest is missing: {manifest}")
    with manifest.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise RuntimeError(f"pilot expects one period, got {len(rows)} in {manifest}")
    path = Path(rows[0]["file"])
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    if not path.is_file():
        raise RuntimeError(f"stage2 period file is missing: {path}")
    return path


def _run_stage2(config_path: Path, log_path: Path) -> None:
    if not SIMULATOR.is_file():
        raise RuntimeError(
            f"missing {SIMULATOR}; build simulate_stage2_statistical before running the pilot"
        )
    command = [str(SIMULATOR), "--config", str(config_path)]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        log.write("command=" + " ".join(command) + "\n")
        log.flush()
        result = subprocess.run(
            command,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
        log.write(f"exit_code={result.returncode}\n")
    if result.returncode != 0:
        raise RuntimeError(f"stage2 failed for {config_path}; see {log_path}")


def _case_config(
    template: Mapping[str, object],
    case_id: str,
    output_dir: Path,
    target_enabled: bool,
    baseline_error_m: float,
) -> Dict[str, object]:
    config = json.loads(json.dumps(template))
    config["case_id"] = case_id
    config["output_dir"] = str(output_dir)
    impairments = config.setdefault("channel_impairments", {})
    impairments["enabled"] = bool(baseline_error_m != 0.0)
    impairments["baseline_error_m"] = float(baseline_error_m)
    for target in config.get("targets", []):
        target["enabled"] = bool(target_enabled)
    return config


def _decode_layout(template: Mapping[str, object]) -> Dict[str, object]:
    waveform = template["waveform"]
    return {
        "pulse_len": int(waveform["pulse_len"]),
        "channel_count": int(waveform["new_protocol_channel_count"]),
        "iq_data_type": str(waveform["iq_data_type"]),
        "fc_hz": float(waveform["fc_ghz"]) * 1.0e9,
        "pulses_per_beam": int(waveform["pulse_num"]),
    }


def _apply_baseline_correction(
    source: Path,
    destination: Path,
    layout: Mapping[str, object],
    baseline_error_m: float,
) -> None:
    """Undo the deterministic Stage2 baseline phase on channels 2 and 4."""

    if destination.exists():
        raise RuntimeError(f"refusing to overwrite existing correction file: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    pulse_len = int(layout["pulse_len"])
    channel_count = int(layout["channel_count"])
    iq_data_type = str(layout["iq_data_type"])
    fc_hz = float(layout["fc_hz"])
    if channel_count != 4:
        raise ValueError("baseline correction requires four channels")
    if iq_data_type.lower() in {"int16", "iq_int16", "short", "s16"}:
        dtype = np.dtype("<i2")
    elif iq_data_type.lower() in {"float32", "float", "f32"}:
        dtype = np.dtype("<f4")
    else:
        raise ValueError(f"unsupported iq_data_type: {iq_data_type}")
    scalar_bytes = dtype.itemsize
    packet_bytes = OBS.HEADER_BYTES + pulse_len * channel_count * 2 * scalar_bytes
    wavelength = OBS.C / fc_hz

    with source.open("rb") as input_stream, destination.open("wb") as output_stream:
        packet_index = 0
        while True:
            header = input_stream.read(OBS.HEADER_BYTES)
            if not header:
                break
            if len(header) != OBS.HEADER_BYTES:
                raise ValueError(f"truncated header at packet {packet_index}")
            declared = int.from_bytes(header[9:13], byteorder="little", signed=False)
            if declared != packet_bytes:
                raise ValueError(
                    f"packet {packet_index} declares {declared} bytes; expected {packet_bytes}"
                )
            payload = input_stream.read(packet_bytes - OBS.HEADER_BYTES)
            if len(payload) != packet_bytes - OBS.HEADER_BYTES:
                raise ValueError(f"truncated payload at packet {packet_index}")
            values = np.frombuffer(payload, dtype=dtype).copy()
            iq = values.reshape(pulse_len, channel_count, 2)
            channels = iq[:, :, 0].astype(np.float64) + 1j * iq[:, :, 1].astype(np.float64)
            theta_deg = int.from_bytes(header[218:220], byteorder="little", signed=True) / 100.0
            phase = 2.0 * math.pi * baseline_error_m * math.sin(math.radians(theta_deg)) / wavelength
            correction = complex(math.cos(-phase), math.sin(-phase))
            channels[:, 1] *= correction
            channels[:, 3] *= correction
            if dtype == np.dtype("<i2"):
                iq[:, 1, 0] = np.rint(np.clip(channels[:, 1].real, -32768, 32767)).astype(dtype)
                iq[:, 1, 1] = np.rint(np.clip(channels[:, 1].imag, -32768, 32767)).astype(dtype)
                iq[:, 3, 0] = np.rint(np.clip(channels[:, 3].real, -32768, 32767)).astype(dtype)
                iq[:, 3, 1] = np.rint(np.clip(channels[:, 3].imag, -32768, 32767)).astype(dtype)
            else:
                iq[:, 1, 0] = channels[:, 1].real.astype(dtype)
                iq[:, 1, 1] = channels[:, 1].imag.astype(dtype)
                iq[:, 3, 0] = channels[:, 3].real.astype(dtype)
                iq[:, 3, 1] = channels[:, 3].imag.astype(dtype)
            output_stream.write(header)
            output_stream.write(values.tobytes())
            packet_index += 1


def _combined_phase_fit(phase_difference: Mapping[str, object]) -> Dict[str, object]:
    rows = [row for row in phase_difference["rows"] if row["phase_error_rad"] is not None]
    return OBS.fit_angle_phase_model(
        [row["theta_cmd_deg"] for row in rows],
        [row["phase_error_rad"] for row in rows],
        [row["weight"] for row in rows],
    )


def _mean_coherence(summary: Mapping[str, object]) -> Optional[float]:
    values = [
        float(pair["mean_coherence"])
        for pair in summary["pairs"].values()
        if pair["mean_coherence"] is not None
    ]
    return None if not values else float(np.mean(values))


def _write_rows(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    rows = list(rows)
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    fieldnames: List[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def run_pilot(output_root: Path) -> Dict[str, object]:
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"refusing to overwrite non-empty pilot output: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    with TEMPLATE_PATH.open("r", encoding="utf-8") as stream:
        template = json.load(stream)
    layout = _decode_layout(template)
    source_commit = _git("rev-parse", "HEAD")
    source_status = _git("status", "--short", "--untracked-files=all")

    cases = (
        ("ideal_off", False, 0.0),
        ("unknown_off", False, BASELINE_ERROR_M),
        ("ideal_on", True, 0.0),
        ("current_unknown", True, BASELINE_ERROR_M),
    )
    config_dir = output_root / "configs"
    log_dir = output_root / "logs"
    raw_dir = output_root / "raw"
    case_paths: Dict[str, Path] = {}
    commands: Dict[str, List[str]] = {}
    for case_id, target_enabled, baseline_error_m in cases:
        case_dir = raw_dir / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / f"{case_id}.run.json"
        config = _case_config(
            template,
            case_id,
            case_dir,
            target_enabled,
            baseline_error_m,
        )
        _write_json(config_path, config)
        command = [str(SIMULATOR), "--config", str(config_path)]
        commands[case_id] = command
        _run_stage2(config_path, log_dir / f"{case_id}.log")
        case_paths[case_id] = _find_period_file(case_dir)

    observable_dir = output_root / "observables"
    summaries: Dict[str, Dict[str, object]] = {}
    for case_id, path in case_paths.items():
        summaries[case_id] = OBS.analyze_file(
            path,
            int(layout["pulse_len"]),
            int(layout["channel_count"]),
            str(layout["iq_data_type"]),
            float(layout["fc_hz"]),
            int(layout["pulses_per_beam"]),
            output_dir=observable_dir / case_id,
        )

    calibration = OBS.paired_phase_difference(
        case_paths["ideal_off"],
        case_paths["unknown_off"],
        int(layout["pulse_len"]),
        int(layout["channel_count"]),
        str(layout["iq_data_type"]),
        int(layout["pulses_per_beam"]),
    )
    validate_estimator_inputs([case_paths["ideal_off"], case_paths["unknown_off"]])
    calibration["combined_fit"] = _combined_phase_fit(calibration)
    estimated_error_m = OBS.estimate_baseline_error(
        calibration["combined_fit"], float(layout["fc_hz"])
    )
    _write_json(output_root / "calibration_phase_difference.json", calibration)
    _write_rows(output_root / "calibration_phase_difference.csv", calibration["rows"])

    known_path = raw_dir / "known_correction" / "period_0000.bin"
    estimated_path = raw_dir / "estimated_correction" / "period_0000.bin"
    _apply_baseline_correction(
        case_paths["current_unknown"], known_path, layout, BASELINE_ERROR_M
    )
    if estimated_error_m is None:
        raise RuntimeError("deterministic calibration fit did not identify a baseline error")
    _apply_baseline_correction(
        case_paths["current_unknown"], estimated_path, layout, estimated_error_m
    )
    corrected_paths = {
        "known_correction": known_path,
        "estimated_correction": estimated_path,
    }
    for condition, path in corrected_paths.items():
        summaries[condition] = OBS.analyze_file(
            path,
            int(layout["pulse_len"]),
            int(layout["channel_count"]),
            str(layout["iq_data_type"]),
            float(layout["fc_hz"]),
            int(layout["pulses_per_beam"]),
            output_dir=observable_dir / condition,
        )

    phase_differences: Dict[str, Dict[str, object]] = {}
    phase_reference = case_paths["ideal_on"]
    for condition in ("current_unknown", "known_correction", "estimated_correction"):
        compared_path = case_paths[condition] if condition in case_paths else corrected_paths[condition]
        comparison = OBS.paired_phase_difference(
            phase_reference,
            compared_path,
            int(layout["pulse_len"]),
            int(layout["channel_count"]),
            str(layout["iq_data_type"]),
            int(layout["pulses_per_beam"]),
        )
        comparison["combined_fit"] = _combined_phase_fit(comparison)
        phase_differences[condition] = comparison
        _write_json(
            output_root / f"phase_difference_to_ideal_{condition}.json", comparison
        )
        _write_rows(
            output_root / f"phase_difference_to_ideal_{condition}.csv",
            comparison["rows"],
        )

    condition_names = {
        "ideal_on": "Ideal/No-error",
        "current_unknown": "Current+unknown-error",
        "known_correction": "Known-error correction upper bound",
        "estimated_correction": "Estimated-error correction",
    }
    validate_condition_set(condition_names)
    condition_metrics: List[Dict[str, object]] = []
    for condition in condition_names:
        phase_residual = 0.0 if condition == "ideal_on" else phase_differences[condition]["mean_abs_phase_error_rad"]
        condition_metrics.append(
            {
                "condition": condition_names[condition],
                "condition_id": condition,
                "phase_residual_to_ideal_rad": phase_residual,
                "mean_pair_coherence": _mean_coherence(summaries[condition]),
                "packet_count": summaries[condition]["packet_count"],
            }
        )
    metric_by_id = {row["condition_id"]: row for row in condition_metrics}
    current_phase = metric_by_id["current_unknown"]["phase_residual_to_ideal_rad"]
    known_phase = metric_by_id["known_correction"]["phase_residual_to_ideal_rad"]
    estimated_phase = metric_by_id["estimated_correction"]["phase_residual_to_ideal_rad"]
    recovery_values = compute_recovery_metrics(
        float(current_phase), float(known_phase), float(estimated_phase)
    )
    recovery = {
        "metric": "phase_residual_to_ideal_rad (lower is better)",
        **recovery_values,
    }
    _write_rows(output_root / "condition_metrics.csv", condition_metrics)
    _write_json(output_root / "condition_metrics.json", {"conditions": condition_metrics, "recovery": recovery})

    raw_files = {}
    for condition, path in {**case_paths, **corrected_paths}.items():
        raw_files[condition] = {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "retained_locally": True,
            "included_in_git_commit": False,
        }
    manifest = {
        "schema": "unknown_system_error_baseline_pilot_v1",
        "status": "raw_iq_pilot_completed",
        "source": {
            "source_commit_before_run": source_commit,
            "worktree_status_before_run": source_status,
            "template": str(TEMPLATE_PATH),
            "template_sha256": _sha256(TEMPLATE_PATH),
            "simulator": str(SIMULATOR),
            "analyzer": str(ANALYZER_PATH),
        },
        "execution": {
            "working_directory": str(ROOT),
            "python": sys.version,
            "platform": platform.platform(),
            "device_validation": "not_applicable: CPU Stage2 raw-IQ pilot; no CUDA result claimed",
            "commands": commands,
        },
        "layout": layout,
        "error_model": {
            "parameter": "baseline_error_m",
            "truth_value_m": BASELINE_ERROR_M,
            "applied_to_channels": [2, 4],
            "phase_model": "2*pi*baseline_error_m*sin(theta_cmd_deg)/wavelength",
            "estimator": "paired no-error reference + six-pair horizontal phase fit",
            "truth_used_in_estimator": False,
            "estimated_value_m": estimated_error_m,
            "absolute_estimation_error_m": abs(float(estimated_error_m) - BASELINE_ERROR_M),
        },
        "raw_files": raw_files,
        "condition_metrics": condition_metrics,
        "recovery": recovery,
        "not_run": [
            "production B0/B1/B2/B3 CSI/STAP end-to-end matrix",
            "CFAR Pd/Pfa and false-cluster evaluation",
            "track/PIPE causal target-retention evaluation",
            "CUDA performance or device result",
        ],
        "limitations": [
            "The pilot uses one deterministic period, seven commanded angles, and eight pulses per beam.",
            "The estimator uses a matched no-error calibration reference; it is not yet a blind operational estimator.",
            "The phase correction is deterministic and restricted to the audited baseline-error injector.",
            "Pure phase rotation does not change pair coherence by itself; end-to-end cancellation and detection remain pending.",
        ],
    }
    _write_json(output_root / "manifest.json", manifest)
    return manifest


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "outputs/unknown_system_error_pilot_20260913",
    )
    args = parser.parse_args(argv)
    manifest = run_pilot(args.output_root.resolve())
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "output_root": str(args.output_root.resolve()),
                "estimated_baseline_error_m": manifest["error_model"]["estimated_value_m"],
                "recovery": manifest["recovery"],
            },
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
