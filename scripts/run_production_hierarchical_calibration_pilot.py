#!/usr/bin/env python3
"""Run the paired production hierarchical calibration delay pilot.

The runner is an orchestration boundary.  Stage-2 generates the paired raw
inputs, ``estimate_channel_delay`` performs the existing fractional-delay
rewrite, and ``run_delay_stage1_formal.run_production_branch`` invokes the
real ``GMTI_pipe_core``/TrackManager path.  This module does not implement a
detector or a replacement tracker.

``--skip-cuda`` (also exposed as ``--contract``) writes only the frozen
contract and provenance.  It intentionally cannot produce algorithmic
results or a GO decision.
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
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CONFIG = ROOT / "configs/research/production_hierarchical_calibration_delay_pilot.json"
TEMPLATE = ROOT / "simulator/scenarios/beam50_one_targets_continuous.run.json"
SIMULATOR = ROOT / "build/simulate_stage2_statistical"
PIPE = ROOT / "build/GMTI_pipe_core"
CONFIGURE_XML = ROOT / "scripts/configure_gmti_xml.py"

METHOD_IDS = ("C0", "C1", "C2", "C3", "C4", "P1", "P2", "PK", "PKR")
COMPACT_EVIDENCE_FILES = (
    "manifest.json",
    "method_contract.csv",
    "gamma_recovery.csv",
    "clutter_metrics.csv",
    "decision_matrix.csv",
    "report.md",
)
ALLOWED_DECISION_LABELS = {
    "GO_EQUIVALENT_CALIBRATION_ONLY",
    "GO_PHYSICAL_CALIBRATION_ONLY",
    "GO_HIERARCHICAL_CALIBRATION",
    "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE",
}
DEFAULT_ADDITIVE_TOLERANCE = 5.0e-5
DEFAULT_COHERENCE_THRESHOLD = 0.0


def _json_safe(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_rows(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    materialized = [dict(row) for row in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in materialized:
        for key in row:
            if key not in fields:
                fields.append(str(key))
    with path.open("w", encoding="utf-8", newline="") as stream:
        if not fields:
            return
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in materialized:
            writer.writerow(_json_safe(row))


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_identity(path: Path) -> dict[str, object]:
    candidate = Path(path).resolve()
    if not candidate.is_file():
        return {"path": str(candidate), "exists": False, "sha256": None}
    return {
        "path": str(candidate),
        "exists": True,
        "bytes": candidate.stat().st_size,
        "sha256": _sha256(candidate),
    }


def _finite_float(value: object, name: str) -> float:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def load_pilot_config(path: Path = CONFIG) -> dict[str, object]:
    """Load and validate the frozen delay-pilot configuration."""

    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    candidate = candidate.resolve()
    if not candidate.is_file():
        raise FileNotFoundError(f"pilot config does not exist: {candidate}")
    config = _read_json(candidate)
    if config.get("stage") != "Production Hierarchical Calibration Pilot":
        raise ValueError("pilot config stage is not frozen to the production hierarchical pilot")
    for key in ("ai_training", "router_enabled", "native_4ch_stap"):
        if config.get(key) is not False:
            raise ValueError(f"{key} must be false for this pilot")
    methods = config.get("methods")
    if not isinstance(methods, list):
        raise ValueError("pilot config methods must be a list")
    ids = tuple(str(item.get("method_id")) for item in methods if isinstance(item, Mapping))
    if ids != METHOD_IDS:
        raise ValueError(f"pilot method order must be {METHOD_IDS}, got {ids}")
    allowlist = config.get("evidence_allowlist")
    if tuple(allowlist or ()) != COMPACT_EVIDENCE_FILES:
        raise ValueError("pilot compact evidence allow-list is not frozen")
    labels = config.get("decision_labels")
    if set(labels or ()) != ALLOWED_DECISION_LABELS:
        raise ValueError("pilot decision labels are not the four allowed labels")
    return config


def build_method_contract(config: Mapping[str, object] | None = None) -> list[dict[str, object]]:
    """Return the immutable C0--PKR method contract."""

    source = config if config is not None else load_pilot_config()
    methods = source.get("methods")
    if not isinstance(methods, list):
        raise ValueError("methods must be a list")
    result: list[dict[str, object]] = []
    for expected, item in zip(METHOD_IDS, methods):
        if not isinstance(item, Mapping) or str(item.get("method_id")) != expected:
            raise ValueError(f"method contract is missing {expected}")
        copied = copy.deepcopy(dict(item))
        copied.setdefault("truth_used_in_estimator", False)
        if copied["truth_used_in_estimator"] is not False:
            raise ValueError(f"{expected} truth leakage is declared in method contract")
        result.append(copied)
    if len(result) != len(METHOD_IDS):
        raise ValueError("method contract must contain exactly nine methods")
    return result


def build_paired_input_contract() -> dict[str, object]:
    """Return the OFF/ON/TO and Mode-A/Mode-B information boundary."""

    return {
        "roles": {"OFF": "C+N", "ON": "S+C+N", "TO": "S"},
        "shared_identity_fields": [
            "seed",
            "background_id",
            "target_id",
            "delay_error_ns",
            "production_identity",
        ],
        "identity_rule": "same seed/background/target/production settings",
        "causal_target_rule": "ON-OFF with TO=S as the independent target control",
        "modes": {
            "Mode-A": {
                "estimator_input_roles": ["OFF"],
                "apply_input_roles": ["ON"],
                "description": "estimate from target-free OFF=C+N and apply to ON=S+C+N",
                "truth_used_in_estimator": False,
                "target_position_allowed": False,
                "injected_error_label_allowed": False,
                "known_error_allowed": False,
            },
            "Mode-B": {
                "estimator_input_roles": ["ON"],
                "apply_input_roles": ["ON"],
                "description": "blind online ON=S+C+N estimate; no target truth or error label",
                "truth_used_in_estimator": False,
                "target_position_allowed": False,
                "injected_error_label_allowed": False,
                "known_error_allowed": False,
            },
        },
    }


def _unique_values(values: object, name: str, *, positive: bool = False) -> list[float]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ValueError(f"{name} must be a list")
    result: list[float] = []
    for item in values:
        value = _finite_float(item, name)
        if positive and value <= 0.0:
            raise ValueError(f"{name} must contain positive values")
        if value not in result:
            result.append(value)
    if not result:
        raise ValueError(f"{name} must not be empty")
    return result


def _unique_ints(values: object, name: str) -> list[int]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ValueError(f"{name} must be a list")
    result: list[int] = []
    for item in values:
        try:
            value = int(item)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} contains an invalid seed") from exc
        if value not in result:
            result.append(value)
    if not result:
        raise ValueError(f"{name} must not be empty")
    return result


def _case_matches_overrides(
    case: Mapping[str, object], overrides: Mapping[str, object] | None,
) -> bool:
    if not overrides:
        return True
    checks = {
        "delay_errors_ns": float(case["delay_error_ns"]),
        "seeds": int(case["seed"]),
        "target_velocities_mps": float(case["target_velocity_mps"]),
        "target_snr_db": float(case["target_snr_db"]),
    }
    for key, actual in checks.items():
        if key not in overrides or overrides[key] is None:
            continue
        values = overrides[key]
        if isinstance(values, (str, int, float)):
            values = [values]
        if actual not in {float(item) for item in values}:  # type: ignore[union-attr]
            return False
    return True


def resolve_selection(
    config: Mapping[str, object],
    mode: str,
    overrides: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Resolve explicit pilot/formal groups without manufacturing a matrix."""

    if mode not in {"pilot", "formal"}:
        raise ValueError("mode must be pilot or formal")
    section = config.get(mode)
    if not isinstance(section, Mapping):
        raise ValueError(f"config is missing {mode} selection")
    selection_mode = str(section.get("selection_mode", ""))
    if not selection_mode or selection_mode == "cartesian":
        raise ValueError("selection must be an explicit targeted selection, not Cartesian")
    delays = _unique_values(section.get("delay_errors_ns"), f"{mode}.delay_errors_ns")
    seeds = _unique_ints(section.get("seeds"), f"{mode}.seeds")
    velocities = _unique_values(
        section.get("target_velocities_mps"), f"{mode}.target_velocities_mps", positive=True
    )
    snrs = _unique_values(section.get("target_snr_db"), f"{mode}.target_snr_db")
    period_count = int(section.get("period_count", 0))
    if period_count <= 0:
        raise ValueError(f"{mode}.period_count must be positive")
    groups = section.get("case_groups")
    if not isinstance(groups, list) or not groups:
        raise ValueError(f"{mode}.case_groups must be a non-empty list")
    cases: list[dict[str, object]] = []
    for group in groups:
        if not isinstance(group, Mapping):
            raise ValueError(f"{mode}.case_groups contains a non-object")
        group_name = str(group.get("name", "")).strip()
        group_cases = group.get("cases")
        if not isinstance(group_cases, list) or not group_cases:
            raise ValueError(f"{mode} group {group_name!r} must have explicit cases")
        for raw_case in group_cases:
            if not isinstance(raw_case, Mapping):
                raise ValueError(f"{mode} group {group_name!r} contains a non-object case")
            case = {
                "group": group_name,
                "delay_error_ns": _finite_float(raw_case.get("delay_error_ns"), "delay_error_ns"),
                "seed": int(raw_case.get("seed")),
                "target_velocity_mps": _finite_float(
                    raw_case.get("target_velocity_mps"), "target_velocity_mps"
                ),
                "target_snr_db": _finite_float(raw_case.get("target_snr_db"), "target_snr_db"),
                "working_point": str(raw_case.get("working_point", "base")),
            }
            if case["target_velocity_mps"] <= 0.0:
                raise ValueError("target_velocity_mps must be positive")
            if case["delay_error_ns"] not in delays:
                raise ValueError(f"case delay is not in {mode}.delay_errors_ns")
            if case["seed"] not in seeds:
                raise ValueError(f"case seed is not in {mode}.seeds")
            if case["target_velocity_mps"] not in velocities:
                raise ValueError(f"case velocity is not in {mode}.target_velocities_mps")
            if case["target_snr_db"] not in snrs:
                raise ValueError(f"case SNR is not in {mode}.target_snr_db")
            case["period_count"] = period_count
            if _case_matches_overrides(case, overrides):
                cases.append(case)
    if not cases:
        raise ValueError("selection overrides removed every explicit case")
    configured_product = len(delays) * len(seeds) * len(velocities) * len(snrs)
    return {
        "mode": mode,
        "selection_mode": selection_mode,
        "cartesian_full_matrix": False,
        "delay_errors_ns": delays,
        "seeds": seeds,
        "target_velocities_mps": velocities,
        "target_snr_db": snrs,
        "working_points": list(section.get("working_points", [])),
        "period_count": period_count,
        "cases": cases,
        "case_count": len(cases),
        "configured_cartesian_case_count": configured_product,
        "overrides": dict(overrides or {}),
    }


def evaluate_support(
    *,
    support_count: object,
    min_support: object,
    denominator: object,
    phase_coherence: object,
    coherence_threshold: float = DEFAULT_COHERENCE_THRESHOLD,
) -> dict[str, object]:
    """Apply the fail-closed support contract used by compact summaries."""

    try:
        support = int(support_count)
        minimum = int(min_support)
        denom = float(denominator)
        coherence = float(phase_coherence)
    except (TypeError, ValueError):
        support, minimum, denom, coherence = -1, 1, math.nan, math.nan
    reason = None
    if support < 0 or minimum <= 0:
        reason = "support_metadata_invalid"
    elif support < minimum:
        reason = "support_below_minimum"
    elif not math.isfinite(denom) or denom <= 0.0:
        reason = "support_denominator_invalid"
    elif not math.isfinite(coherence) or coherence < float(coherence_threshold):
        reason = "phase_coherence_below_threshold"
    if reason is not None:
        return {
            "status": "NOT_EVALUABLE",
            "support_count": max(0, support),
            "min_support": minimum,
            "denominator": denom if math.isfinite(denom) else None,
            "phase_coherence": coherence if math.isfinite(coherence) else None,
            "reason": reason,
            "fallback_method": None,
            "fallback_to_current": False,
        }
    return {
        "status": "evaluable",
        "support_count": support,
        "min_support": minimum,
        "denominator": denom,
        "phase_coherence": coherence,
        "reason": None,
        "fallback_method": None,
        "fallback_to_current": False,
    }


def validate_compact_evidence_root(root: Path) -> dict[str, object]:
    """Require exactly the six tracked evidence files and nothing else."""

    candidate = Path(root)
    if not candidate.is_dir():
        raise ValueError(f"compact evidence root does not exist: {candidate}")
    names = sorted(path.name for path in candidate.iterdir())
    expected = sorted(COMPACT_EVIDENCE_FILES)
    if names != expected:
        raise ValueError(
            f"compact evidence allow-list violation: actual={names}, expected={expected}"
        )
    return {"status": "passed", "files": list(COMPACT_EVIDENCE_FILES)}


def source_snapshot() -> dict[str, object]:
    """Record tracked source identity without enumerating user output roots."""

    commit_proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    status_proc = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    commit = commit_proc.stdout.strip() or None
    tracked_status = status_proc.stdout.strip()
    return {
        "commit": commit,
        "dirty": bool(tracked_status),
        "dirty_tracked": bool(tracked_status),
        "tracked_status": tracked_status,
        "scope": "tracked_source_only; existing untracked output roots are not scanned",
    }


def require_clean_tracked_source(snapshot: Mapping[str, object], *, mode: str) -> dict[str, object]:
    if mode == "formal" and bool(snapshot.get("dirty_tracked", snapshot.get("dirty", False))):
        raise RuntimeError("tracked source is dirty; formal pilot refuses to run")
    return {"status": "passed", "mode": mode, "commit": snapshot.get("commit")}


def _run_logged(
    command: Sequence[object],
    log_path: Path,
    *,
    timeout_s: float = 1800.0,
    env: Mapping[str, str] | None = None,
) -> tuple[int, float]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    returncode = 127
    with log_path.open("w", encoding="utf-8") as stream:
        stream.write(f"$ {shlex.join(str(item) for item in command)}\n")
        stream.flush()
        try:
            completed = subprocess.run(
                [str(item) for item in command],
                cwd=ROOT,
                stdout=stream,
                stderr=subprocess.STDOUT,
                env=dict(env) if env is not None else None,
                timeout=float(timeout_s),
                check=False,
            )
            returncode = int(completed.returncode)
        except FileNotFoundError as exc:
            stream.write(f"\n[file_not_found] {exc}\n")
        except subprocess.TimeoutExpired:
            returncode = 124
            stream.write("\n[timeout_expired]=true\n")
        elapsed = time.monotonic() - start
        stream.write(f"\n[exit_code]={returncode}\n[elapsed_sec]={elapsed:.3f}\n")
    return returncode, time.monotonic() - start


def _probe_nvidia_smi() -> dict[str, object]:
    try:
        result = subprocess.run(
            ["nvidia-smi"], capture_output=True, text=True, timeout=15.0, check=False
        )
        return {
            "command": "nvidia-smi",
            "returncode": int(result.returncode),
            "available": result.returncode == 0,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "command": "nvidia-smi",
            "returncode": 127,
            "available": False,
            "stdout": "",
            "stderr": str(exc),
        }


def _disk_status(path: Path) -> dict[str, object]:
    usage = shutil.disk_usage(Path(path).resolve().parent)
    return {
        "path": str(Path(path).resolve().parent),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
    }


def _build_identity() -> dict[str, object]:
    return {
        "simulate_stage2_statistical": _artifact_identity(SIMULATOR),
        "GMTI_pipe_core": _artifact_identity(PIPE),
    }


def _scene_template(config: Mapping[str, object]) -> dict[str, object]:
    raw = config.get("scenario_template")
    path = Path(str(raw)) if isinstance(raw, str) else TEMPLATE
    if not path.is_absolute():
        path = ROOT / path
    return _read_json(path.resolve())


def _point_registry(config: Mapping[str, object]) -> dict[str, dict[str, object]]:
    registry: dict[str, dict[str, object]] = {}
    for mode in ("pilot", "formal"):
        section = config.get(mode)
        if not isinstance(section, Mapping):
            continue
        for group in section.get("case_groups", []):
            if not isinstance(group, Mapping):
                continue
            for case in group.get("cases", []):
                if isinstance(case, Mapping):
                    name = str(case.get("working_point", "base"))
                    registry.setdefault(name, {
                        "name": name,
                        "beam_id": 50,
                        "expected_bin": 2048,
                        "range_m": 85000.0,
                        "texture_sigma": 0.25,
                        "clutter_rho": 0.98,
                    })
    return registry


def _scene_identity(config: Mapping[str, object], case: Mapping[str, object], period_count: int) -> dict[str, object]:
    points = _point_registry(config)
    point = copy.deepcopy(points.get(str(case["working_point"]), points.get("base", {})))
    return {
        "seed": int(case["seed"]),
        "period_count": int(period_count),
        "beam_id": int(point.get("beam_id", 50)),
        "expected_bin": int(point.get("expected_bin", 2048)),
        "range_m": float(point.get("range_m", 85000.0)),
        "texture_sigma": float(point.get("texture_sigma", 0.25)),
        "clutter_rho": float(point.get("clutter_rho", 0.98)),
        "target_velocity_mps": float(case["target_velocity_mps"]),
        "target_snr_db": float(case["target_snr_db"]),
        "target": {"target_id": "PGRCC_DELAY_PILOT_TARGET", "enabled": True},
        "clutter": {"mean_power": 0.02},
        "noise": {"noise_power": 0.001},
        "production": {"identity": "stage2_production_delay_pilot_v1"},
    }


def _missing_branches(reason: str, method_contract: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for method in method_contract:
        for role in ("OFF", "ON", "TO"):
            rows.append({
                "method_id": method["method_id"],
                "role": role,
                "status": "NOT_EVALUABLE",
                "reason": reason,
                "truth_used_in_estimator": False,
                "fallback_to_current": False,
            })
    return rows


def _prepare_raw_input_record(
    role_paths: Mapping[str, Sequence[Path]],
    *,
    correction: str,
    correction_source: str,
    delay_truth_ns: float,
) -> dict[str, object]:
    return {
        "condition": "A1_Current_unknown_error",
        "correction": correction,
        "correction_source": correction_source,
        "delay_truth_ns": float(delay_truth_ns),
        "delay_estimate_ns": None,
        "correction_applied": False,
        "truth_used_in_estimator": False,
        "truth_used_to_apply_correction": False,
        "period_paths_by_role": {role: list(paths) for role, paths in role_paths.items()},
        "status": "evaluable",
    }


def _collect_branch_artifacts(record: Mapping[str, object]) -> dict[str, object]:
    result_dir = Path(str(record.get("result_dir", ""))) if record.get("result_dir") else None
    debug_dir = Path(str(record.get("track_debug_dir", ""))) if record.get("track_debug_dir") else None
    patterns = {
        "adapter_csv": "production_calibration_adapter.csv",
        "cfar_geometry": "*cfar*geometry*.csv",
        "detection": "detection_results_GMTI*.csv",
        "track_association": "track_association_audit_v2.csv",
        "track_states": "track_states.csv",
        "track_payloads": "track_output_payloads.csv",
    }
    paths: dict[str, list[str]] = {key: [] for key in patterns}
    for key, pattern in patterns.items():
        roots = [path for path in (result_dir, debug_dir) if path is not None and path.is_dir()]
        for root in roots:
            for candidate in root.rglob(pattern):
                if candidate.is_file():
                    paths[key].append(str(candidate.resolve()))
        paths[key] = sorted(set(paths[key]))
    return paths


def _adapter_rows(paths: Sequence[str], method_id: str, role: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    copied: dict[str, object] = dict(row)
                    copied.update({"method_id": method_id, "role": role, "source_path": str(path)})
                    rows.append(copied)
        except OSError:
            continue
    return rows


def _method_xml(
    case_root: Path,
    source_xml: Path,
    method: Mapping[str, object],
    production: Mapping[str, object],
) -> dict[str, object]:
    method_id = str(method["method_id"])
    destination = case_root / "method_xml" / f"{method_id}.xml"
    enable = bool(method["research_calibration_enable"])
    method_name = str(method["research_calibration_method"])
    band = int(production.get("range_band_bins", 6)) if method_name == "robust_ddc_rb" else 0
    overrides = {
        "research_calibration_enable": "true" if enable else "false",
        "research_calibration_method": method_name,
        "research_calibration_min_support": int(production.get("min_support", 8)),
        "research_calibration_range_band_bins": band,
        "research_calibration_robust_phase_threshold_rad": float(
            production.get("robust_phase_threshold_rad", 0.35)
        ),
    }
    command: list[object] = [
        sys.executable,
        CONFIGURE_XML,
        "--input",
        source_xml,
        "--output",
        destination,
    ]
    for key, value in overrides.items():
        command.extend(["--set", f"{key}={value}"])
    rc, elapsed = _run_logged(command, destination.with_suffix(".configure.log"), timeout_s=60.0)
    return {
        "method_id": method_id,
        "path": str(destination),
        "sha256": _sha256(destination) if destination.is_file() else None,
        "overrides": overrides,
        "returncode": rc,
        "elapsed_sec": elapsed,
        "status": "passed" if rc == 0 else "failed",
        "log": str(destination.with_suffix(".configure.log")),
    }


def _materialize_input_dir(root: Path, paths: Sequence[Path]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(paths):
        destination = root / f"period_{index:04d}.bin"
        if destination.exists() or destination.is_symlink():
            raise RuntimeError(f"refuse to overwrite production input link: {destination}")
        destination.symlink_to(Path(source).resolve())
    return root


def _case_manifest_base(config: Mapping[str, object], case: Mapping[str, object], output_root: Path) -> dict[str, object]:
    case_id = (
        f"{case.get('working_point', 'base')}"
        f"__seed_{case.get('seed')}"
        f"__v_{case.get('target_velocity_mps')}"
        f"__snr_{case.get('target_snr_db')}"
        f"__delay_{case.get('delay_error_ns')}ns"
    )
    return {
        "schema_version": 1,
        "stage": config["stage"],
        "case": dict(case),
        "case_id": case_id,
        "output_root": str(output_root),
        "input_contract": build_paired_input_contract(),
        "truth_used_in_estimator": False,
        "algorithmic_results_claimed": False,
        "status": "not_started",
    }


def _run_case(
    config: Mapping[str, object],
    case: Mapping[str, object],
    output_root: Path,
    method_contract: Sequence[Mapping[str, object]],
    *,
    input_mode: str,
    skip_cuda: bool,
) -> dict[str, object]:
    """Run one explicit case; all raw outputs stay below ``output_root``."""

    from scripts import run_delay_stage1_formal as delay_runner
    from scripts import run_track_manager_e2e as e2e

    case_name = (
        f"{str(case['working_point'])}__seed_{int(case['seed'])}"
        f"__v_{float(case['target_velocity_mps']):g}"
        f"__snr_{float(case['target_snr_db']):g}"
        f"__delay_{float(case['delay_error_ns']):g}ns"
    ).replace("-", "m").replace(".", "d")
    case_root = output_root / "cases" / case_name
    case_root.mkdir(parents=True, exist_ok=False)
    manifest = _case_manifest_base(config, case, case_root)
    production_config = config.get("production")
    if not isinstance(production_config, Mapping):
        raise ValueError("pilot production section must be an object")
    period_count = int(case.get("period_count", production_config.get("period_count", 5)))
    manifest["scene_identity"] = _scene_identity(config, case, period_count)
    manifest["pair_identity"] = {
        "seed": int(case["seed"]),
        "background_id": str((case_root / "scenes" / "A0_background").resolve()),
        "target_id": "PGRCC_DELAY_PILOT_TARGET",
        "delay_error_ns": float(case["delay_error_ns"]),
        "production_identity": "stage2_production_delay_pilot_v1",
    }
    if skip_cuda:
        manifest.update({
            "status": "skip_cuda",
            "reason": "contract_only_no_simulator_or_production_execution",
            "algorithmic_results_claimed": False,
            "method_branches": _missing_branches("skip_cuda_contract_only", method_contract),
        })
        _write_json(case_root / "case_manifest.json", manifest)
        return manifest

    scenario_base = _scene_template(config)
    scene_variants = delay_runner.build_scene_variants(
        scenario_base,
        manifest["scene_identity"],
        float(case["delay_error_ns"]),
        case_root / "scenes",
    )
    manifest["scene_variants"] = {key: str(path) for key, path in scene_variants.items()}
    simulation: dict[str, dict[str, object]] = {}
    simulation_order = ["A0_ON", "A0_OFF", "A0_TO", "A1_ON", "A1_OFF", "A1_TO"]
    for key in simulation_order:
        scenario_path = scene_variants[key]
        output_dir = delay_runner._scenario_output_dir(scenario_path)
        rc, elapsed = _run_logged(
            [SIMULATOR, "--config", scenario_path],
            case_root / "scenes" / "logs" / f"{key}.simulate.log",
            timeout_s=1800.0,
        )
        simulation[key] = {
            "scenario": str(scenario_path),
            "scenario_sha256": _sha256(scenario_path),
            "output_dir": str(output_dir),
            "returncode": rc,
            "elapsed_sec": elapsed,
            "status": "passed" if rc == 0 else "failed",
            "command": [str(SIMULATOR), "--config", str(scenario_path)],
        }
    manifest["simulation"] = simulation
    if not all(item["status"] == "passed" for item in simulation.values()):
        manifest.update({
            "status": "completed_with_gaps",
            "reason": "stage2_simulation_failed",
            "method_branches": _missing_branches("stage2_simulation_failed", method_contract),
        })
        _write_json(case_root / "case_manifest.json", manifest)
        return manifest

    scenario = _read_json(scene_variants["A0_ON"])
    layout = e2e._protocol_layout_from_scenario(scenario)
    manifest["protocol_layout"] = layout
    scene_periods: dict[str, list[Path]] = {}
    for key in simulation_order:
        output_dir = Path(str(simulation[key]["output_dir"]))
        scene_periods[key] = delay_runner._period_paths_from_output(output_dir, period_count)
    raw_by_role = {
        "OFF": scene_periods["A1_OFF"],
        "ON": scene_periods["A1_ON"],
        "TO": scene_periods["A1_TO"],
    }
    calibration_input = delay_runner._merge_binary_files(
        raw_by_role["OFF"], case_root / "inputs" / "target_free_OFF_C_plus_N.bin"
    )
    manifest["inputs"] = {
        role: [
            {"path": str(path), "bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in paths
        ]
        for role, paths in raw_by_role.items()
    }
    manifest["calibration_input"] = {
        "path": str(calibration_input),
        "bytes": calibration_input.stat().st_size,
        "sha256": _sha256(calibration_input),
        "role": "OFF=C+N",
        "truth_used_in_estimator": False,
    }
    source_xml = Path(str(simulation["A0_ON"]["output_dir"])) / "config" / "temp_config_stage2_period_0000.xml"
    truth_root = Path(str(simulation["A0_ON"]["output_dir"])) / "truth"
    truth = e2e._truth_by_period(Path(str(simulation["A0_ON"]["output_dir"])), period_count)
    manifest["source_xml"] = str(source_xml)
    manifest["source_xml_sha256"] = _sha256(source_xml) if source_xml.is_file() else None

    try:
        estimator = delay_runner._estimate_delay_from_calibration(calibration_input, layout)
        estimator["input_role"] = "OFF=C+N"
        estimator["truth_used_in_estimator"] = False
    except (OSError, ValueError, RuntimeError, SystemExit) as exc:
        estimator = {
            "status": "NOT_EVALUABLE",
            "input_role": "OFF=C+N",
            "truth_used_in_estimator": False,
            "reason": str(exc),
        }
    manifest["delay_estimator"] = estimator
    estimated_delay = estimator.get("delta_tau_ns") if estimator.get("status") == "estimated" else None
    delay_truth = float(case["delay_error_ns"])

    prepared: dict[str, dict[str, object]] = {
        "none": _prepare_raw_input_record(
            raw_by_role,
            correction="none",
            correction_source="none",
            delay_truth_ns=delay_truth,
        )
    }
    if estimated_delay is not None and math.isfinite(float(estimated_delay)):
        blind: dict[str, dict[str, object]] = {}
        for role, paths in raw_by_role.items():
            blind[role] = delay_runner.prepare_condition_inputs(
                case_root / "inputs" / "blind" / role,
                "A3_Blind_target_free_estimated_correction",
                paths,
                calibration_input,
                delay_truth,
                float(estimated_delay),
                layout,
            )
        prepared["blind"] = {
            "condition": "A3_Blind_target_free_estimated_correction",
            "correction": "raw_channel_2_fractional_delay",
            "correction_source": "target_free_OFF_phase_vs_fast_frequency",
            "delay_truth_ns": delay_truth,
            "delay_estimate_ns": float(estimated_delay),
            "correction_applied": True,
            "truth_used_in_estimator": False,
            "truth_used_to_apply_correction": False,
            "period_paths_by_role": {role: list(item["period_paths"]) for role, item in blind.items()},
            "correction_audit_by_role": {role: item.get("correction_audit", []) for role, item in blind.items()},
            "status": "evaluable",
        }
    else:
        prepared["blind"] = {
            "condition": "A3_Blind_target_free_estimated_correction",
            "correction": "raw_channel_2_fractional_delay",
            "correction_source": "target_free_OFF_phase_vs_fast_frequency",
            "delay_truth_ns": delay_truth,
            "delay_estimate_ns": None,
            "correction_applied": False,
            "truth_used_in_estimator": False,
            "truth_used_to_apply_correction": False,
            "period_paths_by_role": {role: [] for role in raw_by_role},
            "status": "NOT_EVALUABLE",
            "reason": "target_free_OFF_delay_estimate_missing; refusing Current fallback",
        }
    known: dict[str, dict[str, object]] = {}
    for role, paths in raw_by_role.items():
        known[role] = delay_runner.prepare_condition_inputs(
            case_root / "inputs" / "known" / role,
            "A2_Known_error_correction_upper_bound",
            paths,
            calibration_input,
            delay_truth,
            None,
            layout,
        )
    prepared["known"] = {
        "condition": "A2_Known_error_correction_upper_bound",
        "correction": "raw_channel_2_fractional_delay",
        "correction_source": "truth_evaluation_only",
        "delay_truth_ns": delay_truth,
        "delay_estimate_ns": delay_truth,
        "correction_applied": True,
        "truth_used_in_estimator": False,
        "truth_used_to_apply_correction": True,
        "known_error_use": "evaluator_only",
        "period_paths_by_role": {role: list(item["period_paths"]) for role, item in known.items()},
        "correction_audit_by_role": {role: item.get("correction_audit", []) for role, item in known.items()},
        "status": "evaluable",
    }
    manifest["prepared_inputs"] = {
        kind: {
            key: value if key not in {"period_paths_by_role"} else {
                role: [str(path) for path in paths]
                for role, paths in value.items()
            }
            for key, value in record.items()
            if key != "period_paths_by_role" or isinstance(value, Mapping)
        }
        for kind, record in prepared.items()
    }

    # The paired causal identity is checked on every input family.  The
    # evaluator is read-only and does not enter any estimator call.
    manifest["causal_triplets"] = {}
    for kind, record in prepared.items():
        paths_by_role = record.get("period_paths_by_role")
        if not isinstance(paths_by_role, Mapping) or not all(paths_by_role.get(role) for role in ("OFF", "ON", "TO")):
            manifest["causal_triplets"][kind] = {
                "status": "NOT_EVALUABLE",
                "reason": record.get("reason", "missing_OFF_ON_TO_inputs"),
            }
            continue
        try:
            import numpy as np

            arrays = {
                role: delay_runner._load_channels_from_periods(paths_by_role[role], layout)
                for role in ("OFF", "ON", "TO")
            }
            manifest["causal_triplets"][kind] = delay_runner.audit_additive_triplet(
                arrays["ON"], arrays["OFF"], arrays["TO"], DEFAULT_ADDITIVE_TOLERANCE
            )
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            manifest["causal_triplets"][kind] = {
                "status": "NOT_EVALUABLE",
                "reason": f"causal_triplet_load_failed:{exc}",
            }

    production = production_config
    xml_records: dict[str, dict[str, object]] = {}
    for method in method_contract:
        xml_records[str(method["method_id"])] = _method_xml(
            case_root, source_xml, method, production
        )
    manifest["method_xml"] = xml_records

    method_branches: list[dict[str, object]] = []
    for method in method_contract:
        method_id = str(method["method_id"])
        if method_id in {"P1", "P2"}:
            family = "blind"
        elif method_id in {"PK", "PKR"}:
            family = "known"
        else:
            family = "none"
        prepared_family = prepared[family]
        for role in ("OFF", "ON", "TO"):
            base_record: dict[str, object] = {
                "method_id": method_id,
                "role": role,
                "method_family": method["family"],
                "research_calibration_enable": method["research_calibration_enable"],
                "research_calibration_method": method["research_calibration_method"],
                "input_correction": method["input_correction"],
                "known_error": method["known_error"],
                "known_error_use": method["known_error_use"],
                "truth_used_in_estimator": False,
                "estimator_input_roles": method["estimator_input_roles"],
                "mode_contract": build_paired_input_contract()["modes"],
                "fallback_to_current": False,
                "input_identity": manifest["pair_identity"],
            }
            if prepared_family.get("status") != "evaluable":
                base_record.update({
                    "status": "NOT_EVALUABLE",
                    "reason": prepared_family.get("reason", "input_preparation_failed"),
                })
                method_branches.append(base_record)
                continue
            paths_by_role = prepared_family.get("period_paths_by_role")
            if not isinstance(paths_by_role, Mapping) or not isinstance(paths_by_role.get(role), Sequence):
                base_record.update({"status": "NOT_EVALUABLE", "reason": "missing_role_input"})
                method_branches.append(base_record)
                continue
            input_dir = _materialize_input_dir(
                case_root / "production_inputs" / method_id / role,
                [Path(path) for path in paths_by_role[role]],
            )
            branch_name = f"{method_id}_{role}"
            try:
                record = delay_runner.run_production_branch(
                    case_root,
                    branch_name,
                    input_dir,
                    Path(str(xml_records[method_id]["path"])),
                    truth,
                    period_count,
                    input_mode,
                    layout,
                    truth_path=truth_root,
                )
            except (OSError, RuntimeError, ValueError, KeyError) as exc:
                record = {
                    "status": "NOT_EVALUABLE",
                    "reason": f"production_branch_exception:{exc}",
                    "production_returncode": None,
                }
            base_record.update(record)
            base_record["input_dir"] = str(input_dir)
            base_record["input_paths"] = [str(path) for path in paths_by_role[role]]
            base_record["input_sha256"] = [_sha256(Path(path)) for path in paths_by_role[role]]
            base_record["truth_used_in_estimator"] = False
            base_record["artifacts"] = _collect_branch_artifacts(record)
            base_record["adapter_rows"] = _adapter_rows(
                base_record["artifacts"].get("adapter_csv", []), method_id, role  # type: ignore[union-attr]
            )
            method_branches.append(base_record)
    manifest["method_branches"] = method_branches
    causal_ok = all(
        isinstance(value, Mapping) and value.get("status") == "passed"
        for value in manifest.get("causal_triplets", {}).values()
    )
    denominator_ok = all(
        not isinstance(branch.get("target_off_waterfall"), Mapping)
        or (
            isinstance(branch["target_off_waterfall"].get("cell_false_hit_fraction"), Mapping)
            and branch["target_off_waterfall"]["cell_false_hit_fraction"].get("status") != "NOT_EVALUABLE"
        )
        for branch in method_branches
        if isinstance(branch, Mapping) and str(branch.get("role")) == "OFF"
    )
    manifest["evidence_gate"] = {
        "causal_triplets": "passed" if causal_ok else "NOT_EVALUABLE",
        "valid_cut_denominators": "passed" if denominator_ok else "NOT_EVALUABLE",
        "truth_used_in_estimator": False,
        "fallback_to_current": False,
    }
    manifest["status"] = (
        "completed"
        if all(str(item.get("status")) == "passed" for item in method_branches)
        and causal_ok
        and denominator_ok
        else "completed_with_gaps"
    )
    manifest["algorithmic_results_claimed"] = False
    _write_json(case_root / "case_manifest.json", manifest)
    return manifest


def _flatten_rows(cases: Sequence[Mapping[str, object]]) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    method_rows: list[dict[str, object]] = []
    gamma_rows: list[dict[str, object]] = []
    clutter_rows: list[dict[str, object]] = []
    for case in cases:
        case_values = case.get("case", {})
        case_id = str(case.get("case_id", case_values.get("group", ""))) if isinstance(case_values, Mapping) else str(case.get("case_id", ""))
        for branch in case.get("method_branches", []):
            if not isinstance(branch, Mapping):
                continue
            common = {
                "case": case_id,
                "method_id": branch.get("method_id"),
                "role": branch.get("role"),
                "status": branch.get("status"),
                "reason": branch.get("reason"),
                "truth_used_in_estimator": branch.get("truth_used_in_estimator", False),
                "fallback_to_current": branch.get("fallback_to_current", False),
            }
            method_rows.append(common)
            for adapter in branch.get("adapter_rows", []):
                if isinstance(adapter, Mapping):
                    gamma = dict(adapter)
                    gamma.update({"case": case_id, "method_id": branch.get("method_id"), "role": branch.get("role")})
                    gamma_rows.append(gamma)
            if str(branch.get("role")) == "OFF":
                waterfall = branch.get("target_off_waterfall")
                if isinstance(waterfall, Mapping):
                    clutter_rows.append({
                        "case": case_id,
                        "method_id": branch.get("method_id"),
                        "cell_false_hit_status": (waterfall.get("cell_false_hit_fraction") or {}).get("status") if isinstance(waterfall.get("cell_false_hit_fraction"), Mapping) else "NOT_EVALUABLE",
                        "cell_false_hit_fraction": (waterfall.get("cell_false_hit_fraction") or {}).get("value") if isinstance(waterfall.get("cell_false_hit_fraction"), Mapping) else None,
                        "hit_cut_count": (waterfall.get("cell_false_hit_fraction") or {}).get("hit_cell_count") if isinstance(waterfall.get("cell_false_hit_fraction"), Mapping) else None,
                        "valid_cut_count": (waterfall.get("cell_false_hit_fraction") or {}).get("valid_cut_count") if isinstance(waterfall.get("cell_false_hit_fraction"), Mapping) else None,
                        "cluster_layer": waterfall.get("false_clusters"),
                        "protocol_detection_layer": waterfall.get("protocol_false_detections"),
                        "track_layer": waterfall.get("false_tracks"),
                    })
    return method_rows, gamma_rows, clutter_rows


def _initial_manifest(
    config_path: Path,
    config: Mapping[str, object],
    selection: Mapping[str, object],
    source: Mapping[str, object],
    output_root: Path,
    args: argparse.Namespace,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "stage": config["stage"],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "not_started",
        "mode": selection["mode"],
        "skip_cuda": bool(args.skip_cuda),
        "algorithmic_results_claimed": False,
        "decision": "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE",
        "method_contract": build_method_contract(config),
        "input_contract": build_paired_input_contract(),
        "production_contract": config.get("production"),
        "selection": selection,
        "ai_training": False,
        "router_enabled": False,
        "native_4ch_stap": False,
        "source": dict(source),
        "config": {
            "path": str(config_path.resolve()),
            "sha256": _sha256(config_path),
        },
        "runner": {"path": str(Path(__file__).resolve()), "sha256": _sha256(Path(__file__))},
        "analyzer": {
            "path": str((ROOT / "scripts/analyze_production_hierarchical_calibration_pilot.py").resolve()),
            "sha256": _sha256(ROOT / "scripts/analyze_production_hierarchical_calibration_pilot.py")
            if (ROOT / "scripts/analyze_production_hierarchical_calibration_pilot.py").is_file()
            else None,
        },
        "platform": platform.platform(),
        "python": sys.version,
        "gpu_probe": _probe_nvidia_smi(),
        "disk": _disk_status(output_root),
        "build": _build_identity(),
        "command": [str(item) for item in args._command_args],
        "output_root": str(output_root),
        "external_commands": [],
        "cases": [],
    }


def _write_skip_compact_evidence(manifest_path: Path, evidence_root: Path) -> dict[str, object]:
    from scripts.analyze_production_hierarchical_calibration_pilot import build_compact_evidence

    outputs = build_compact_evidence(manifest_path, evidence_root)
    return {name: str(path) for name, path in outputs.items()}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--mode", choices=("pilot", "formal"), default=None)
    parser.add_argument("--pilot", action="store_true", help="select the targeted pilot group")
    parser.add_argument("--formal", action="store_true", help="select explicit formal targeted groups")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, default=None)
    parser.add_argument("--input-mode", choices=("local", "shm"), default=None)
    parser.add_argument("--skip-cuda", "--contract", dest="skip_cuda", action="store_true")
    parser.add_argument("--delay-errors-ns", action="append", default=None)
    parser.add_argument("--seeds", action="append", default=None)
    parser.add_argument("--target-velocities-mps", action="append", default=None)
    parser.add_argument("--target-snr-db", action="append", default=None)
    parser.add_argument("--max-cases", type=int, default=None)
    return parser


def _parse_override_values(raw: object, converter: type[int] | type[float]) -> list[object] | None:
    if raw is None:
        return None
    values = raw if isinstance(raw, list) else [raw]
    result: list[object] = []
    for item in values:
        for token in str(item).split(","):
            if token.strip():
                result.append(converter(token.strip()))
    return result


def run_cli(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    args._command_args = list(argv) if argv is not None else sys.argv[1:]
    if args.pilot and args.formal:
        parser.error("--pilot and --formal are mutually exclusive")
    mode = args.mode or ("formal" if args.formal else "pilot")
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    config_path = config_path.resolve()
    try:
        config = load_pilot_config(config_path)
        overrides = {
            "delay_errors_ns": _parse_override_values(args.delay_errors_ns, float),
            "seeds": _parse_override_values(args.seeds, int),
            "target_velocities_mps": _parse_override_values(args.target_velocities_mps, float),
            "target_snr_db": _parse_override_values(args.target_snr_db, float),
        }
        overrides = {key: value for key, value in overrides.items() if value is not None}
        selection = resolve_selection(config, mode, overrides)
        if args.max_cases is not None:
            if args.max_cases <= 0:
                raise ValueError("--max-cases must be positive")
            selection["cases"] = selection["cases"][: args.max_cases]
            selection["case_count"] = len(selection["cases"])
            selection["truncated_by_max_cases"] = True
        source = source_snapshot()
        require_clean_tracked_source(source, mode=mode)
        output_root = Path(args.output_root).resolve()
        if output_root.exists() and any(output_root.iterdir()):
            raise RuntimeError(f"refuse to overwrite non-empty output root: {output_root}")
        output_root.mkdir(parents=True, exist_ok=True)
        production = config.get("production")
        if not isinstance(production, Mapping):
            raise ValueError("pilot config production section is missing")
        input_mode = args.input_mode or str(production.get("input_mode", "local"))
        if input_mode not in {"local", "shm"}:
            raise ValueError("input mode must be local or shm")
        manifest = _initial_manifest(config_path, config, selection, source, output_root, args)
        manifest["input_mode"] = input_mode
        manifest_path = output_root / "manifest.json"
        _write_json(manifest_path, manifest)
        method_contract = build_method_contract(config)
        if args.skip_cuda:
            cases = [
                _run_case(
                    config,
                    case,
                    output_root,
                    method_contract,
                    input_mode=input_mode,
                    skip_cuda=True,
                )
                for case in selection["cases"]
            ]
            manifest["cases"] = cases
            manifest.update({
                "status": "skip_cuda",
                "algorithmic_results_claimed": False,
                "decision": "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE",
                "skip_reason": "no simulator/GMTI_pipe_core execution; contract only",
            })
        else:
            cases: list[dict[str, object]] = []
            for case in selection["cases"]:
                try:
                    result = _run_case(
                        config,
                        case,
                        output_root,
                        method_contract,
                        input_mode=input_mode,
                        skip_cuda=False,
                    )
                except (OSError, RuntimeError, ValueError, KeyError) as exc:
                    result = _case_manifest_base(config, case, output_root / "cases")
                    result.update({
                        "status": "NOT_EVALUABLE",
                        "reason": f"case_exception:{exc}",
                        "method_branches": _missing_branches(f"case_exception:{exc}", method_contract),
                    })
                cases.append(result)
                manifest["cases"] = cases
                _write_json(manifest_path, manifest)
            manifest["status"] = (
                "completed"
                if all(case.get("status") == "completed" for case in cases)
                else "completed_with_gaps"
            )
            manifest["algorithmic_results_claimed"] = False
            method_rows, gamma_rows, clutter_rows = _flatten_rows(cases)
            manifest["compact_rows"] = {
                "method_contract": method_rows,
                "gamma_recovery": gamma_rows,
                "clutter_metrics": clutter_rows,
            }
        _write_json(manifest_path, manifest)
        evidence_root = (
            Path(args.evidence_root).resolve()
            if args.evidence_root is not None
            else output_root / "compact_evidence"
        )
        manifest["compact_evidence_root"] = str(evidence_root)
        _write_json(manifest_path, manifest)
        manifest["compact_evidence"] = _write_skip_compact_evidence(manifest_path, evidence_root)
        _write_json(manifest_path, manifest)
        return 0
    except (FileNotFoundError, RuntimeError, ValueError, OSError) as exc:
        print(f"[production_hierarchical_calibration_pilot][ERROR] {exc}", file=sys.stderr)
        return 2


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
