#!/usr/bin/env python3
"""Aggregate compact mixed-mechanism stress rows without training AI.

Inputs are already-produced feature tables.  This intentionally avoids a
large Cartesian simulator sweep; callers can provide a small LHS/Sobol sample
of M1+M2, M1+M3, M2+M3, and all-mechanism rows and receive a reproducible
confusion matrix plus confidence buckets.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def confidence(row: dict[str, str]) -> float:
    values = []
    for key in ("deterministic_phase_confidence", "channel_coherence",
                "feature_confidence", "csi_coherence"):
        try:
            value = float(row.get(key, "nan"))
        except ValueError:
            continue
        if math.isfinite(value):
            values.append(max(0.0, min(1.0, value)))
    return sum(values) / len(values) if values else 0.0


def predicted_family(diagnosis: str) -> str:
    return {
        "delay_like": "M1",
        "phase_drift_like": "M2",
        "decorrelation_like": "M3",
        "H0_no_clutter": "H0",
        "H0_low_coherence": "H0",
        "unknown_mixed": "unknown_mixed",
    }.get(diagnosis, "unknown_mixed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-table", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    rows: list[dict[str, Any]] = []
    for table in args.feature_table:
        for row in read_rows(table.resolve()):
            actual = row.get("actual_mechanism", row.get("mechanism_label", "unknown"))
            diagnosis = row.get("feature_diagnosis", row.get("diagnosis", "unknown_mixed"))
            predicted = predicted_family(diagnosis)
            conf = confidence(row)
            rows.append({"actual_mechanism": actual, "predicted_mechanism": predicted,
                         "diagnosis": diagnosis,
                         "confidence": conf,
                         "confidence_bucket": "high" if conf >= 0.8 else "medium" if conf >= 0.5 else "low",
                         "source": row.get("source", "")})
    counts = Counter((row["actual_mechanism"], row["predicted_mechanism"]) for row in rows)
    matrix_rows = [{"actual_mechanism": actual, "predicted_mechanism": predicted, "count": count}
                   for (actual, predicted), count in sorted(counts.items())]
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / "mechanism_confusion_matrix.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["actual_mechanism", "predicted_mechanism", "count"])
        writer.writeheader()
        writer.writerows(matrix_rows)
    with (output / "mechanism_stress_rows.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["actual_mechanism", "predicted_mechanism", "diagnosis", "confidence",
                  "confidence_bucket", "source"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "schema_version": 1,
        "ai_training": False,
        "row_count": len(rows),
        "mixed_mechanism_rows": sum("+" in row["actual_mechanism"] or "all" in row["actual_mechanism"].lower()
                                     for row in rows),
        "confusion_matrix": matrix_rows,
        "confidence_buckets": {bucket: sum(row["confidence_bucket"] == bucket for row in rows)
                               for bucket in ("high", "medium", "low")},
    }
    (output / "mechanism_mixed_stress_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "row_count": len(rows),
                      "mixed_mechanism_rows": summary["mixed_mechanism_rows"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
