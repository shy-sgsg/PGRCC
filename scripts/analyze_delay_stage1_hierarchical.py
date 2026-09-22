#!/usr/bin/env python3
"""Scene-block statistics for the channel-delay Stage-1 evidence.

The unit of resampling in this module is a physical scene block.  Delay is a
within-block treatment and is deliberately excluded from the block identity.
The reader is evidence-first: it consumes compact CSV summaries and reports
missing, duplicate, or non-evaluable pairs instead of filling them silently.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


DEFAULT_DELAYS_NS = (-8.0, -4.0, -2.0, -1.0, 1.0, 2.0, 4.0, 8.0)
_NA_STATUS = {"", "NA", "N/A", "NOT_EVALUABLE", "MISSING", "ERROR"}


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _finite(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _status_ok(value: object) -> bool:
    return _text(value).upper() not in _NA_STATUS


def _number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else float(value)


def _delay(row: Mapping[str, object]) -> float | None:
    for key in ("delay_error_ns", "delay_ns", "treatment"):
        value = _finite(row.get(key))
        if value is not None:
            return value
    return None


def _delay_scene_identity(value: object) -> str:
    text = _text(value)
    if not text:
        return ""
    # The formal case naming convention uses delay_p1ns/delay_m1ns.  The
    # second form also handles a decimal suffix used by smaller pilots.
    text = re.sub(r"(?:__|_)delay_[pm]-?\d+(?:\.\d+)?ns$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:__|_)delay_(?:p|m)\d+(?:\.\d+)?ns$", "", text, flags=re.IGNORECASE)
    return text


def make_physical_block_id(row: Mapping[str, object]) -> str:
    """Build a delay-independent physical scene-block identity."""

    scene = _delay_scene_identity(row.get("scene_id", row.get("case_id", row.get("block_id"))))
    fields = (
        ("scene", scene),
        ("seed", row.get("seed")),
        ("velocity", row.get("target_velocity_mps", row.get("velocity_mps"))),
        ("snr", row.get("target_snr_db", row.get("snr_db"))),
        ("working_point", row.get("working_point")),
        ("texture_sigma", row.get("texture_sigma")),
        ("clutter_rho", row.get("clutter_rho", row.get("temporal_correlation_rho"))),
        ("geometry", row.get("geometry_id", row.get("geometry"))),
        ("beam", row.get("beam_id")),
        ("bin", row.get("expected_bin")),
    )
    tokens = [f"{name}={_text(value)}" for name, value in fields if _text(value)]
    if not tokens:
        tokens = ["scene=" + (scene or "unknown")]
    return "|".join(tokens)


def _block(row: Mapping[str, object]) -> str:
    return make_physical_block_id(row)


def _metric_direction(row: Mapping[str, object]) -> str:
    direction = _text(row.get("metric_direction", "higher_is_better")).lower()
    return "lower_is_better" if direction.startswith("lower") else "higher_is_better"


def _comparison_fields(comparison: str) -> tuple[str, str]:
    normalized = comparison.replace("→", "_").replace("-", "_").lower()
    if "a1" in normalized and "a2" in normalized:
        return "A1_value", "A2_value"
    if "a1" in normalized and "a3" in normalized:
        return "A1_value", "A3_value"
    raise ValueError(f"unsupported paired comparison: {comparison}")


def _directional_effect(row: Mapping[str, object], comparison: str) -> float | None:
    current_key, comparison_key = _comparison_fields(comparison)
    current = _finite(row.get(current_key))
    compared = _finite(row.get(comparison_key))
    if current is None or compared is None:
        return None
    return current - compared if _metric_direction(row) == "lower_is_better" else compared - current


def _bootstrap_values(values: Sequence[float], *, trials: int, seed: int) -> tuple[float, float]:
    import numpy as np

    array = np.asarray(values, dtype=float)
    if array.size == 1:
        return float(array[0]), float(array[0])
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, array.size, size=(max(1, int(trials)), array.size))
    means = array[draws].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def block_bootstrap_effect(
    rows: Sequence[Mapping[str, object]],
    *,
    block_key: str,
    treatment_key: str,
    value_key: str,
    comparison: str,
    trials: int,
    seed: int,
) -> dict[str, object]:
    """Estimate a mean effect with physical blocks as bootstrap units."""

    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        block = _text(row.get(block_key))
        treatment = _text(row.get(treatment_key))
        if not block:
            continue
        value = _finite(row.get(value_key))
        if value is None:
            try:
                current_key, comparison_key = _comparison_fields(comparison)
            except ValueError:
                value = None
            else:
                current = _finite(row.get(current_key))
                compared = _finite(row.get(comparison_key))
                if current is not None and compared is not None:
                    value = compared - current
        if value is not None:
            grouped[(block, treatment)].append(value)

    block_values: dict[tuple[str, str], float] = {
        key: float(sum(values) / len(values))
        for key, values in grouped.items()
        if values
    }
    if not block_values:
        return {
            "comparison": comparison,
            "status": "NOT_EVALUABLE",
            "reason": "no_finite_block_effects",
            "effect": None,
            "mean_effect": None,
            "ci_low": None,
            "ci_high": None,
            "block_count": 0,
            "effective_block_count": 0,
            "duplicate_block_treatment_count": 0,
        }

    values = list(block_values.values())
    ci_low, ci_high = _bootstrap_values(values, trials=trials, seed=seed)
    return {
        "comparison": comparison,
        "status": "passed",
        "reason": "block_bootstrap",
        "effect": float(sum(values) / len(values)),
        "mean_effect": float(sum(values) / len(values)),
        "ci_low": ci_low,
        "ci_high": ci_high,
        "block_count": len(values),
        "effective_block_count": len(values),
        "duplicate_block_treatment_count": sum(max(0, len(values) - 1) for values in grouped.values()),
    }


def paired_delay_effects(
    rows: Sequence[Mapping[str, object]],
    *,
    comparisons: Sequence[str],
    delays_ns: Sequence[float],
    trials: int = 2000,
    seed: int = 0,
) -> list[dict[str, object]]:
    """Return direction-normalized per-delay effects and block diagnostics."""

    normalized: list[dict[str, object]] = []
    for source in rows:
        delay = _delay(source)
        if delay is None:
            continue
        item = dict(source)
        item["block_id"] = _block(source)
        item["_delay"] = delay
        normalized.append(item)

    metrics = sorted({_text(row.get("metric")) for row in normalized if _text(row.get("metric"))})
    if not metrics:
        metrics = ["unspecified_metric"]
    requested = [float(delay) for delay in delays_ns]
    output: list[dict[str, object]] = []
    for metric in metrics:
        metric_rows = [row for row in normalized if _text(row.get("metric")) == metric]
        expected_blocks = {_text(row["block_id"]) for row in metric_rows}
        for delay in requested:
            delay_rows = [row for row in metric_rows if math.isclose(float(row["_delay"]), delay, abs_tol=1.0e-9)]
            by_block: dict[str, list[dict[str, object]]] = defaultdict(list)
            for row in delay_rows:
                by_block[_text(row["block_id"])].append(row)
            duplicate_count = sum(max(0, len(values) - 1) for values in by_block.values())
            missing_blocks = sorted(expected_blocks - set(by_block))
            for comparison_index, comparison in enumerate(comparisons):
                complete: list[tuple[str, dict[str, object], float]] = []
                unavailable_count = 0
                conflicting_duplicate = False
                for block, candidates in by_block.items():
                    if len(candidates) > 1:
                        signatures = {
                            tuple(
                                _text(candidate.get(field))
                                for field in (
                                    "metric_direction",
                                    "A1_value", "A1_status",
                                    "A2_value", "A2_status",
                                    "A3_value", "A3_status",
                                )
                            )
                            for candidate in candidates
                        }
                        if len(signatures) > 1:
                            conflicting_duplicate = True
                            continue
                        # Exact duplicate rows are an upstream serialization
                        # repeat, not independent observations.  Keep one and
                        # retain duplicate_block_delay_count in the output.
                        candidates = candidates[:1]
                    if not candidates:
                        continue
                    row = candidates[0]
                    current_key, comparison_key = _comparison_fields(comparison)
                    if not _status_ok(row.get(current_key.replace("_value", "_status"))) or not _status_ok(row.get(comparison_key.replace("_value", "_status"))):
                        unavailable_count += 1
                        continue
                    effect = _directional_effect(row, comparison)
                    if effect is not None:
                        complete.append((block, row, effect))
                    else:
                        unavailable_count += 1

                base = {
                    "metric": metric,
                    "metric_direction": _metric_direction(metric_rows[0]) if metric_rows else "higher_is_better",
                    "comparison": comparison,
                    "delay_error_ns": _number(delay),
                    "block_count": len(complete),
                    "effective_block_count": len(complete),
                    "expected_block_count": len(expected_blocks),
                    "missing_block_count": len(missing_blocks),
                    "duplicate_block_delay_count": duplicate_count,
                    "unavailable_block_count": unavailable_count,
                    "missing_blocks": "|".join(missing_blocks),
                    "source_classification": "Formal-v1 exploratory",
                    "effect": None,
                    "effect_mean": None,
                    "ci_low": None,
                    "ci_high": None,
                    "recoverable_space": None,
                    "actual_recovered": None,
                    "recovery_ratio": None,
                    "status": "NOT_EVALUABLE",
                    "reason": "missing_block_delay_pair" if missing_blocks else "no_complete_pairs",
                }
                if duplicate_count and conflicting_duplicate:
                    base["reason"] = "duplicate_block_delay_pair"
                if complete:
                    values = [item[2] for item in complete]
                    effect = float(sum(values) / len(values))
                    ci_low, ci_high = _bootstrap_values(
                        values,
                        trials=trials,
                        seed=seed + int(delay * 1000) + comparison_index,
                    )
                    recoverable_values = [
                        _directional_effect(item[1], "A1_to_A2")
                        for item in complete
                    ]
                    actual_values = [
                        _directional_effect(item[1], "A1_to_A3")
                        for item in complete
                    ]
                    recoverable = [value for value in recoverable_values if value is not None]
                    actual = [value for value in actual_values if value is not None]
                    recoverable_mean = float(sum(recoverable) / len(recoverable)) if recoverable else None
                    actual_mean = float(sum(actual) / len(actual)) if actual else None
                    base.update({
                        "effect": effect,
                        "effect_mean": effect,
                        "ci_low": ci_low,
                        "ci_high": ci_high,
                        "recoverable_space": recoverable_mean,
                        "actual_recovered": actual_mean,
                        "recovery_ratio": (
                            actual_mean / recoverable_mean
                            if actual_mean is not None and recoverable_mean is not None and abs(recoverable_mean) > 1.0e-12
                            else None
                        ),
                        "status": "passed",
                        "reason": "paired_scene_block_effect",
                    })
                    if recoverable_mean is not None and abs(recoverable_mean) <= 1.0e-12:
                        base["status"] = "NOT_EVALUABLE"
                        base["reason"] = "zero_recoverable_space"
                        base["recovery_ratio"] = None
                    elif duplicate_count:
                        base["reason"] = "exact_duplicate_block_delay_rows_collapsed"
                if conflicting_duplicate:
                    base["status"] = "NOT_EVALUABLE"
                    base["reason"] = "duplicate_block_delay_pair"
                output.append(base)
    return output


def _binary_value(value: object) -> int | None:
    parsed = _finite(value)
    if parsed is None:
        return None
    if parsed not in {0.0, 1.0}:
        return int(parsed > 0.0)
    return int(parsed)


def _mcnemar_p_value(left: int, right: int) -> float | None:
    discordant = left + right
    if discordant == 0:
        return 1.0
    numerator = sum(math.comb(discordant, k) for k in range(0, min(left, right) + 1))
    return min(1.0, 2.0 * numerator / (2.0 ** discordant))


def binary_cluster_paired(
    rows: Sequence[Mapping[str, object]],
    *,
    block_key: str,
    treatment_key: str,
    current_key: str,
    comparison_key: str,
    trials: int = 2000,
    seed: int = 0,
) -> dict[str, object]:
    """Compute paired binary effects after collapsing each block/treatment."""

    grouped: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    for row in rows:
        block = _text(row.get(block_key))
        if not block:
            continue
        current = _binary_value(row.get(current_key))
        compared = _binary_value(row.get(comparison_key))
        if current is None or compared is None:
            continue
        grouped[(block, _text(row.get(treatment_key)))].append((current, compared))
    pairs = {
        key: (
            int(round(sum(pair[0] for pair in values) / len(values))),
            int(round(sum(pair[1] for pair in values) / len(values))),
        )
        for key, values in grouped.items()
        if values
    }
    if not pairs:
        return {
            "status": "NOT_EVALUABLE",
            "reason": "no_complete_binary_block_pairs",
            "block_count": 0,
            "effective_block_count": 0,
            "effect": None,
            "ci_low": None,
            "ci_high": None,
            "discordant_current_miss_comparison_hit": 0,
            "discordant_current_hit_comparison_miss": 0,
        }

    effects = [compared - current for current, compared in pairs.values()]
    miss_to_hit = sum(current == 0 and compared == 1 for current, compared in pairs.values())
    hit_to_miss = sum(current == 1 and compared == 0 for current, compared in pairs.values())
    ci_low, ci_high = _bootstrap_values(effects, trials=trials, seed=seed)
    return {
        "status": "passed",
        "reason": "paired_block_binary_effect",
        "block_count": len(pairs),
        "effective_block_count": len(pairs),
        "effect": float(sum(effects) / len(effects)),
        "ci_low": ci_low,
        "ci_high": ci_high,
        "discordant_current_miss_comparison_hit": int(miss_to_hit),
        "discordant_current_hit_comparison_miss": int(hit_to_miss),
        "p_value": _mcnemar_p_value(miss_to_hit, hit_to_miss),
        "duplicate_block_treatment_count": sum(max(0, len(values) - 1) for values in grouped.values()),
    }


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _find_sources(input_paths: Sequence[Path]) -> dict[str, Path]:
    names = {
        "a0": "A0_A1_A2_A3_summary.csv",
        "on": "target_on_detection_summary.csv",
        "off": "target_off_false_alarm_summary.csv",
        "track": "track_summary.csv",
        "switch": "id_switch_audit.csv",
        "tap": "cfar_geometry_diagnostics.csv",
    }
    found: dict[str, Path] = {}
    for input_path in input_paths:
        path = Path(input_path)
        candidates = [path] if path.is_file() else list(path.rglob("*.csv")) if path.is_dir() else []
        for candidate in candidates:
            for key, name in names.items():
                if candidate.name == name and key not in found:
                    found[key] = candidate
    return found


def _source_classification(input_paths: Sequence[Path]) -> str:
    """Classify evidence from a nearby compact manifest without guessing."""

    for input_path in input_paths:
        path = Path(input_path)
        manifest = path.parent / "manifest.json" if path.is_file() else path / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("evidence_version") == "formal-v2":
            return "Formal-v2 confirmatory"
    return "Formal-v1 exploratory"


def _condition_pairs(rows: Sequence[Mapping[str, object]], *, key_name: str) -> list[dict[str, object]]:
    grouped: dict[tuple[str, float, str], dict[str, Mapping[str, object]]] = defaultdict(dict)
    for source in rows:
        delay = _delay(source)
        metric = _text(source.get(key_name))
        condition = _text(source.get("condition"))
        if delay is None or not metric or not condition:
            continue
        grouped[(_block(source), delay, metric)][condition] = source
    output: list[dict[str, object]] = []
    for (block, delay, metric), conditions in grouped.items():
        a1 = conditions.get("A1_Current_unknown_error")
        if a1 is None:
            continue
        for comparison, condition in (("A1_to_A2", "A2_Known_error_correction_upper_bound"), ("A1_to_A3", "A3_Blind_target_free_estimated_correction")):
            compared = conditions.get(condition)
            if compared is None:
                continue
            current = _finite(a1.get("value"))
            other = _finite(compared.get("value"))
            if current is None or other is None:
                continue
            output.append({
                "block_id": block,
                "delay_error_ns": _number(delay),
                "metric": metric,
                "comparison": comparison,
                "current": int(current > 0.0),
                "comparison_value": int(other > 0.0),
            })
    return output


def _write_rows(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (list, dict)) else value for field, value in row.items()})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_hierarchical_evidence(
    input_paths: Sequence[Path],
    output_dir: Path,
    *,
    seed: int,
    trials: int,
) -> dict[str, object]:
    """Read compact summaries and write scene-block hierarchical evidence."""

    sources = _find_sources(input_paths)
    source_classification = _source_classification(input_paths)
    a0_rows = _read_csv(sources["a0"]) if "a0" in sources else []
    requested = list(DEFAULT_DELAYS_NS)
    effects = paired_delay_effects(
        a0_rows,
        comparisons=("A1_to_A2", "A1_to_A3"),
        delays_ns=requested,
        trials=trials,
        seed=seed,
    )
    for row in effects:
        row["source_classification"] = source_classification

    binary_rows: list[dict[str, object]] = []
    binary_results: list[dict[str, object]] = []
    for source_key, metric_key in (("off", "layer"), ("track", "metric")):
        rows = _read_csv(sources[source_key]) if source_key in sources else []
        for metric in sorted({_text(row.get(metric_key)) for row in rows if _text(row.get(metric_key))}):
            metric_rows = [row for row in rows if _text(row.get(metric_key)) == metric]
            pairs = _condition_pairs(metric_rows, key_name=metric_key)
            for comparison in ("A1_to_A2", "A1_to_A3"):
                for delay in requested:
                    selected = [
                        row for row in pairs
                        if row["comparison"] == comparison
                        and math.isclose(float(row["delay_error_ns"]), delay, abs_tol=1.0e-9)
                    ]
                    if not selected:
                        continue
                    result = binary_cluster_paired(
                        selected,
                        block_key="block_id",
                        treatment_key="delay_error_ns",
                        current_key="current",
                        comparison_key="comparison_value",
                        trials=trials,
                        seed=seed,
                    )
                    binary_results.append({
                        "source": source_key,
                        "metric": metric,
                        "comparison": comparison,
                        "delay_error_ns": _number(delay),
                        **result,
                        "source_classification": source_classification,
                    })
            binary_rows.extend(
                {"source": source_key, "source_classification": source_classification, **row}
                for row in pairs
            )

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    _write_rows(output / "hierarchical_effects.csv", effects)
    _write_rows(output / "hierarchical_binary_pairs.csv", binary_rows)
    _write_rows(output / "hierarchical_binary_effects.csv", binary_results)
    missingness = [
        {
            "metric": row.get("metric"),
            "comparison": row.get("comparison"),
            "delay_error_ns": row.get("delay_error_ns"),
            "expected_block_count": row.get("expected_block_count"),
            "block_count": row.get("block_count"),
            "missing_block_count": row.get("missing_block_count"),
            "duplicate_block_delay_count": row.get("duplicate_block_delay_count"),
            "unavailable_block_count": row.get("unavailable_block_count"),
            "status": row.get("status"),
            "reason": row.get("reason"),
            "source_classification": source_classification,
        }
        for row in effects
        if int(row.get("missing_block_count", 0) or 0)
        or int(row.get("duplicate_block_delay_count", 0) or 0)
        or int(row.get("unavailable_block_count", 0) or 0)
    ]
    _write_rows(output / "hierarchical_missingness.csv", missingness)
    unique_blocks = sorted({_block(row) for row in a0_rows})
    available_delays = sorted({_delay(row) for row in a0_rows if _delay(row) is not None})
    manifest: dict[str, object] = {
        "schema_version": 1,
        "ai_training": False,
        "router_enabled": False,
        "native_four_channel_stap": False,
        "status": "completed" if a0_rows else "NOT_EVALUABLE",
        "source_classification": source_classification,
        "physical_block_definition": "seed+velocity+SNR+working_point/texture/geometry+delay-independent scene identity",
        "delay_is_within_block_treatment": True,
        "requested_delays_ns": [_number(value) for value in requested],
        "available_delays_ns": [_number(value) for value in available_delays],
        "physical_block_count": len(unique_blocks),
        "expected_registered_block_count": 15,
        "source_files": {
            key: {"path": str(path), "sha256": _sha256(path), "rows": len(_read_csv(path))}
            for key, path in sources.items()
        },
        "row_counts": {
            "a0_a1_a2_a3": len(a0_rows),
            "continuous_effects": len(effects),
            "binary_pairs": len(binary_rows),
            "binary_effects": len(binary_results),
            "missingness": len(missingness),
        },
        "continuous_status_counts": dict(Counter(str(row.get("status", "")) for row in effects)),
        "duplicate_block_delay_count_total": sum(int(row.get("duplicate_block_delay_count", 0) or 0) for row in effects),
        "missing_block_count_total": sum(int(row.get("missing_block_count", 0) or 0) for row in effects),
        "seed": int(seed),
        "bootstrap_trials": int(trials),
        "limitations": [
            "Formal-v1 compact evidence is exploratory and not a final confirmatory analysis",
            "missing raw v2 CFAR/TrackManager tap rows remain NOT_EVALUABLE for those mechanisms",
        ],
    }
    (output / "hierarchical_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


__all__ = [
    "binary_cluster_paired",
    "block_bootstrap_effect",
    "make_physical_block_id",
    "paired_delay_effects",
    "write_hierarchical_evidence",
]
