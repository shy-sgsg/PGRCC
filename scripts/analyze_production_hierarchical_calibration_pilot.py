#!/usr/bin/env python3
"""Compact, fail-closed analyzer for the production delay pilot."""

from __future__ import annotations

import argparse
import csv
import json
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

    decision = choose_decision(manifest)
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
            "role",
            "method",
            "status",
            "support_count",
            "excluded_count",
            "gamma_real",
            "gamma_imag",
            "gamma_abs",
            "phase_coherence",
            "truth_used_in_estimator",
        ],
    )

    outputs["clutter_metrics.csv"] = destination / "clutter_metrics.csv"
    _write_csv(
        outputs["clutter_metrics.csv"],
        clutter_rows,
        [
            "case",
            "method_id",
            "cell_false_hit_status",
            "cell_false_hit_fraction",
            "hit_cut_count",
            "valid_cut_count",
            "cluster_layer",
            "protocol_detection_layer",
            "track_layer",
        ],
    )

    outputs["decision_matrix.csv"] = destination / "decision_matrix.csv"
    decision_row = {
        "decision": decision,
        "status": manifest.get("status"),
        "algorithmic_results_claimed": manifest.get("algorithmic_results_claimed", False),
        "causal_triplet_required": True,
        "valid_cut_denominator_required": True,
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
            "causal_triplet_required",
            "valid_cut_denominator_required",
            "source_dirty_refused_for_formal",
        ],
    )

    outputs["report.md"] = destination / "report.md"
    skip_note = (
        "This is a contract/skip artifact; no production algorithm result is claimed."
        if manifest.get("skip_cuda")
        else "Production results require the recorded CUDA run and all downstream audit files."
    )
    outputs["report.md"].write_text(
        "# Production Hierarchical Calibration Pilot\n\n"
        f"- Status: `{manifest.get('status')}`\n"
        f"- Decision: `{decision}`\n"
        f"- {skip_note}\n"
        "- Cell Pfa is evaluable only as `hit_cut_count / valid_cut_count` with a positive denominator.\n"
        "- Target accounting requires the causal `OFF=C+N`, `ON=S+C+N`, `TO=S` triplet.\n"
        "- Raw simulator and production outputs remain outside this compact directory.\n",
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
