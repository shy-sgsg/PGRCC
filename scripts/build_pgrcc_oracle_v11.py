#!/usr/bin/env python3
"""Run the V1.1 fine-grid PGRCC Oracle headroom audit.

This is an audit-only driver.  It reuses the existing Stage2 case generator,
Current CSI implementation, physical residual experts, local screen metrics,
and exact GO-CFAR implementation from :mod:`build_pgrcc_oracle`, but keeps all
V1.1 artifacts in a new output tree.  No neural-network training is performed.
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


EXACT_METRICS = (
    "exact_SCNR_improvement_dB",
    "exact_target_loss_dB",
    "exact_residual_p95_dB",
    "exact_residual_cvar95_dB",
    "exact_background_Pfa",
    "exact_target_cfar_margin_dB",
    "exact_target_Pd",
)


def finite(value: object) -> float:
    return v10.finite(value)


def json_default(value: object) -> object:
    return v10.json_default(value)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=json_default) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    v10.write_csv(path, rows)


def candidate_key(row: Mapping[str, object]) -> Tuple[object, ...]:
    return (
        str(row.get("region_id", "")),
        str(row.get("expert", "")),
        round(finite(row.get("delta_log_amplitude")), 12),
        round(finite(row.get("delta_phase")), 12),
        round(finite(row.get("gate")), 12),
    )


def candidate_metric_key(row: Mapping[str, object], key: str, maximize: bool) -> Tuple[float, Tuple[object, ...]]:
    value = finite(row.get(key))
    if not math.isfinite(value):
        value = -math.inf if maximize else math.inf
    return ((-value if maximize else value), candidate_key(row))


def set_current_deltas(row: Dict[str, object]) -> None:
    for key in (
        "delta_scnr_dB",
        "delta_target_preservation_dB",
        "delta_residual_p95_dB",
        "delta_residual_p99_dB",
        "delta_residual_cvar95_dB",
        "delta_high_tail_fraction",
        "delta_background_pfa",
        "delta_detection_margin_dB",
    ):
        row[key] = 0.0


def fine_candidate_set(
    data: v1.CaseData,
    arrays: Mapping[str, object],
    feature_values: Mapping[str, object],
    row: int,
    lo: int,
    hi: int,
    threshold_bg: np.ndarray,
    threshold_target: np.ndarray,
    region_config: Mapping[str, object],
) -> List[Dict[str, object]]:
    """Evaluate the deterministic V1.1 fine grid using local metrics only."""

    alignment = arrays["alignment"]
    assert isinstance(alignment, oracle.Alignment)
    f1_bg = alignment.f1_bg[row, lo:hi]
    f2_bg = alignment.f2_bg[row, lo:hi]
    f1_target = alignment.f1_target[row, lo:hi]
    f2_target = alignment.f2_target[row, lo:hi]
    current_bg = np.asarray(arrays["current_bg"])[row, lo:hi]
    current_target = np.asarray(arrays["current_target"])[row, lo:hi]
    current_row = v10.candidate_row(
        data,
        arrays,
        feature_values,
        row,
        lo,
        hi,
        "Current",
        "current",
        0.0,
        0.0,
        0.0,
        complex(np.exp(1j * arrays["p38_phase"][row])),
        current_bg,
        current_target,
        threshold_bg,
        threshold_target,
    )
    set_current_deltas(current_row)
    current_row["search_stage"] = "current_fallback"

    experts = {
        "current_p38": complex(np.exp(1j * arrays["p38_phase"][row])),
        "row_ls": complex(arrays["row_ls_alpha"][row]),
        "robust_row_ls": complex(arrays["robust_alpha"][row]),
    }
    delta_a_values = [float(value) for value in region_config["fine_delta_log_amplitude"]]
    delta_phi_values = [math.radians(float(value)) for value in region_config["fine_delta_phase_deg"]]
    gate_values = [float(value) for value in region_config["fine_gate"]]
    rows: List[Dict[str, object]] = [current_row]
    for expert, alpha_phy in experts.items():
        if abs(alpha_phy) <= 1.0e-12 or not (math.isfinite(alpha_phy.real) and math.isfinite(alpha_phy.imag)):
            continue
        for delta_a in delta_a_values:
            for delta_phi in delta_phi_values:
                corrected_bg = f1_bg - alpha_phy * math.exp(delta_a) * np.exp(1j * delta_phi) * f2_bg
                corrected_target = f1_target - alpha_phy * math.exp(delta_a) * np.exp(1j * delta_phi) * f2_target
                for gate in gate_values:
                    if abs(gate) <= 1.0e-15:
                        # The exact Current fallback is already represented;
                        # evaluating a second identity candidate adds no evidence.
                        continue
                    blended_bg = (1.0 - gate) * current_bg + gate * corrected_bg
                    blended_target = (1.0 - gate) * current_target + gate * corrected_target
                    candidate = v10.candidate_row(
                        data,
                        arrays,
                        feature_values,
                        row,
                        lo,
                        hi,
                        expert,
                        "bounded_corrected",
                        delta_a,
                        delta_phi,
                        gate,
                        alpha_phy,
                        blended_bg,
                        blended_target,
                        threshold_bg,
                        threshold_target,
                    )
                    candidate["search_stage"] = "fine_grid"
                    rows.append(candidate)
    return rows


def top_k_from_front(front: Sequence[Mapping[str, object]], top_k: int) -> List[Dict[str, object]]:
    """Select a bounded number by objective-wise Pareto diagnostics.

    The selection is a union of per-objective extrema, not a weighted scalar
    score.  This keeps the separate SCNR, target, tail, Pfa, and margin tradeoff
    visible while bounding the later refinement and exact workload.
    """

    current = next((dict(row) for row in front if str(row.get("candidate_type")) == "current"), None)
    if current is None:
        raise RuntimeError("V1.1 candidate pool lost the Current fallback")
    selected: List[Dict[str, object]] = [current]
    seen = {candidate_key(current)}
    bounded = [row for row in front if str(row.get("candidate_type")) != "current"]
    for key, maximize in v10.OBJECTIVE_SPECS:
        if len(selected) - 1 >= max(0, int(top_k)):
            break
        if not bounded:
            break
        choice = sorted(bounded, key=lambda item: candidate_metric_key(item, key, maximize))[0]
        marker = candidate_key(choice)
        if marker not in seen:
            selected.append(dict(choice))
            seen.add(marker)
    if len(selected) - 1 < max(0, int(top_k)):
        for choice in sorted(bounded, key=candidate_key):
            marker = candidate_key(choice)
            if marker in seen:
                continue
            selected.append(dict(choice))
            seen.add(marker)
            if len(selected) - 1 >= int(top_k):
                break
    for row in selected[1:]:
        row["selection_stage"] = "pareto_top_k"
    return selected


def refine_candidates(
    data: v1.CaseData,
    arrays: Mapping[str, object],
    feature_values: Mapping[str, object],
    selected: Sequence[Mapping[str, object]],
    threshold_bg: np.ndarray,
    threshold_target: np.ndarray,
    region_config: Mapping[str, object],
) -> List[Dict[str, object]]:
    """Refine only the selected candidates within small bounded neighborhoods."""

    alignment = arrays["alignment"]
    assert isinstance(alignment, oracle.Alignment)
    output: List[Dict[str, object]] = []
    seen = {candidate_key(row) for row in selected}
    a_step = float(region_config.get("refinement_delta_log_amplitude_step", 0.01))
    phi_step = math.radians(float(region_config.get("refinement_delta_phase_step_deg", 1.0)))
    gate_step = float(region_config.get("refinement_gate_step", 0.125))
    a_min = min(float(value) for value in region_config["fine_delta_log_amplitude"])
    a_max = max(float(value) for value in region_config["fine_delta_log_amplitude"])
    phi_min = math.radians(min(float(value) for value in region_config["fine_delta_phase_deg"]))
    phi_max = math.radians(max(float(value) for value in region_config["fine_delta_phase_deg"]))
    for base in selected:
        if str(base.get("candidate_type")) == "current":
            continue
        row = int(base["doppler_row"])
        lo = int(base["range_block_start"])
        hi = int(base["range_block_stop"])
        expert = str(base["expert"])
        if expert == "current_p38":
            alpha_phy = complex(np.exp(1j * arrays["p38_phase"][row]))
        elif expert == "row_ls":
            alpha_phy = complex(arrays["row_ls_alpha"][row])
        elif expert == "robust_row_ls":
            alpha_phy = complex(arrays["robust_alpha"][row])
        else:
            raise ValueError(f"unknown V1.1 expert {expert!r}")
        local_alignment = alignment
        f1_bg = local_alignment.f1_bg[row, lo:hi]
        f2_bg = local_alignment.f2_bg[row, lo:hi]
        f1_target = local_alignment.f1_target[row, lo:hi]
        f2_target = local_alignment.f2_target[row, lo:hi]
        current_bg = np.asarray(arrays["current_bg"])[row, lo:hi]
        current_target = np.asarray(arrays["current_target"])[row, lo:hi]
        base_a = finite(base.get("delta_log_amplitude"))
        base_phi = finite(base.get("delta_phase"))
        base_gate = finite(base.get("gate"))
        for da_offset in (-a_step, 0.0, a_step):
            delta_a = min(a_max, max(a_min, base_a + da_offset))
            for phi_offset in (-phi_step, 0.0, phi_step):
                delta_phi = min(phi_max, max(phi_min, base_phi + phi_offset))
                corrected_bg = f1_bg - alpha_phy * math.exp(delta_a) * np.exp(1j * delta_phi) * f2_bg
                corrected_target = f1_target - alpha_phy * math.exp(delta_a) * np.exp(1j * delta_phi) * f2_target
                for gate_offset in (-gate_step, 0.0, gate_step):
                    gate = min(1.0, max(0.0, base_gate + gate_offset))
                    if gate <= 1.0e-15:
                        continue
                    candidate_key_value = (
                        f"{data.case['case_id']}:r{row}:c{lo}-{hi}",
                        expert,
                        round(delta_a, 12),
                        round(delta_phi, 12),
                        round(gate, 12),
                    )
                    if candidate_key_value in seen:
                        continue
                    seen.add(candidate_key_value)
                    blended_bg = (1.0 - gate) * current_bg + gate * corrected_bg
                    blended_target = (1.0 - gate) * current_target + gate * corrected_target
                    candidate = v10.candidate_row(
                        data,
                        arrays,
                        feature_values,
                        row,
                        lo,
                        hi,
                        expert,
                        "bounded_corrected",
                        delta_a,
                        delta_phi,
                        gate,
                        alpha_phy,
                        blended_bg,
                        blended_target,
                        threshold_bg,
                        threshold_target,
                    )
                    candidate["search_stage"] = "local_refinement"
                    output.append(candidate)
    return output


def policy_values_from_exact(row: Mapping[str, object]) -> Dict[str, float]:
    return {
        "delta_scnr_dB": finite(row.get("delta_exact_SCNR_improvement_dB")),
        "delta_target_preservation_dB": finite(row.get("delta_exact_target_loss_dB")),
        "delta_residual_p95_dB": finite(row.get("delta_exact_residual_p95_dB")),
        "delta_residual_cvar95_dB": finite(row.get("delta_exact_residual_cvar95_dB")),
        "delta_background_pfa": finite(row.get("delta_exact_background_Pfa")),
        "delta_detection_margin_dB": finite(row.get("delta_exact_target_cfar_margin_dB")),
    }


def make_exact_row(
    data: v1.CaseData,
    candidate: Mapping[str, object],
    metrics: Mapping[str, object],
    current_metrics: Mapping[str, object],
    selection_reason: str,
    strict_policy: Mapping[str, object],
    noninferior_policy: Mapping[str, object],
) -> Dict[str, object]:
    row: Dict[str, object] = {
        "case_id": str(data.case["case_id"]),
        "seed": int(data.case["seed"]),
        "factor_family": str(data.case.get("factor_family", "unknown")),
        "factor": str(data.case.get("factor", "unknown")),
        "selection_reason": selection_reason,
        "region_id": str(candidate.get("region_id", "current_reference")),
        "expert": str(candidate.get("expert", "Current")),
        "candidate_type": str(candidate.get("candidate_type", "current")),
        "doppler_row": int(candidate.get("doppler_row", data.truth_row)),
        "range_block_start": int(candidate.get("range_block_start", data.p.rg_st)),
        "range_block_stop": int(candidate.get("range_block_stop", data.p.rg_ed + 1)),
        "delta_log_amplitude": finite(candidate.get("delta_log_amplitude")),
        "delta_phase": finite(candidate.get("delta_phase")),
        "delta_phase_deg": math.degrees(finite(candidate.get("delta_phase"))),
        "gate": finite(candidate.get("gate")),
    }
    for metric in EXACT_METRICS:
        row[metric] = finite(metrics.get(metric))
        if str(candidate.get("candidate_type")) == "current":
            continue
        candidate_value = finite(metrics.get(metric))
        current_value = finite(current_metrics.get(metric))
        if math.isfinite(candidate_value) and math.isfinite(current_value):
            row[f"delta_{metric}"] = candidate_value - current_value
        else:
            row[f"delta_{metric}"] = math.nan
    policy_values = policy_values_from_exact(row)
    row.update({f"exact_{key}": value for key, value in policy_values.items()})
    is_current = str(candidate.get("candidate_type")) == "current"
    row["exact_strict_worthwhile"] = bool(not is_current and v10.meets_policy(policy_values, strict_policy))
    row["exact_noninferior_worthwhile"] = bool(not is_current and v10.meets_policy(policy_values, noninferior_policy))
    row["exact_positive_headroom"] = bool(not is_current and finite(row.get("delta_exact_SCNR_improvement_dB")) > 0.0)
    row.update({f"screen_{key}": finite(candidate.get(key)) for key in (
        "delta_scnr_dB",
        "delta_target_preservation_dB",
        "delta_residual_p95_dB",
        "delta_background_pfa",
        "delta_detection_margin_dB",
    )})
    return row


def exact_rows_for_case(
    data: v1.CaseData,
    arrays: Mapping[str, object],
    region_fronts: Sequence[Sequence[Mapping[str, object]]],
    exact_top_k: int,
    strict_policy: Mapping[str, object],
    noninferior_policy: Mapping[str, object],
) -> List[Dict[str, object]]:
    current_candidate = {"candidate_type": "current", "expert": "Current"}
    current_bg, current_target = v10.materialize_candidate(data, arrays, current_candidate)
    current_metrics = v10.exact_global_metrics(data, arrays, current_bg, current_target)
    output = [
        make_exact_row(
            data,
            current_candidate,
            current_metrics,
            current_metrics,
            "current_reference",
            strict_policy,
            noninferior_policy,
        )
    ]
    seen: set[Tuple[object, ...]] = set()
    for front in region_fronts:
        selected = top_k_from_front(front, exact_top_k)
        for candidate in selected:
            if str(candidate.get("candidate_type")) == "current":
                continue
            marker = candidate_key(candidate)
            if marker in seen:
                continue
            seen.add(marker)
            bg, target = v10.materialize_candidate(data, arrays, candidate)
            metrics = v10.exact_global_metrics(data, arrays, bg, target)
            output.append(
                make_exact_row(
                    data,
                    candidate,
                    metrics,
                    current_metrics,
                    "region_top_k_exact",
                    strict_policy,
                    noninferior_policy,
                )
            )
    return output


def process_case(
    data: v1.CaseData,
    method_config: Mapping[str, object],
    region_config: Mapping[str, object],
    strict_policy: Mapping[str, object],
    noninferior_policy: Mapping[str, object],
) -> Dict[str, object]:
    arrays = v10.current_and_expert_arrays(data, method_config)
    threshold_bg = v10.cfar_threshold_map(np.asarray(arrays["current_detector_bg"]), data.p)
    threshold_target = v10.cfar_threshold_map(np.asarray(arrays["current_detector_target"]), data.p)
    regions = v10.region_rows(data, region_config)
    region_map: List[Dict[str, object]] = []
    pareto_rows: List[Dict[str, object]] = []
    region_fronts: List[List[Dict[str, object]]] = []
    started = time.perf_counter()
    top_k = int(region_config.get("top_k_per_region", 8))
    for index, (row, lo, hi) in enumerate(regions):
        features = v10.region_features(data, arrays, row, lo, hi)
        candidates = fine_candidate_set(
            data,
            arrays,
            features,
            row,
            lo,
            hi,
            threshold_bg,
            threshold_target,
            region_config,
        )
        screen_front = v10.pareto_front(candidates)
        selected = top_k_from_front(screen_front, top_k)
        refinements = refine_candidates(
            data,
            arrays,
            features,
            selected,
            threshold_bg,
            threshold_target,
            region_config,
        )
        final_pool = selected + refinements
        final_front = v10.pareto_front(final_pool)
        for item in final_front:
            item["region_index"] = index
            item["screen_pareto_count"] = len(screen_front)
            item["top_k_count"] = max(0, len(selected) - 1)
            item["refinement_count"] = len(refinements)
            item["search_pipeline"] = "fine_grid_local_screen_pareto_top_k_local_refinement"
            pareto_rows.append(item)
        region_fronts.append(final_front)

        current_candidate = next(item for item in candidates if str(item.get("candidate_type")) == "current")
        bounded = [item for item in candidates if str(item.get("candidate_type")) != "current"]
        best_scnr = max(bounded, key=lambda item: candidate_metric_key(item, "delta_scnr_dB", True), default=current_candidate)
        best_target = max(bounded, key=lambda item: candidate_metric_key(item, "delta_target_preservation_dB", True), default=current_candidate)
        best_tail = min(bounded, key=lambda item: candidate_metric_key(item, "delta_residual_p95_dB", False), default=current_candidate)
        best_cvar = min(bounded, key=lambda item: candidate_metric_key(item, "delta_residual_cvar95_dB", False), default=current_candidate)
        best_pfa = min(bounded, key=lambda item: candidate_metric_key(item, "delta_background_pfa", False), default=current_candidate)
        best_margin = max(bounded, key=lambda item: candidate_metric_key(item, "delta_detection_margin_dB", True), default=current_candidate)
        strict_primary = v10.select_primary(final_front, strict_policy)
        noninferior_primary = v10.select_primary(final_front, noninferior_policy)
        current_is_dominated = any(
            v10.dominates(item, current_candidate)
            for item in bounded
        )

        row_result: Dict[str, object] = {
            "case_id": str(data.case["case_id"]),
            "seed": int(data.case["seed"]),
            "factor_family": str(data.case.get("factor_family", "unknown")),
            "factor": str(data.case.get("factor", "unknown")),
            "factor_level": data.case.get("factor_level", "audit"),
            "region_id": f"{data.case['case_id']}:r{row}:c{lo}-{hi}",
            "doppler_row": row,
            "range_block_start": lo,
            "range_block_stop": hi,
            "fine_candidate_count": len(candidates),
            "screen_pareto_count": len(screen_front),
            "top_k_count": max(0, len(selected) - 1),
            "refinement_count": len(refinements),
            "final_pareto_count": len(final_front),
            "strict_worthwhile": bool(str(strict_primary.get("candidate_type")) != "current" and v10.meets_policy(strict_primary, strict_policy)),
            "noninferior_worthwhile": bool(str(noninferior_primary.get("candidate_type")) != "current" and v10.meets_policy(noninferior_primary, noninferior_policy)),
            "strict_primary_expert": str(strict_primary.get("expert", "")),
            "strict_primary_candidate_type": str(strict_primary.get("candidate_type", "")),
            "strict_primary_reason": str(strict_primary.get("primary_reason", "")),
            "noninferior_primary_expert": str(noninferior_primary.get("expert", "")),
            "noninferior_primary_candidate_type": str(noninferior_primary.get("candidate_type", "")),
            "noninferior_primary_reason": str(noninferior_primary.get("primary_reason", "")),
            "current_pareto_optimal": bool(not current_is_dominated),
            "strict_delta_log_amplitude": finite(strict_primary.get("delta_log_amplitude")),
            "strict_delta_phase": finite(strict_primary.get("delta_phase")),
            "strict_delta_phase_deg": math.degrees(finite(strict_primary.get("delta_phase"))),
            "strict_gate": finite(strict_primary.get("gate")),
            "strict_gain_over_current_dB": finite(strict_primary.get("delta_scnr_dB")),
            "strict_delta_target_preservation_dB": finite(strict_primary.get("delta_target_preservation_dB")),
            "strict_delta_residual_p95_dB": finite(strict_primary.get("delta_residual_p95_dB")),
            "strict_delta_background_pfa": finite(strict_primary.get("delta_background_pfa")),
            "strict_delta_detection_margin_dB": finite(strict_primary.get("delta_detection_margin_dB")),
            "noninferior_delta_log_amplitude": finite(noninferior_primary.get("delta_log_amplitude")),
            "noninferior_delta_phase": finite(noninferior_primary.get("delta_phase")),
            "noninferior_delta_phase_deg": math.degrees(finite(noninferior_primary.get("delta_phase"))),
            "noninferior_gate": finite(noninferior_primary.get("gate")),
            "noninferior_gain_over_current_dB": finite(noninferior_primary.get("delta_scnr_dB")),
            "noninferior_delta_target_preservation_dB": finite(noninferior_primary.get("delta_target_preservation_dB")),
            "noninferior_delta_residual_p95_dB": finite(noninferior_primary.get("delta_residual_p95_dB")),
            "noninferior_delta_background_pfa": finite(noninferior_primary.get("delta_background_pfa")),
            "noninferior_delta_detection_margin_dB": finite(noninferior_primary.get("delta_detection_margin_dB")),
            # Keep the V1 correlation/predictor helpers compatible while
            # exposing the V1.1 policy-selected gain explicitly.
            "oracle_gain_over_current_dB": finite(noninferior_primary.get("delta_scnr_dB")),
            "diagnostic_best_scnr_expert": str(best_scnr.get("expert", "")),
            "diagnostic_best_scnr_gain_dB": finite(best_scnr.get("delta_scnr_dB")),
            "diagnostic_best_scnr_target_preservation_dB": finite(best_scnr.get("delta_target_preservation_dB")),
            "diagnostic_best_scnr_delta_residual_p95_dB": finite(best_scnr.get("delta_residual_p95_dB")),
            "diagnostic_best_scnr_delta_background_pfa": finite(best_scnr.get("delta_background_pfa")),
            "diagnostic_best_scnr_delta_detection_margin_dB": finite(best_scnr.get("delta_detection_margin_dB")),
            "diagnostic_max_target_preservation_dB": finite(best_target.get("delta_target_preservation_dB")),
            "diagnostic_min_residual_p95_dB": finite(best_tail.get("delta_residual_p95_dB")),
            "diagnostic_min_residual_cvar95_dB": finite(best_cvar.get("delta_residual_cvar95_dB")),
            "diagnostic_min_background_pfa": finite(best_pfa.get("delta_background_pfa")),
            "diagnostic_max_detection_margin_dB": finite(best_margin.get("delta_detection_margin_dB")),
            "screen_positive_headroom": bool(finite(best_scnr.get("delta_scnr_dB")) > 0.0),
            "target_row_truth": int(data.truth_row),
            "target_range_bin_truth": int(data.truth_col),
            "target_fd_truth_hz": float(data.target_fd),
            "status": "ok",
        }
        row_result.update(features)
        region_map.append(row_result)
        if (index + 1) % 32 == 0:
            print(
                f"[pgrcc-oracle-v11] {data.case['case_id']}: regions {index + 1}/{len(regions)} "
                f"elapsed={time.perf_counter() - started:.1f}s",
                flush=True,
            )
    exact_rows = exact_rows_for_case(
        data,
        arrays,
        region_fronts,
        int(region_config.get("exact_top_k_per_region", 3)),
        strict_policy,
        noninferior_policy,
    )
    return {
        "region_map": region_map,
        "pareto": pareto_rows,
        "exact": exact_rows,
        "region_count": len(regions),
    }


def repeatability_pass(
    cases: Sequence[Mapping[str, object]],
    config: Dict[str, object],
    base_config_path: Path,
    method_config: Mapping[str, object],
    repeats: int,
    keep_data: bool,
) -> Tuple[List[Dict[str, object]], Dict[str, float]]:
    if repeats < 2:
        raise ValueError("repeatability.repeats must be at least 2")
    metric_values: Dict[str, List[float]] = {metric: [] for metric in EXACT_METRICS}
    output: List[Dict[str, object]] = []
    for case_index, original in enumerate(cases, start=1):
        measurements: List[Dict[str, float]] = []
        for repeat_index in range(repeats):
            case = copy.deepcopy(dict(original))
            case["case_id"] = f"{original['case_id']}_repeat_{repeat_index + 1}"
            case["target_id"] = f"{original.get('target_id', 'PGRCC_TARGET')}_repeat_{repeat_index + 1}"
            paths: Dict[str, object] | None = None
            try:
                paths = v1.prepare_case_data(base_config_path, config, case, keep_data)
                data = v1.load_case_data(case, paths)
                arrays = v10.current_and_expert_arrays(data, method_config)
                current_bg, current_target = v10.materialize_candidate(data, arrays, {"candidate_type": "current"})
                measurements.append(v10.exact_global_metrics(data, arrays, current_bg, current_target))
            finally:
                if paths is not None and not keep_data:
                    v1.cleanup_case(paths)
        for metric in EXACT_METRICS:
            values = [finite(item.get(metric)) for item in measurements]
            values = [value for value in values if math.isfinite(value)]
            if not values:
                continue
            pair_diffs = []
            for pair_index, (value_a, value_b) in enumerate(zip(values[:-1], values[1:]), start=1):
                difference = abs(value_b - value_a)
                pair_diffs.append(difference)
                output.append({
                    "case_id": str(original["case_id"]),
                    "seed": int(original["seed"]),
                    "repeat_pair": pair_index,
                    "metric": metric,
                    "value_a": value_a,
                    "value_b": value_b,
                    "abs_difference": difference,
                    "same_seed": True,
                    "same_case_parameters": True,
                })
            metric_values[metric].extend(pair_diffs)
        print(f"[pgrcc-oracle-v11] repeatability {case_index}/{len(cases)}: {original['case_id']}", flush=True)
    epsilon: Dict[str, float] = {}
    for metric, values in metric_values.items():
        epsilon[metric] = float(np.percentile(values, 95.0)) if values else 0.0
    for row in output:
        row["epsilon_95"] = epsilon[str(row["metric"])]
    return output, epsilon


def noninferior_policy(config: Mapping[str, object], epsilon: Mapping[str, float]) -> Dict[str, float]:
    source = config["noninferiority_policy"]
    return {
        "min_delta_scnr_db": float(source["min_delta_scnr_db"]),
        "min_delta_target_preservation_db": -float(epsilon.get("exact_target_loss_dB", 0.0)),
        "max_delta_tail_p95_db": float(epsilon.get("exact_residual_p95_dB", 0.0)),
        "max_delta_tail_cvar95_db": float(epsilon.get("exact_residual_cvar95_dB", 0.0)),
        "max_delta_background_pfa": float(epsilon.get("exact_background_Pfa", 0.0)),
        "min_delta_detection_margin_db": -float(epsilon.get("exact_target_cfar_margin_dB", 0.0)),
    }


def stats(values: Sequence[object]) -> Dict[str, object]:
    numbers = [finite(value) for value in values]
    numbers = [value for value in numbers if math.isfinite(value)]
    return {
        "count": len(numbers),
        "mean": float(np.mean(numbers)) if numbers else math.nan,
        "median": float(np.median(numbers)) if numbers else math.nan,
        "p95": float(np.percentile(numbers, 95.0)) if numbers else math.nan,
    }


def headroom_rows(
    region_map: Sequence[Mapping[str, object]],
    exact_rows: Sequence[Mapping[str, object]],
    near_zero_db: float,
) -> List[Dict[str, object]]:
    groups: Dict[Tuple[str, str], List[Mapping[str, object]]] = {("overall", "overall"): list(region_map)}
    for row in region_map:
        groups.setdefault(("factor_family", str(row.get("factor_family", "unknown"))), []).append(row)
        groups.setdefault(("case", str(row.get("case_id", "unknown"))), []).append(row)
    output: List[Dict[str, object]] = []
    for (scope, group), rows in sorted(groups.items()):
        case_ids = sorted({str(row.get("case_id")) for row in rows})
        exact_group = [
            row for row in exact_rows
            if str(row.get("candidate_type")) != "current"
            and (scope != "factor_family" or str(row.get("factor_family")) == group)
            and (scope != "case" or str(row.get("case_id")) == group)
        ]
        case_best: List[float] = []
        exact_positive_cases = 0
        exact_worthwhile_cases = 0
        exact_strict_cases = 0
        for case_id in case_ids:
            candidates = [row for row in exact_group if str(row.get("case_id")) == case_id]
            gains = [finite(row.get("delta_exact_SCNR_improvement_dB")) for row in candidates]
            gains = [value for value in gains if math.isfinite(value)]
            if gains:
                case_best.append(max(gains))
                exact_positive_cases += int(max(gains) > 0.0)
            exact_worthwhile_cases += int(any(bool(row.get("exact_noninferior_worthwhile")) for row in candidates))
            exact_strict_cases += int(any(bool(row.get("exact_strict_worthwhile")) for row in candidates))
        screen_gains = [finite(row.get("diagnostic_best_scnr_gain_dB")) for row in rows]
        exact_best_tail = []
        for case_id in case_ids:
            candidates = [row for row in exact_group if str(row.get("case_id")) == case_id]
            tails = [finite(row.get("delta_exact_residual_p95_dB")) for row in candidates]
            tails = [value for value in tails if math.isfinite(value)]
            if tails:
                exact_best_tail.append(min(tails))
        screen_stats = stats(screen_gains)
        exact_gain_stats = stats(case_best)
        exact_tail_stats = stats(exact_best_tail)
        output.append({
            "scope": scope,
            "group": group,
            "region_count": len(rows),
            "case_count": len(case_ids),
            "strict_worthwhile_region_fraction": float(np.mean([bool(row.get("strict_worthwhile")) for row in rows])) if rows else math.nan,
            "noninferior_worthwhile_region_fraction": float(np.mean([bool(row.get("noninferior_worthwhile")) for row in rows])) if rows else math.nan,
            "screen_positive_headroom_region_fraction": float(np.mean([bool(row.get("screen_positive_headroom")) for row in rows])) if rows else math.nan,
            "exact_positive_headroom_case_fraction": exact_positive_cases / max(1, len(case_ids)),
            "exact_noninferior_worthwhile_case_fraction": exact_worthwhile_cases / max(1, len(case_ids)),
            "exact_strict_worthwhile_case_fraction": exact_strict_cases / max(1, len(case_ids)),
            "screen_gain_mean_dB": screen_stats["mean"],
            "screen_gain_median_dB": screen_stats["median"],
            "screen_gain_p95_dB": screen_stats["p95"],
            "exact_best_gain_count": exact_gain_stats["count"],
            "exact_best_gain_mean_dB": exact_gain_stats["mean"],
            "exact_best_gain_median_dB": exact_gain_stats["median"],
            "exact_best_gain_p95_dB": exact_gain_stats["p95"],
            "exact_best_residual_p95_mean_dB": exact_tail_stats["mean"],
            "exact_best_residual_p95_median_dB": exact_tail_stats["median"],
            "exact_best_residual_p95_p95_dB": exact_tail_stats["p95"],
            "near_zero_gain_abs_threshold_dB": near_zero_db,
            "exact_best_gain_near_zero_fraction": float(np.mean([abs(value) <= near_zero_db for value in case_best])) if case_best else math.nan,
            "interpretation": "A physical headroom summary; exact rows are full-map GO-CFAR checks of regional top-K candidates",
        })
    return output


def predictability_rows(region_map: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    output: List[Dict[str, object]] = []
    for row in v10.correlation_rows(region_map):
        item = dict(row)
        item["row_type"] = "feature_correlation"
        output.append(item)
    for row in v10.predictor_rows(region_map):
        item = dict(row)
        item["row_type"] = "grouped_predictor"
        output.append(item)
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/research/pgrcc_oracle_v11.json"))
    parser.add_argument("--out", type=Path, default=Path("outputs/pgrcc_oracle_v11"))
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
        raise RuntimeError(f"refusing to overwrite non-empty V1.1 output: {out}; use a new --out or --allow-existing")
    config = copy.deepcopy(json.loads(config_path.read_text(encoding="utf-8")))
    out.mkdir(parents=True, exist_ok=True)
    config["output_root"] = v10.repo_path(out / "cases")
    cases = v10.build_cases(config, args.case_limit)
    if not cases:
        raise RuntimeError("V1.1 audit produced no cases")
    base_config_path = (ROOT / str(config["base_config"])).resolve()
    method_config = dict(config.get("method_config", {}))
    region_config = dict(config["regions"])
    strict_policy = dict(config["strict_worthwhile_policy"])
    repeat_rows, epsilon = repeatability_pass(
        cases,
        config,
        base_config_path,
        method_config,
        int(config.get("repeatability", {}).get("repeats", 2)),
        args.keep_data,
    )
    noninferior = noninferior_policy(config, epsilon)
    print(f"[pgrcc-oracle-v11] repeatability epsilons={json.dumps(epsilon, sort_keys=True)}", flush=True)

    all_regions: List[Dict[str, object]] = []
    all_pareto: List[Dict[str, object]] = []
    all_exact: List[Dict[str, object]] = []
    case_rows: List[Dict[str, object]] = []
    failures: List[Dict[str, object]] = []
    started = time.perf_counter()
    command = ["python3", v10.repo_path(Path(__file__)), "--config", v10.repo_path(config_path), "--out", v10.repo_path(out)]
    if args.case_limit is not None:
        command += ["--case-limit", str(args.case_limit)]
    provenance = v10.source_provenance(command)
    for case_index, case in enumerate(cases, start=1):
        print(f"[pgrcc-oracle-v11] case {case_index}/{len(cases)}: {case['case_id']}", flush=True)
        paths: Dict[str, object] | None = None
        try:
            paths = v1.prepare_case_data(base_config_path, config, case, args.keep_data)
            data = v1.load_case_data(case, paths)
            result = process_case(data, method_config, region_config, strict_policy, noninferior)
            all_regions.extend(result["region_map"])
            all_pareto.extend(result["pareto"])
            all_exact.extend(result["exact"])
            case_rows.append(v10.case_manifest_row(data))
            print(
                f"[pgrcc-oracle-v11] completed {case['case_id']}: regions={result['region_count']} "
                f"elapsed={time.perf_counter() - started:.1f}s",
                flush=True,
            )
        except Exception as exc:
            failures.append({"case_id": str(case.get("case_id")), "error": repr(exc)})
            print(f"[pgrcc-oracle-v11][ERR] {case.get('case_id')}: {exc}", file=sys.stderr, flush=True)
        finally:
            if paths is not None and not args.keep_data:
                v1.cleanup_case(paths)
    if not all_regions:
        raise RuntimeError(f"V1.1 audit produced no region rows; failures={failures}")

    near_zero_db = float(config.get("reporting", {}).get("near_zero_gain_abs_db", 0.05))
    headroom = headroom_rows(all_regions, all_exact, near_zero_db)
    predictability = predictability_rows(all_regions)
    write_csv(out / "oracle_v11_region_map.csv", all_regions)
    write_csv(out / "oracle_v11_pareto_summary.csv", all_pareto)
    write_csv(out / "oracle_v11_exact_summary.csv", all_exact)
    write_csv(out / "oracle_v11_repeatability.csv", repeat_rows)
    write_csv(out / "oracle_v11_headroom_summary.csv", headroom)
    write_csv(out / "oracle_v11_predictability_summary.csv", predictability)

    overall = next(row for row in headroom if row["scope"] == "overall")
    exact_case_gains = []
    exact_case_worthwhile = []
    for case_id in sorted({str(row.get("case_id")) for row in all_exact}):
        rows = [row for row in all_exact if str(row.get("case_id")) == case_id and str(row.get("candidate_type")) != "current"]
        gains = [finite(row.get("delta_exact_SCNR_improvement_dB")) for row in rows]
        gains = [value for value in gains if math.isfinite(value)]
        if gains:
            exact_case_gains.append(max(gains))
        exact_case_worthwhile.append(any(bool(row.get("exact_noninferior_worthwhile")) for row in rows))
    worthwhile_gains = [value for value, row in zip(exact_case_gains, [True] * len(exact_case_gains)) if math.isfinite(value)]
    min_case_fraction = float(config["decision"]["minimum_exact_noninferior_case_fraction"])
    min_median_gain = float(config["decision"]["minimum_median_exact_gain_dB"])
    median_gain = float(np.median(worthwhile_gains)) if worthwhile_gains else math.nan
    exact_fraction = float(np.mean(exact_case_worthwhile)) if exact_case_worthwhile else 0.0
    go = bool(
        not failures
        and exact_fraction >= min_case_fraction
        and math.isfinite(median_gain)
        and median_gain >= min_median_gain
    )
    manifest = {
        "schema_id": "pgrcc_oracle_headroom_audit_v1_1",
        "mode": "oracle_headroom_audit_v1_1",
        "config": v10.repo_path(config_path),
        "output_dir": v10.repo_path(out),
        "provenance": provenance,
        "case_count_requested": len(cases),
        "case_count_completed": len(case_rows),
        "region_count": len(all_regions),
        "pareto_candidate_count": len(all_pareto),
        "exact_candidate_count": len(all_exact),
        "failures": failures,
        "cases": case_rows,
        "method_config": method_config,
        "region_config": region_config,
        "strict_worthwhile_policy": strict_policy,
        "noninferior_policy": noninferior,
        "repeatability_epsilon_95": epsilon,
        "repeatability_policy": "same seed and same requested case parameters; no manual epsilon widening",
        "fine_grid": {
            "delta_log_amplitude": region_config["fine_delta_log_amplitude"],
            "delta_phase_deg": region_config["fine_delta_phase_deg"],
            "gate": region_config["fine_gate"],
            "pipeline": "local screen -> Pareto -> top-K per region -> bounded local refinement -> regional top-K exact full-map GO-CFAR",
        },
        "inference_feature_names": v10.FEATURE_NAMES,
        "forbidden_inference_features": config.get("provenance", {}).get("forbidden_inference_features", []),
        "frozen_benchmark_outputs": config.get("provenance", {}).get("frozen_benchmark_outputs", []),
        "headroom_summary_overall": overall,
        "predictability_scope": "exploratory grouped predictors and feature correlations; not a production model",
        "decision": {
            "go_complex_weight_residual": go,
            "code": "GO_COMPLEX_WEIGHT_RESIDUAL" if go else "NO_GO_COMPLEX_WEIGHT_RESIDUAL",
            "exact_noninferior_worthwhile_case_fraction": exact_fraction,
            "median_exact_best_gain_dB": median_gain,
            "minimum_exact_noninferior_case_fraction": min_case_fraction,
            "minimum_median_exact_gain_dB": min_median_gain,
            "strict_zero_control_region_fraction": overall["strict_worthwhile_region_fraction"],
            "rule": "V1.1 complex-residual headroom requires exact full-map non-inferior worthwhile cases and the fixed meaningful-gain threshold; strict-zero results remain a control.",
        },
        "runtime_seconds": time.perf_counter() - started,
        "status": "ok" if not failures else "failed_cases_present",
    }
    write_json(out / "oracle_v11_manifest.json", manifest)
    print(
        f"[pgrcc-oracle-v11] decision={manifest['decision']['code']} "
        f"regions={len(all_regions)} exact={len(all_exact)} "
        f"noninferior_case_fraction={exact_fraction:.4f} median_gain_dB={median_gain:.4f} failures={len(failures)}",
        flush=True,
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[pgrcc-oracle-v11][FATAL] {exc}", file=sys.stderr)
        raise
