#!/usr/bin/env python3
"""Derive and audit the evaluator-only geometry-correction deadband.

The production estimator never sees this file or the sweep truth.  The sweep
is used here only to quantify the zero-error estimator floor and to make the
no-action decision reproducible for an evaluation report.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Iterable

import numpy as np

from unknown_geometry_correction import (
    decide_unknown_geometry_correction,
    derive_deadband_from_sweep,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_value(root: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "--git-dir=.git-real", "--work-tree=.", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _read_rows(path: Path) -> list[dict[str, object]]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        rows: list[dict[str, object]] = []
        for row in reader:
            rows.append(dict(row))
    if not rows:
        raise ValueError(f"no rows in {path}")
    return rows


def _finite_values(rows: Iterable[dict[str, object]], key: str) -> np.ndarray:
    values: list[float] = []
    for row in rows:
        try:
            value = float(row[key])
        except (KeyError, TypeError, ValueError):
            continue
        if np.isfinite(value):
            values.append(value)
    if not values:
        raise ValueError(f"no finite {key} values")
    return np.asarray(values, dtype=np.float64)


def _decision_row(
    label: str,
    estimate: float | None,
    uncertainty_m: float,
    deadband_m: float,
) -> dict[str, object]:
    decision = decide_unknown_geometry_correction(
        estimated_delta_m=estimate,
        uncertainty_m=uncertainty_m,
        deadband_m=deadband_m,
    )
    return {"label": label, **decision}


def audit(input_dir: Path, output_dir: Path, repo_root: Path) -> dict[str, object]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    input_csv = input_dir / "matrix_rows.csv"
    rows = _read_rows(input_csv)
    summary = derive_deadband_from_sweep(rows)

    zero_estimates = _finite_values(
        [row for row in rows if float(row["truth_delta_m"]) == 0.0],
        "six_estimate_m",
    )
    uncertainty_m = float(np.std(zero_estimates, ddof=1)) if len(zero_estimates) > 1 else 0.0
    deadband_m = float(summary["deadband_m"])
    decision_rows = [
        _decision_row(
            "baseline_zero_mean",
            float(np.mean(zero_estimates)),
            uncertainty_m,
            deadband_m,
        ),
        _decision_row(
            "representative_plus_2p5mm_mean",
            float(
                np.mean(
                    _finite_values(
                        [row for row in rows if float(row["truth_delta_m"]) == 0.0025],
                        "six_estimate_m",
                    )
                )
            ),
            uncertainty_m,
            deadband_m,
        ),
        _decision_row("missing_estimate", None, uncertainty_m, deadband_m),
    ]

    with (output_dir / "correction_decisions.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(decision_rows[0]))
        writer.writeheader()
        writer.writerows(decision_rows)

    (output_dir / "deadband_summary.json").write_text(
        json.dumps(
            {
                **summary,
                "zero_level_sample_std_m": uncertainty_m,
                "input_matrix_rows": len(rows),
                "input_matrix_csv": str(input_csv),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema": "unknown_geometry_deadband_audit_manifest_v1",
        "input_dir": str(input_dir),
        "input_matrix_csv": str(input_csv),
        "input_matrix_sha256": _sha256(input_csv),
        "output_dir": str(output_dir),
        "row_count": len(rows),
        "source_commit": _git_value(repo_root, "rev-parse", "HEAD"),
        "tracked_worktree_status": _git_value(
            repo_root, "status", "--short", "--untracked-files=no"
        ),
        "evaluator_only": True,
        "production_estimator_changed": False,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return {"summary": summary, "decisions": decision_rows, "manifest": manifest}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    result = audit(args.input_dir, args.output_dir, args.repo_root)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
