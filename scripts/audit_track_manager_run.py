#!/usr/bin/env python3
"""Audit one production TrackManager diagnostic directory.

The audit deliberately consumes the accepted-assignment stream instead of the
full candidate matrix.  It therefore proves properties of updates that really
reached the production TrackManager while remaining cheap enough for routine
11--19 regression runs.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


EPS = 1.0e-9


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"required TrackManager audit file is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _legacy_assigned_rows(path: Path) -> list[dict[str, str]]:
    """Stream the pre-accepted-audit candidate matrix without loading it."""
    if not path.is_file():
        raise FileNotFoundError(f"required TrackManager audit file is missing: {path}")
    assigned: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("assigned") == "1":
                assigned.append(row)
    return assigned


def _float(row: dict[str, str], name: str) -> float:
    try:
        value = float(row[name])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid numeric field {name!r} in row {row!r}") from exc
    if not math.isfinite(value):
        raise ValueError(f"non-finite field {name!r}={value!r} in row {row!r}")
    return value


def _int(row: dict[str, str], name: str) -> int:
    try:
        return int(row[name])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid integer field {name!r} in row {row!r}") from exc


def _period(value: str) -> int:
    match = re.search(r"(\d+)$", value)
    if match is None:
        raise ValueError(f"result_id has no numeric period suffix: {value!r}")
    return int(match.group(1))


def _duplicate_count(keys: Iterable[tuple[int, int]]) -> int:
    return sum(count - 1 for count in Counter(keys).values() if count > 1)


def _near(lhs: float, rhs: float, *, atol: float = 2.0e-6) -> bool:
    return math.isclose(lhs, rhs, rel_tol=1.0e-9, abs_tol=atol)


def audit_debug_dir(debug_dir: Path) -> dict[str, Any]:
    debug_dir = debug_dir.resolve()
    accepted_path = debug_dir / "track_association_accepts.csv"
    detailed_accepts = accepted_path.is_file()
    accepts = (_rows(accepted_path) if detailed_accepts else
               _legacy_assigned_rows(debug_dir / "track_association_log.csv"))
    frames = _rows(debug_dir / "track_frames.csv")
    states = _rows(debug_dir / "track_states.csv")
    detections = _rows(debug_dir / "track_detections.csv")
    payloads = _rows(debug_dir / "track_output_payloads.csv")

    distance_violations = 0
    mahalanobis_violations = 0
    speed_violations = 0
    cost_violations = 0
    flag_violations = 0
    max_distance_ratio = 0.0
    max_mahalanobis_ratio = 0.0
    max_speed_ratio = 0.0
    max_distance_m = 0.0
    max_instant_speed_mps = 0.0
    accepted_track_keys: list[tuple[int, int]] = []
    accepted_detection_keys: list[tuple[int, int]] = []

    for row in accepts:
        period = _period(row["result_id"] if detailed_accepts else row["period_id"])
        track_id = _int(row, "track_id")
        det_index = _int(row, "det_index" if detailed_accepts else "det_id")
        accepted_track_keys.append((period, track_id))
        accepted_detection_keys.append((period, det_index))

        if not detailed_accepts:
            distance = _float(row, "distance_m")
            mahalanobis_distance = _float(row, "mahalanobis_distance")
            max_distance_m = max(max_distance_m, distance)
            flag_violations += int(
                row.get("gate_passed") != "1"
                or row.get("gate_reason") != "None"
                or row.get("assigned") != "1"
            )
            # The legacy matrix records sqrt(d2), not d2, and does not include
            # the effective tentative gates or instantaneous speed.
            max_mahalanobis_ratio = max(
                max_mahalanobis_ratio, mahalanobis_distance
            )
            continue

        distance = _float(row, "euclidean_dist_m")
        distance_gate = _float(row, "euclidean_gate_m")
        mahalanobis = _float(row, "mahalanobis_d2")
        mahalanobis_gate = _float(row, "mahalanobis_gate_d2")
        speed = _float(row, "instant_speed_mps")
        speed_gate = _float(row, "max_speed_gate_mps")
        cost = _float(row, "total_cost")
        dummy_cost = _float(row, "dummy_cost")

        max_distance_m = max(max_distance_m, distance)
        max_instant_speed_mps = max(max_instant_speed_mps, speed)
        if distance_gate > 0.0:
            max_distance_ratio = max(max_distance_ratio, distance / distance_gate)
            distance_violations += int(distance > distance_gate + EPS)
        if mahalanobis_gate > 0.0:
            max_mahalanobis_ratio = max(
                max_mahalanobis_ratio, mahalanobis / mahalanobis_gate
            )
            mahalanobis_violations += int(mahalanobis > mahalanobis_gate + EPS)
        if speed_gate > 0.0:
            max_speed_ratio = max(max_speed_ratio, speed / speed_gate)
            speed_violations += int(speed > speed_gate + EPS)
        # Equality may be explicitly allowed by configuration.  The recorded
        # decision flag is authoritative for that boundary; a value strictly
        # above the dummy cost is invalid in every mode.
        cost_violations += int(cost > dummy_cost + EPS)
        flag_violations += int(
            row.get("gate_passed") != "1"
            or row.get("cost_beats_dummy") != "1"
            or row.get("post_assignment_accepted") != "1"
        )

    frame_matches = sum(_int(row, "num_matched_tracks") for row in frames)
    frame_outputs = sum(_int(row, "num_outputs") for row in frames)
    frame_result_ids = [_period(row["result_id"]) for row in frames]

    states_by_key: dict[tuple[int, int], dict[str, str]] = {}
    output_states: dict[tuple[int, int], dict[str, str]] = {}
    invalid_output_states = 0
    duplicate_states = 0
    duplicate_output_states = 0
    for row in states:
        key = (_period(row["result_id"]), _int(row, "track_id"))
        duplicate_states += int(key in states_by_key)
        states_by_key[key] = row
        if row.get("is_output") != "1":
            continue
        duplicate_output_states += int(key in output_states)
        output_states[key] = row
        invalid_output_states += int(
            row.get("state") != "Confirmed" or row.get("matched_this_frame") != "1"
        )

    state_history: dict[int, list[tuple[int, dict[str, str]]]] = {}
    for (period, track_id), row in states_by_key.items():
        state_history.setdefault(track_id, []).append((period, row))
    state_step_count = 0
    matched_state_step_count = 0
    coasted_state_step_count = 0
    measurement_step_count = 0
    max_state_step_m = 0.0
    max_matched_state_step_m = 0.0
    max_coasted_state_step_m = 0.0
    max_measurement_step_m = 0.0
    max_measurement_speed_mps = 0.0
    transitions: dict[tuple[int, int], dict[str, Any]] = {}
    for history in state_history.values():
        history.sort(key=lambda item: item[0])
        previous: tuple[int, dict[str, str]] | None = None
        previous_measurement: tuple[int, dict[str, str]] | None = None
        for period, row in history:
            if previous is not None and period > previous[0]:
                prev_row = previous[1]
                distance = math.hypot(
                    _float(row, "e") - _float(prev_row, "e"),
                    _float(row, "n") - _float(prev_row, "n"),
                )
                state_step_count += 1
                max_state_step_m = max(max_state_step_m, distance)
                transition = transitions.setdefault(
                    (previous[0], period),
                    {
                        "from_result_id": previous[0],
                        "to_result_id": period,
                        "state_steps": 0,
                        "max_state_step_m": 0.0,
                        "coasted_steps": 0,
                        "nonzero_coasted_steps": 0,
                        "max_coasted_step_m": 0.0,
                    },
                )
                transition["state_steps"] += 1
                transition["max_state_step_m"] = max(
                    transition["max_state_step_m"], distance
                )
                if row.get("matched_this_frame") == "1":
                    matched_state_step_count += 1
                    max_matched_state_step_m = max(max_matched_state_step_m, distance)
                if row.get("state") == "Coasted":
                    coasted_state_step_count += 1
                    max_coasted_state_step_m = max(max_coasted_state_step_m, distance)
                    transition["coasted_steps"] += 1
                    transition["nonzero_coasted_steps"] += int(distance > 1.0e-6)
                    transition["max_coasted_step_m"] = max(
                        transition["max_coasted_step_m"], distance
                    )
            if row.get("matched_this_frame") == "1":
                if previous_measurement is not None and period > previous_measurement[0]:
                    prev_row = previous_measurement[1]
                    distance = math.hypot(
                        _float(row, "e") - _float(prev_row, "e"),
                        _float(row, "n") - _float(prev_row, "n"),
                    )
                    dt = _float(row, "utc") - _float(prev_row, "utc")
                    measurement_step_count += 1
                    max_measurement_step_m = max(max_measurement_step_m, distance)
                    if dt > 1.0e-6:
                        max_measurement_speed_mps = max(
                            max_measurement_speed_mps, distance / dt
                        )
                previous_measurement = (period, row)
            previous = (period, row)

    matched_detections: dict[tuple[int, int], dict[str, str]] = {}
    invalid_detection_links = 0
    for row in detections:
        if row.get("matched") != "1":
            continue
        key = (_period(row["result_id"]), _int(row, "det_index"))
        matched_detections[key] = row
        invalid_detection_links += int(_int(row, "matched_track_id") < 0)

    accepted_state_link_violations = 0
    accepted_detection_link_violations = 0
    for row in accepts:
        period = _period(row["result_id"] if detailed_accepts else row["period_id"])
        track_id = _int(row, "track_id")
        det_index = _int(row, "det_index" if detailed_accepts else "det_id")
        state = states_by_key.get((period, track_id))
        detection = matched_detections.get((period, det_index))
        accepted_state_link_violations += int(
            state is None
            or state.get("matched_this_frame") != "1"
            or _int(state, "matched_det_index") != det_index
        )
        accepted_detection_link_violations += int(
            detection is None or _int(detection, "matched_track_id") != track_id
        )

    payload_keys: list[tuple[int, int]] = []
    payload_provenance_violations = 0
    payload_numeric_violations = 0
    resolved_sources: Counter[str] = Counter()
    expected_labels = {
        "measurement": {
            "position_source": "matched_detection",
            "speed_source": "track_displacement_over_elapsed_time",
            "range_source": "matched_detection",
            "direction_source": "matched_detection",
            "time_source": "matched_detection",
        },
        "prediction": {
            "position_source": "kalman_prior",
            "speed_source": "kalman_prior_velocity",
            "range_source": "previous_detection_zero_order_hold",
            "direction_source": "previous_detection_zero_order_hold",
            "time_source": "prediction_epoch",
        },
        "kalman_filtered": {
            "position_source": "kalman_posterior",
            "speed_source": "kalman_posterior_velocity",
            "range_source": "matched_detection",
            "direction_source": "matched_detection",
            "time_source": "matched_detection",
        },
    }

    for row in payloads:
        period = _period(row["result_id"])
        track_id = _int(row, "track_id")
        det_index = _int(row, "matched_det_index")
        key = (period, track_id)
        payload_keys.append(key)
        source = row.get("resolved_source", "")
        resolved_sources[source] += 1
        labels = expected_labels.get(source)
        if labels is None:
            payload_provenance_violations += 1
            continue
        payload_provenance_violations += sum(
            row.get(name) != expected for name, expected in labels.items()
        )

        state = output_states.get(key)
        detection = matched_detections.get((period, det_index))
        if state is None or detection is None:
            payload_provenance_violations += 1
        else:
            payload_provenance_violations += int(
                _int(detection, "matched_track_id") != track_id
                or _int(state, "matched_det_index") != det_index
            )

        comparisons: list[tuple[str, str]]
        if source == "measurement":
            comparisons = [
                ("output_utc", "measurement_utc"),
                ("output_e", "measurement_e"),
                ("output_n", "measurement_n"),
                ("output_lat", "measurement_lat"),
                ("output_lon", "measurement_lon"),
                ("output_range", "measurement_range"),
                ("output_direction", "measurement_direction"),
            ]
            expected_speed = None
        elif source == "prediction":
            comparisons = [
                ("output_utc", "prediction_utc"),
                ("output_e", "prediction_e"),
                ("output_n", "prediction_n"),
                ("output_range", "prediction_range"),
                ("output_direction", "prediction_direction"),
            ]
            expected_speed = math.hypot(
                _float(row, "prediction_ve"), _float(row, "prediction_vn")
            )
        else:
            comparisons = [
                ("output_utc", "filtered_utc"),
                ("output_e", "filtered_e"),
                ("output_n", "filtered_n"),
                ("output_range", "filtered_range"),
                ("output_direction", "filtered_direction"),
            ]
            expected_speed = math.hypot(
                _float(row, "filtered_ve"), _float(row, "filtered_vn")
            )
        payload_numeric_violations += sum(
            not _near(_float(row, output), _float(row, source_name))
            for output, source_name in comparisons
        )
        if expected_speed is not None:
            payload_numeric_violations += int(
                not _near(_float(row, "output_speed"), expected_speed)
            )

    payload_key_set = set(payload_keys)
    output_state_key_set = set(output_states)
    accepted_track_key_set = set(accepted_track_keys)
    payload_without_accepted_match = len(payload_key_set - accepted_track_key_set)
    state_payload_key_mismatch = len(payload_key_set ^ output_state_key_set)

    violations = {
        "distance_gate": distance_violations,
        "mahalanobis_gate": mahalanobis_violations,
        "speed_gate": speed_violations,
        "cost_vs_dummy": cost_violations,
        "accepted_flags": flag_violations,
        "accepted_track_one_to_one": _duplicate_count(accepted_track_keys),
        "accepted_detection_one_to_one": _duplicate_count(accepted_detection_keys),
        "frame_match_count": int(frame_matches != len(accepts)),
        "frame_output_count": int(frame_outputs != len(payloads)),
        "output_state_semantics": invalid_output_states,
        "duplicate_states": duplicate_states,
        "duplicate_output_states": duplicate_output_states,
        "detection_links": invalid_detection_links,
        "accepted_state_links": accepted_state_link_violations,
        "accepted_detection_links": accepted_detection_link_violations,
        "payload_provenance": payload_provenance_violations,
        "payload_numeric_source": payload_numeric_violations,
        "payload_without_accepted_match": payload_without_accepted_match,
        "state_payload_key_mismatch": state_payload_key_mismatch,
    }
    total_violations = sum(violations.values())
    frame_utc_by_period = {
        _period(row["result_id"]): _float(row, "frame_utc") for row in frames
    }
    frame_transitions = []
    for key in sorted(transitions):
        item = transitions[key]
        from_utc = frame_utc_by_period.get(key[0])
        to_utc = frame_utc_by_period.get(key[1])
        item["frame_dt_s"] = (
            to_utc - from_utc if from_utc is not None and to_utc is not None else None
        )
        frame_transitions.append(item)
    return {
        "schema_version": 1,
        "debug_dir": str(debug_dir),
        "status": "pass" if total_violations == 0 else "fail",
        "association_evidence": (
            "accepted_assignment_audit" if detailed_accepts
            else "legacy_assigned_candidate_log"
        ),
        "unverified_checks": ([] if detailed_accepts else [
            "effective gate ratios are unavailable in the legacy schema",
            "accepted instantaneous speed is unavailable in the legacy schema",
            "accepted cost versus dummy is unavailable in the legacy schema",
        ]),
        "periods": frame_result_ids,
        "frame_transitions": frame_transitions,
        "counts": {
            "frames": len(frames),
            "accepted_associations": len(accepts),
            "frame_reported_matches": frame_matches,
            "output_states": len(output_states),
            "payloads": len(payloads),
            "frame_reported_outputs": frame_outputs,
            "state_steps": state_step_count,
            "matched_state_steps": matched_state_step_count,
            "coasted_state_steps": coasted_state_step_count,
            "measurement_steps": measurement_step_count,
        },
        "maxima": {
            "accepted_distance_m": max_distance_m,
            "accepted_instant_speed_mps": (
                max_instant_speed_mps if detailed_accepts else None
            ),
            "distance_to_gate_ratio": (
                max_distance_ratio if detailed_accepts else None
            ),
            "mahalanobis_to_gate_ratio": (
                max_mahalanobis_ratio if detailed_accepts else None
            ),
            "legacy_max_mahalanobis_distance": (
                None if detailed_accepts else max_mahalanobis_ratio
            ),
            "speed_to_gate_ratio": max_speed_ratio if detailed_accepts else None,
            "state_step_m": max_state_step_m,
            "matched_state_step_m": max_matched_state_step_m,
            "coasted_state_step_m": max_coasted_state_step_m,
            "measurement_step_m": max_measurement_step_m,
            "measurement_step_speed_mps": max_measurement_speed_mps,
        },
        "resolved_sources": dict(sorted(resolved_sources.items())),
        "violations": violations,
        "total_violations": total_violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit accepted TrackManager associations and output provenance"
    )
    parser.add_argument("--debug-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="optional JSON result path")
    args = parser.parse_args()

    try:
        report = audit_debug_dir(args.debug_dir)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    rendered = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    print(rendered, end="")
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
