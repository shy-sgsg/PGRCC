#!/usr/bin/env python3
"""Rebuild compact R21 audit tables and figures from retained CUDA text evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence
from xml.sax.saxutils import escape
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_delay_stage1_formal as stage1  # noqa: E402
from scripts.phase_r import r20_harmonized_task_review as r20  # noqa: E402
from scripts.phase_r import run_r21_cuda_pilot as runner  # noqa: E402
from scripts.phase_r import r21_historical_task_review as review  # noqa: E402

DEFAULT_ARCHIVE = ROOT / "docs/research/phase_r/R21/evidence/cuda_pilot/r21_cuda_text_audit_evidence_20261027.zip"
SEED_RUNS = {
    1001: ("seed1001_gatefixed_6f261b2", "seed1001_evaluation_replay_0957f49"),
    1002: ("seed1002_0957f49", "seed1002_0957f49"),
}
METHOD_PAIRS = (("M1", "Known"), ("M1", "D3_R9_fixed"), ("M1", "D3_Huber"))
PARAMETER_METRICS = (
    "angle_deg", "position_m", "range_m", "folded_doppler_hz",
    "radial_velocity_mps", "ground_speed_mps",
)
CANONICAL_TO_CONDITION = {
    "A0": "A0_Ideal",
    "B": "A1_Current_unknown_error",
    "Known": "A2_Known_error_correction_upper_bound",
    "D3_R9_fixed": "R21_D3_R9_Fixed",
    "D3_Huber": "R21_D3_Huber_B_ON",
    "M1": "R21_M1_Physics_Matched",
}
RUN_PREFIXES = (
    "cuda_pilot/seed1001_gatefixed_6f261b2/",
    "cuda_pilot/seed1001_evaluation_replay_0957f49/",
    "cuda_pilot/seed1002_0957f49/",
    "historical_replay_physical_range_audit/results/",
)


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _int(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number.is_integer() else None


def pairwise_common_success_rows(
    *,
    seed: int,
    metric: str,
    samples_by_method: Mapping[str, Sequence[Mapping[str, Any]]],
    pairs: Sequence[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Compute paired RMSE only on identical, valid target opportunity keys."""

    rows: list[dict[str, Any]] = []
    for method, reference in pairs:
        left = list(samples_by_method.get(method, ()))
        right = list(samples_by_method.get(reference, ()))
        common = r20.common_success_keys({method: left, reference: right})
        left_rmse = r20.rmse_from_samples([row for row in left if row.get("sample_key") in common])
        right_rmse = r20.rmse_from_samples([row for row in right if row.get("sample_key") in common])
        left_value, right_value = left_rmse.get("rmse"), right_rmse.get("rmse")
        rows.append({
            "seed": seed,
            "metric": metric,
            "method": method,
            "reference": reference,
            "status": "EVALUABLE_DESCRIPTIVE_COMMON_SUCCESS" if common else "NOT_EVALUABLE_NO_COMMON_SUCCESS",
            "method_success_count": len(left),
            "reference_success_count": len(right),
            "common_success_opportunities": len(common),
            "method_rmse_common": left_value,
            "reference_rmse_common": right_value,
            "rmse_difference_method_minus_reference": (
                left_value - right_value if left_value is not None and right_value is not None else None
            ),
            "common_sample_keys": json.dumps(sorted(common), ensure_ascii=False),
        })
    return rows


def ground_speed_parameter_samples(
    *,
    truths: Sequence[Mapping[str, Any]],
    associations: Sequence[Mapping[str, Any]],
    observations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return ground-speed errors only for one current confirmed Production payload."""

    association_by_key = {str(row.get("sample_key", "")): row for row in associations}
    samples: list[dict[str, Any]] = []
    for truth in truths:
        if not r20._visible_truth(truth):
            continue
        period, beam = _int(truth.get("period_id")), _int(truth.get("beam_id"))
        target = str(truth.get("target_id", "")).strip()
        if period is None or beam is None or not target:
            continue
        sample_key = f"{target}/{period}/{beam}"
        association = association_by_key.get(sample_key)
        if association is None or not _truthy(association.get("matched")):
            continue
        det_id = str(association.get("det_id", "")).strip()
        linked = [row for row in observations
                  if _truthy(row.get("matched_this_frame"))
                  and _truthy(row.get("output_payload"))
                  and _int(row.get("period_id")) == period
                  and _int(row.get("beam_id")) == beam
                  and str(row.get("det_id", "")).strip() == det_id]
        confirmed = [row for row in linked if str(row.get("state", "")).strip().lower() == "confirmed"]
        duplicate = any((_int(row.get("payload_rows_for_key")) or 0) > 1 for row in linked)
        if duplicate or len(confirmed) != 1:
            continue
        speed = _number(confirmed[0].get("output_speed"))
        ve, vn = _number(truth.get("ve_mps")), _number(truth.get("vn_mps"))
        if speed is None or ve is None or vn is None:
            continue
        samples.append({"sample_key": sample_key, "error": speed - math.hypot(ve, vn)})
    return samples


def _csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _serialize(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _serialize(value) for key, value in row.items()})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_extract_evidence(archive: Path, destination: Path) -> None:
    with ZipFile(archive) as zf:
        selected = [info for info in zf.infolist() if info.filename.startswith(RUN_PREFIXES)]
        if not selected:
            raise ValueError("R21 audit archive does not contain the frozen seed evidence")
        for info in selected:
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe archive member: {info.filename}")
        if sum(info.file_size for info in selected) > 120_000_000:
            raise ValueError("selected R21 text evidence exceeds the 120 MB extraction limit")
        for info in selected:
            zf.extract(info, destination)


def _case_paths(extracted: Path, seed: int) -> tuple[Path, Path, Path]:
    run_name, evaluation_name = SEED_RUNS[seed]
    run_root = extracted / "cuda_pilot" / run_name
    case_dirs = sorted((run_root / "cases").glob("base__seed_*"))
    if len(case_dirs) != 1:
        raise ValueError(f"seed {seed} expected one case directory, found {len(case_dirs)}")
    return case_dirs[0], run_root, extracted / "cuda_pilot" / evaluation_name


def _evaluate_seed(extracted: Path, seed: int) -> dict[str, Any]:
    case_root, production_run_root, evaluation_run_root = _case_paths(extracted, seed)
    case = _json(case_root / "case_manifest.json")
    if not case:
        raise ValueError(f"missing case manifest for seed {seed}")
    bundles = runner._build_bundles(case_root, case)
    evaluated: dict[tuple[str, str], dict[str, Any]] = {}
    bundle_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    all_doppler: list[dict[str, Any]] = []
    h0_rows: list[dict[str, Any]] = []
    for bundle in bundles:
        key = (str(bundle["canonical_condition"]), str(bundle["role"]))
        bundle_by_key[key] = bundle
        range_audit = review._range_axis_audit(bundle)
        doppler_rows = []
        if bundle["role"] in {"ON", "TO"}:
            doppler_rows = [
                review._doppler_audit_row_v3(bundle, truth)
                for truth in bundle["truths"] if r20._visible_truth(truth)
            ]
            all_doppler.extend(doppler_rows)
        result = review._evaluate_bundle_v3(bundle, range_audit, doppler_rows)
        evaluated[key] = {**result, "range_axis_audit": range_audit, "doppler_rows": doppler_rows}
        if bundle["role"] == "OFF":
            h0_rows.extend(r20._off_h0_rows(bundle))
    return {
        "seed": seed,
        "case_root": case_root,
        "production_run_root": production_run_root,
        "evaluation_run_root": evaluation_run_root,
        "case": case,
        "bundles": bundle_by_key,
        "evaluations": evaluated,
        "doppler_rows": all_doppler,
        "h0_rows": h0_rows,
        "outer_manifest": _json(production_run_root / "manifest.json"),
        "evaluation_manifest": _json(evaluation_run_root / "manifest.json"),
        "auxiliary": _json(evaluation_run_root / "evaluation/r21_condition_auxiliary_metrics.json"),
    }


def _f2_closure_rows(seed_data: Mapping[str, Any]) -> list[dict[str, Any]]:
    seed = seed_data["seed"]
    case = seed_data["case"]
    evaluation_root = Path(seed_data["evaluation_run_root"]) / "evaluation"
    rows: list[dict[str, Any]] = []
    for row in _csv_rows(evaluation_root / "r21_corrected_input_closure.csv"):
        channels = json.loads(row.get("channels") or "{}")
        f1, f2 = channels.get("F1", {}), channels.get("F2", {})
        raw_condition = str(row.get("condition", ""))
        canonical = runner.CANONICAL_CONDITIONS.get(raw_condition, raw_condition)
        rows.append({
            "seed": seed, "condition": raw_condition, "canonical_condition": canonical,
            "role": row.get("role"), "reference": "A0 fused F1/F2 input",
            "status": row.get("status"), "source": "saved evaluator closure result",
            "f1_relative_rms": f1.get("relative_rms_error"),
            "f1_phase_rms_rad": f1.get("phase_rms_rad"),
            "f1_sample_count": f1.get("sample_count"),
            "f2_relative_rms": f2.get("relative_rms_error"),
            "f2_phase_rms_rad": f2.get("phase_rms_rad"),
            "f2_sample_count": f2.get("sample_count"),
            "closure_tolerance_relative_rms": row.get("tolerance_relative_rms"),
            "strict_known_closure_passed": row.get("status") == "passed",
        })

    for role in runner.ROLES:
        reference = case["conditions"]["A0_Ideal"]["input_preparation"][role].get("period_paths", [])
        candidate = case["conditions"]["A1_Current_unknown_error"]["input_preparation"][role].get("period_paths", [])
        if reference and candidate and all(Path(value).is_file() for value in (*reference, *candidate)):
            audit = stage1.audit_fused_input_closure(
                [Path(value) for value in reference], [Path(value) for value in candidate],
                case["protocol_layout"],
            )
            channels = audit.get("channels", {})
            f1, f2 = channels.get("F1", {}), channels.get("F2", {})
            rows.append({
                "seed": seed, "condition": "A1_Current_unknown_error",
                "canonical_condition": "B", "role": role, "reference": "paired A0 fused F1/F2 input",
                "status": "DISTANCE_RECORDED" if audit.get("status") in {"passed", "failed"} else audit.get("status"),
                "source": "recomputed from retained Stage1 protocol IQ",
                "f1_relative_rms": f1.get("relative_rms_error"),
                "f1_phase_rms_rad": f1.get("phase_rms_rad"),
                "f1_sample_count": f1.get("sample_count"),
                "f2_relative_rms": f2.get("relative_rms_error"),
                "f2_phase_rms_rad": f2.get("phase_rms_rad"),
                "f2_sample_count": f2.get("sample_count"),
                "closure_tolerance_relative_rms": 1.0e-3,
                "strict_known_closure_passed": False,
                "distance_status": audit.get("status"),
                "reason": audit.get("reason"),
            })
        else:
            rows.append({
                "seed": seed, "condition": "A1_Current_unknown_error", "canonical_condition": "B",
                "role": role, "reference": "paired A0 fused F1/F2 input",
                "status": "NOT_RECOMPUTED_RAW_INPUT_UNAVAILABLE",
                "source": "frozen text audit package only",
                "reason": "the Stage1 protocol IQ period files are not available at their recorded paths",
            })
    return rows


def _parameter_samples(evaluation: Mapping[str, Any], bundle: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    target = evaluation.get("target_result") or {}
    result = {key: list(value) for key, value in target.get("parameter_samples", {}).items()}
    result["ground_speed_mps"] = ground_speed_parameter_samples(
        truths=bundle.get("truths", []),
        associations=target.get("associations", []),
        observations=(bundle.get("track") or {}).get("observations", []),
    ) if bundle.get("role") == "ON" else []
    return result


def _condition_identity_rows(seed_data: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    case = seed_data["case"]
    for (method, role), bundle in sorted(seed_data["bundles"].items()):
        condition = CANONICAL_TO_CONDITION[method]
        condition_record = case.get("conditions", {}).get(condition, {})
        production_record = (condition_record.get("production") or {}).get(role, {})
        branch_dir = Path(bundle["branch_dir"])
        branch_manifest_path = branch_dir / "branch_manifest.json"
        runtime_xml = branch_dir / "production.xml"
        log = branch_dir / "gmt_pipe_core.log"
        prep = (condition_record.get("input_preparation") or {}).get(role, {})
        period_ids = production_record.get("period_paths", [])
        prep_inputs = prep.get("raw_input_identities", [])
        rows.append({
            "seed": seed_data["seed"], "condition": condition, "canonical_condition": method,
            "role": role, "branch_status": production_record.get("status"),
            "production_exit_code": production_record.get("production_returncode"),
            "source_commit": seed_data["outer_manifest"].get("source_identity_after", {}).get("git", {}).get("commit"),
            "production_binary_sha256": (seed_data["outer_manifest"].get("production_binary") or {}).get("sha256"),
            "runtime_xml_sha256": _sha256(runtime_xml) if runtime_xml.is_file() else None,
            "branch_manifest_sha256": _sha256(branch_manifest_path) if branch_manifest_path.is_file() else None,
            "production_log_sha256": _sha256(log) if log.is_file() else None,
            "period_input_identities": [
                {k: item.get(k) for k in ("period_id", "path", "bytes", "sha256")}
                for item in period_ids if isinstance(item, Mapping)
            ],
            "estimator_source_input_identities": [
                {k: item.get(k) for k in ("path", "bytes", "sha256")}
                for item in prep_inputs if isinstance(item, Mapping)
            ],
            "truth_used_in_estimator": prep.get("truth_used_in_estimator", False),
            "truth_used_to_apply_correction": prep.get("truth_used_to_apply_correction", False),
        })
    return rows


def _method_contract_rows() -> list[dict[str, Any]]:
    return [
        {"method": "A0", "input_role": "Ideal/No-error reference", "available_channels": "protocol C1-C4 fused to F1/F2", "required_prior": "none", "uses_truth": False, "uses_A0": True, "uses_Known": False, "uses_paired_OFF": False, "uses_future": False, "online_deployability": "reference only", "estimated_parameter": "none", "correction_operator": "none"},
        {"method": "B", "input_role": "Current +4 ns", "available_channels": "protocol C1-C4 fused to F1/F2", "required_prior": "none", "uses_truth": False, "uses_A0": False, "uses_Known": False, "uses_paired_OFF": False, "uses_future": False, "online_deployability": "production baseline", "estimated_parameter": "none", "correction_operator": "none"},
        {"method": "Known", "input_role": "B input with evaluator-known correction", "available_channels": "B protocol IQ; truth used only by correction adapter", "required_prior": "+4 ns evaluator-only", "uses_truth": True, "uses_A0": False, "uses_Known": False, "uses_paired_OFF": False, "uses_future": False, "online_deployability": "upper-bound reference, not blind", "estimated_parameter": "none; fixed by evaluator", "correction_operator": "rewrite_float32_protocol_delay"},
        {"method": "D3_R9_fixed", "input_role": "B input with frozen R9 value", "available_channels": "same-seed B input; fixed value 3.4099650318669545 ns", "required_prior": "R9 frozen calibration", "uses_truth": False, "uses_A0": False, "uses_Known": False, "uses_paired_OFF": False, "uses_future": False, "online_deployability": "fixed calibration; not same-input estimate", "estimated_parameter": "none at R21 inference", "correction_operator": "rewrite_float32_protocol_delay"},
        {"method": "D3_Huber", "input_role": "same-seed B ON raw C1/C2 estimate applied to B ON/OFF/TO", "available_channels": "B ON C1/C2 and B geometry", "required_prior": "Huber weighted LS phase-slope model", "uses_truth": False, "uses_A0": False, "uses_Known": False, "uses_paired_OFF": False, "uses_future": False, "online_deployability": "blind estimate; production path executed", "estimated_parameter": "channel delay", "correction_operator": "rewrite_float32_protocol_delay"},
        {"method": "M1", "input_role": "same-seed B ON raw C1/C2 estimate applied to B ON/OFF/TO", "available_channels": "B ON C1/C2 and B Production geometry", "required_prior": "physics-matched delay model", "uses_truth": False, "uses_A0": False, "uses_Known": False, "uses_paired_OFF": False, "uses_future": False, "online_deployability": "blind estimate; production path executed", "estimated_parameter": "channel delay", "correction_operator": "rewrite_float32_protocol_delay"},
    ]


def _svg_text(x: float, y: float, value: Any, *, size: int = 14, anchor: str = "start", color: str = "#222") -> str:
    return f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-family="Arial,sans-serif" font-size="{size}" fill="{color}">{escape(str(value))}</text>'


def _bar_svg(path: Path, title: str, subtitle: str, categories: Sequence[str], series: Sequence[tuple[str, Sequence[float], str]], *, ymax: float, ylabel: str) -> None:
    width, height = 1080, 560
    left, top, plot_w, plot_h = 90, 120, 930, 340
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">', '<rect width="100%" height="100%" fill="#fff"/>']
    parts += [_svg_text(left, 38, title, size=24), _svg_text(left, 66, subtitle, size=14, color="#555")]
    parts.append(f'<path d="M {left} {top+plot_h} H {left+plot_w} M {left} {top} V {top+plot_h}" stroke="#333" fill="none"/>')
    ticks = 4
    for i in range(ticks + 1):
        value = ymax * i / ticks
        y = top + plot_h - plot_h * value / ymax
        parts.append(f'<path d="M {left} {y:.1f} H {left+plot_w}" stroke="#ddd"/>')
        parts.append(_svg_text(left - 12, y + 5, f"{value:.2g}", size=12, anchor="end", color="#555"))
    group_w = plot_w / max(1, len(categories))
    bar_group_w = group_w * 0.68
    bar_w = bar_group_w / max(1, len(series))
    for ci, category in enumerate(categories):
        x0 = left + ci * group_w + (group_w - bar_group_w) / 2
        for si, (name, values, color) in enumerate(series):
            value = values[ci] if ci < len(values) else 0
            x = x0 + si * bar_w
            bh = max(0, min(plot_h, plot_h * value / ymax))
            y = top + plot_h - bh
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w-3:.1f}" height="{bh:.1f}" fill="{color}"/>')
            parts.append(_svg_text(x + (bar_w-3)/2, max(top + 14, y - 6), f"{value:.3g}", size=11, anchor="middle"))
        parts.append(_svg_text(left + (ci + .5) * group_w, top + plot_h + 24, category, size=12, anchor="middle"))
    parts.append(f'<text transform="translate(24 {top+plot_h/2}) rotate(-90)" text-anchor="middle" font-family="Arial,sans-serif" font-size="13" fill="#333">{escape(ylabel)}</text>')
    legend_x = left
    for name, _, color in series:
        parts.append(f'<rect x="{legend_x}" y="{height-48}" width="14" height="14" fill="{color}"/>')
        parts.append(_svg_text(legend_x+20, height-36, name, size=12))
        legend_x += 190
    parts.append('</svg>')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")


def _scatter_delay_svg(path: Path, delay_rows: Sequence[Mapping[str, Any]], closure_rows: Sequence[Mapping[str, Any]]) -> None:
    width, height = 1080, 590
    left, top, pw, ph = 100, 120, 820, 360
    y_min, y_max = 1e-5, .5
    rows: list[tuple[str, int, float, float]] = []
    by_seed_condition = {(int(r["seed"]), r["canonical_condition"]): r for r in delay_rows}
    closure = {(int(r["seed"]), r["canonical_condition"], r["role"]): r for r in closure_rows}
    for (seed, method), d in by_seed_condition.items():
        c = closure.get((seed, method, "ON"))
        if c is None or _number(c.get("f2_relative_rms")) is None:
            continue
        residual = _number(d.get("residual_delay_ns"))
        f2 = _number(c.get("f2_relative_rms"))
        if residual is not None and residual >= 0 and f2 is not None and f2 > 0:
            rows.append((method, seed, residual, f2))
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">','<rect width="100%" height="100%" fill="#fff"/>',_svg_text(left,38,"R21 delay residual and F2 input closure",size=24),_svg_text(left,66,"Log y-axis; ON role, per seed. B is uncorrected; Known is evaluator-only.",size=14,color="#555")]
    parts.append(f'<path d="M {left} {top+ph} H {left+pw} M {left} {top} V {top+ph}" stroke="#333" fill="none"/>')
    for i in range(5):
        xval=i*.2; x=left+pw*xval/0.8
        parts.append(f'<path d="M {x:.1f} {top} V {top+ph}" stroke="#eee"/>')
        parts.append(_svg_text(x,top+ph+24,f"{xval:.1f}",size=12,anchor="middle",color="#555"))
    ticks=[1e-4,1e-3,1e-2,1e-1]
    for value in ticks:
        frac=(math.log10(value)-math.log10(y_min))/(math.log10(y_max)-math.log10(y_min))
        y=top+ph-ph*frac
        parts.append(f'<path d="M {left} {y:.1f} H {left+pw}" stroke="#ddd"/>')
        parts.append(_svg_text(left-12,y+5,f"{value:g}",size=12,anchor="end",color="#555"))
    palette={"B":"#9c4f00","Known":"#555555","D3_R9_fixed":"#0072b2","D3_Huber":"#cc79a7","M1":"#d55e00"}
    offsets={1001:-5,1002:5}
    for method,seed,xval,yval in rows:
        xf=left+pw*xval/0.8; yf=top+ph-ph*(math.log10(yval)-math.log10(y_min))/(math.log10(y_max)-math.log10(y_min))+offsets.get(seed,0)
        color=palette.get(method,"#666")
        parts.append(f'<circle cx="{xf:.1f}" cy="{yf:.1f}" r="6" fill="{color}" stroke="#222" stroke-width="1"/>')
        parts.append(_svg_text(xf+9,yf-8,f"{method} s{seed}: {yval:.3g}",size=10,color=color))
    parts.append(_svg_text(left+pw/2,top+ph+52,"Absolute residual delay (ns)",size=13,anchor="middle"))
    parts.append(f'<text transform="translate(24 {top+ph/2}) rotate(-90)" text-anchor="middle" font-family="Arial,sans-serif" font-size="13" fill="#333">F2 relative RMS</text>')
    parts.append('</svg>')
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text("\n".join(parts),encoding="utf-8")


def _common_parameter_svg(path: Path, pair_rows: Sequence[Mapping[str, Any]]) -> None:
    width, height = 1160, 640
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">','<rect width="100%" height="100%" fill="#fff"/>',_svg_text(55,36,"M1 versus deterministic references on common successful target periods",size=21),_svg_text(55,62,"Per-seed RMSE; small descriptive samples. Lower is better.",size=13,color="#555")]
    panels=[("angle_deg","Angle RMSE (deg)",80,100,470,420),("position_m","Position RMSE (m)",620,100,470,420)]
    colors={"Known":"#666666","D3_R9_fixed":"#0072b2","D3_Huber":"#cc79a7"}
    for metric,label,x,y,w,h in panels:
        rows=[r for r in pair_rows if r["metric"]==metric and r["status"]!="NOT_EVALUABLE_NO_COMMON_SUCCESS"]
        cap=max([_number(r.get(k)) or 0 for r in rows for k in ("method_rmse_common","reference_rmse_common")]+[1e-6])*1.12
        parts += [_svg_text(x,y-18,label,size=15),f'<path d="M {x} {y+h} H {x+w} M {x} {y} V {y+h}" stroke="#333" fill="none"/>']
        for i in range(4):
            val=cap*i/3; py=y+h-h*val/cap
            parts.append(f'<path d="M {x} {py:.1f} H {x+w}" stroke="#ddd"/>');parts.append(_svg_text(x-8,py+4,f"{val:.2g}",size=10,anchor="end",color="#555"))
        categories=[]
        for seed in sorted({int(r["seed"]) for r in rows}):
            for ref in ("Known","D3_R9_fixed","D3_Huber"):
                match=next((r for r in rows if int(r["seed"])==seed and r["reference"]==ref),None)
                if match:categories.append((seed,ref,match))
        gw=w/max(1,len(categories))
        for j,(seed,ref,r) in enumerate(categories):
            cx=x+(j+.5)*gw
            m=_number(r.get("method_rmse_common"));rv=_number(r.get("reference_rmse_common"))
            for idx,(val,color) in enumerate(((m,"#d55e00"),(rv,colors[ref]))):
                if val is None:continue
                bw=gw*.28; bx=cx+(idx-.5)*bw; bh=h*val/cap;by=y+h-bh
                parts.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw-2:.1f}" height="{bh:.1f}" fill="{color}"/>')
                parts.append(_svg_text(bx+bw/2,by-4,f"{val:.3g}",size=9,anchor="middle"))
            parts.append(_svg_text(cx,y+h+22,f"s{seed} {ref}",size=9,anchor="middle"))
            parts.append(_svg_text(cx,y+h+37,f"n={r['common_success_opportunities']}",size=9,anchor="middle",color="#555"))
    parts += ['<rect x="390" y="596" width="13" height="13" fill="#d55e00"/>',_svg_text(410,607,"M1",size=11),'<rect x="475" y="596" width="13" height="13" fill="#666"/>',_svg_text(495,607,"reference method",size=11)]
    parts.append('</svg>');path.parent.mkdir(parents=True,exist_ok=True);path.write_text("\n".join(parts),encoding="utf-8")


def _track_svg(path: Path, track_rows: Sequence[Mapping[str, Any]]) -> None:
    methods=["A0","B","Known","D3_R9_fixed","D3_Huber","M1"]
    sums={m:{"target_confirmed_unique_period_count":0,"false_confirmed_track_count":0} for m in methods}
    for row in track_rows:
        m=str(row.get("canonical_condition"))
        if m not in sums:continue
        for key in sums[m]:sums[m][key]+=_int(row.get(key)) or 0
    categories=methods
    first=[sums[m]["target_confirmed_unique_period_count"] for m in methods]
    second=[sums[m]["false_confirmed_track_count"] for m in methods]
    _bar_svg(path,"Production TrackManager target and false-confirmed track counts","Sum across two seeds; target matched periods and never-target-matched confirmed IDs are distinct counts.",categories,[
        ("target-matched periods",first,"#0072b2"),("never-target-matched tracks",second,"#d55e00")
    ],ymax=max(first+second+[1])*1.15,ylabel="Count across two seed runs")


def _failure_chain_svg(path: Path, causal_rows: Sequence[Mapping[str, Any]], closure_rows: Sequence[Mapping[str, Any]]) -> None:
    by_seed={int(r["seed"]):r for r in causal_rows if r["canonical_condition"]=="Known"}
    b_f2={int(r["seed"]):r for r in closure_rows if r["canonical_condition"]=="B" and r["role"]=="ON"}
    k_f2={int(r["seed"]):r for r in closure_rows if r["canonical_condition"]=="Known" and r["role"]=="ON"}
    lines=[
        f"B(+4 ns): ON F2 RMS {(_number(b_f2.get(s,{}).get('f2_relative_rms')) or float('nan')):.3f}; hits {by_seed[s]['b_hits']}/3 vs A0 {by_seed[s]['a0_hits']}/3"
        for s in sorted(by_seed)
    ]
    lines += [
        f"Known: ON F2 RMS {(_number(k_f2.get(s,{}).get('f2_relative_rms')) or float('nan')):.2g}; hits {by_seed[s]['known_hits']}/3"
        for s in sorted(by_seed)
    ]
    w,h=1080,490
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">','<rect width="100%" height="100%" fill="#fff"/>',_svg_text(45,36,"Observed R21 causal chain (single frozen geometry and two seeds)",size=21)]
    boxes=[(40,105,190,110,"A0 / B / Known","paired input intervention"),(295,105,210,110,"F1/F2 input","B mismatch; Known closes"),(570,105,200,110,"CSI / CFAR","SCNR proxy varies by seed"),(835,105,205,110,"Detection","B below A0; Known returns")]
    for x,y,bw,bh,title,body in boxes:
        parts.append(f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="4" fill="#f7f8fa" stroke="#444"/>')
        parts.append(_svg_text(x+12,y+28,title,size=15));parts.append(_svg_text(x+12,y+53,body,size=12,color="#444"))
    for x1,x2 in ((230,295),(505,570),(770,835)):
        parts.append(f'<path d="M {x1} 160 H {x2-8} l -8 -5 m 8 5 l -8 5" fill="none" stroke="#555" stroke-width="2"/>')
    parts.append(_svg_text(45,265,"Input closure and correct target hits",size=16))
    for i,line in enumerate(lines):parts.append(_svg_text(60,296+i*25,line,size=13,color="#333"))
    parts.append(_svg_text(45,405,"Blind estimators recover hit counts in this pilot, but do not close to Known.",size=14))
    parts.append(_svg_text(45,432,"Output SCNR proxy and adaptive CSI transfer do not explain the detection pattern consistently.",size=13,color="#555"))
    parts.append(_svg_text(45,459,"Causal interpretation is limited to this controlled +4 ns scenario; two seeds are feasibility evidence only.",size=12,color="#555"))
    parts.append('</svg>');path.parent.mkdir(parents=True,exist_ok=True);path.write_text("\n".join(parts),encoding="utf-8")


def _manifest_extract(archive: Path, extracted: Path, relative_path: str) -> dict[str, Any]:
    path = extracted / relative_path
    return _json(path)


def build_evidence(archive: Path, results_dir: Path, figures_dir: Path) -> dict[str, Any]:
    archive = archive.resolve()
    if not archive.is_file():
        raise FileNotFoundError(archive)
    results_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pgrcc-r21-finalize-") as temp:
        extracted = Path(temp)
        _safe_extract_evidence(archive, extracted)
        seed_data = {seed: _evaluate_seed(extracted, seed) for seed in SEED_RUNS}

        task_rows: list[dict[str, Any]] = []
        opportunities: list[dict[str, Any]] = []
        doppler_rows: list[dict[str, Any]] = []
        parameter_rows: list[dict[str, Any]] = []
        pairwise_rows: list[dict[str, Any]] = []
        track_rows: list[dict[str, Any]] = []
        false_rows: list[dict[str, Any]] = []
        target_rows: list[dict[str, Any]] = []
        h0_rows: list[dict[str, Any]] = []
        identity_rows: list[dict[str, Any]] = []
        delay_rows: list[dict[str, Any]] = []
        closure_rows: list[dict[str, Any]] = []
        causal_rows: list[dict[str, Any]] = []
        method_rows: list[dict[str, Any]] = []
        evaluability_rows: list[dict[str, Any]] = []
        experiment_seeds: list[dict[str, Any]] = []

        for seed, data in seed_data.items():
            case = data["case"]
            aux = data["auxiliary"]
            seed_eval_rows: dict[str, dict[str, Any]] = {}
            sample_sets: dict[str, dict[str, list[dict[str, Any]]]] = {}
            for (method, role), bundle in sorted(data["bundles"].items()):
                evaluation = data["evaluations"][(method, role)]
                target_result = evaluation.get("target_result") or {}
                metrics = target_result.get("metrics", {})
                sample_map = _parameter_samples(evaluation, bundle)
                if role == "ON":
                    sample_sets[method] = sample_map
                    seed_eval_rows[method] = evaluation
                    for metric in PARAMETER_METRICS:
                        samples = sample_map.get(metric, [])
                        rmse = r20.rmse_from_samples(samples)
                        raw = next((row for row in evaluation.get("parameter_records", []) if row.get("metric") == metric), {})
                        valid_count = len(samples)
                        missing_reasons = raw.get("missing_reasons", {})
                        if isinstance(missing_reasons, str):
                            try: missing_reasons = json.loads(missing_reasons)
                            except json.JSONDecodeError: missing_reasons = {}
                        if valid_count:
                            status = "EVALUABLE_DESCRIPTIVE_SMALL_SAMPLE"
                        elif metric == "radial_velocity_mps" and missing_reasons.get("ambiguity_unresolved"):
                            status = "NOT_EVALUABLE_AMBIGUITY_UNRESOLVED"
                        else:
                            status = "NOT_EVALUABLE_NO_VALID_PARAMETER_OPPORTUNITIES"
                        parameter_rows.append({
                            "seed": seed, "condition": CANONICAL_TO_CONDITION[method],
                            "canonical_condition": method, "role": role, "metric": metric,
                            "truth_opportunities": raw.get("truth_opportunities", metrics.get("truth_opportunities")),
                            "matched_detection_opportunities": raw.get("matched_detection_opportunities"),
                            "valid_parameter_opportunities": valid_count,
                            "missing_parameter_opportunities": (raw.get("truth_opportunities") or 0) - valid_count,
                            "missing_reasons": missing_reasons,
                            "own_success_rmse": rmse.get("rmse"), "own_success_count": valid_count,
                            "status": status,
                            "interpretation": "own-success RMSE; use r21_pairwise_common_success.csv for paired method comparison",
                        })
                        evaluability_rows.append({
                            "dataset": "R21", "seed": seed, "condition": method, "role": role,
                            "metric": metric, "value": rmse.get("rmse"),
                            "numerator": valid_count, "denominator": raw.get("truth_opportunities"),
                            "unit": metric, "status": status,
                            "reason": missing_reasons if valid_count == 0 else "own-success; small descriptive sample",
                            "evidence_source": "r21_task_evaluator_v3 replay from preserved Production text records",
                        })

                if role in {"ON", "TO"}:
                    association_map = {str(row.get("sample_key")): row for row in target_result.get("associations", [])}
                    sample_by_metric = {
                        metric: {str(row.get("sample_key")): row.get("error") for row in samples}
                        for metric, samples in sample_map.items()
                    }
                    for opportunity in target_result.get("per_opportunity_status", []):
                        key = f"{opportunity.get('target_id')}/{opportunity.get('period_id')}/{opportunity.get('beam_id')}"
                        association = association_map.get(key, {})
                        row = {
                            "seed": seed, "condition": CANONICAL_TO_CONDITION[method],
                            "canonical_condition": method, "role": role,
                            "period_id": opportunity.get("period_id"), "beam_id": opportunity.get("beam_id"),
                            "target_id": opportunity.get("target_id"), "status": opportunity.get("status"),
                            "reasons": opportunity.get("reasons"),
                            "matched": association.get("matched"), "det_id": association.get("det_id"),
                            "range_bin_audit": opportunity.get("range_bin_audit"),
                        }
                        for metric in PARAMETER_METRICS:
                            row[f"{metric}_error"] = sample_by_metric.get(metric, {}).get(key)
                        opportunities.append(row)

                if role == "ON":
                    track = evaluation.get("track_metrics") or {}
                    track_row = {
                        "dataset": "R21", "seed": seed, "condition": CANONICAL_TO_CONDITION[method],
                        "canonical_condition": method, "role": role, **track,
                    }
                    track_rows.append(track_row)
                    detections = bundle.get("detections", {}).get("rows", [])
                    false_rows.append({
                        "seed": seed, "condition": method, "role": role,
                        "final_detection_rows": len(detections),
                        "truth_matched_detection_rows": metrics.get("unique_hits"),
                        "truth_unmatched_final_detection_rows": metrics.get("truth_unmatched_detection_rows"),
                        "false_confirmed_track_ids_without_any_target_match": track.get("false_confirmed_track_ids_without_any_target_match"),
                        "unmatched_confirmed_payload_count": track.get("unmatched_confirmed_payload_count"),
                        "unmatched_payload_after_target_match_count": track.get("unmatched_payload_after_target_match_count"),
                        "mixed_track_ids": track.get("mixed_target_matched_and_unmatched_track_ids"),
                        "statistical_false_alarm_status": "NOT_EVALUABLE_FROM_TRACK_OR_DETECTION_COUNTS",
                    })
                    cfar_path = Path(bundle["branch_dir"]) / "result/debug/cfar_geometry_diagnostics.csv"
                    cfar = _csv_rows(cfar_path)
                    dynamic = [row for row in cfar if row.get("branch") == "union_dynamic_in_band"]
                    full = [row for row in cfar if row.get("branch") == "union_full_band"]
                    false_rows[-1].update({
                        "dynamic_union_hit_cut_count": sum(_int(row.get("hit_cut_count")) or 0 for row in dynamic),
                        "full_union_hit_cut_count": sum(_int(row.get("hit_cut_count")) or 0 for row in full),
                        "valid_cut_count_per_period": sorted({_int(row.get("valid_cut_count")) for row in dynamic if _int(row.get("valid_cut_count")) is not None}),
                        "cfar_false_cut_count_status": sorted({row.get("false_counts_status") for row in dynamic}),
                    })

                task_rows.append({
                    "seed": seed, "condition": CANONICAL_TO_CONDITION[method],
                    "canonical_condition": method, "role": role,
                    "status": evaluation.get("status"),
                    "truth_opportunities": metrics.get("truth_opportunities"),
                    "valid_physics_verified_opportunities": metrics.get("valid_physics_verified_truth_opportunities"),
                    "hits": metrics.get("unique_hits"), "misses": metrics.get("misses"),
                    "hit_fraction": metrics.get("truth_opportunity_hit_fraction"),
                    "invalid_truth_opportunities": metrics.get("invalid_truth_opportunities"),
                    "truth_unmatched_detection_rows": metrics.get("truth_unmatched_detection_rows"),
                    "production_path_passed": bool((case["conditions"][CANONICAL_TO_CONDITION[method]].get("production") or {}).get(role, {}).get("status") == "passed"),
                })
            doppler_rows.extend(data["doppler_rows"])
            h0_rows.extend(data["h0_rows"])
            identity_rows.extend(_condition_identity_rows(data))
            closure_rows.extend(_f2_closure_rows(data))

            for method, prep_condition in CANONICAL_TO_CONDITION.items():
                prep = case["conditions"][prep_condition].get("input_preparation", {}).get("ON", {})
                applied = _number(prep.get("applied_delay_ns"))
                if method == "B":
                    applied = 0.0
                residual = (4.0 - applied) if applied is not None and method != "A0" else (4.0 if method == "B" else None)
                delay_rows.append({
                    "seed": seed, "method": method, "canonical_condition": method,
                    "condition": prep_condition,
                    "status": prep.get("status", "REFERENCE" if method in {"A0", "B"} else "UNKNOWN"),
                    "applied_delay_ns": applied,
                    "true_delay_ns_evaluator_only": 4.0 if method == "Known" else None,
                    "residual_delay_ns": residual,
                    "correction_source": prep.get("correction_source"),
                    "truth_used_in_estimator": prep.get("truth_used_in_estimator", False),
                    "truth_used_to_apply_correction": prep.get("truth_used_to_apply_correction", False),
                })

            for method in CANONICAL_TO_CONDITION:
                ev = seed_eval_rows[method]
                metrics = (ev.get("target_result") or {}).get("metrics", {})
                track = ev.get("track_metrics") or {}
                params = {row["metric"]: row for row in parameter_rows if row["seed"] == seed and row["canonical_condition"] == method}
                by_role = {r["role"]: r for r in task_rows if r["seed"] == seed and r["canonical_condition"] == method}
                aux_method = aux.get(CANONICAL_TO_CONDITION[method], {})
                transfer = (aux_method.get("target_transfer") or {}).get("paired_complex_transfer", {})
                target_only = aux_method.get("target_transfer") or {}
                h0_csi = aux_method.get("h0_csi") or {}
                method_rows.append({
                    "seed": seed, "method": method,
                    "hits": metrics.get("unique_hits"), "opportunities": metrics.get("truth_opportunities"),
                    "hit_fraction": metrics.get("truth_opportunity_hit_fraction"),
                    "truth_unmatched_detection_rows": metrics.get("truth_unmatched_detection_rows"),
                    "false_confirmed_tracks": track.get("false_confirmed_track_count"),
                    "unmatched_confirmed_payloads": track.get("unmatched_confirmed_payload_count"),
                    "angle_rmse_own_success": params.get("angle_deg", {}).get("own_success_rmse"),
                    "angle_n": params.get("angle_deg", {}).get("own_success_count"),
                    "position_rmse_own_success_m": params.get("position_m", {}).get("own_success_rmse"),
                    "position_n": params.get("position_m", {}).get("own_success_count"),
                    "ground_speed_rmse_own_success_mps": params.get("ground_speed_mps", {}).get("own_success_rmse"),
                    "ground_speed_n": params.get("ground_speed_mps", {}).get("own_success_count"),
                    "target_only_csi_retention_db": target_only.get("target_only_csi_retention_db"),
                    "output_scnr_proxy_db": target_only.get("output_scnr_proxy_db"),
                    "h0_csi_cancellation_db": h0_csi.get("cancellation_db"),
                    "paired_complex_transfer_amplitude_db": transfer.get("amplitude_ratio_db"),
                    "paired_complex_transfer_phase_deg": transfer.get("phase_error_deg"),
                    "paired_complex_transfer_residual_rms": transfer.get("normalized_residual_rms"),
                    "status": "EVALUABLE_DESCRIPTIVE_TWO_SEED_PILOT",
                })

            a0 = seed_eval_rows["A0"]["target_result"]["metrics"]
            b = seed_eval_rows["B"]["target_result"]["metrics"]
            known = seed_eval_rows["Known"]["target_result"]["metrics"]
            for method in ("Known", "D3_R9_fixed", "D3_Huber", "M1"):
                evaluated_metrics = seed_eval_rows[method]["target_result"]["metrics"]
                recoverable = (known.get("unique_hits") - b.get("unique_hits")) if None not in (known.get("unique_hits"), b.get("unique_hits")) else None
                actual = (evaluated_metrics.get("unique_hits") - b.get("unique_hits")) if None not in (evaluated_metrics.get("unique_hits"), b.get("unique_hits")) else None
                ratio = actual / recoverable if recoverable not in (None, 0) and actual is not None else None
                a0_fraction, b_fraction = a0.get("truth_opportunity_hit_fraction"), b.get("truth_opportunity_hit_fraction")
                known_fraction, est_fraction = known.get("truth_opportunity_hit_fraction"), evaluated_metrics.get("truth_opportunity_hit_fraction")
                gain_fraction = est_fraction - b_fraction if est_fraction is not None and b_fraction is not None else None
                recoverable_fraction = known_fraction - b_fraction if known_fraction is not None and b_fraction is not None else None
                causal_rows.append({
                    "seed": seed, "canonical_condition": method,
                    "a0_hits": a0.get("unique_hits"), "b_hits": b.get("unique_hits"),
                    "known_hits": known.get("unique_hits"), "method_hits": evaluated_metrics.get("unique_hits"),
                    "a0_fraction": a0_fraction, "b_fraction": b_fraction,
                    "known_fraction": known_fraction, "method_fraction": est_fraction,
                    "recoverable_space_known_minus_b_hits": recoverable,
                    "actual_recovered_method_minus_b_hits": actual,
                    "recovery_ratio_count_scale": ratio,
                    "recoverable_space_known_minus_b_fraction": recoverable_fraction,
                    "actual_recovered_method_minus_b_fraction": gain_fraction,
                    "recovery_ratio_fraction_scale": gain_fraction / recoverable_fraction if recoverable_fraction not in (None, 0) and gain_fraction is not None else None,
                    "interpretation": "paired opportunity counts; two independent seeds only; ratios above 1 are descriptive and not a claim above Known physical bound",
                })

            area = data["outer_manifest"].get("stage2_area_clutter_audit", {})
            experiment_seeds.append({
                "seed": seed,
                "native_cuda_outer_status": data["outer_manifest"].get("status"),
                "evaluation_status": data["evaluation_manifest"].get("status"),
                "production_source_commit": (data["outer_manifest"].get("source_identity_after") or {}).get("git", {}).get("commit"),
                "production_binary_sha256": (data["outer_manifest"].get("production_binary") or {}).get("sha256"),
                "production_backend": (data["outer_manifest"].get("production_binary") or {}).get("backend"),
                "use_corex": (data["outer_manifest"].get("production_binary") or {}).get("use_corex"),
                "condition_count": len(case.get("conditions", {})),
                "production_branch_count": sum(len(x.get("production", {})) for x in case.get("conditions", {}).values()),
                "production_branches_passed": sum(
                    record.get("status") == "passed"
                    for condition in case.get("conditions", {}).values()
                    for record in (condition.get("production") or {}).values()
                ),
                "area_clutter_status": area.get("status"),
                "continuous_area_packets": area.get("continuous_area_packets"),
                "continuous_area_samples": area.get("continuous_area_samples"),
                "area_mean_power_measured": area.get("area_mean_power_measured"),
                "shared_background_reuse_mapping_passed": area.get("all_reuse_mappings_matched"),
                "runtime_xml_count": sum(1 for row in identity_rows if row["seed"] == seed and row.get("runtime_xml_sha256")),
            })

            for method in ("M1",):
                for reference in ("Known", "D3_R9_fixed", "D3_Huber"):
                    for metric in PARAMETER_METRICS:
                        pairwise_rows.extend(pairwise_common_success_rows(
                            seed=seed, metric=metric,
                            samples_by_method={
                                method: sample_sets.get(method, {}).get(metric, []),
                                reference: sample_sets.get(reference, {}).get(metric, []),
                            },
                            pairs=[(method, reference)],
                        ))

        # Keep the frozen historical evaluator regression as-is and append no rewritten values.
        historical_root = extracted / "historical_replay_physical_range_audit/results"
        historical_regression = _csv_rows(historical_root / "r21_evaluator_regression.csv")
        historical_tracks = _csv_rows(historical_root / "r21_track_semantics_audit.csv")
        historical_evaluability = _csv_rows(historical_root / "r21_metric_evaluability_audit.csv")
        historical_doppler = _csv_rows(historical_root / "r21_doppler_opportunity_audit.csv")
        historical_h0 = _csv_rows(historical_root / "r21_h0_descriptive_metrics.csv")
        for row in h0_rows:
            if row.get("empirical_pfa_status") or row.get("status"):
                evaluability_rows.append({
                    "dataset": "R21", "seed": row.get("seed"), "condition": row.get("condition"),
                    "role": row.get("role"), "metric": "h0_statistical_pfa",
                    "value": None, "numerator": row.get("observed_cut_hit_count"),
                    "denominator": row.get("valid_cut_count"), "unit": "probability",
                    "status": "NOT_EVALUABLE_NO_INDEPENDENT_H0_CUT_UNIT",
                    "reason": "spatially correlated CUTs do not form independent Pfa trials",
                    "evidence_source": "r21_h0_descriptive_metrics.csv",
                })

        # Final output bundle.
        output_tables = {
            "r21_evaluator_regression.csv": historical_regression,
            "r21_track_semantics_audit.csv": historical_tracks + track_rows,
            "r21_metric_evaluability_audit.csv": historical_evaluability + evaluability_rows,
            "r21_baseline_information_contract.csv": _method_contract_rows(),
            "r21_input_identity.csv": identity_rows,
            "r21_doppler_truth_mapping.csv": historical_doppler + doppler_rows,
            "r21_delay_estimates.csv": delay_rows,
            "r21_f2_physical_closure.csv": closure_rows,
            "r21_detection_opportunities.csv": opportunities,
            "r21_seed_level_causal_metrics.csv": causal_rows,
            "r21_parameter_evaluability.csv": parameter_rows,
            "r21_pairwise_common_success.csv": pairwise_rows,
            "r21_track_metrics.csv": track_rows,
            "r21_false_output_metrics.csv": false_rows,
            "r21_target_retention.csv": target_rows,
            "r21_h0_descriptive_metrics.csv": historical_h0 + h0_rows,
            "r21_method_comparison.csv": method_rows,
        }

        # Target transfer and H0 CSI summaries are sourced from the saved evaluator output JSON.
        target_rows.clear()
        for seed, data in seed_data.items():
            for method, condition in CANONICAL_TO_CONDITION.items():
                aux = data["auxiliary"].get(condition, {})
                transfer = (aux.get("target_transfer") or {}).get("paired_complex_transfer", {})
                target = aux.get("target_transfer") or {}
                h0 = aux.get("h0_csi") or {}
                target_rows.append({
                    "seed": seed, "method": method, "condition": condition,
                    "target_transfer_status": transfer.get("status"),
                    "target_transfer_sample_count": transfer.get("sample_count"),
                    "target_transfer_amplitude_ratio_db": transfer.get("amplitude_ratio_db"),
                    "target_transfer_phase_error_deg": transfer.get("phase_error_deg"),
                    "target_transfer_normalized_residual_rms": transfer.get("normalized_residual_rms"),
                    "target_only_csi_retention_db": target.get("target_only_csi_retention_db"),
                    "output_scnr_proxy_db": target.get("output_scnr_proxy_db"),
                    "h0_csi_status": h0.get("status"),
                    "h0_csi_cancellation_db": h0.get("cancellation_db"),
                    "interpretation": "descriptive; CSI is adaptive per role and transfer is not a fixed linear target gain",
                })
        for name, rows in output_tables.items():
            if name == "r21_target_retention.csv":
                rows = target_rows
            elif name == "r21_h0_descriptive_metrics.csv":
                rows = historical_h0 + h0_rows
            _write_csv(results_dir / name, rows)

        # Review figures are generated from these audited rows only.
        methods = ["A0", "B", "Known", "D3_R9_fixed", "D3_Huber", "M1"]
        by_seed_method = {(int(row["seed"]), row["method"]): row for row in method_rows}
        _bar_svg(
            figures_dir / "r21_detection_hits_by_seed.svg",
            "R21 correct target detections by method and seed",
            "Three target opportunities per independent seed; descriptive counts only.", methods,
            [(f"seed {seed}", [float(by_seed_method[(seed, m)].get("hit_fraction") or 0) for m in methods], color)
             for seed, color in ((1001, "#0072b2"), (1002, "#d55e00"))],
            ymax=1.0, ylabel="Correct target hit fraction (hits / 3)",
        )
        recovery_rows = []
        for seed in sorted(SEED_RUNS):
            subset = [r for r in causal_rows if r["seed"] == seed]
            known = next(r for r in subset if r["canonical_condition"] == "Known")
            for method in ("Known", "D3_R9_fixed", "D3_Huber", "M1"):
                row = next(r for r in subset if r["canonical_condition"] == method)
                recovery_rows.append({"seed": seed, "method": method,
                                      "known_recoverable_hits": known["recoverable_space_known_minus_b_hits"],
                                      "method_recovered_hits": row["actual_recovered_method_minus_b_hits"]})
        recovery_categories = [f"s{seed} {method}" for seed in sorted(SEED_RUNS) for method in ("Known", "D3_R9_fixed", "D3_Huber", "M1")]
        _bar_svg(
            figures_dir / "r21_detection_recovery_space.svg",
            "Known recoverable space and observed method recovery",
            "Count-scale differences vs B(+4 ns); hit-count ratios above 1 are not an upper-bound claim.",
            recovery_categories,
            [("Known − B", [float(r["known_recoverable_hits"] or 0) for r in recovery_rows], "#666666"),
             ("Method − B", [float(r["method_recovered_hits"] or 0) for r in recovery_rows], "#d55e00")],
            ymax=3, ylabel="Recovered correct detections (period counts)",
        )
        _scatter_delay_svg(figures_dir / "r21_delay_error_vs_f2_closure.svg", delay_rows, closure_rows)
        _common_parameter_svg(figures_dir / "r21_common_success_angle_position.svg", pairwise_rows)
        _track_svg(figures_dir / "r21_track_true_false_outputs.svg", track_rows)
        _failure_chain_svg(figures_dir / "r21_failure_chain.svg", causal_rows, closure_rows)

        archive_hash = _sha256(archive)
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, text=True, capture_output=True).stdout.strip()
        status = subprocess.run(["git", "status", "--short", "--untracked-files=no"], cwd=ROOT, check=True, text=True, capture_output=True).stdout
        generated = sorted(results_dir.glob("*.csv")) + sorted(figures_dir.glob("*.svg"))
        experiment_manifest = {
            "schema_version": "r21-experiment-manifest-v1",
            "repository": "https://github.com/shy-sgsg/PGRCC",
            "branch": "codex/unknown-system-error-next-stage",
            "evidence_archive": str(archive.relative_to(ROOT)),
            "evidence_archive_sha256": archive_hash,
            "frozen_conditions": {"target_speed_mps": 6.7, "target_snr_db": 30.0, "delay_error_ns": 4.0, "target_periods_per_seed": 3},
            "methods": list(CANONICAL_TO_CONDITION),
            "seeds": experiment_seeds,
            "stage1_config_sha256": "ad47b0d02b05ddc4839da39a7498faf1af25ca3ba8eec678597c0ce7a0f651f6",
            "stage2_template_sha256": "1f4f9c8b9383614601dc019414b8c8a8788b195301526438b95a672b1ee13998",
            "production_binary_sha256": "9ffc232966bd92a39edfb39b90730851014dfa1ae7237a4ac172a764f31cc566",
            "evaluator": "r21-task-eval-v3",
            "status_labels": {"IMPLEMENTED": True, "EXECUTED": True, "EVALUABLE": True, "PERFORMANCE_VERIFIED": False},
            "scope_note": "re-evaluation uses retained text outputs; F2 current-input closure is recomputed only if Stage1 protocol IQ is present at its manifest path",
        }
        exp_path = results_dir / "r21_experiment_manifest.json"
        exp_path.write_text(json.dumps(experiment_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        evaluation_manifest = {
            "schema_version": "r21-evaluation-manifest-v1",
            "analysis_source_commit_at_generation": commit,
            "tracked_worktree_dirty_at_generation": bool(status.strip()),
            "analysis_script": str(Path(__file__).resolve().relative_to(ROOT)),
            "analysis_script_sha256": _sha256(Path(__file__).resolve()),
            "evaluator_version": "r21-task-eval-v3",
            "evidence_archive_sha256": archive_hash,
            "outputs": [{"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size, "sha256": _sha256(p)} for p in generated],
            "parameter_pairing": "same seed, same target_id/period_id/beam_id successfully matched by both compared methods",
            "sample_scope": "two independent seeds; per-seed counts and RMSE are descriptive; no statistical Pd/Pfa claim",
        }
        eval_path = results_dir / "r21_evaluation_manifest.json"
        eval_path.write_text(json.dumps(evaluation_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"experiment_manifest": experiment_manifest, "evaluation_manifest": evaluation_manifest,
                "output_csv_count": len(output_tables), "output_figure_count": 6}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-zip", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--results-dir", type=Path, default=ROOT / "docs/research/phase_r/R21/evidence/results")
    parser.add_argument("--figures-dir", type=Path, default=ROOT / "docs/research/phase_r/R21/evidence/figures")
    args = parser.parse_args(argv)
    result = build_evidence(args.evidence_zip, args.results_dir, args.figures_dir)
    print(json.dumps({"status": "completed", "output_csv_count": result["output_csv_count"],
                      "output_figure_count": result["output_figure_count"],
                      "results_dir": str(args.results_dir), "figures_dir": str(args.figures_dir)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
