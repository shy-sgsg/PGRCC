import json

from scripts.phase_r.evaluate_r13_minimal_baseline_pilot import (
    audit_paired_scene_inputs,
    audit_final_union_counts,
    audit_shared_f1f2_inputs,
    strict_track_associations,
    _hash_record,
)


def test_union_audit_requires_map_log_geometry_and_branch_count_closure():
    geometry = [
        {
            "period_id": "0", "beam_id": "50", "branch": "union_dynamic_in_band",
            "height": "2", "width": "8", "guard_cells": "0", "background_cells": "0",
            "total_cells": "16", "edge_invalid_cells": "0", "excluded_cells": "0",
            "cut_band_filtered_cells": "0", "cut_band_mode": "0",
            "exclude_row_start": "-1", "exclude_row_end": "-1",
            "valid_cut_count": "16", "threshold_test_count": "16", "hit_cut_count": "11",
        },
        {
            "period_id": "0", "beam_id": "50", "branch": "union_full_band",
            "height": "2", "width": "8", "guard_cells": "0", "background_cells": "0",
            "total_cells": "16", "edge_invalid_cells": "0", "excluded_cells": "0",
            "cut_band_filtered_cells": "0", "cut_band_mode": "0",
            "exclude_row_start": "-1", "exclude_row_end": "-1",
            "valid_cut_count": "16", "threshold_test_count": "16", "hit_cut_count": "12",
        },
    ]

    audited = audit_final_union_counts(
        geometry,
        map_rows=2,
        map_cols=8,
        positive_map_cells=13,
        positive_valid_cut_cells=13,
        log_beam_id=50,
        log_hit_cells=13,
    )

    assert audited["audit_verified"] is True
    assert audited["unique_union_hit_cells"] == 13
    assert audited["valid_cut_count"] == 16
    assert audited["observed_hit_fraction"] == 13 / 16


def test_union_audit_fails_closed_when_log_or_valid_cut_geometry_disagrees():
    geometry = [
        {
            "period_id": "0", "beam_id": "50", "branch": branch,
            "height": "2", "width": "8", "guard_cells": "0", "background_cells": "0",
            "total_cells": "16", "edge_invalid_cells": "0", "excluded_cells": "0",
            "cut_band_filtered_cells": "0", "cut_band_mode": "0",
            "exclude_row_start": "-1", "exclude_row_end": "-1",
            "valid_cut_count": str(valid), "threshold_test_count": str(valid),
            "hit_cut_count": str(hits),
        }
        for branch, hits, valid in (
            ("union_dynamic_in_band", 11, 16),
            ("union_full_band", 12, 15),
        )
    ]

    audited = audit_final_union_counts(
        geometry,
        map_rows=2,
        map_cols=8,
        positive_map_cells=13,
        positive_valid_cut_cells=13,
        log_beam_id=50,
        log_hit_cells=12,
    )

    assert audited["audit_verified"] is False
    assert "geometry_branch_definition_mismatch" in audited["audit_reasons"]
    assert "production_log_map_count_mismatch" in audited["audit_reasons"]


def test_track_associations_require_confirmed_current_matched_payload_chain():
    states = [
        {"result_id": "1", "track_id": "t1", "state": "Confirmed", "matched_this_frame": "1", "matched_det_index": "0"},
        {"result_id": "1", "track_id": "t2", "state": "Tentative", "matched_this_frame": "1", "matched_det_index": "1"},
        {"result_id": "1", "track_id": "t3", "state": "Confirmed", "matched_this_frame": "0", "matched_det_index": "2"},
    ]
    track_detections = [
        {"result_id": "1", "det_index": "0", "matched": "1", "matched_track_id": "t1"},
        {"result_id": "1", "det_index": "1", "matched": "1", "matched_track_id": "t2"},
        {"result_id": "1", "det_index": "2", "matched": "1", "matched_track_id": "t3"},
    ]
    payloads = [
        {"result_id": "1", "track_id": "t1", "matched_det_index": "0", "output_speed": "5.0"},
        {"result_id": "1", "track_id": "t2", "matched_det_index": "1", "output_speed": "7.0"},
        {"result_id": "1", "track_id": "t3", "matched_det_index": "2", "output_speed": "9.0"},
    ]
    detections = [
        {"result_id": "GMTI01", "period_id": "0", "beam_id": "50", "det_id": "0"},
        {"result_id": "GMTI01", "period_id": "0", "beam_id": "50", "det_id": "1"},
        {"result_id": "GMTI01", "period_id": "0", "beam_id": "50", "det_id": "2"},
    ]

    rows = strict_track_associations(
        track_states=states,
        track_detections=track_detections,
        track_payloads=payloads,
        final_detections=detections,
        result_to_period={"GMTI01": 0},
    )

    assert rows == [
        {
            "period_id": 0,
            "beam_id": 50,
            "det_id": "0",
            "track_id": "t1",
            "confirmed": 1,
            "matched_this_frame": 1,
            "track_result_number": 1,
            "output_speed": 5.0,
        }
    ]


def test_f1f2_input_audit_requires_identical_maps_for_all_selected_conditions():
    rows = [
        {
            "scene": "normal",
            "role": "OFF",
            "period_id": "0",
            "method_id": method,
            "f1_input_sha256": "f1-same",
            "f2_input_sha256": "f2-same",
            "f1_input_active_support_mean_power": 4.0,
            "f2_input_active_support_mean_power": 9.0,
        }
        for method in ("A0", "C0", "C2", "C3")
    ]

    audit = audit_shared_f1f2_inputs(rows)
    assert audit == [{
        "scene": "normal",
        "role": "OFF",
        "period_id": "0",
        "method_count": 4,
        "methods_present": "A0,C0,C2,C3",
        "f1_hash_unique_count": 1,
        "f2_hash_unique_count": 1,
        "f1_active_support_mean_power": 4.0,
        "f2_active_support_mean_power": 9.0,
        "audit_verified": True,
        "audit_status": "verified_identical_f1_f2_inputs",
        "audit_reasons": "",
    }]

    rows[-1]["f2_input_sha256"] = "f2-different"
    mismatch = audit_shared_f1f2_inputs(rows)[0]
    assert mismatch["audit_verified"] is False
    assert mismatch["audit_reasons"] == "f2_hash_mismatch"


def test_f1f2_input_audit_accepts_the_selected_c1_gamma_one_subset():
    conditions = ("A0", "C0", "C1")
    rows = [
        {
            "scene": "normal_seed505",
            "role": role,
            "period_id": str(period),
            "method_id": method,
            "f1_input_sha256": f"f1-{role}-{period}",
            "f2_input_sha256": f"f2-{role}-{period}",
            "f1_input_active_support_mean_power": 4.0,
            "f2_input_active_support_mean_power": 9.0,
        }
        for role in ("OFF", "ON", "TO")
        for period in range(3)
        for method in conditions
    ]

    audits = audit_shared_f1f2_inputs(rows, conditions)

    assert len(audits) == 9
    assert all(row["audit_verified"] for row in audits)
    assert all(row["method_count"] == 3 for row in audits)
    assert all(row["methods_present"] == "A0,C0,C1" for row in audits)


def test_paired_input_audit_proves_off_uses_area_background_and_on_uses_writer(tmp_path):
    case_root = tmp_path / "case"
    background = case_root / "scenes" / "A0_background"
    off_path = case_root / "scenes" / "A0_OFF" / "data" / "period000.bin"
    on_path = case_root / "scenes" / "A1_ON" / "data" / "period000.bin"
    owner_path = case_root / "scenes" / "A0_ON" / "data" / "period000.bin"
    scenario_path = case_root / "scenes" / "scenarios" / "A0_ON.json"
    report_path = case_root / "scenes" / "A0_ON" / "reports" / "stage2_simulation_report.md"
    for path in (background / "data" / "period000.bin", off_path, on_path, owner_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    scenario_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    (background / "data" / "period000.bin").write_bytes(b"C+N")
    off_path.write_bytes(b"C+N")
    on_path.write_bytes(b"S+C+N")
    owner_path.write_bytes(b"S+C+N")
    scenario_path.write_text(
        json.dumps({"paired_background_output_dir": str(background)}),
        encoding="utf-8",
    )
    report_path.write_text(
        "- continuous_area_packets: 390\n"
        "- continuous_area_samples: 1952655\n"
        "- area_mean_power_measured: 0.0223353\n"
        "- area_rho_lag1_measured: 0.98\n"
        "- area_rho_at_CTDR_lag: 0.96\n",
        encoding="utf-8",
    )
    case = {
        "scene_variants": {"A0_ON": str(scenario_path)},
        "simulation": {"A0_ON": {"output_dir": str(owner_path.parents[1])}},
        "area_clutter_gate": {"status": "passed"},
        "scene_identity": {"period_count": 1},
        "method_branches": [
            {"method_id": "C0", "role": "OFF", "period_paths": [{"path": str(off_path), "sha256": _hash_record(off_path)["sha256"]}]},
            {"method_id": "C0", "role": "ON", "period_paths": [{"path": str(on_path), "sha256": _hash_record(on_path)["sha256"]}]},
        ],
    }
    audit = audit_paired_scene_inputs(case=case, scene="fixture")
    assert len(audit) == 1
    assert audit[0]["audit_verified"] is True
    assert audit[0]["area_owner_continuous_packets"] == "390"

    (background / "data" / "period000.bin").write_bytes(b"noise-only")
    try:
        audit_paired_scene_inputs(case=case, scene="fixture")
    except ValueError as exc:
        assert "paired area input hash closure failed" in str(exc)
    else:
        raise AssertionError("OFF input that differs from the area background must fail closed")
