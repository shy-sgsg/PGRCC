#!/usr/bin/env python3
"""Repair retained summary CSVs after tightening the target-match gate.

The formal runs retain ``standard_mc_period_metrics.csv`` even after raw maps and
per-cycle outputs are removed.  This utility applies the production event rule
to those retained rows only:

    target_event = target_event_raw AND cluster_event

It then recomputes the screen and curve summaries.  No raw BIN/F32, detector
output, or hit rate is opened or fitted; the repair is therefore reproducible
after the authorized intermediate cleanup.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


ANGLES = (0.0, 30.0, 45.0)


def number(value: object, default: float = float("nan")) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def integer(value: object, default: int = 0) -> int:
    parsed = number(value)
    return int(round(parsed)) if math.isfinite(parsed) else default


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str] | None = None) -> None:
    if fields is None:
        fields = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def wilson(hits: int, trials: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if trials <= 0:
        return float("nan"), float("nan")
    p = hits / trials
    den = 1.0 + z * z / trials
    centre = (p + z * z / (2.0 * trials)) / den
    half = z * math.sqrt(max(0.0, p * (1.0 - p) / trials + z * z / (4.0 * trials * trials))) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def repair_root(root: Path) -> dict[str, object]:
    changed = 0
    before = 0
    after = 0
    for angle in ANGLES:
        token = f"angle_{angle:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")
        angle_dir = root / token
        period_path = angle_dir / "standard_mc_period_metrics.csv"
        screen_path = angle_dir / "standard_mc_screen_metrics.csv"
        curve_path = angle_dir / "standard_mc_curve_summary.csv"
        if not period_path.is_file() or not screen_path.is_file() or not curve_path.is_file():
            raise SystemExit(f"缺少保留汇总：{angle_dir}")
        periods: list[dict[str, object]] = [dict(row) for row in read_csv(period_path)]
        for row in periods:
            raw = integer(row.get("target_event_raw"), integer(row.get("target_event"), 0))
            old = integer(row.get("target_event"), 0)
            gated = int(raw and integer(row.get("cluster_event"), 0))
            before += old
            after += gated
            changed += int(old != gated)
            row["target_event_raw"] = raw
            row["target_event"] = gated
            row["target_gate_reason"] = (
                "raw_truth_match_and_cluster_pass" if gated else
                "raw_truth_match_rejected_without_cluster" if raw else
                "no_raw_truth_match")
        period_fields = list(read_csv(period_path)[0].keys())
        for extra in ("target_event_raw", "target_gate_reason"):
            if extra not in period_fields:
                period_fields.append(extra)
        write_csv(period_path, periods, period_fields)

        old_screens = read_csv(screen_path)
        screens: list[dict[str, object]] = []
        for group in old_screens:
            start = integer(group.get("period_start"))
            count = integer(group.get("period_count"), 3)
            values = periods[start:start + count]
            target = [integer(row.get("target_event")) for row in values]
            angle_errors = [number(row.get("angle_error_deg")) for row in values]
            finite_errors = [x for x in angle_errors if math.isfinite(x)]
            group_out: dict[str, object] = dict(group)
            group_out["screen1_hit"] = target[0] if len(target) > 0 else 0
            group_out["screen2_hit"] = target[1] if len(target) > 1 else 0
            group_out["screen3_hit"] = target[2] if len(target) > 2 else 0
            group_out["screen_hit_sum"] = sum(target)
            group_out["track_2of3_hit"] = int(sum(target) >= 2)
            group_out["angle_rmse_deg"] = (math.sqrt(sum(x * x for x in finite_errors) / len(finite_errors))
                                             if finite_errors else float("nan"))
            screens.append(group_out)
        screen_fields = list(old_screens[0].keys()) if old_screens else list(screens[0].keys())
        write_csv(screen_path, screens, screen_fields)

        old_curves = read_csv(curve_path)
        curves: list[dict[str, object]] = []
        for old_curve in old_curves:
            level = integer(old_curve.get("level_index"), -1)
            values = [row for row in periods if integer(row.get("input_snr_db_index"), -1) == level]
            groups = [row for row in screens if integer(row.get("level_index"), -1) == level]
            out: dict[str, object] = dict(old_curve)
            for field in ("unit_exact", "unit_resolution", "cluster", "target"):
                if field == "unit_exact": hits = sum(integer(row.get("unit_exact")) for row in values)
                elif field == "unit_resolution": hits = sum(integer(row.get("unit_resolution")) for row in values)
                elif field == "cluster": hits = sum(integer(row.get("cluster_event")) for row in values)
                elif field == "target": hits = sum(integer(row.get("target_event")) for row in values)
                trials = len(values)
                out[f"{field}_hits"] = hits; out[f"{field}_trials"] = trials
                out[f"{field}_pd"] = hits / trials if trials else float("nan")
                low, high = wilson(hits, trials)
                out[f"{field}_wilson_low"] = low; out[f"{field}_wilson_high"] = high
            track_hits = sum(integer(row.get("track_2of3_hit")) for row in groups)
            track_trials = len(groups)
            out["track_hits"] = track_hits; out["track_trials"] = track_trials
            out["track_pd"] = track_hits / track_trials if track_trials else float("nan")
            out["track_wilson_low"], out["track_wilson_high"] = wilson(track_hits, track_trials)
            errors = [number(row.get("angle_error_deg")) for row in values]
            errors = [x for x in errors if math.isfinite(x)]
            out["angle_matched"] = len(errors)
            out["angle_rmse_deg"] = math.sqrt(sum(x * x for x in errors) / len(errors)) if errors else float("nan")
            out["angle_bias_deg"] = sum(errors) / len(errors) if errors else float("nan")
            curves.append(out)
        curve_fields = list(old_curves[0].keys()) if old_curves else list(curves[0].keys())
        write_csv(curve_path, curves, curve_fields)
    audit = {"status": "pass", "root": str(root.resolve()), "target_gate": "target_event = target_event_raw AND cluster_event", "period_rows_changed": changed, "target_events_before": before, "target_events_after": after, "raw_bin_opened": False, "f32_opened": False}
    (root / "target_gate_repair.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, action="append", required=True)
    args = parser.parse_args()
    results = [repair_root(path.resolve()) for path in args.root]
    print(json.dumps({"status": "pass", "roots": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
