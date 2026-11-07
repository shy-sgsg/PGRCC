#!/usr/bin/env python3
"""Re-evaluate archived R8/R9/R12 task metrics with the current R7 CFAR evaluator."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EVALUATOR = ROOT / "scripts/phase_r/evaluate_r7_task_metrics.py"
R13_EVIDENCE = ROOT / "docs/research/phase_r/evidence/r13_baseline_cuda_20261024"

COLUMNS = [
    "phase",
    "evaluation",
    "condition",
    "role",
    "previous_status",
    "previous_reported_union_hit_cells",
    "previous_union_value_semantics",
    "previous_branch_hit_sum",
    "current_status",
    "current_union_hit_cells",
    "current_valid_cut_count",
    "current_observed_hit_fraction",
    "current_branch_hit_sum",
    "current_union_map_rows",
    "expected_period_beam_rows",
]


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _evaluate(manifest_path: Path, temp_root: Path, index: int) -> dict:
    output_json = temp_root / f"metrics_{index}.json"
    command = [
        sys.executable,
        str(EVALUATOR),
        "--manifest",
        str(manifest_path),
        "--output-json",
        str(output_json),
        "--association-csv",
        str(temp_root / f"associations_{index}.csv"),
        "--lineage-csv",
        str(temp_root / f"lineage_{index}.csv"),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0 or not output_json.is_file():
        raise RuntimeError(
            f"current evaluator failed for {manifest_path}:\n{completed.stdout[-3000:]}"
        )
    return _read_json(output_json)


def _rows(phase: str, evaluation_root: Path, temp_root: Path, start_index: int) -> list[dict]:
    if not evaluation_root.is_dir():
        raise FileNotFoundError(f"historical evaluation root not found: {evaluation_root}")
    manifests = sorted(evaluation_root.rglob("evaluation_manifest.json"))
    if not manifests:
        raise FileNotFoundError(f"no evaluation manifests found under {evaluation_root}")

    result = []
    for offset, manifest_path in enumerate(manifests):
        previous_path = manifest_path.with_name("task_metrics.json")
        if not previous_path.is_file():
            raise FileNotFoundError(f"historical task metrics not found: {previous_path}")
        previous = _read_json(previous_path).get("metrics", {})
        old_union = previous.get("unique_union_hit_cells", {})
        manifest = _read_json(manifest_path)
        current = _evaluate(manifest_path, temp_root, start_index + offset).get("metrics", {})
        union = current.get("unique_union_hit_cells", {})
        previous_count = old_union.get("count")
        result.append(
            {
                "phase": phase,
                "evaluation": manifest_path.parent.relative_to(evaluation_root).as_posix(),
                "condition": manifest.get("condition"),
                "role": manifest.get("mode"),
                "previous_status": old_union.get("status"),
                "previous_reported_union_hit_cells": previous_count,
                "previous_union_value_semantics": (
                    "max_of_period_values" if previous_count is not None else "not_evaluable"
                ),
                "previous_branch_hit_sum": previous.get("cfar_branch_hit_cells"),
                "current_status": union.get("status"),
                "current_union_hit_cells": union.get("count"),
                "current_valid_cut_count": union.get("valid_cut_count"),
                "current_observed_hit_fraction": union.get("observed_hit_fraction"),
                "current_branch_hit_sum": current.get("cfar_branch_hit_cells"),
                "current_union_map_rows": len(union.get("period_beam_rows", [])),
                "expected_period_beam_rows": len(union.get("expected_period_beam_keys") or []),
            }
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--r8-evaluation-root",
        type=Path,
        default=R13_EVIDENCE.parent / "r8_unknown_delay_multiseed_20261023/evaluation",
    )
    parser.add_argument(
        "--r9-evaluation-root",
        type=Path,
        default=ROOT / "outputs/phase_r_r9_deterministic_delay_20261023/evaluation",
    )
    parser.add_argument(
        "--r12-evaluation-root",
        type=Path,
        default=R13_EVIDENCE.parent / "r12_physics_matched_delay_20261024/evaluation",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=R13_EVIDENCE / "analysis/historical_cfar_impact.csv",
    )
    args = parser.parse_args()

    roots = [("R8", args.r8_evaluation_root), ("R9", args.r9_evaluation_root), ("R12", args.r12_evaluation_root)]
    rows: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="r13-historical-cfar-") as temp:
        temp_root = Path(temp)
        next_index = 0
        for phase, root in roots:
            phase_rows = _rows(phase, root.resolve(), temp_root, next_index)
            rows.extend(phase_rows)
            next_index += len(phase_rows)

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    summary = {}
    for phase, _ in roots:
        phase_rows = [row for row in rows if row["phase"] == phase]
        summary[phase] = {
            "evaluations": len(phase_rows),
            "map_evaluable": sum(row["current_status"] == "evaluable" for row in phase_rows),
            "not_evaluable": sum(row["current_status"] != "evaluable" for row in phase_rows),
        }
    print(json.dumps({"output_csv": str(args.output_csv), "summary": summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
