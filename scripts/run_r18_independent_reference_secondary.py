#!/usr/bin/env python3
"""Run the pre-registered R18 seed-909 independent-reference secondary.

The reference scene is generated as target-free area clutter plus thermal
noise. Its Gamma files are then applied to the existing seed-606 evaluation
case through the normal CUDA Production TrackManager path. Reproducible raw
arrays stay in a disposable scratch root; compact audit evidence is archived
before scratch data is removed.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_PRIMARY_MANIFEST = ROOT / ".scratch/r18_formal_seed606_v6p7_snr30/manifest.json"
DEFAULT_CONFIG = ROOT / "configs/research/phase_r18_native_cuda.json"
DEFAULT_SCRATCH_ROOT = ROOT / ".scratch/r18_independent_reference_seed909_20261025"
DEFAULT_EVIDENCE_ROOT = (
    ROOT / "docs/research/phase_r/R18/evidence/independent_seed909_secondary"
)
METHOD_IDS = ("C2", "C3")
ROLES = ("OFF", "ON", "TO")
RAW_ARRAY_SUFFIXES = {".bin", ".npy", ".npz"}
AREA_RELATIVE_TOLERANCES = {
    "area_mean_power_measured": 0.10,
    "area_power_cv_measured": 0.15,
    "mean_noise_power_per_complex_sample": 0.05,
}
AREA_ABSOLUTE_TOLERANCES = {
    "area_rho_lag1_measured": 0.02,
    "area_rho_at_CTDR_lag": 0.02,
}
APPLICATION_GAMMA_ABS_TOLERANCE = 1.0e-5


def build_target_free_reference_scenario(
    source: Mapping[str, Any],
    *,
    output_dir: Path,
    seed: int = 909,
) -> dict[str, Any]:
    """Clone the frozen case physics while removing targets and paired inputs."""

    scenario = copy.deepcopy(dict(source))
    random = scenario.get("random")
    scene = scenario.get("scene")
    if not isinstance(random, dict) or not isinstance(scene, dict):
        raise ValueError("reference scenario requires random and scene objects")
    area = scene.get("area_clutter")
    if (
        not isinstance(area, Mapping)
        or area.get("enabled") is not True
        or area.get("model") != "continuous_texture"
    ):
        raise ValueError("reference scenario requires continuous area clutter")
    random["random_seed"] = int(seed)
    scenario["output_dir"] = str(Path(output_dir).resolve())
    scenario["scene_mode"] = "area_clutter_only"
    scenario["truth_output"] = False
    scene["mode"] = "area_clutter_only"
    for scatterer_name in ("strong_scatterers", "line_scatterers"):
        scatterers = scene.get(scatterer_name)
        if isinstance(scatterers, dict):
            scatterers["enabled"] = False
    targets = scenario.get("targets", [])
    if not isinstance(targets, list):
        raise ValueError("reference scenario targets must be an array")
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError("reference scenario target entries must be objects")
        target["enabled"] = False
    for key in list(scenario):
        if (
            key.startswith("stage1_")
            or key in {
                "pairing_provenance",
                "background_input_dir",
                "paired_background_output_dir",
                "truth_input_dir",
                "truth_path",
            }
        ):
            del scenario[key]
    return scenario


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _project_allocated_bytes(path: Path) -> int:
    total = 0
    for directory, dirnames, filenames in os.walk(path, followlinks=False):
        dirnames[:] = [name for name in dirnames if not (Path(directory) / name).is_symlink()]
        for filename in filenames:
            candidate = Path(directory) / filename
            if candidate.is_symlink():
                continue
            try:
                total += candidate.stat().st_blocks * 512
            except FileNotFoundError:
                continue
    return total


def _file_identity(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"required executable is missing: {resolved}")
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _run_logged(
    command: Sequence[str],
    log_path: Path,
    *,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            list(command),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        output = result.stdout + ("\n" if result.stdout and result.stderr else "") + result.stderr
        log_path.write_text(output, encoding="utf-8", errors="replace")
        return {
            "command": list(command),
            "returncode": int(result.returncode),
            "log": str(log_path.resolve()),
            "log_sha256": _sha256(log_path),
            "status": "passed" if result.returncode == 0 else "failed",
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        log_path.write_text(stdout + "\n" + stderr + "\nTIMEOUT\n", encoding="utf-8")
        return {
            "command": list(command),
            "returncode": None,
            "log": str(log_path.resolve()),
            "log_sha256": _sha256(log_path),
            "status": "failed",
            "reason": f"timeout_after_{timeout_seconds}_seconds",
        }


def _find_primary_case(manifest: Mapping[str, Any]) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for case in manifest.get("cases", []):
        if not isinstance(case, dict):
            continue
        identity = case.get("case", {})
        if not isinstance(identity, Mapping):
            continue
        try:
            matches_case = (
                int(identity.get("seed", -1)) == 606
                and math.isclose(float(identity.get("target_velocity_mps")), 6.7)
                and math.isclose(float(identity.get("target_snr_db")), 30.0)
                and math.isclose(float(identity.get("delay_error_ns")), 0.0)
            )
        except (TypeError, ValueError):
            matches_case = False
        if matches_case:
            matches.append(case)
    if len(matches) != 1:
        raise ValueError(f"expected exactly one seed606/6.7mps/30dB case, found {len(matches)}")
    return matches[0]


def _scenario_signature(scenario: Mapping[str, Any], layout: Mapping[str, Any]) -> dict[str, Any]:
    random = scenario.get("random", {})
    scene = scenario.get("scene", {})
    if not isinstance(random, Mapping) or not isinstance(scene, Mapping):
        raise ValueError("scenario signature requires random and scene objects")
    scene_fields = (
        "range_min_m",
        "range_max_m",
        "azimuth_min_deg",
        "azimuth_max_deg",
        "ground_z_m",
        "clutter_amplitude_scale",
        "area_clutter",
        "thermal_noise",
    )
    return {
        "platform": scenario.get("platform"),
        "waveform": scenario.get("waveform"),
        "scene": {key: scene.get(key) for key in scene_fields},
        "period_start": random.get("period_start"),
        "period_count": random.get("period_count"),
        "beam_start": random.get("beam_start"),
        "beam_count": random.get("beam_count"),
        "protocol_layout": dict(layout),
    }


def load_primary_area_evidence(case_manifest: Mapping[str, Any]) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    """Load the actual area-injection owner recorded by the completed case.

    OFF/TO scenes may copy the paired background and therefore report zero new
    injections. The case-level area gate points to the Stage2 scene that owns
    the generated area clutter; do not substitute the OFF role report.
    """

    gate = case_manifest.get("area_clutter_gate")
    if not isinstance(gate, Mapping) or gate.get("status") != "passed":
        raise ValueError("seed606 case has no passed area-clutter owner gate")
    report_path = Path(str(gate.get("report_path", ""))).resolve()
    scenario_path = Path(str(gate.get("scenario_path", ""))).resolve()
    if not report_path.is_file() or not scenario_path.is_file():
        raise FileNotFoundError("seed606 area-clutter owner report/scenario is missing")
    if _sha256(report_path) != gate.get("report_sha256"):
        raise RuntimeError("seed606 area-clutter owner report SHA mismatch")
    if _sha256(scenario_path) != gate.get("scenario_sha256"):
        raise RuntimeError("seed606 area-clutter owner scenario SHA mismatch")
    return dict(gate), report_path, _read_json(scenario_path)


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def compare_area_statistics(
    primary_gate: Mapping[str, Any],
    reference_gate: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply the thresholds frozen in 03_information_fairness_protocol.md."""

    checks: list[dict[str, Any]] = []
    for name, tolerance in AREA_RELATIVE_TOLERANCES.items():
        if name == "area_mean_power_measured":
            left = _float(primary_gate.get(name))
            right = _float(reference_gate.get(name))
        else:
            left = _float((primary_gate.get("report_values") or {}).get(name))
            right = _float((reference_gate.get("report_values") or {}).get(name))
        if left is None or right is None or max(abs(left), abs(right)) <= 0.0:
            checks.append({"metric": name, "status": "NOT_EVALUABLE", "reason": "missing_or_zero"})
            continue
        delta = abs(right - left) / max(abs(left), abs(right))
        checks.append({
            "metric": name,
            "primary": left,
            "independent_reference": right,
            "relative_difference": delta,
            "tolerance": tolerance,
            "status": "passed" if delta <= tolerance else "failed",
        })
    for name, tolerance in AREA_ABSOLUTE_TOLERANCES.items():
        left = _float((primary_gate.get("report_values") or {}).get(name))
        right = _float((reference_gate.get("report_values") or {}).get(name))
        if left is None or right is None:
            checks.append({"metric": name, "status": "NOT_EVALUABLE", "reason": "missing_or_invalid"})
            continue
        delta = abs(right - left)
        checks.append({
            "metric": name,
            "primary": left,
            "independent_reference": right,
            "absolute_difference": delta,
            "tolerance": tolerance,
            "status": "passed" if delta <= tolerance else "failed",
        })
    positive = all(
        _float(gate.get("area_mean_power_measured")) is not None
        and float(gate["area_mean_power_measured"]) > 0.0
        and int(gate.get("continuous_area_packets") or 0) > 0
        and int(gate.get("continuous_area_samples") or 0) > 0
        for gate in (primary_gate, reference_gate)
    )
    if not positive:
        checks.append({"metric": "effective_area_clutter", "status": "failed"})
    passed = positive and all(item.get("status") == "passed" for item in checks)
    return {
        "status": "passed" if passed else "NOT_EVALUABLE",
        "classification": "compatible_under_frozen_tolerances_not_statistically_proven" if passed else "mismatch_or_missing_statistics",
        "checks": checks,
        "gamma_distribution_claim": "descriptive_only_no_acceptance_threshold",
    }


def _archive_snapshot(source_root: Path, destination: Path) -> dict[str, Any]:
    if not source_root.is_dir():
        raise FileNotFoundError(f"snapshot source directory missing: {source_root}")
    if destination.exists():
        raise FileExistsError(f"refuse to overwrite evidence snapshot: {destination}")
    copied: list[dict[str, Any]] = []
    omitted: Counter[str] = Counter()
    omitted_bytes: Counter[str] = Counter()
    skipped_symlinks = 0
    for source in sorted(source_root.rglob("*")):
        if source.is_symlink():
            skipped_symlinks += 1
            continue
        if not source.is_file():
            continue
        suffix = source.suffix.lower()
        size = source.stat().st_size
        if suffix in RAW_ARRAY_SUFFIXES:
            omitted[suffix or "<none>"] += 1
            omitted_bytes[suffix or "<none>"] += size
            continue
        relative = source.relative_to(source_root)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        digest = _sha256(target)
        if digest != _sha256(source):
            raise IOError(f"snapshot copy verification failed: {relative}")
        copied.append({
            "relative_path": str(relative),
            "bytes": size,
            "sha256": digest,
        })
    index = {
        "schema_version": 1,
        "source_root": str(source_root.resolve()),
        "snapshot_root": str(destination.resolve()),
        "copied_file_count": len(copied),
        "copied_bytes": sum(row["bytes"] for row in copied),
        "omitted_reproducible_arrays": dict(omitted),
        "omitted_array_bytes": dict(omitted_bytes),
        "skipped_symlink_count": skipped_symlinks,
        "files": copied,
    }
    _write_json(destination / "snapshot_index.json", index)
    return index


def _read_gamma_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return [dict(row) for row in csv.DictReader(stream)]


def _gamma_application_check(reference_path: Path, applied_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    reference_rows = _read_gamma_csv(reference_path)

    def build(rows: Sequence[Mapping[str, Any]]) -> tuple[dict[tuple[int, int, str], tuple[float, float]], list[str]]:
        values: dict[tuple[int, int, str], tuple[float, float]] = {}
        reasons: list[str] = []
        for row in rows:
            try:
                key = (int(str(row["period_id"])), int(str(row["beam_id"])), str(row["group_id"]))
                gamma = (float(str(row["gamma_real"])), float(str(row["gamma_imag"])))
            except (KeyError, TypeError, ValueError):
                reasons.append("invalid_gamma_row")
                continue
            if str(row.get("status", "")) != "OK":
                reasons.append("gamma_row_status_not_ok")
            if str(row.get("truth_used_in_estimator", "")).strip().lower() != "false":
                reasons.append("truth_used_in_estimator_or_missing")
            if key in values:
                reasons.append("duplicate_gamma_group")
            values[key] = gamma
        return values, reasons

    reference, reference_reasons = build(reference_rows)
    applied, applied_reasons = build(applied_rows)
    reasons = reference_reasons + applied_reasons
    if not reference:
        reasons.append("reference_gamma_rows_missing")
    if set(reference) != set(applied):
        reasons.append("applied_gamma_group_keys_mismatch")
    maximum_difference = 0.0
    for key in set(reference) & set(applied):
        difference = max(abs(reference[key][0] - applied[key][0]), abs(reference[key][1] - applied[key][1]))
        maximum_difference = max(maximum_difference, difference)
        if difference > APPLICATION_GAMMA_ABS_TOLERANCE:
            reasons.append("applied_gamma_value_mismatch")
    return {
        "status": "passed" if not reasons else "NOT_EVALUABLE",
        "reason": sorted(set(reasons)),
        "reference_group_count": len(reference),
        "applied_group_count": len(applied),
        "maximum_gamma_absolute_difference": maximum_difference,
        "tolerance": APPLICATION_GAMMA_ABS_TOLERANCE,
    }


def _mean_csi_taps(paths: Sequence[str]) -> dict[str, Any]:
    wanted = (
        "before_mean_power",
        "after_mean_power",
        "CA_ROI_dB",
        "phase_model_weighted_coherence",
        "phase_model_weighted_circular_rmse_rad",
    )
    values: dict[str, list[float]] = {key: [] for key in wanted}
    rows_seen = 0
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            continue
        for row in _read_gamma_csv(path):
            if row.get("roi_name") != "active_support_unmasked":
                continue
            rows_seen += 1
            for key in wanted:
                value = _float(row.get(key))
                if value is not None:
                    values[key].append(value)
    return {
        "row_count": rows_seen,
        **{
            key: sum(items) / len(items) if items else None
            for key, items in values.items()
        },
        "interpretation": "Production CSI diagnostic; mean includes clutter and noise, not target-only SCNR",
    }


def _comparison_rows(
    baseline: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    method_id: str,
    role: str,
    baseline_artifacts: Mapping[str, Any],
    current_artifacts: Mapping[str, Any],
) -> list[dict[str, Any]]:
    fields = (
        "target_detection_opportunity_count",
        "target_detection_hit_count",
        "raw_detection_count",
        "detection_record_false_alarm_count",
        "cfar_hit_cell_count",
        "cfar_cluster_count",
        "cfar_selected_cluster_count",
        "cfar_pfa",
        "unique_false_track_count",
        "track_output_count",
        "angle_rmse_deg",
        "velocity_rmse_mps_ground_speed",
        "position_rmse_m",
    )
    rows: list[dict[str, Any]] = []
    for field in fields:
        left = baseline.get(field)
        right = current.get(field)
        left_num = _float(left)
        right_num = _float(right)
        rows.append({
            "method_id": method_id,
            "role": role,
            "metric": field,
            "mode_b_baseline": left_num,
            "independent_reference": right_num,
            "difference": right_num - left_num if left_num is not None and right_num is not None else None,
            "status": "evaluable" if left_num is not None and right_num is not None else "NOT_EVALUABLE",
        })
    baseline_csi = _mean_csi_taps(baseline_artifacts.get("csi_metric_tap_summary", []))
    current_csi = _mean_csi_taps(current_artifacts.get("csi_metric_tap_summary", []))
    for field in (
        "before_mean_power",
        "after_mean_power",
        "CA_ROI_dB",
        "phase_model_weighted_coherence",
        "phase_model_weighted_circular_rmse_rad",
    ):
        left = _float(baseline_csi.get(field))
        right = _float(current_csi.get(field))
        rows.append({
            "method_id": method_id,
            "role": role,
            "metric": f"csi_tap_{field}",
            "mode_b_baseline": left,
            "independent_reference": right,
            "difference": right - left if left is not None and right is not None else None,
            "status": "evaluable" if left is not None and right is not None else "NOT_EVALUABLE",
        })
    return rows


def _write_comparison_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fields = (
        "method_id", "role", "metric", "mode_b_baseline",
        "independent_reference", "difference", "status",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _gpu_probe() -> dict[str, Any]:
    command = [
        "nvidia-smi",
        "--query-gpu=name,driver_version,pstate,temperature.gpu,power.draw,utilization.gpu,clocks.gr,clocks.mem",
        "--format=csv",
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    output = result.stdout.strip()
    return {
        "command": command,
        "returncode": result.returncode,
        "output": output,
        "status": "passed" if result.returncode == 0 and output else "failed",
    }


def _archive_and_remove(source: Path, destination: Path) -> dict[str, Any]:
    before = {
        "file_count": 0,
        "logical_bytes": 0,
        "allocated_bytes": 0,
    }
    for path in source.rglob("*"):
        if path.is_file() and not path.is_symlink():
            stat = path.stat()
            before["file_count"] += 1
            before["logical_bytes"] += stat.st_size
            before["allocated_bytes"] += stat.st_blocks * 512
    snapshot = _archive_snapshot(source, destination)
    shutil.rmtree(source)
    return {"removed": before, "snapshot": snapshot, "status": "passed"}


def _write_report(path: Path, manifest: Mapping[str, Any]) -> None:
    gates = manifest.get("gates", {})
    lines = [
        "# R18 独立 seed909 参考校正次级评估",
        "",
        f"状态：`{manifest.get('status', 'NOT_EVALUABLE')}`；性能结论：单 seed 描述性比较，不作统计显著性声明。",
        "",
        "## 预注册信息边界与执行身份",
        "",
        f"- 独立参考 seed：{manifest.get('reference_seed')}；评价 case：seed {manifest.get('evaluation_seed')}、6.7 m/s、30 dB。",
        f"- 参考场景：`area_clutter_only`、目标关闭、truth 输出关闭、配对背景字段已剔除；估计器 truth 使用：`{manifest.get('truth_used_in_estimator')}`。",
        f"- Production binary SHA-256：`{manifest.get('binaries', {}).get('GMTI_pipe_core', {}).get('sha256')}`。",
        f"- CUDA 设备探测：`{manifest.get('gpu_probe', {}).get('status')}`；{manifest.get('gpu_probe', {}).get('output', '').replace(chr(10), '; ')}",
        "",
        "## 有效面积杂波与跨 seed 可比性",
        "",
        f"- Stage2 area gate：`{gates.get('reference_area', {}).get('status')}`；packets={gates.get('reference_area', {}).get('continuous_area_packets')}，samples={gates.get('reference_area', {}).get('continuous_area_samples')}，measured mean power={gates.get('reference_area', {}).get('area_mean_power_measured')}。",
        f"- seed909 与 seed606 预注册统计门：`{gates.get('area_statistics_comparability', {}).get('status')}`（{gates.get('area_statistics_comparability', {}).get('classification')}）。",
        "- 群组 key 与每组 support count 要求完全一致；Gamma 分布只作描述性比较。",
        "",
        "## C2/C3 生产闭环",
        "",
        f"- Gamma 参考生成：`{gates.get('reference_gamma_generation', {}).get('status')}`。",
        f"- 六个 OFF/ON/TO 生产分支：`{gates.get('evaluation_branches', {}).get('status')}`。",
        f"- 外部 Gamma 在实际 runtime XML 和生产 tap 中的应用审计：`{gates.get('reference_application', {}).get('status')}`。",
        "- Mode-B 基线与独立参考条件的逐分支数值见 `branch_comparison.csv`。CSI tap 同时含杂波与噪声，不作为目标 SCNR；检测行数不作为经验 Pfa。",
        "- 本次 `cfar_pfa` / 经验 Pfa 若没有独立 truth-filtered valid-CUT 估计仍标为 `NOT_EVALUABLE`；seed909 参考不是独立统计重复数。",
        "",
        "## 限制与解释边界",
        "",
        "本次只检验一个 seed606 评价场景与一个独立 seed909 模拟参考。即使所有运行门和可比性门通过，也只证明该固定场景上的独立 Gamma 参考应用可执行并可评价；不证明 airborne 实测统计同质、在线可部署性、普遍 GMTI 收益或 Pd/Pfa 显著改善。",
        "",
        f"审计清单：`{manifest.get('snapshot_index_path')}`；完整运行 manifest：`manifest.json`。",
        "",
    ]
    diagnostic = manifest.get("diagnostic_review")
    if isinstance(diagnostic, Mapping):
        lines.extend([
            "## 执行门失败诊断",
            "",
            str(diagnostic.get("finding", "")),
            "",
            str(diagnostic.get("correction", "")),
            "",
        ])
    failures = manifest.get("preflight_failures")
    if isinstance(failures, list) and failures:
        lines.extend(["## 保留的启动/审计失败记录", ""])
        for item in failures:
            if isinstance(item, Mapping):
                lines.append(
                    f"- {item.get('attempt')}: `{item.get('status')}`；{item.get('finding')}；记录：`{item.get('record_path')}`。"
                )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary-manifest", type=Path, default=DEFAULT_PRIMARY_MANIFEST)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--scratch-root", type=Path, default=DEFAULT_SCRATCH_ROOT)
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    parser.add_argument("--simulator", type=Path)
    parser.add_argument("--production-pipe", type=Path)
    parser.add_argument("--project-budget-gib", type=float, default=13.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    primary_manifest_path = args.primary_manifest.resolve()
    config_path = args.config.resolve()
    scratch_root = args.scratch_root.resolve()
    evidence_root = args.evidence_root.resolve()
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "stage": "R18 independent seed909 reference secondary",
        "status": "RUNNING",
        "implementation_status": "IMPLEMENTED",
        "execution_status": "NOT_STARTED",
        "evaluation_status": "NOT_EVALUABLE",
        "reference_seed": 909,
        "evaluation_seed": 606,
        "evaluation_velocity_mps": 6.7,
        "evaluation_snr_db": 30.0,
        "selected_methods": list(METHOD_IDS),
        "roles": list(ROLES),
        "truth_used_in_estimator": False,
        "algorithmic_results_claimed": False,
        "performance_verified": False,
        "statistical_significance_claimed": False,
        "primary_manifest": str(primary_manifest_path),
        "config": str(config_path),
        "scratch_root": str(scratch_root),
        "evidence_root": str(evidence_root),
        "gates": {},
        "reference_methods": {},
        "evaluation_branches": {},
        "comparisons": [],
    }
    scratch_archived = False
    try:
        if not primary_manifest_path.is_file() or not config_path.is_file():
            raise FileNotFoundError("primary manifest or frozen config is missing")
        if scratch_root.exists() or evidence_root.exists():
            raise FileExistsError("refuse to overwrite an existing scratch/evidence run")
        scratch_root.mkdir(parents=True)
        evidence_root.mkdir(parents=True)
        manifest["primary_manifest_sha256"] = _sha256(primary_manifest_path)
        manifest["config_sha256"] = _sha256(config_path)
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
        )
        status = subprocess.run(
            ["git", "status", "--short", "--", str(Path(__file__).resolve().relative_to(ROOT))],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        manifest["source_identity"] = {
            "git_commit": revision.stdout.strip() if revision.returncode == 0 else None,
            "git_revision_status": "passed" if revision.returncode == 0 else "failed",
            "runner_path": str(Path(__file__).resolve()),
            "runner_sha256": _sha256(Path(__file__).resolve()),
            "worktree_status_for_runner": status.stdout.strip(),
        }

        project_root = ROOT.parent.parent
        used_bytes = _project_allocated_bytes(project_root)
        budget_bytes = int(args.project_budget_gib * (1024**3))
        manifest["space_guard"] = {
            "project_root": str(project_root),
            "used_bytes_before_run": used_bytes,
            "budget_bytes": budget_bytes,
            "status": "passed" if used_bytes < budget_bytes else "failed",
        }
        if used_bytes >= budget_bytes:
            raise RuntimeError("project footprint is at/above the pre-run 13 GiB guard")

        primary = _read_json(primary_manifest_path)
        primary_case = _find_primary_case(primary)
        case_dir = Path(str(primary_case["output_root"])).resolve()
        case_manifest_path = case_dir / "case_manifest.json"
        primary_case_manifest = _read_json(case_manifest_path)
        manifest["primary_case_manifest"] = str(case_manifest_path)
        manifest["primary_case_manifest_sha256"] = _sha256(case_manifest_path)
        source_scenario_path = Path(str(primary_case["simulation"]["A0_OFF"]["scenario"])).resolve()
        source_scenario = _read_json(source_scenario_path)
        source_xml = Path(str(primary_case["source_xml"])).resolve()
        if not source_xml.is_file():
            raise FileNotFoundError(f"production source XML missing: {source_xml}")
        manifest["source_scenario"] = {
            "path": str(source_scenario_path),
            "sha256": _sha256(source_scenario_path),
        }
        manifest["source_xml"] = {"path": str(source_xml), "sha256": _sha256(source_xml)}

        identities = primary.get("build_identity_after_run", primary.get("build", {}))
        stage2_identity = identities.get("simulate_stage2_statistical", {})
        pipe_identity = identities.get("GMTI_pipe_core", {})
        simulator = (args.simulator or Path(str(stage2_identity.get("path", "")))).resolve()
        production_pipe = (args.production_pipe or Path(str(pipe_identity.get("path", "")))).resolve()
        current_stage2 = _file_identity(simulator)
        current_pipe = _file_identity(production_pipe)
        if current_stage2["sha256"] != stage2_identity.get("sha256"):
            raise RuntimeError("Stage2 simulator SHA differs from the completed R18 matrix")
        if current_pipe["sha256"] != pipe_identity.get("sha256"):
            raise RuntimeError("Production pipe SHA differs from the completed R18 matrix")
        manifest["binaries"] = {
            "simulate_stage2_statistical": current_stage2,
            "GMTI_pipe_core": current_pipe,
        }
        os.environ["PGRCC_STAGE2_SIMULATOR"] = str(simulator)
        os.environ["PGRCC_GMTI_PIPE_CORE"] = str(production_pipe)
        os.environ["PGRCC_EXPECTED_STAGE2_SIMULATOR_SHA256"] = str(current_stage2["sha256"])
        os.environ["PGRCC_EXPECTED_GMTI_PIPE_CORE_SHA256"] = str(current_pipe["sha256"])

        gpu = _gpu_probe()
        manifest["gpu_probe"] = gpu
        if gpu["status"] != "passed":
            raise RuntimeError("nvidia-smi failed; Native CUDA secondary will not be represented as executed")

        # Imports happen after executable paths and expected identities are pinned.
        from scripts import run_delay_stage1_formal as delay_runner
        from scripts import run_production_hierarchical_calibration_pilot as pilot
        from scripts import run_track_manager_e2e as e2e

        config = pilot.load_pilot_config(config_path)
        methods = pilot.build_method_contract(config, method_ids=METHOD_IDS)
        method_by_id = {str(method["method_id"]): method for method in methods}
        production = config.get("production")
        if not isinstance(production, Mapping):
            raise ValueError("frozen config has no production object")
        layout = primary_case_manifest.get("protocol_layout")
        if not isinstance(layout, Mapping):
            raise ValueError("seed606 case manifest has no protocol layout")
        period_count = int(primary_case_manifest["case"]["period_count"])
        input_mode = str(production.get("input_mode", "local"))
        if input_mode != "local":
            raise ValueError(f"frozen R18 input mode changed unexpectedly: {input_mode}")

        scenario_output = scratch_root / "reference_scene"
        reference_scenario = build_target_free_reference_scenario(
            source_scenario,
            output_dir=scenario_output,
            seed=909,
        )
        serialized_scenario = json.dumps(reference_scenario, sort_keys=True)
        if any(marker in serialized_scenario for marker in ("background_input_dir", "stage1_pairing", str(case_dir))):
            raise RuntimeError("independent reference scenario still contains paired/original case metadata")
        scenario_path = scratch_root / "reference_scene.json"
        _write_json(scenario_path, reference_scenario)
        manifest["reference_scenario"] = {
            "path": str(scenario_path),
            "sha256": _sha256(scenario_path),
            "output_dir": str(scenario_output),
            "scene_mode": reference_scenario["scene_mode"],
            "truth_output": reference_scenario["truth_output"],
            "targets_enabled": any(bool(target.get("enabled")) for target in reference_scenario.get("targets", [])),
            "random_seed": reference_scenario["random"]["random_seed"],
        }

        source_signature = _scenario_signature(source_scenario, layout)
        reference_signature = _scenario_signature(reference_scenario, layout)
        recorded_primary_area, primary_report_path, primary_area_scenario = load_primary_area_evidence(
            primary_case_manifest
        )
        area_owner_signature = _scenario_signature(primary_area_scenario, layout)
        physics_match = (
            source_signature == reference_signature
            and area_owner_signature == reference_signature
        )
        manifest["gates"]["physics_configuration_match"] = {
            "status": "passed" if physics_match else "NOT_EVALUABLE",
            "source_signature": source_signature,
            "area_owner_signature": area_owner_signature,
            "reference_signature": reference_signature,
        }
        if manifest["gates"]["physics_configuration_match"]["status"] != "passed":
            raise RuntimeError("independent scene changed frozen physical/clutter/noise/layout settings")

        primary_area_audit = delay_runner.audit_stage2_area_clutter_report(
            primary_report_path.read_text(encoding="utf-8", errors="replace"),
            primary_area_scenario,
        )
        primary_area_gate = {
            **recorded_primary_area,
            "recomputed_audit": primary_area_audit,
        }
        if (
            primary_area_audit.get("status") != "passed"
            or primary_area_audit.get("continuous_area_packets") != recorded_primary_area.get("continuous_area_packets")
            or primary_area_audit.get("continuous_area_samples") != recorded_primary_area.get("continuous_area_samples")
            or _float(primary_area_audit.get("area_mean_power_measured"))
            != _float(recorded_primary_area.get("area_mean_power_measured"))
        ):
            raise RuntimeError("seed606 recorded area-owner gate does not match its actual report")
        stage2_record = _run_logged(
            [str(simulator), "--config", str(scenario_path)],
            scratch_root / "stage2_reference.log",
        )
        manifest["stage2_reference"] = stage2_record
        manifest["execution_status"] = (
            "PARTIALLY_EXECUTED" if stage2_record["status"] == "passed" else "EXECUTED_WITH_FAILURE"
        )
        _write_json(scratch_root / "experiment_manifest.json", manifest)
        if stage2_record["status"] != "passed":
            raise RuntimeError("seed909 Stage2 Native run failed")
        report_path = scenario_output / "reports/stage2_simulation_report.md"
        if not report_path.is_file():
            raise FileNotFoundError("seed909 Stage2 did not produce its actual simulation report")
        reference_report_text = report_path.read_text(encoding="utf-8", errors="replace")
        reference_area_gate = delay_runner.audit_stage2_area_clutter_report(
            reference_report_text,
            reference_scenario,
        )
        manifest["gates"]["primary_area"] = primary_area_gate
        manifest["gates"]["reference_area"] = {
            **reference_area_gate,
            "report_path": str(report_path),
            "report_sha256": _sha256(report_path),
            "scenario_path": str(scenario_path),
            "scenario_sha256": _sha256(scenario_path),
        }
        if reference_area_gate.get("status") != "passed" or primary_area_gate.get("status") != "passed":
            raise RuntimeError("effective Stage2 area-clutter gate failed")
        area_comparison = compare_area_statistics(primary_area_gate, reference_area_gate)
        manifest["gates"]["area_statistics_comparability"] = area_comparison
        if area_comparison.get("status") != "passed":
            raise RuntimeError("seed909 Stage2 clutter/noise statistics failed frozen comparability gates")

        reference_periods = sorted((scenario_output / "data").glob("stage2_statistical_newprotocol_period_*.bin"))
        if len(reference_periods) != period_count:
            raise RuntimeError(f"expected {period_count} seed909 period files, found {len(reference_periods)}")
        reference_input_dir = pilot._materialize_input_dir(
            scratch_root / "reference_inputs",
            reference_periods,
        )
        manifest["reference_inputs"] = [
            {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in reference_periods
        ]

        # Baseline role inputs and their hashes come from the completed seed606 manifest.
        prepared = primary_case_manifest.get("prepared_inputs", {}).get("none", {})
        periods_by_role = prepared.get("period_paths_by_role", {})
        if not isinstance(periods_by_role, Mapping):
            raise ValueError("seed606 C+N prepared input roles are missing")
        baseline_hashes: dict[str, list[str]] = {}
        for role in ROLES:
            paths = [Path(str(path)).resolve() for path in periods_by_role.get(role, [])]
            if len(paths) != period_count or any(not path.is_file() for path in paths):
                raise RuntimeError(f"seed606 {role} input files are missing or incomplete")
            hashes = [_sha256(path) for path in paths]
            if set(hashes) & {row["sha256"] for row in manifest["reference_inputs"]}:
                raise RuntimeError("seed909 and seed606 inputs share a period file identity")
            baseline_hashes[role] = hashes
        manifest["evaluation_inputs"] = {
            role: [
                {"path": str(Path(str(path)).resolve()), "bytes": Path(str(path)).stat().st_size, "sha256": digest}
                for path, digest in zip(periods_by_role[role], baseline_hashes[role])
            ]
            for role in ROLES
        }

        source_xml_copy = source_xml
        reference_products_root = scratch_root / "reference_production"
        evidence_gamma_root = evidence_root / "reference_gamma"
        evidence_gamma_root.mkdir(parents=True)
        gamma_support_gates: dict[str, Any] = {}
        for method_id in METHOD_IDS:
            method = method_by_id[method_id]
            branch_root = reference_products_root / method_id
            xml_record = pilot._method_xml(
                branch_root,
                source_xml_copy,
                method,
                production,
                layout=layout,
                mode="Mode-A",
                role="OFF",
            )
            if xml_record.get("status") != "passed":
                raise RuntimeError(f"{method_id} seed909 reference XML configuration failed")
            record = delay_runner.run_production_branch(
                branch_root,
                f"{method_id}_Mode-A_reference_OFF",
                reference_input_dir,
                Path(str(xml_record["path"])),
                {},
                period_count,
                input_mode,
                layout,
                xml_overrides=xml_record["overrides"],
            )
            gate = delay_runner.audit_production_path_evidence(record)
            artifacts = pilot._collect_branch_artifacts(record)
            rows = pilot._reference_gamma_rows(
                artifacts.get("reference_gamma_csv", []), method_id, "OFF"
            )
            validation = pilot.validate_reference_gamma_rows(
                rows,
                method_id=method_id,
                min_support=int(production.get("min_support", 8)),
                expected_method=str(method["research_calibration_method"]),
            )
            observed_periods = sorted({int(str(row.get("period_id", -1))) for row in rows})
            if observed_periods != list(range(1, period_count + 1)):
                validation = {**validation, "status": "NOT_EVALUABLE", "reason": "period_coverage_mismatch"}
            reference_path = evidence_gamma_root / f"{method_id}_seed909_reference_gamma.csv"
            artifact = pilot.write_reference_gamma_artifact(
                rows,
                reference_path,
                method_id=method_id,
                min_support=int(production.get("min_support", 8)),
                expected_method=str(method["research_calibration_method"]),
            )
            if gate.get("status") != "passed" or validation.get("status") != "evaluable" or artifact.get("status") != "passed":
                raise RuntimeError(f"{method_id} seed909 Gamma generation failed its Production/CSI gate")
            primary_fit = primary_case_manifest["method_mode_roles"][f"{method_id}_Mode-A"]["OFF"]
            baseline_artifacts = primary_fit.get("artifacts", {})
            baseline_rows = pilot._reference_gamma_rows(
                baseline_artifacts.get("reference_gamma_csv", []), method_id, "OFF"
            )
            baseline_validation = pilot.validate_reference_gamma_rows(
                baseline_rows,
                method_id=method_id,
                min_support=int(production.get("min_support", 8)),
                expected_method=str(method["research_calibration_method"]),
            )
            key = lambda row: (
                int(str(row.get("period_id", -1))),
                int(str(row.get("beam_id", -1))),
                str(row.get("group_id", "")),
                int(str(row.get("support_count", -1))),
            )
            expected_support = sorted(key(row) for row in baseline_rows)
            observed_support = sorted(key(row) for row in rows)
            support_gate = {
                "status": "passed" if baseline_validation.get("status") == "evaluable" and expected_support == observed_support else "NOT_EVALUABLE",
                "baseline_row_count": len(baseline_rows),
                "reference_row_count": len(rows),
                "keys_and_support_counts_equal": expected_support == observed_support,
                "baseline_validation": baseline_validation,
                "reference_validation": validation,
            }
            gamma_support_gates[method_id] = support_gate
            manifest["reference_methods"][method_id] = {
                "production_record": record,
                "production_gate": gate,
                "runtime_xml": xml_record,
                "artifacts": artifacts,
                "validation": validation,
                "gamma_artifact": artifact,
                "gamma_artifact_path": str(reference_path),
                "gamma_artifact_sha256": _sha256(reference_path),
                "support_gate": support_gate,
                "gamma_summary": pilot.compact_reference_gamma_summary(
                    rows,
                    validation,
                    source_paths=artifacts.get("reference_gamma_csv", []),
                ),
            }
            manifest["gates"]["gamma_support_coverage"] = {
                "status": "passed" if all(item["status"] == "passed" for item in gamma_support_gates.values()) else "NOT_EVALUABLE",
                "methods": gamma_support_gates,
            }
            _write_json(scratch_root / "experiment_manifest.json", manifest)
            if support_gate["status"] != "passed":
                raise RuntimeError(f"{method_id} Gamma support/group coverage differs from seed606 Mode-A OFF")

        manifest["gates"]["gamma_support_coverage"] = {
            "status": "passed" if all(item["status"] == "passed" for item in gamma_support_gates.values()) else "NOT_EVALUABLE",
            "methods": gamma_support_gates,
        }
        manifest["gates"]["reference_gamma_generation"] = {"status": "passed", "methods": list(METHOD_IDS)}

        truth_root = Path(str(primary_case["simulation"]["A0_ON"]["output_dir"])).resolve()
        truth = e2e._truth_by_period(truth_root, period_count)
        if set(truth) != set(range(period_count)):
            raise RuntimeError("seed606 evaluator truth does not cover all frozen periods")
        comparisons: list[dict[str, Any]] = []
        branch_gates: list[dict[str, Any]] = []
        gamma_application_gates: list[dict[str, Any]] = []
        for method_id in METHOD_IDS:
            method = method_by_id[method_id]
            reference_path = evidence_gamma_root / f"{method_id}_seed909_reference_gamma.csv"
            for role in ROLES:
                baseline_record = primary_case_manifest["method_mode_roles"][f"{method_id}_Mode-B"][role]
                baseline_paths = [Path(str(path)).resolve() for path in baseline_record.get("input_paths", [])]
                if [_sha256(path) for path in baseline_paths] != baseline_hashes[role]:
                    raise RuntimeError(f"{method_id} Mode-B {role} input SHA differs from prepared seed606 input")
                input_dir = pilot._materialize_input_dir(
                    scratch_root / "evaluation_inputs" / f"{method_id}_{role}",
                    [Path(str(path)) for path in periods_by_role[role]],
                )
                branch_root = scratch_root / "evaluation_production" / method_id / role
                xml_record = pilot._method_xml(
                    branch_root,
                    source_xml_copy,
                    method,
                    production,
                    layout=layout,
                    mode="Mode-B",
                    role=role,
                    reference_path=reference_path,
                )
                if xml_record.get("status") != "passed":
                    raise RuntimeError(f"{method_id} independent-reference XML failed for {role}")
                record = delay_runner.run_production_branch(
                    branch_root,
                    f"{method_id}_independent_reference_{role}",
                    input_dir,
                    Path(str(xml_record["path"])),
                    truth,
                    period_count,
                    input_mode,
                    layout,
                    truth_path=truth_root,
                    xml_overrides=xml_record["overrides"],
                )
                production_gate = delay_runner.audit_production_path_evidence(record)
                actual_xml = Path(str(record.get("production_xml", "")))
                xml_audit = pilot.audit_xml_fields(actual_xml, xml_record["overrides"]) if actual_xml.is_file() else {"status": "failed", "reason": "runtime_xml_missing"}
                artifacts = pilot._collect_branch_artifacts(record)
                applied_rows = pilot._reference_gamma_rows(
                    artifacts.get("reference_gamma_csv", []), method_id, role
                )
                applied_validation = pilot.validate_reference_gamma_rows(
                    applied_rows,
                    method_id=method_id,
                    min_support=int(production.get("min_support", 8)),
                    expected_method=str(method["research_calibration_method"]),
                )
                application = _gamma_application_check(reference_path, applied_rows)
                application["xml_audit"] = xml_audit
                application["applied_validation"] = applied_validation
                if xml_audit.get("status") != "passed" or applied_validation.get("status") != "evaluable":
                    application["status"] = "NOT_EVALUABLE"
                gamma_application_gates.append({"method_id": method_id, "role": role, **application})
                branch_gate = {
                    "method_id": method_id,
                    "role": role,
                    "status": "passed" if record.get("status") == "passed" and production_gate.get("status") == "passed" else "NOT_EVALUABLE",
                    "production_status": record.get("status"),
                    "production_gate": production_gate,
                }
                branch_gates.append(branch_gate)
                manifest["evaluation_branches"][f"{method_id}_{role}"] = {
                    "record": record,
                    "production_gate": production_gate,
                    "runtime_xml": xml_record,
                    "runtime_xml_audit": xml_audit,
                    "artifacts": artifacts,
                    "applied_reference_validation": applied_validation,
                    "reference_application": application,
                    "input_sha256": baseline_hashes[role],
                    "reference_gamma_path": str(reference_path),
                    "reference_gamma_sha256": _sha256(reference_path),
                    "external_reference_contract": {
                        "reference_source": "seed909_target_free_area_clutter_only",
                        "estimator_called": False,
                        "evaluator_only": True,
                        "truth_used_in_estimator": False,
                        "fallback_to_current": False,
                    },
                }
                comparisons.extend(_comparison_rows(
                    baseline_record.get("metrics", {}),
                    record.get("metrics", {}),
                    method_id=method_id,
                    role=role,
                    baseline_artifacts=baseline_record.get("artifacts", {}),
                    current_artifacts=artifacts,
                ))
                _write_json(scratch_root / "experiment_manifest.json", manifest)

        branch_status = "passed" if len(branch_gates) == 6 and all(item["status"] == "passed" for item in branch_gates) else "NOT_EVALUABLE"
        application_status = "passed" if len(gamma_application_gates) == 6 and all(item["status"] == "passed" for item in gamma_application_gates) else "NOT_EVALUABLE"
        manifest["gates"]["evaluation_branches"] = {"status": branch_status, "branches": branch_gates}
        manifest["gates"]["reference_application"] = {"status": application_status, "branches": gamma_application_gates}
        if branch_status != "passed" or application_status != "passed":
            raise RuntimeError("one or more seed606 Production evaluation branches failed closed gates")

        manifest["comparisons"] = comparisons
        comparison_path = evidence_root / "branch_comparison.csv"
        _write_comparison_csv(comparison_path, comparisons)
        manifest["comparison_csv"] = {"path": str(comparison_path), "sha256": _sha256(comparison_path), "row_count": len(comparisons)}
        manifest["status"] = "EVALUABLE"
        manifest["execution_status"] = "EXECUTED"
        manifest["evaluation_status"] = "EVALUABLE"
        manifest["performance_claim_status"] = "SINGLE_SCENE_DESCRIPTIVE_ONLY"
        manifest["limitations"] = [
            "One independent reference seed and one evaluation seed; no statistical significance claim.",
            "Pfa remains NOT_EVALUABLE unless an independent truth-filtered valid-CUT estimator is available.",
            "Production CSI tap is clutter/noise mixed and is not target-only SCNR.",
            "The independent reference is generated by the Stage2 simulator and does not establish real airborne deployability.",
        ]
        _write_json(evidence_root / "manifest.json", manifest)
        _write_report(evidence_root / "report.md", manifest)

        # Archive the generated raw case before deleting its reproducible arrays.
        scratch_archive = _archive_snapshot(scratch_root, evidence_root / "experiment_snapshot")
        manifest["scratch_snapshot"] = scratch_archive
        manifest["snapshot_index_path"] = str(evidence_root / "experiment_snapshot/snapshot_index.json")
        manifest["scratch_cleanup_status"] = "archived_and_removed"
        _write_json(evidence_root / "manifest.json", manifest)
        _write_report(evidence_root / "report.md", manifest)
        shutil.rmtree(scratch_root)
        scratch_archived = True

        # The seed606 primary raw data is no longer needed after all paired roles ran.
        primary_snapshot = ROOT / "docs/research/phase_r/R18/evidence/formal_matrix/seed606_v6p7_snr30/experiment_snapshot"
        cleanup = _archive_and_remove(primary_manifest_path.parent, primary_snapshot)
        manifest["seed606_primary_cleanup"] = cleanup
        manifest["primary_snapshot_index_path"] = str(primary_snapshot / "snapshot_index.json")
        _write_json(evidence_root / "manifest.json", manifest)
        _write_report(evidence_root / "report.md", manifest)
        print(json.dumps({
            "status": manifest["status"],
            "evidence_root": str(evidence_root),
            "snapshot_index": manifest["snapshot_index_path"],
            "seed606_snapshot": manifest["primary_snapshot_index_path"],
            "project_used_bytes_after": _project_allocated_bytes(project_root),
        }, ensure_ascii=False))
        return 0
    except Exception as exc:  # preserve and archive generated audit records on a failed gate
        manifest["status"] = "NOT_EVALUABLE"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        try:
            if evidence_root.is_dir():
                _write_json(evidence_root / "manifest.json", manifest)
                _write_report(evidence_root / "report.md", manifest)
            if scratch_root.is_dir() and not scratch_archived:
                index = _archive_snapshot(scratch_root, evidence_root / "experiment_snapshot")
                manifest["scratch_snapshot"] = index
                manifest["snapshot_index_path"] = str(evidence_root / "experiment_snapshot/snapshot_index.json")
                manifest["scratch_cleanup_status"] = "archived_and_removed"
                shutil.rmtree(scratch_root)
                scratch_archived = True
                if evidence_root.is_dir():
                    _write_json(evidence_root / "manifest.json", manifest)
                    _write_report(evidence_root / "report.md", manifest)
        except Exception as archive_error:
            manifest["archive_error"] = f"{type(archive_error).__name__}: {archive_error}"
            if evidence_root.is_dir():
                _write_json(evidence_root / "manifest.json", manifest)
                _write_report(evidence_root / "report.md", manifest)
        print(json.dumps({
            "status": manifest["status"],
            "error": manifest.get("error"),
            "archive_error": manifest.get("archive_error"),
            "evidence_root": str(evidence_root),
            "scratch_root_retained": scratch_root.exists(),
        }, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
