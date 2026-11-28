#!/usr/bin/env python3
"""Compare saved C4 gamma summaries; no per-cell or CUDA inference."""

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE.parent / "evidence" / "screening_checkpoint"
INPUTS = {
    "on20": EVIDENCE / "snr20_c4_modeb_gamma.csv",
    "on30": EVIDENCE / "snr30_c4_modeb_gamma.csv",
    "off30": HERE / "snr30_off_reference_gamma.csv",
}
KEYS = ("period_id", "beam_id", "az_index", "range_start", "range_end")


def load(path):
    with path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    result = {}
    for row in rows:
        key = tuple(int(row[field]) for field in KEYS)
        if key in result:
            raise ValueError(f"duplicate group {key}: {path}")
        result[key] = row
    return result


def main():
    data = {name: load(path) for name, path in INPUTS.items()}
    keys = set(data["on20"])
    if not keys or any(set(rows) != keys for rows in data.values()):
        raise ValueError("reference group identities differ")
    fields = list(KEYS) + [
        "on20_status", "on20_support", "on20_coherence", "on30_status",
        "on30_support", "on30_coherence", "off30_status", "off30_support",
        "off30_coherence", "on30_minus_on20_support", "on30_minus_off30_support",
    ]
    changes = Counter()
    failure = (5, 50, 52, 1065, 1197)
    if failure not in keys:
        raise ValueError("expected failure group missing")
    with (HERE / "group_comparison.csv").open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for key in sorted(keys):
            a, b, c = (data[name][key] for name in ("on20", "on30", "off30"))
            counts = [int(row["support_count"]) for row in (a, b, c)]
            delta = counts[1] - counts[0]
            changes["decreased" if delta < 0 else "increased" if delta > 0 else "unchanged"] += 1
            row = dict(zip(KEYS, key))
            for name, source in (("on20", a), ("on30", b), ("off30", c)):
                row.update({f"{name}_status": source["status"],
                            f"{name}_support": source["support_count"],
                            f"{name}_coherence": source["phase_coherence"]})
            row["on30_minus_on20_support"] = delta
            row["on30_minus_off30_support"] = counts[1] - counts[2]
            writer.writerow(row)
    summary = {
        "independent_seeds": 1,
        "group_count": len(keys),
        "on30_vs_on20_group_support_changes": dict(changes),
        "failure_group": dict(zip(KEYS, failure)),
        "failure_group_rows": {name: data[name][failure] for name in data},
        "source_sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest()
                          for name, path in INPUTS.items()},
        "per_cell_phase_and_support": "NOT_AVAILABLE_FROM_GROUP_SUMMARY",
        "performance_verified": False,
    }
    (HERE / "group_comparison_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"groups": len(keys), "changes": dict(changes),
                      "failure_support_on20_on30_off30": [
                          int(data[name][failure]["support_count"])
                          for name in ("on20", "on30", "off30")]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
