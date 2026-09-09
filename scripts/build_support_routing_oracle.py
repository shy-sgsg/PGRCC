#!/usr/bin/env python3
"""Audit physical support routing between Current CSI and a bypass expert.

The router is deliberately non-neural: it uses background-only physical
features to form a hard binary or soft row-wise CSI weight.  Target truth is
used only for evaluation and policy selection of audit candidates; it is not a
routing feature and no future/oracle output is fed into the route.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_pgrcc_oracle as v10
import run_ai_csi_oracle as oracle
import run_baseline_benchmark as v1


def finite(value: object) -> float:
    return v10.finite(value)


def db(value: float) -> float:
    return v10.db(value)


def write_csv(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    v10.write_csv(path, rows)


def write_json(path: Path, value: object) -> None:
    v10.write_json(path, value)


def sigmoid(value: np.ndarray | float) -> np.ndarray | float:
    clipped = np.clip(np.asarray(value, dtype=float), -60.0, 60.0)
    result = 1.0 / (1.0 + np.exp(-clipped))
    if np.ndim(result) == 0:
        return float(result)
    return result


def build_cases(config: Mapping[str, object], limit: int | None) -> List[Dict[str, object]]:
    base = copy.deepcopy(config["base_case"])
    specs = list(config["support_cases"])
    if limit is not None:
        specs = specs[: int(limit)]
    cases: List[Dict[str, object]] = []
    for spec in specs:
        case = copy.deepcopy(base)
        case_id = str(spec["case_id"])
        case["case_id"] = case_id
        case["target_id"] = str(spec.get("target_id", f"{base.get('target_id', 'SUPPORT_TARGET')}_{case_id}"))
        case["seed"] = int(spec["seed"])
        case["label"] = str(spec.get("label", case_id))
        case["factor"] = str(spec.get("scenario", case_id))
        case["factor_family"] = str(spec.get("scenario", case_id))
        case["support_scenario"] = str(spec.get("scenario", case_id))
        requested: Dict[str, object] = {}
        for name, value in dict(spec.get("overrides", {})).items():
            v10.set_audit_value(case, str(name), value)
            requested[str(name)] = value
        case["audit_requested_values"] = requested
        case["audit_values"] = v10.audit_parameter_metadata(case, requested)
        case["use_paired_background"] = not bool(case.get("impairments", {}).get("enabled", False))
        cases.append(case)
    return cases


def routing_features(data: v1.CaseData, arrays: Mapping[str, object]) -> Dict[str, np.ndarray]:
    alignment = arrays["alignment"]
    assert isinstance(alignment, oracle.Alignment)
    support = np.asarray(data.current_support, dtype=bool)
    rows = np.arange(support.size, dtype=float)
    support_rows = np.flatnonzero(support)
    start, stop = int(support_rows[0]), int(support_rows[-1])
    signed_distance = np.where(
        support,
        np.minimum(rows - float(start), float(stop) - rows),
        -np.minimum(np.abs(rows - float(start)), np.abs(rows - float(stop))),
    )
    input_power = np.mean(np.abs(alignment.f1_bg) ** 2, axis=1)
    current_power = np.mean(np.abs(np.asarray(arrays["current_bg"]) ** 2), axis=1)
    scale = max(float(np.median(input_power)), 1.0e-300)
    residual_ratio_db = 10.0 * np.log10(np.maximum(current_power, 1.0e-300) / scale)
    return {
        "support": support,
        "coherence": np.nan_to_num(np.asarray(alignment.coherence, dtype=float), nan=0.0),
        "robust_confidence": np.nan_to_num(np.asarray(arrays["robust_confidence"], dtype=float), nan=0.0),
        "robust_condition": np.nan_to_num(np.asarray(arrays["robust_condition"], dtype=float), nan=0.0, posinf=1.0e12),
        "support_edge_distance_rows": signed_distance,
        "distance_to_clutter_ridge_hz": np.abs(np.asarray(data.axis, dtype=float) - float(data.fa_ctr)),
        "residual_ratio_dB": residual_ratio_db,
    }


def bypass_and_csi(
    data: v1.CaseData,
    arrays: Mapping[str, object],
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    alignment = arrays["alignment"]
    assert isinstance(alignment, oracle.Alignment)
    return (
        np.asarray(arrays["current_bg"]),
        np.asarray(arrays["current_target"]),
        alignment.f1_bg,
        alignment.f1_target,
    )


def route_map(csi: np.ndarray, bypass: np.ndarray, p_clutter: np.ndarray) -> np.ndarray:
    return p_clutter[:, None] * csi + (1.0 - p_clutter[:, None]) * bypass


def route_policy_candidates(
    features: Mapping[str, np.ndarray],
    config: Mapping[str, object],
) -> List[Tuple[str, str, np.ndarray, Dict[str, object]]]:
    support = features["support"]
    edge = features["support_edge_distance_rows"]
    confidence = features["robust_confidence"]
    coherence = features["coherence"]
    candidates: List[Tuple[str, str, np.ndarray, Dict[str, object]]] = []
    hard_cfg = config["hard_grid"]
    for guard in hard_cfg["edge_guard_rows"]:
        for conf_threshold in hard_cfg["robust_confidence_threshold"]:
            for coh_threshold in hard_cfg["coherence_threshold"]:
                p = (
                    support
                    & (edge >= float(guard))
                    & (confidence >= float(conf_threshold))
                    & (coherence >= float(coh_threshold))
                ).astype(float)
                metadata = {
                    "edge_guard_rows": float(guard),
                    "robust_confidence_threshold": float(conf_threshold),
                    "coherence_threshold": float(coh_threshold),
                    "route_formula": "Y=p_clutter*Y_CSI+(1-p_clutter)*Y_bypass",
                }
                candidates.append(("hard_binary", f"hard_g{guard}_c{conf_threshold}_coh{coh_threshold}", p, metadata))
    soft_cfg = config["soft_grid"]
    for center in soft_cfg["robust_confidence_center"]:
        for scale in soft_cfg["robust_confidence_scale"]:
            for guard in soft_cfg["edge_center_rows"]:
                for edge_scale in soft_cfg["edge_scale_rows"]:
                    confidence_weight = np.asarray(sigmoid((confidence - float(center)) / max(float(scale), 1.0e-6)))
                    edge_weight = np.asarray(sigmoid((edge - float(guard)) / max(float(edge_scale), 1.0e-6)))
                    p = support.astype(float) * confidence_weight * edge_weight
                    metadata = {
                        "robust_confidence_center": float(center),
                        "robust_confidence_scale": float(scale),
                        "edge_center_rows": float(guard),
                        "edge_scale_rows": float(edge_scale),
                        "route_formula": "Y=p_clutter*Y_CSI+(1-p_clutter)*Y_bypass",
                    }
                    candidates.append(("soft_blend", f"soft_c{center}_s{scale}_g{guard}_e{edge_scale}", p, metadata))
    return candidates


def exact_route_metrics(
    data: v1.CaseData,
    arrays: Mapping[str, object],
    bg: np.ndarray,
    target: np.ndarray,
    detector_bg: np.ndarray,
    detector_target: np.ndarray,
    p_clutter: np.ndarray,
) -> Dict[str, object]:
    alignment = arrays["alignment"]
    assert isinstance(alignment, oracle.Alignment)
    col_start = max(0, min(bg.shape[1], int(data.p.rg_st)))
    col_stop = max(col_start + 1, min(bg.shape[1], int(data.p.rg_ed) + 1))
    residual_power = np.abs(bg[:, col_start:col_stop]) ** 2
    input_power = np.abs(alignment.f1_bg[:, col_start:col_stop]) ** 2
    input_median = float(np.median(input_power))
    p95 = float(np.percentile(residual_power, 95.0))
    p99 = float(np.percentile(residual_power, 99.0))
    cvar = float(np.mean(residual_power[residual_power >= p95]))
    r0, r1 = max(0, data.truth_row - 2), min(bg.shape[0], data.truth_row + 3)
    c0, c1 = max(0, data.truth_col - 2), min(bg.shape[1], data.truth_col + 3)
    before_signal = alignment.f1_target[r0:r1, c0:c1] - alignment.f1_bg[r0:r1, c0:c1]
    after_signal = target[r0:r1, c0:c1] - bg[r0:r1, c0:c1]
    before_signal_power = float(np.mean(np.abs(before_signal) ** 2))
    after_signal_power = float(np.mean(np.abs(after_signal) ** 2))
    before_clutter = float(np.mean(np.abs(alignment.f1_bg[r0:r1, c0:c1]) ** 2))
    after_clutter = float(np.mean(np.abs(bg[r0:r1, c0:c1]) ** 2))
    target_result = oracle.go_cfar(detector_target, data.truth_row, data.truth_col, data.p)
    background_result = oracle.go_cfar(detector_bg, bg.shape[0] + 100, bg.shape[1] + 100, data.p)
    radius = int(data.p.cfar_guard + data.p.cfar_background)
    test_cells = float(bg.shape[0] * max(bg.shape[1] - 2 * radius, 0))
    threshold = v10.cfar_threshold_map(detector_target, data.p)
    threshold_roi = threshold[r0:r1, c0:c1]
    valid = np.isfinite(threshold_roi) & (threshold_roi > 0.0)
    if np.any(valid):
        margin = db(float(np.max(np.abs(detector_target[r0:r1, c0:c1][valid]) ** 2 / threshold_roi[valid])))
    else:
        margin = math.nan
    support_rows = np.flatnonzero(data.current_support)
    edge_distance = min(data.truth_row - int(support_rows[0]), int(support_rows[-1]) - data.truth_row)
    inside = bool(data.current_support[data.truth_row])
    fallback_fraction = float(np.mean(1.0 - p_clutter[support_rows])) if support_rows.size else math.nan
    return {
        "SCNR_improvement_dB": db(after_signal_power / max(after_clutter, 1.0e-300)) - db(before_signal_power / max(before_clutter, 1.0e-300)),
        "target_loss_dB": db(after_signal_power / max(before_signal_power, 1.0e-300)),
        "residual_p95_dB": db(p95 / max(input_median, 1.0e-300)),
        "residual_p99_dB": db(p99 / max(input_median, 1.0e-300)),
        "residual_cvar95_dB": db(cvar / max(input_median, 1.0e-300)),
        "background_Pfa": float(background_result["false_alarm_count"] / max(test_cells, 1.0)),
        "target_Pd": float(target_result["Pd"]),
        "target_cfar_margin_dB": margin,
        "target_peak_power": float(target_result["target_peak_power"]),
        "background_false_alarm_count": float(background_result["false_alarm_count"]),
        "support_edge_distance_rows": int(edge_distance),
        "target_inside_support": inside,
        "target_support_status": "inside" if inside else "outside",
        "support_edge_loss_dB": db(after_signal_power / max(before_signal_power, 1.0e-300)) if abs(edge_distance) <= 2 else math.nan,
        "fallback_fraction_inside_support": fallback_fraction,
        "csi_fraction_inside_support": 1.0 - fallback_fraction if math.isfinite(fallback_fraction) else math.nan,
    }


def delta_metrics(row: Mapping[str, object], current: Mapping[str, object]) -> Dict[str, float]:
    mapping = {
        "delta_scnr_dB": "SCNR_improvement_dB",
        "delta_target_loss_dB": "target_loss_dB",
        "delta_residual_p95_dB": "residual_p95_dB",
        "delta_residual_cvar95_dB": "residual_cvar95_dB",
        "delta_background_pfa": "background_Pfa",
        "delta_target_cfar_margin_dB": "target_cfar_margin_dB",
        "delta_target_Pd": "target_Pd",
    }
    output = {}
    for delta_name, metric in mapping.items():
        a, b = finite(row.get(metric)), finite(current.get(metric))
        output[delta_name] = a - b if math.isfinite(a) and math.isfinite(b) else math.nan
    return output


def support_meets_policy(row: Mapping[str, object], policy: Mapping[str, object]) -> bool:
    checks = (
        ("delta_scnr_dB", float(policy["min_delta_scnr_db"]), lambda a, b: a >= b),
        ("delta_target_loss_dB", float(policy["min_delta_target_loss_db"]), lambda a, b: a >= b),
        ("delta_residual_p95_dB", float(policy["max_delta_tail_p95_db"]), lambda a, b: a <= b),
        ("delta_residual_cvar95_dB", float(policy["max_delta_tail_cvar95_db"]), lambda a, b: a <= b),
        ("delta_background_pfa", float(policy["max_delta_background_pfa"]), lambda a, b: a <= b),
        ("delta_target_cfar_margin_dB", float(policy["min_delta_detection_margin_db"]), lambda a, b: a >= b),
    )
    return all(math.isfinite(finite(row.get(key))) and predicate(finite(row.get(key)), bound) for key, bound, predicate in checks)


def route_for_target_shift(
    data: v1.CaseData,
    arrays: Mapping[str, object],
    p_clutter: np.ndarray,
    mode: str,
    shift_rows: int,
) -> Tuple[np.ndarray, np.ndarray]:
    alignment = arrays["alignment"]
    assert isinstance(alignment, oracle.Alignment)
    signal_f1 = alignment.f1_target - alignment.f1_bg
    signal_f2 = alignment.f2_target - alignment.f2_bg
    target_f1 = alignment.f1_bg + np.roll(signal_f1, int(shift_rows), axis=0)
    target_f2 = alignment.f2_bg + np.roll(signal_f2, int(shift_rows), axis=0)
    if mode == "current_dynamic":
        target = oracle.current_cancel(target_f1, target_f2, np.asarray(arrays["p38_phase"]), data.current_support)
        detector = v1.dynamic_detector(target, target_f2, data.current_support)
        return target, detector
    target_csi = oracle.current_cancel(target_f1, target_f2, np.asarray(arrays["p38_phase"]), data.current_support)
    target = route_map(target_csi, target_f1, p_clutter)
    return target, target


def mdv_proxy_route(
    data: v1.CaseData,
    arrays: Mapping[str, object],
    p_clutter: np.ndarray,
    mode: str,
) -> Dict[str, float]:
    center = int(np.argmin(np.abs(data.axis - data.fa_ctr)))
    offsets = (0, 1, 2, 4, 6, 8, 12, 16, 24, 32)
    candidate_rows = {max(0, min(data.p.pulse_num - 1, center + offset)) for offset in offsets}
    candidate_rows.update(max(0, min(data.p.pulse_num - 1, center - offset)) for offset in offsets)
    ordered = sorted(candidate_rows, key=lambda row: abs(float(data.axis[row] - data.fa_ctr)))
    detected: List[Tuple[float, int]] = []
    for row in ordered:
        _, detector = route_for_target_shift(data, arrays, p_clutter, mode, int(row - data.truth_row))
        if oracle.go_cfar(detector, row, data.truth_col, data.p)["Pd"] >= 0.5:
            velocity = abs(float(data.axis[row] - data.fa_ctr)) * (oracle.C0 / data.p.fc_hz) / 2.0
            detected.append((velocity, row))
    if not detected:
        return {"MDV_mps": math.nan, "MDV_detected_row": math.nan, "MDV_sweep_points": float(len(ordered))}
    velocity, row = min(detected, key=lambda item: item[0])
    return {"MDV_mps": velocity, "MDV_detected_row": float(row), "MDV_sweep_points": float(len(ordered))}


def enrich_row(
    data: v1.CaseData,
    arrays: Mapping[str, object],
    row: Dict[str, object],
    current: Mapping[str, object],
    p_clutter: np.ndarray,
    method: str,
    route_type: str,
    metadata: Mapping[str, object],
) -> Dict[str, object]:
    row = dict(row)
    row.update(delta_metrics(row, current))
    row.update(metadata)
    row.update({
        "case_id": str(data.case["case_id"]),
        "seed": int(data.case["seed"]),
        "factor_family": str(data.case.get("factor_family", "unknown")),
        "support_scenario": str(data.case.get("support_scenario", "unknown")),
        "method": method,
        "route_type": route_type,
        "target_row_truth": int(data.truth_row),
        "target_fd_truth_hz": float(data.target_fd),
        "target_clutter_ridge_distance_hz": float(abs(data.axis[data.truth_row] - data.fa_ctr)),
        "target_inside_support": bool(data.current_support[data.truth_row]),
        "inference_features": "coherence,robust_confidence,robust_condition,support_edge_distance_rows,distance_to_clutter_ridge_hz,residual_ratio_dB,valid_sample_fraction",
    })
    return row


def choose_best(rows: Sequence[Mapping[str, object]], route_type: str, policy: Mapping[str, object]) -> Tuple[Dict[str, object], str]:
    candidates = [dict(row) for row in rows if str(row.get("route_type")) == route_type]
    feasible = [row for row in candidates if support_meets_policy(row, policy)]
    if feasible:
        best = max(feasible, key=lambda row: (finite(row.get("delta_scnr_dB")), finite(row.get("delta_target_Pd")), -finite(row.get("delta_residual_p95_dB"))))
        return best, "best_noninferior_gain"
    if not candidates:
        raise RuntimeError(f"no support routing candidates for {route_type}")
    best = max(candidates, key=lambda row: (finite(row.get("delta_scnr_dB")), finite(row.get("delta_target_Pd")), -finite(row.get("delta_residual_p95_dB"))))
    return best, "diagnostic_best_gain_no_noninferior_candidate"


def evaluate_case(
    data: v1.CaseData,
    method_config: Mapping[str, object],
    config: Mapping[str, object],
    policy: Mapping[str, object],
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    arrays = v10.current_and_expert_arrays(data, method_config)
    csi_bg, csi_target, bypass_bg, bypass_target = bypass_and_csi(data, arrays)
    current_dynamic = exact_route_metrics(
        data,
        arrays,
        csi_bg,
        csi_target,
        np.asarray(arrays["current_detector_bg"]),
        np.asarray(arrays["current_detector_target"]),
        np.asarray(data.current_support, dtype=float),
    )
    split_p = np.asarray(data.current_support, dtype=float)
    split_bg = route_map(csi_bg, bypass_bg, split_p)
    split_target = route_map(csi_target, bypass_target, split_p)
    split_metrics = exact_route_metrics(data, arrays, split_bg, split_target, split_bg, split_target, split_p)
    features = routing_features(data, arrays)
    detailed: List[Dict[str, object]] = []
    current_row = enrich_row(data, arrays, current_dynamic, current_dynamic, np.asarray(data.current_support, dtype=float), "Current dynamic", "baseline_current_dynamic", {})
    current_row["delta_scnr_dB"] = 0.0
    current_row["delta_target_loss_dB"] = 0.0
    current_row["delta_residual_p95_dB"] = 0.0
    current_row["delta_residual_cvar95_dB"] = 0.0
    current_row["delta_background_pfa"] = 0.0
    current_row["delta_target_cfar_margin_dB"] = 0.0
    current_row["delta_target_Pd"] = 0.0
    detailed.append(current_row)
    split_row = enrich_row(data, arrays, split_metrics, current_dynamic, split_p, "Current split", "baseline_current_split", {"split_guard_rows": 0.0})
    detailed.append(split_row)
    for route_type, name, p_clutter, metadata in route_policy_candidates(features, config):
        bg = route_map(csi_bg, bypass_bg, p_clutter)
        target = route_map(csi_target, bypass_target, p_clutter)
        metrics = exact_route_metrics(data, arrays, bg, target, bg, target, p_clutter)
        detailed.append(enrich_row(data, arrays, metrics, current_dynamic, p_clutter, name, route_type, metadata))

    best_hard, hard_reason = choose_best(detailed, "hard_binary", policy)
    best_soft, soft_reason = choose_best(detailed, "soft_blend", policy)
    selected: List[Dict[str, object]] = [current_row, split_row, dict(best_hard), dict(best_soft)]
    for row, reason in ((selected[2], hard_reason), (selected[3], soft_reason)):
        row["selection_reason"] = reason
    selected[0]["selection_reason"] = "fixed_current_dynamic"
    selected[1]["selection_reason"] = "fixed_current_split"
    for row in selected:
        p_clutter = split_p if row["route_type"] == "baseline_current_split" else next(
            (p for _, name, p, _ in route_policy_candidates(features, config) if name == row["method"]),
            split_p,
        )
        mode = "current_dynamic" if row["route_type"] == "baseline_current_dynamic" else "routed"
        row.update(mdv_proxy_route(data, arrays, p_clutter, mode))
        row["selected_summary"] = True
    summary = []
    for row in selected:
        summary.append(dict(row))
    return detailed, summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/research/support_routing_oracle.json"))
    parser.add_argument("--out", type=Path, default=Path("outputs/support_routing_oracle"))
    parser.add_argument("--case-limit", type=int, default=None)
    parser.add_argument("--keep-data", action="store_true")
    parser.add_argument("--allow-existing", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = (ROOT / args.config).resolve() if not args.config.is_absolute() else args.config.resolve()
    out = (ROOT / args.out).resolve() if not args.out.is_absolute() else args.out.resolve()
    if not config_path.exists():
        raise FileNotFoundError(config_path)
    if out.exists() and any(out.iterdir()) and not args.allow_existing:
        raise RuntimeError(f"refusing to overwrite non-empty support output: {out}")
    config = copy.deepcopy(json.loads(config_path.read_text(encoding="utf-8")))
    out.mkdir(parents=True, exist_ok=True)
    config["output_root"] = v10.repo_path(out / "cases")
    cases = build_cases(config, args.case_limit)
    base_config_path = (ROOT / str(config["base_config"])).resolve()
    method_config = dict(config.get("method_config", {}))
    policy = dict(config["worthwhile_policy"])
    detailed: List[Dict[str, object]] = []
    summary: List[Dict[str, object]] = []
    manifests: List[Dict[str, object]] = []
    failures: List[Dict[str, object]] = []
    started = time.perf_counter()
    command = ["python3", v10.repo_path(Path(__file__)), "--config", v10.repo_path(config_path), "--out", v10.repo_path(out)]
    if args.case_limit is not None:
        command += ["--case-limit", str(args.case_limit)]
    provenance = v10.source_provenance(command)
    for index, case in enumerate(cases, start=1):
        print(f"[support-oracle] case {index}/{len(cases)}: {case['case_id']}", flush=True)
        paths = None
        try:
            paths = v1.prepare_case_data(base_config_path, config, case, args.keep_data)
            data = v1.load_case_data(case, paths)
            case_detailed, case_summary = evaluate_case(data, method_config, config, policy)
            detailed.extend(case_detailed)
            summary.extend(case_summary)
            manifest_case = v10.case_manifest_row(data)
            manifest_case["support_scenario"] = case.get("support_scenario")
            manifest_case["target_support_status"] = "inside" if data.current_support[data.truth_row] else "outside"
            manifest_case["target_support_edge_distance_rows"] = min(
                data.truth_row - int(np.flatnonzero(data.current_support)[0]),
                int(np.flatnonzero(data.current_support)[-1]) - data.truth_row,
            )
            manifests.append(manifest_case)
            print(f"[support-oracle] completed {case['case_id']}: detailed={len(case_detailed)}", flush=True)
        except Exception as exc:
            failures.append({"case_id": str(case.get("case_id")), "error": repr(exc)})
            print(f"[support-oracle][ERR] {case.get('case_id')}: {exc}", file=sys.stderr, flush=True)
        finally:
            if paths is not None and not args.keep_data:
                v1.cleanup_case(paths)
    if not summary:
        raise RuntimeError(f"support routing produced no summary rows; failures={failures}")
    write_csv(out / "support_routing_metrics.csv", detailed)
    write_csv(out / "support_routing_summary.csv", summary)
    method_counts = {}
    for row in summary:
        method_counts[str(row["method"])] = method_counts.get(str(row["method"]), 0) + 1
    best_hard = [row for row in summary if str(row.get("route_type")) == "hard_binary"]
    best_soft = [row for row in summary if str(row.get("route_type")) == "soft_blend"]
    hard_worthwhile = float(np.mean([support_meets_policy(row, policy) for row in best_hard])) if best_hard else 0.0
    soft_worthwhile = float(np.mean([support_meets_policy(row, policy) for row in best_soft])) if best_soft else 0.0
    any_route_gain = [finite(row.get("delta_scnr_dB")) > 0.0 for row in summary if str(row.get("route_type")) in {"hard_binary", "soft_blend"}]
    no_go = bool(not failures and hard_worthwhile == 0.0 and soft_worthwhile == 0.0)
    manifest = {
        "schema_id": "pgrcc_support_routing_oracle_v1",
        "mode": "support_routing_oracle",
        "config": v10.repo_path(config_path),
        "output_dir": v10.repo_path(out),
        "provenance": provenance,
        "cases": manifests,
        "case_count_requested": len(cases),
        "case_count_completed": len(manifests),
        "detailed_row_count": len(detailed),
        "summary_row_count": len(summary),
        "failures": failures,
        "method_counts": method_counts,
        "feature_policy": "background-only physical features; target truth/oracle/future output are evaluation-only",
        "feature_names": [
            "coherence",
            "robust_confidence",
            "robust_condition",
            "support_edge_distance_rows",
            "distance_to_clutter_ridge_hz",
            "residual_ratio_dB",
            "valid_sample_fraction",
        ],
        "formula": "Y=p_clutter*Y_CSI+(1-p_clutter)*Y_bypass; hard p in {0,1}; soft p in [0,1]",
        "worthwhile_policy": policy,
        "coverage": [
            "dynamic support edge in/out",
            "split guard",
            "local CSI/bypass",
            "hard binary",
            "soft blend",
            "clutter ridge",
            "support edge",
            "low speed",
            "high speed outside support",
            "nonuniform clutter",
            "sample loss",
        ],
        "summary_metrics": [
            "SCNR_improvement_dB",
            "target_loss_dB",
            "target_Pd",
            "background_Pfa",
            "target_cfar_margin_dB",
            "residual_p95_dB",
            "residual_cvar95_dB",
            "MDV_mps",
            "support_edge_loss_dB",
            "fallback_fraction_inside_support",
        ],
        "decision": {
            "code": "NO_GO_SUPPORT_ROUTING" if no_go else "SUPPORT_ROUTING_HEADROOM_PRESENT",
            "hard_best_noninferior_fraction": hard_worthwhile,
            "soft_best_noninferior_fraction": soft_worthwhile,
            "positive_gain_fraction_among_routed_summaries": float(np.mean(any_route_gain)) if any_route_gain else 0.0,
            "rule": "Support routing is no-go when neither best hard nor best soft routing is worthwhile under the fixed policy across the tested cases.",
        },
        "runtime_seconds": time.perf_counter() - started,
        "status": "ok" if not failures else "failed_cases_present",
    }
    write_json(out / "support_routing_manifest.json", manifest)
    print(
        f"[support-oracle] decision={manifest['decision']['code']} cases={len(manifests)} "
        f"detailed={len(detailed)} failures={len(failures)}",
        flush=True,
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[support-oracle][FATAL] {exc}", file=sys.stderr)
        raise
