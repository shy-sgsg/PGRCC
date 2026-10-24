from __future__ import annotations

import copy
import csv
from pathlib import Path

import pytest

from scripts.audit_track_manager_run import protocol_target_is_eligible
from scripts import run_track_manager_e2e as driver


def _valid_evidence() -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    payload = {
        "result_id": "GMTI12",
        "track_id": "7",
        "matched_det_index": "3",
        "resolved_source": "measurement",
    }
    state = {
        "result_id": "12",
        "track_id": "7",
        "state": "Confirmed",
        "matched_this_frame": "1",
        "matched_det_index": "3",
        "is_output": "1",
    }
    detection = {
        "result_id": "12",
        "det_index": "3",
        "matched": "1",
        "matched_track_id": "7",
    }
    association = {
        "result_id": "GMTI12",
        "track_id": "7",
        "det_index": "3",
        "post_assignment_accepted": "1",
    }
    return payload, state, detection, association


@pytest.mark.parametrize(
    ("name", "change", "expected_reason"),
    [
        ("tentative", {"state": "Tentative"}, "state_not_confirmed"),
        ("coasted_only", {"state": "Coasted", "matched_this_frame": "0"}, "state_not_confirmed"),
        ("prior_frame_only", {"result_id": "11"}, "period_identity_mismatch"),
        ("raw_detection_only", {"missing_state": True}, "missing_track_state"),
        (
            "unmatched_predicted",
            {"resolved_source": "prediction", "matched_this_frame": "0"},
            "state_not_matched_this_frame",
        ),
    ],
)
def test_protocol_target_negative_fixtures_are_excluded(
    name: str,
    change: dict[str, object],
    expected_reason: str,
) -> None:
    payload, state, detection, association = _valid_evidence()
    if name == "raw_detection_only":
        state_value = None
    else:
        state_value = copy.deepcopy(state)
        for key, value in change.items():
            if key != "missing_state":
                state_value[key] = value
    if name == "prior_frame_only":
        detection["result_id"] = change["result_id"]
    if name == "unmatched_predicted":
        payload["resolved_source"] = change["resolved_source"]
        state_value["matched_this_frame"] = change["matched_this_frame"]
    eligible, reason = protocol_target_is_eligible(
        payload, state_value, detection, association
    )
    assert not eligible, name
    assert reason == expected_reason


def test_protocol_target_valid_fixture_requires_same_period_confirmed_match() -> None:
    evidence = _valid_evidence()
    assert protocol_target_is_eligible(*evidence) == (True, "eligible")


def test_branch_contract_has_current_blind_known_with_shared_production_gate() -> None:
    contract = driver.branch_contract()
    assert contract["branches"] == [
        "Current",
        "blind_calibrated",
        "known_error_calibrated",
    ]
    assert all(contract["shared_rules"].values())
    assert contract["blind_calibrated_status"] == "estimated_correction_required"
    assert contract["known_error_calibrated_status"] == "known_error_correction_upper_bound"
    assert contract["correction_requirements"]["blind_calibrated"]["correction_applied"] is True
    assert contract["correction_requirements"]["known_error_calibrated"]["correction_applied"] is True


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_track_metrics_deduplicate_multiple_payloads_in_one_period(tmp_path: Path) -> None:
    result_dir = tmp_path / "result"
    debug_dir = tmp_path / "track_debug"
    result_dir.mkdir()
    debug_dir.mkdir()
    _write_csv(
        result_dir / "detection_results_GMTI01.csv",
        [{
            "theta_used_deg": 38.0,
            "range_m": 1000.0,
            "new_e": 100.0,
            "new_n": 200.0,
        }],
    )
    _write_csv(
        debug_dir / "track_output_payloads.csv",
        [
            {
                "result_id": "GMTI01", "track_id": 7, "matched_det_index": 0,
                "resolved_source": "measurement", "output_e": 100.0,
                "output_n": 200.0, "output_speed": 5.0,
            },
            {
                "result_id": "GMTI01", "track_id": 8, "matched_det_index": 0,
                "resolved_source": "measurement", "output_e": 100.0,
                "output_n": 200.0, "output_speed": 5.0,
            },
        ],
    )
    _write_csv(debug_dir / "track_detections.csv", [{"result_id": "1"}])
    _write_csv(debug_dir / "track_states.csv", [{"result_id": "1"}])
    truth = {
        0: {
            "target_id": "target",
            "e_mid": "100.0", "n_mid": "200.0", "range_m": "1000.0",
            "azimuth_deg": "38.0", "ve_mps": "3.0", "vn_mps": "4.0",
        },
    }
    metrics, _, _ = driver._evaluate_branch(
        "Current", result_dir, debug_dir, truth, 1,
        {"status": "pass", "protocol_target_failures": {}},
    )
    assert metrics["matched_protocol_payload_count"] == 2
    assert metrics["matched_protocol_period_count"] == 1
    assert metrics["track_pd_all_visible"] == 1.0
    assert metrics["track_continuity"] == 1.0
