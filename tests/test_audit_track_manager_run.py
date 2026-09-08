import csv
from pathlib import Path

from scripts.audit_track_manager_run import audit_debug_dir


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _make_valid_run(root: Path) -> None:
    _write(
        root / "track_association_accepts.csv",
        [{
            "result_id": "GMTI12", "track_id": 7, "det_index": 3,
            "euclidean_dist_m": 20, "euclidean_gate_m": 300,
            "mahalanobis_d2": 1.2, "mahalanobis_gate_d2": 9.21,
            "instant_speed_mps": 4, "max_speed_gate_mps": 100,
            "total_cost": 0.2, "dummy_cost": 1.5,
            "gate_passed": 1, "cost_beats_dummy": 1,
            "post_assignment_accepted": 1,
        }],
    )
    _write(
        root / "track_frames.csv",
        [{
            "result_id": "12", "frame_utc": 10,
            "num_matched_tracks": 1, "num_outputs": 1,
        }],
    )
    _write(
        root / "track_states.csv",
        [{
            "result_id": "12", "track_id": 7, "state": "Confirmed",
            "matched_this_frame": 1, "matched_det_index": 3, "is_output": 1,
            "e": 100, "n": 200, "utc": 10,
        }],
    )
    _write(
        root / "track_detections.csv",
        [{
            "result_id": "12", "det_index": 3, "matched": 1,
            "matched_track_id": 7,
        }],
    )
    _write(
        root / "track_output_payloads.csv",
        [{
            "result_id": "GMTI12", "track_id": 7, "matched_det_index": 3,
            "resolved_source": "measurement",
            "position_source": "matched_detection",
            "speed_source": "track_displacement_over_elapsed_time",
            "range_source": "matched_detection",
            "direction_source": "matched_detection",
            "time_source": "matched_detection",
            "measurement_utc": 10, "measurement_e": 100, "measurement_n": 200,
            "measurement_lat": 39, "measurement_lon": 118,
            "measurement_range": 400, "measurement_direction": -20,
            "prediction_utc": 10, "prediction_e": 99, "prediction_n": 199,
            "prediction_ve": 3, "prediction_vn": 4,
            "prediction_range": 401, "prediction_direction": -21,
            "filtered_utc": 10, "filtered_e": 100.5, "filtered_n": 200.5,
            "filtered_ve": 6, "filtered_vn": 8,
            "filtered_range": 400, "filtered_direction": -20,
            "output_utc": 10, "output_e": 100, "output_n": 200,
            "output_lat": 39, "output_lon": 118, "output_speed": 2,
            "output_range": 400, "output_direction": -20,
        }],
    )


def test_valid_production_audit_links_all_evidence(tmp_path: Path) -> None:
    _make_valid_run(tmp_path)
    report = audit_debug_dir(tmp_path)
    assert report["status"] == "pass"
    assert report["total_violations"] == 0
    assert report["resolved_sources"] == {"measurement": 1}


def test_payload_value_from_wrong_source_fails(tmp_path: Path) -> None:
    _make_valid_run(tmp_path)
    path = tmp_path / "track_output_payloads.csv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["output_e"] = "999"
    _write(path, rows)
    report = audit_debug_dir(tmp_path)
    assert report["status"] == "fail"
    assert report["violations"]["payload_numeric_source"] == 1


def test_legacy_candidate_matrix_is_streamed_as_limited_evidence(tmp_path: Path) -> None:
    _make_valid_run(tmp_path)
    (tmp_path / "track_association_accepts.csv").unlink()
    _write(
        tmp_path / "track_association_log.csv",
        [{
            "period_id": 12, "track_id": 7, "det_id": 3,
            "assigned": 1, "gate_passed": 1, "gate_reason": "None",
            "distance_m": 20, "mahalanobis_distance": 1.1,
        }],
    )
    report = audit_debug_dir(tmp_path)
    assert report["status"] == "pass"
    assert report["association_evidence"] == "legacy_assigned_candidate_log"
    assert report["unverified_checks"]


def test_prediction_and_filtered_payload_snapshots_pass(tmp_path: Path) -> None:
    variants = {
        "prediction": {
            "position_source": "kalman_prior",
            "speed_source": "kalman_prior_velocity",
            "range_source": "previous_detection_zero_order_hold",
            "direction_source": "previous_detection_zero_order_hold",
            "time_source": "prediction_epoch",
            "output_utc": 10, "output_e": 99, "output_n": 199,
            "output_speed": 5, "output_range": 401, "output_direction": -21,
        },
        "kalman_filtered": {
            "position_source": "kalman_posterior",
            "speed_source": "kalman_posterior_velocity",
            "range_source": "matched_detection",
            "direction_source": "matched_detection",
            "time_source": "matched_detection",
            "output_utc": 10, "output_e": 100.5, "output_n": 200.5,
            "output_speed": 10, "output_range": 400, "output_direction": -20,
        },
    }
    for source, changes in variants.items():
        root = tmp_path / source
        root.mkdir()
        _make_valid_run(root)
        path = root / "track_output_payloads.csv"
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        rows[0]["resolved_source"] = source
        rows[0].update({key: str(value) for key, value in changes.items()})
        _write(path, rows)
        report = audit_debug_dir(root)
        assert report["status"] == "pass"
        assert report["resolved_sources"] == {source: 1}
