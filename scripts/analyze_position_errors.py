#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Analyze GMTI positioning errors by matching detection_results.csv with truth_targets_by_beam.csv.

Typical usage:
  python analyze_position_errors.py \
      --detection detection_results.csv \
      --truth truth_targets_by_beam.csv \
      --out-prefix eval/beam51

The algorithm software should not know truth coordinates. This evaluator joins truth offline
and computes per-method positioning errors for old / iterative / analytic / root1d / new.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd


METHOD_COORDS = {
    "old": ("old_e", "old_n"),
    "iterative": ("iterative_e", "iterative_n"),
    "analytic": ("analytic_e", "analytic_n"),
    "root1d": ("root1d_e", "root1d_n"),
    "new": ("new_e", "new_n"),
    # Optional fallbacks sometimes present in older outputs.
    "reported": ("e", "n"),
}

TRUTH_COORD_CANDIDATES = [
    ("target_e_truth", "target_n_truth"),
    ("target_e", "target_n"),
    ("truth_e", "truth_n"),
    ("e_truth", "n_truth"),
    ("e", "n"),
]

MATCH_COL_CANDIDATES = ["period_id", "beam_id", "range_bin"]


def read_csv_auto(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    # utf-8-sig handles CSVs exported by Excel without hurting normal UTF-8.
    return pd.read_csv(path, encoding="utf-8-sig")


def first_existing_pair(df: pd.DataFrame, pairs: Iterable[Tuple[str, str]]) -> Tuple[str, str]:
    for a, b in pairs:
        if a in df.columns and b in df.columns:
            return a, b
    raise ValueError(
        "Cannot find truth E/N columns. Tried: "
        + ", ".join([f"({a},{b})" for a, b in pairs])
    )


def finite_mask(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan).notna()


def normalize_truth(truth: pd.DataFrame) -> pd.DataFrame:
    truth = truth.copy()
    e_col, n_col = first_existing_pair(truth, TRUTH_COORD_CANDIDATES)
    truth["truth_e_eval"] = pd.to_numeric(truth[e_col], errors="coerce")
    truth["truth_n_eval"] = pd.to_numeric(truth[n_col], errors="coerce")
    truth = truth[finite_mask(truth["truth_e_eval"]) & finite_mask(truth["truth_n_eval"])].copy()
    if truth.empty:
        raise ValueError("Truth file has no valid truth coordinates after filtering.")

    # Normalize common aliases.
    if "truth_beam_id" in truth.columns and "beam_id" not in truth.columns:
        truth["beam_id"] = truth["truth_beam_id"]
    if "truth_range_bin" in truth.columns and "range_bin" not in truth.columns:
        truth["range_bin"] = truth["truth_range_bin"]
    return truth


def normalize_detection(det: pd.DataFrame) -> pd.DataFrame:
    det = det.copy()
    # If the detection CSV already contains truth, keep it as optional check/fallback.
    if "target_e_truth" in det.columns and "target_n_truth" in det.columns:
        det["det_embedded_truth_e"] = pd.to_numeric(det["target_e_truth"], errors="coerce")
        det["det_embedded_truth_n"] = pd.to_numeric(det["target_n_truth"], errors="coerce")
    return det


def score_candidates(cand: pd.DataFrame, truth_row: pd.Series, range_weight: float, row_weight: float) -> pd.Series:
    score = pd.Series(0.0, index=cand.index)
    if "range_bin" in cand.columns and "range_bin" in truth_row.index and pd.notna(truth_row.get("range_bin")):
        score += range_weight * (pd.to_numeric(cand["range_bin"], errors="coerce") - float(truth_row["range_bin"])).abs().fillna(1e9)
    elif "col" in cand.columns and "range_bin" in truth_row.index and pd.notna(truth_row.get("range_bin")):
        score += range_weight * (pd.to_numeric(cand["col"], errors="coerce") - float(truth_row["range_bin"])).abs().fillna(1e9)

    row_truth_col = None
    for c in ("row_truth", "expected_row", "row"):
        if c in truth_row.index and pd.notna(truth_row.get(c)):
            row_truth_col = c
            break
    if row_truth_col and "row" in cand.columns:
        score += row_weight * (pd.to_numeric(cand["row"], errors="coerce") - float(truth_row[row_truth_col])).abs().fillna(1e9)

    # Prefer higher power if the geometric score ties.
    if "power" in cand.columns:
        p = pd.to_numeric(cand["power"], errors="coerce")
        # Tiny negative contribution only breaks ties; it should not dominate range/row gates.
        p_norm = (p - p.min()) / (p.max() - p.min() + 1e-12)
        score -= 1e-6 * p_norm.fillna(0.0)
    return score


def match_truth_to_detections(
    det: pd.DataFrame,
    truth: pd.DataFrame,
    range_gate: float,
    row_gate: Optional[float],
    range_weight: float,
    row_weight: float,
    allow_reuse_detection: bool,
) -> pd.DataFrame:
    """Greedy one-truth-to-one-detection matching.

    This is designed for cooperative-target evaluation where truth gives beam/range_bin.
    It first gates by period_id/beam_id/range_bin when available, then chooses nearest range/row
    and highest power as tie-breaker.
    """
    matched_rows: List[pd.Series] = []
    used_det_idx: set[int] = set()

    for truth_idx, tr in truth.iterrows():
        cand = det.copy()

        for key in ("period_id", "beam_id"):
            if key in cand.columns and key in tr.index and pd.notna(tr.get(key)):
                cand = cand[pd.to_numeric(cand[key], errors="coerce") == float(tr[key])]

        if "range_bin" in cand.columns and "range_bin" in tr.index and pd.notna(tr.get("range_bin")):
            rb = float(tr["range_bin"])
            cand = cand[(pd.to_numeric(cand["range_bin"], errors="coerce") - rb).abs() <= range_gate]
        elif "col" in cand.columns and "range_bin" in tr.index and pd.notna(tr.get("range_bin")):
            rb = float(tr["range_bin"])
            cand = cand[(pd.to_numeric(cand["col"], errors="coerce") - rb).abs() <= range_gate]

        if row_gate is not None and "row" in cand.columns:
            row_truth_val = None
            for c in ("row_truth", "expected_row", "row"):
                if c in tr.index and pd.notna(tr.get(c)):
                    row_truth_val = float(tr[c])
                    break
            if row_truth_val is not None:
                cand = cand[(pd.to_numeric(cand["row"], errors="coerce") - row_truth_val).abs() <= row_gate]

        if not allow_reuse_detection and used_det_idx:
            cand = cand[~cand.index.isin(used_det_idx)]

        if cand.empty:
            out = pd.Series(dtype="object")
            out["truth_index"] = truth_idx
            out["matched"] = False
            for c in truth.index.names:
                pass
            for c in truth.columns:
                out[f"truth_{c}"] = tr[c]
            matched_rows.append(out)
            continue

        scores = score_candidates(cand, tr, range_weight=range_weight, row_weight=row_weight)
        best_idx = scores.idxmin()
        used_det_idx.add(int(best_idx))
        best = cand.loc[best_idx]

        out = pd.Series(dtype="object")
        out["truth_index"] = truth_idx
        out["detection_index"] = best_idx
        out["matched"] = True
        out["match_score"] = float(scores.loc[best_idx])
        for c in truth.columns:
            out[f"truth_{c}"] = tr[c]
        for c in det.columns:
            out[c] = best[c]
        out["truth_e_eval"] = tr["truth_e_eval"]
        out["truth_n_eval"] = tr["truth_n_eval"]
        matched_rows.append(out)

    return pd.DataFrame(matched_rows)


def add_method_errors(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    df = df.copy()
    methods: List[str] = []
    if "truth_e_eval" not in df.columns or "truth_n_eval" not in df.columns:
        raise ValueError("Internal error: truth_e_eval/truth_n_eval missing.")

    te = pd.to_numeric(df["truth_e_eval"], errors="coerce")
    tn = pd.to_numeric(df["truth_n_eval"], errors="coerce")

    for method, (ec, nc) in METHOD_COORDS.items():
        if ec not in df.columns or nc not in df.columns:
            continue
        e = pd.to_numeric(df[ec], errors="coerce")
        n = pd.to_numeric(df[nc], errors="coerce")
        valid = e.notna() & n.notna() & te.notna() & tn.notna() & df["matched"].astype(bool)
        if not valid.any():
            continue
        methods.append(method)
        df[f"{method}_dx_m"] = e - te
        df[f"{method}_dy_m"] = n - tn
        df[f"{method}_position_error_m"] = np.sqrt((e - te) ** 2 + (n - tn) ** 2)
    return df, methods


def summarize(df: pd.DataFrame, methods: List[str]) -> pd.DataFrame:
    rows = []
    for method in methods:
        col = f"{method}_position_error_m"
        if col not in df.columns:
            continue
        x = pd.to_numeric(df[col], errors="coerce").dropna()
        if x.empty:
            continue
        rows.append({
            "method": method,
            "count": int(x.size),
            "mean_m": float(x.mean()),
            "median_m": float(x.median()),
            "rmse_m": float(math.sqrt(np.mean(x.to_numpy() ** 2))),
            "p90_m": float(x.quantile(0.90)),
            "p95_m": float(x.quantile(0.95)),
            "max_m": float(x.max()),
            "hit_5m": float((x <= 5).mean()),
            "hit_10m": float((x <= 10).mean()),
            "hit_50m": float((x <= 50).mean()),
            "hit_100m": float((x <= 100).mean()),
        })
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate GMTI positioning errors by joining truth and detection CSVs.")
    ap.add_argument("--detection", required=True, help="Path to detection_results.csv")
    ap.add_argument("--truth", required=True, help="Path to truth_targets_by_beam.csv or equivalent truth CSV")
    ap.add_argument("--out-prefix", default="position_eval", help="Output prefix, e.g. eval/beam51")
    ap.add_argument("--range-gate", type=float, default=5.0, help="Max |det.range_bin - truth.range_bin| for matching")
    ap.add_argument("--row-gate", type=float, default=None, help="Optional max |det.row - truth.row_truth| for matching")
    ap.add_argument("--range-weight", type=float, default=1.0, help="Match score weight for range-bin difference")
    ap.add_argument("--row-weight", type=float, default=0.2, help="Match score weight for row difference")
    ap.add_argument("--allow-reuse-detection", action="store_true", help="Allow one detection to match multiple truth rows")
    args = ap.parse_args()

    det = normalize_detection(read_csv_auto(args.detection))
    truth = normalize_truth(read_csv_auto(args.truth))

    matched = match_truth_to_detections(
        det=det,
        truth=truth,
        range_gate=args.range_gate,
        row_gate=args.row_gate,
        range_weight=args.range_weight,
        row_weight=args.row_weight,
        allow_reuse_detection=args.allow_reuse_detection,
    )
    matched, methods = add_method_errors(matched)
    summary = summarize(matched, methods)

    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    matched_path = out_prefix.with_name(out_prefix.name + "_matched_errors.csv")
    summary_path = out_prefix.with_name(out_prefix.name + "_summary.csv")
    matched.to_csv(matched_path, index=False, encoding="utf-8-sig")
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")

    print(f"Matched truth targets: {int(matched['matched'].sum())}/{len(matched)}")
    print(f"Per-target output: {matched_path}")
    print(f"Summary output:    {summary_path}")
    if not summary.empty:
        with pd.option_context("display.max_columns", None, "display.width", 160):
            print(summary.to_string(index=False))
    else:
        print("No method coordinates were found or no matched rows had valid coordinates.")


if __name__ == "__main__":
    main()
