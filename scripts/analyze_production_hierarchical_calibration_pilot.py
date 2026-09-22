#!/usr/bin/env python3
"""Compact, fail-closed analyzer for the production delay pilot."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_production_hierarchical_calibration_pilot import (
    ALLOWED_DECISION_LABELS,
    COMPACT_EVIDENCE_FILES,
    _json_safe,
    _write_json,
    validate_compact_evidence_root,
)


def validate_decision_label(label: object) -> str:
    value = str(label)
    if value not in ALLOWED_DECISION_LABELS:
        raise ValueError(f"invalid decision label: {value}")
    return value


def choose_decision(evidence: Mapping[str, object]) -> str:
    """Choose only from the frozen labels; missing evidence stays conservative."""

    if str(evidence.get("status", "")).lower() in {
        "skip_cuda",
        "not_evaluable",
        "completed_with_gaps",
    }:
        return "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE"
    if evidence.get("algorithmic_results_claimed") is not True:
        return "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE"
    candidate = evidence.get("decision_candidate")
    if candidate is None:
        return "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE"
    return validate_decision_label(candidate)


def empirical_cell_pfa(hit_cut_count: object, valid_cut_count: object) -> dict[str, object]:
    """Compute cell Pfa only from the exported hit/valid CUT denominator."""

    try:
        hit = int(hit_cut_count)
        valid = int(valid_cut_count)
    except (TypeError, ValueError):
        hit, valid = -1, 0
    if valid <= 0 or hit < 0:
        return {
            "status": "NOT_EVALUABLE",
            "value": None,
            "hit_cut_count": max(0, hit),
            "valid_cut_count": max(0, valid),
            "reason": "valid_cut_count_missing_or_non_positive",
            "definition": "hit_cut_count / valid_cut_count",
        }
    return {
        "status": "evaluable",
        "value": float(hit) / float(valid),
        "hit_cut_count": hit,
        "valid_cut_count": valid,
        "reason": None,
        "definition": "hit_cut_count / valid_cut_count",
    }


def causal_triplet_status(
    off: object, on: object, target_only: object, *, tolerance: float = 5.0e-5
) -> dict[str, object]:
    """Audit an in-memory ON-OFF-TO relation without broadcasting."""

    try:
        import numpy as np

        arrays = [np.asarray(item) for item in (off, on, target_only)]
        if any(array.shape != arrays[0].shape for array in arrays[1:]):
            return {"status": "NOT_EVALUABLE", "reason": "shape_mismatch"}
        if arrays[0].size == 0:
            return {"status": "NOT_EVALUABLE", "reason": "empty_triplet"}
        values = [array.astype(np.complex128, copy=False) for array in arrays]
        if not all(bool(np.all(np.isfinite(array))) for array in values):
            return {"status": "NOT_EVALUABLE", "reason": "non_finite_triplet"}
        error = np.abs(values[1] - values[0] - values[2])
        maximum = float(np.max(error))
        return {
            "status": "passed" if maximum <= float(tolerance) else "NOT_EVALUABLE",
            "max_abs_error": maximum,
            "sample_count": int(arrays[0].size),
            "tolerance": float(tolerance),
            "reason": None if maximum <= float(tolerance) else "on_minus_off_minus_to_exceeds_tolerance",
        }
    except (TypeError, ValueError, OverflowError):
        return {"status": "NOT_EVALUABLE", "reason": "invalid_triplet"}


_CAUSAL_TARGET_FIELDS = ("target_detection_pd", "track_pd")


def _finite_metric(metrics: Mapping[str, object], *names: str) -> float | None:
    for name in names:
        try:
            value = float(metrics[name])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(value):
            return value
    return None


def residual_closure(
    conditions: Mapping[str, Mapping[str, object]], *, tolerance_db: float = 3.0
) -> dict[str, object]:
    """Require A0/PK/PKR residuals to close before estimator attribution.

    The check is deliberately agnostic to the direction of an improvement: it
    only establishes that the three reference conditions are present, finite,
    and mutually close within the declared tolerance.  It therefore cannot
    promote a missing or incomparable known-error result.
    """

    required = ["A0", "PK", "PKR"]
    values: dict[str, float] = {}
    if not math.isfinite(float(tolerance_db)) or float(tolerance_db) < 0:
        return {
            "status": "NOT_EVALUABLE",
            "required_conditions": required,
            "estimator_class_allowed": False,
            "decision": "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE",
            "reason": "invalid_tolerance_db",
        }
    for name in required:
        item = conditions.get(name)
        if not isinstance(item, Mapping):
            return {
                "status": "NOT_EVALUABLE",
                "required_conditions": required,
                "estimator_class_allowed": False,
                "decision": "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE",
                "reason": f"missing_condition:{name}",
            }
        value = _finite_metric(item, "residual_power_db")
        if value is None:
            return {
                "status": "NOT_EVALUABLE",
                "required_conditions": required,
                "estimator_class_allowed": False,
                "decision": "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE",
                "reason": f"residual_not_finite:{name}",
            }
        values[name] = value
    spread = max(values.values()) - min(values.values())
    passed = spread <= float(tolerance_db)
    return {
        "status": "passed" if passed else "NOT_EVALUABLE",
        "required_conditions": required,
        "values_db": values,
        "spread_db": spread,
        "tolerance_db": float(tolerance_db),
        "estimator_class_allowed": passed,
        "decision": "GO_EQUIVALENT_CALIBRATION_ONLY" if passed else "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE",
        "reason": None if passed else "a0_pk_pkr_residual_not_closed",
    }


def causal_target_metrics(
    off: Mapping[str, object], on: Mapping[str, object], target_only: Mapping[str, object]
) -> dict[str, object]:
    """Return target metrics from OFF/ON/TO only; never infer from ON power."""

    if not all(isinstance(item, Mapping) for item in (off, on, target_only)):
        return {"status": "NOT_EVALUABLE", "reason": "causal_metrics_not_mappings"}
    on_minus_off: dict[str, float] = {}
    to_values: dict[str, float] = {}
    for field in _CAUSAL_TARGET_FIELDS:
        off_value = _finite_metric(off, field)
        on_value = _finite_metric(on, field)
        to_value = _finite_metric(target_only, field)
        if off_value is None or on_value is None or to_value is None:
            return {"status": "NOT_EVALUABLE", "reason": f"missing_causal_metric:{field}"}
        on_minus_off[field] = on_value - off_value
        to_values[field] = to_value
    return {
        "status": "evaluable",
        "on_minus_off": on_minus_off,
        "to": to_values,
        "target_protection_rule": "causal_ON_minus_OFF_and_TO; never_ON_power_alone",
    }


def _artifact_count(value: object) -> int:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return len(value)
    return 1 if value else 0


def waterfall_layers(branch: Mapping[str, object]) -> dict[str, object]:
    """Keep CFAR, cluster, protocol, and TrackManager evidence independent."""

    geometry = branch.get("cfar_geometry")
    geometry = geometry if isinstance(geometry, Mapping) else {}
    pfa = empirical_cell_pfa(geometry.get("hit_cut_count"), geometry.get("valid_cut_count"))
    metrics = branch.get("metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    artifacts = branch.get("artifacts")
    artifacts = artifacts if isinstance(artifacts, Mapping) else {}
    detection_count = _artifact_count(artifacts.get("detection"))
    track_files = (
        _artifact_count(artifacts.get("track_association")),
        _artifact_count(artifacts.get("track_states")),
        _artifact_count(artifacts.get("track_payloads")),
    )
    track_status = "evaluable" if all(track_files) else "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG"
    layers = [
        {
            "layer": "CFAR",
            "status": pfa["status"],
            "count": pfa["hit_cut_count"],
            "valid_cut_count": pfa["valid_cut_count"],
            "cell_pfa": pfa["value"],
        },
        {
            "layer": "cluster",
            "status": "evaluable" if "cfar_cluster_count" in metrics else "NOT_EVALUABLE",
            "count": metrics.get("cfar_cluster_count"),
            "selected_count": metrics.get("cfar_selected_cluster_count"),
        },
        {
            "layer": "protocol_detection",
            "status": "evaluable" if detection_count else "NOT_EVALUABLE",
            "count": detection_count,
        },
        {
            "layer": "track",
            "status": track_status,
            "count": _artifact_count(artifacts.get("track_states")) if track_status == "evaluable" else None,
            "evidence": {
                "track_association": artifacts.get("track_association", []),
                "track_states": artifacts.get("track_states", []),
                "track_payloads": artifacts.get("track_payloads", []),
            },
        },
    ]
    return {
        "status": "evaluable" if pfa["status"] == "evaluable" else "NOT_EVALUABLE",
        "layers": layers,
        "target_protection_rule": "causal_ON_minus_OFF_and_TO; never_ON_power_alone",
    }


def classify_id_switches(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Classify only switches observable from association truth/track IDs."""

    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return {"status": "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG", "classification": "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG"}
    required = ("frame", "track_id", "truth_id")
    if any(not isinstance(row, Mapping) or any(key not in row for key in required) for row in rows):
        return {"status": "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG", "classification": "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG"}
    ordered = sorted(rows, key=lambda row: (int(row["track_id"]), int(row["frame"])))
    switches = 0
    for previous, current in zip(ordered, ordered[1:]):
        if previous["track_id"] == current["track_id"] and previous["truth_id"] != current["truth_id"]:
            switches += 1
    return {
        "status": "evaluable",
        "classification": "ID_SWITCH" if switches else "NO_ID_SWITCH",
        "id_switch_count": switches,
    }


def analyze_delay_evidence(manifest: Mapping[str, object]) -> dict[str, object]:
    """Summarize delay-stage gates without promoting incomplete evidence."""

    residual = manifest.get("residual_closure")
    if not isinstance(residual, Mapping):
        residual = residual_closure({})
    rows = manifest.get("compact_rows")
    rows = rows if isinstance(rows, Mapping) else {}
    clutter = [item for item in rows.get("clutter_metrics", []) if isinstance(item, Mapping)]
    causal_rows = [item for item in clutter if item.get("layer") == "ON_minus_OFF"]
    waterfall_rows = [
        item for item in clutter
        if item.get("layer") in {"CFAR", "cluster", "protocol_detection", "track"}
    ]
    pfa_rows = [item for item in waterfall_rows if item.get("layer") == "CFAR"]
    pfa_evaluable = bool(pfa_rows) and all(item.get("cell_pfa") is not None for item in pfa_rows)
    causal_evaluable = bool(causal_rows) and all(item.get("status") == "evaluable" for item in causal_rows)
    track_rows = [item for item in waterfall_rows if item.get("layer") == "track"]
    id_switch_known = bool(track_rows) and all(
        item.get("id_switch_classification") != "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG"
        for item in track_rows
    )
    complete = (
        str(manifest.get("status")) == "completed"
        and manifest.get("skip_cuda") is not True
        and manifest.get("algorithmic_results_claimed") is True
        and residual.get("status") == "passed"
        and causal_evaluable
        and pfa_evaluable
    )
    candidate = manifest.get("decision_candidate")
    decision = str(candidate) if complete and candidate in ALLOWED_DECISION_LABELS else "NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE"
    questions = {
        "1_robust_ddc_production_f1_f2": "evaluable" if waterfall_rows else "NOT_EVALUABLE",
        "2_current_vs_ordinary_subtraction": "evaluable" if manifest.get("method_contract") else "NOT_EVALUABLE",
        "3_ddc_fast_time_delay_clutter_recovery": "evaluable" if residual.get("status") == "passed" else "NOT_EVALUABLE",
        "4_ddc_range_angle_position_velocity_track": "evaluable" if causal_evaluable else "NOT_EVALUABLE",
        "5_blind_physical_delay_recovery": "evaluable" if manifest.get("prepared_inputs") else "NOT_EVALUABLE",
        "6_physical_plus_robust_complementarity": "evaluable" if causal_evaluable else "NOT_EVALUABLE",
        "7_known_delay_to_a0": residual.get("status", "NOT_EVALUABLE"),
        "8_target_preservation": "causal_ON_minus_OFF_and_TO_required" if causal_evaluable else "NOT_EVALUABLE",
        "9_false_alarm_waterfall": "evaluable" if pfa_evaluable and waterfall_rows else "NOT_EVALUABLE",
        "10_delay_class": "NOT_EVALUABLE_until_single_error_closure",
        "11_channel_delay_research_value": "NOT_EVALUABLE_without_formal_delay_matrix",
        "12_servo_velocity_gate": "CLOSED",
        "13_coupled_physical_state_gate": "CLOSED",
        "14_ai_gate": "CLOSED",
    }
    return {
        "status": "passed" if complete else "NOT_EVALUABLE",
        "decision": decision,
        "algorithmic_results_claimed": bool(complete),
        "residual_closure": dict(residual),
        "causal_metrics": {"status": "evaluable" if causal_evaluable else "NOT_EVALUABLE"},
        "empirical_cell_pfa": {"status": "evaluable" if pfa_evaluable else "NOT_EVALUABLE"},
        "waterfall": {
            "status": "evaluable" if waterfall_rows else "NOT_EVALUABLE",
            "layers": sorted({str(item.get("layer")) for item in waterfall_rows}),
        },
        "id_switch": {
            "status": "evaluable" if id_switch_known else "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG",
            "classification": "classified" if id_switch_known else "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG",
        },
        "questions": questions,
        "reason": None if complete else "formal_production_evidence_incomplete",
    }


def _read_manifest(path: Path) -> dict[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("pilot manifest must be a JSON object")
    return value


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(_json_safe(dict(row)))


def _compact_manifest(manifest: Mapping[str, object], decision: str) -> dict[str, object]:
    source = manifest.get("source")
    if not isinstance(source, Mapping):
        source = {}
    selection = manifest.get("selection")
    if not isinstance(selection, Mapping):
        selection = {}
    methods = manifest.get("method_contract")
    method_ids = [
        str(item.get("method_id"))
        for item in methods or []
        if isinstance(item, Mapping)
    ]
    return {
        "schema_version": 1,
        "stage": manifest.get("stage"),
        "status": manifest.get("status"),
        "mode": manifest.get("mode"),
        "skip_cuda": manifest.get("skip_cuda"),
        "algorithmic_results_claimed": manifest.get("algorithmic_results_claimed", False),
        "decision": decision,
        "source": {
            "commit": source.get("commit"),
            "dirty": source.get("dirty"),
            "dirty_tracked": source.get("dirty_tracked"),
        },
        "config": manifest.get("config"),
        "runner": manifest.get("runner"),
        "analyzer": manifest.get("analyzer"),
        "selection": {
            "selection_mode": selection.get("selection_mode"),
            "cartesian_full_matrix": selection.get("cartesian_full_matrix"),
            "case_count": selection.get("case_count"),
            "delay_errors_ns": selection.get("delay_errors_ns"),
            "seeds": selection.get("seeds"),
            "target_velocities_mps": selection.get("target_velocities_mps"),
            "target_snr_db": selection.get("target_snr_db"),
        },
        "method_ids": method_ids,
        "input_contract": manifest.get("input_contract"),
        "downstream_compact_contract": manifest.get("downstream_compact_contract"),
        "production_contract": manifest.get("production_contract"),
        "evidence_files": list(COMPACT_EVIDENCE_FILES),
        "raw_outputs": "kept outside this compact tracked evidence directory",
    }


def build_compact_evidence(manifest_path: Path, destination: Path) -> dict[str, Path]:
    """Create exactly six compact files from a runner manifest."""

    manifest = _read_manifest(Path(manifest_path))
    destination = Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"refuse to overwrite non-empty compact evidence root: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    analysis = analyze_delay_evidence(manifest)
    decision = str(analysis["decision"])
    methods = manifest.get("method_contract")
    static_methods = [dict(item) for item in methods or [] if isinstance(item, Mapping)]
    rows = manifest.get("compact_rows")
    if not isinstance(rows, Mapping):
        rows = {}
    method_rows = rows.get("method_contract")
    gamma_rows = rows.get("gamma_recovery")
    clutter_rows = rows.get("clutter_metrics")
    gamma_rows = [dict(item) for item in gamma_rows or [] if isinstance(item, Mapping)]
    clutter_rows = [dict(item) for item in clutter_rows or [] if isinstance(item, Mapping)]

    outputs: dict[str, Path] = {}
    compact_manifest = _compact_manifest(manifest, decision)
    compact_manifest["analysis"] = analysis
    outputs["manifest.json"] = destination / "manifest.json"
    _write_json(outputs["manifest.json"], compact_manifest)

    method_fields = [
        "method_id",
        "description",
        "family",
        "research_calibration_enable",
        "research_calibration_method",
        "input_correction",
        "known_error",
        "known_error_use",
        "truth_used_in_estimator",
    ]
    outputs["method_contract.csv"] = destination / "method_contract.csv"
    _write_csv(outputs["method_contract.csv"], static_methods, method_fields)

    outputs["gamma_recovery.csv"] = destination / "gamma_recovery.csv"
    _write_csv(
        outputs["gamma_recovery.csv"],
        gamma_rows,
        [
            "case",
            "method_id",
            "mode",
            "role",
            "method",
            "status",
            "reason",
            "support_count",
            "excluded_count",
            "gamma_real",
            "gamma_imag",
            "gamma_abs",
            "phase_coherence",
            "truth_used_in_estimator",
            "reference_source",
            "group_id",
            "az_index",
            "range_start",
            "range_end",
        ],
    )

    outputs["clutter_metrics.csv"] = destination / "clutter_metrics.csv"
    _write_csv(
        outputs["clutter_metrics.csv"],
        clutter_rows,
        [
            "case",
            "method_id",
            "mode",
            "role",
            "layer",
            "status",
            "target_protection_rule",
            "cell_false_hit_status",
            "cell_false_hit_fraction",
            "hit_cut_count",
            "valid_cut_count",
            "cell_pfa",
            "on_minus_off_target_detection_pd",
            "on_minus_off_track_pd",
            "to_target_detection_pd",
            "to_track_pd",
            "cluster_layer",
            "protocol_detection_layer",
            "track_layer",
            "track_association_audit_v2",
            "track_states",
            "track_output_payloads",
            "id_switch_classification",
        ],
    )

    outputs["decision_matrix.csv"] = destination / "decision_matrix.csv"
    decision_row = {
        "decision": decision,
        "status": manifest.get("status"),
        "algorithmic_results_claimed": manifest.get("algorithmic_results_claimed", False),
        "analysis_status": analysis.get("status"),
        "residual_closure_status": analysis.get("residual_closure", {}).get("status"),
        "empirical_cell_pfa_status": analysis.get("empirical_cell_pfa", {}).get("status"),
        "causal_metrics_status": analysis.get("causal_metrics", {}).get("status"),
        "waterfall_status": analysis.get("waterfall", {}).get("status"),
        "id_switch_status": analysis.get("id_switch", {}).get("status"),
            "causal_triplet_required": True,
            "valid_cut_denominator_required": True,
            "target_protection_rule": "causal_ON_minus_OFF_and_TO; never_ON_power_alone",
            "track_evidence_required": "track_association_audit_v2,track_states,track_output_payloads,id_switch_classification_or_NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG",
            "source_dirty_refused_for_formal": bool(
            isinstance(manifest.get("source"), Mapping)
            and manifest["source"].get("dirty_tracked")
        ),
    }
    _write_csv(
        outputs["decision_matrix.csv"],
        [decision_row],
        [
            "decision",
            "status",
            "algorithmic_results_claimed",
            "analysis_status",
            "residual_closure_status",
            "empirical_cell_pfa_status",
            "causal_metrics_status",
            "waterfall_status",
            "id_switch_status",
            "causal_triplet_required",
            "valid_cut_denominator_required",
            "target_protection_rule",
            "track_evidence_required",
            "source_dirty_refused_for_formal",
        ],
    )

    outputs["report.md"] = destination / "report.md"
    skip_note = (
        "This is a contract/skip artifact; no production algorithm result is claimed."
        if manifest.get("skip_cuda")
        else "Production results require the recorded CUDA run and all downstream audit files."
    )
    question_lines = "\n".join(
        f"- {key}: `{value}`" for key, value in analysis["questions"].items()
    )
    outputs["report.md"].write_text(
        "# Production Hierarchical Calibration Pilot\n\n"
        f"- Status: `{manifest.get('status')}`\n"
        f"- Decision: `{decision}`\n"
        f"- {skip_note}\n"
        "- Cell Pfa 只使用 `hit_cut_count / valid_cut_count`，且分母必须为正；configured Pfa 不是实测 Pfa。\n"
        "- 目标保护只从 `OFF=C+N`、`ON=S+C+N`、`TO=S` 的 ON−OFF 与 TO 因果量报告，禁止用 ON power alone。\n"
        "- 下游层级严格分开：CFAR → cluster → protocol detection → TrackManager track。\n"
        "- Track evidence 使用 association/state/payload；现有 debug 无法解释 ID-switch 时标记 `NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG`。\n"
        "- A0/PK/PKR residual closure 在 estimator 归因之前检查。\n\n"
        "## Delay-stage questions\n\n" + question_lines + "\n\n"
        "原始 simulator/production 输出保留在 compact 目录之外。\n",
        encoding="utf-8",
    )
    validate_compact_evidence_root(destination)
    return outputs


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    try:
        outputs = build_compact_evidence(args.manifest, args.output_root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[production_hierarchical_calibration_pilot_analyzer][ERROR] {exc}")
        return 2
    print(json.dumps({key: str(value) for key, value in outputs.items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
