#!/usr/bin/env python3
"""Evaluate positive-target Pd from a production mismatch result.

This is an offline evaluator only.  It consumes the production detection
snapshot and Stage2 truth; it never changes detection output and never uses
the diagnostic ``target_*_truth`` columns carried by detection rows as an
estimated position.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

from gmti_eval_match import one_to_one_match


ROOT = Path(__file__).resolve().parents[1]
MATCH_CONFIG = ROOT / "configs/eval/localization_match_config.json"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def evaluate_variant(variant: Path) -> dict[str, Any]:
    """Return positive-run detection counts using the existing truth matcher."""

    snapshot = variant / "stage2/algorithm_result/period_0000/detection_results_GMTI01.csv"
    truth_path = variant / "stage2/truth/truth_targets_by_beam.csv"
    if not snapshot.is_file() or not truth_path.is_file():
        return {
            "status": "unavailable",
            "reason": "missing production detection snapshot or visible-target truth",
            "Pd": math.nan,
            "visible_target_count": math.nan,
            "detection_count": math.nan,
            "matched_target_count": math.nan,
            "false_alarm_count": math.nan,
        }

    detections = read_csv(snapshot)
    truth = [
        row for row in read_csv(truth_path)
        if str(row.get("visible", "1")).strip().lower() not in {"0", "false", "no"}
    ]
    if not truth:
        return {
            "status": "unavailable",
            "reason": "no visible target in truth",
            "Pd": math.nan,
            "visible_target_count": 0,
            "detection_count": len(detections),
            "matched_target_count": 0,
            "false_alarm_count": len(detections),
        }

    match_config = json.loads(MATCH_CONFIG.read_text(encoding="utf-8"))
    matches, _, _ = one_to_one_match(
        detections,
        truth,
        match_config,
        "det_id",
        use_beam=True,
        use_range=True,
        use_velocity=False,
    )
    matched = len(matches)
    return {
        "status": "measured",
        "reason": (
            "production detection snapshot matched one-to-one to visible truth "
            "with localization_match_config.json; item position excludes target_*_truth"
        ),
        "Pd": matched / len(truth),
        "visible_target_count": len(truth),
        "detection_count": len(detections),
        "matched_target_count": matched,
        "false_alarm_count": len(detections) - matched,
        "source_detection_snapshot": str(snapshot),
        "source_truth": str(truth_path),
    }
