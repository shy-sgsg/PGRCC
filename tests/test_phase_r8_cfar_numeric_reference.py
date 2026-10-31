import numpy as np

from scripts.phase_r.audit_r8_cfar_numeric_reference import (
    audit_branch_maps,
    role_branch_manifest_path,
    on_branch_manifest_path,
)


def _geometry_row():
    return {
        "height": "3",
        "width": "5",
        "guard_cells": "0",
        "background_cells": "1",
        "doppler_circular": "true",
        "exclude_row_start": "-1",
        "exclude_row_end": "-1",
        "cut_band_start": "0",
        "cut_band_end": "2",
        "cut_band_mode": "0",
        "alpha": "1.0",
        "valid_cut_count": "9",
        "threshold_test_count": "9",
    }


def test_r8_cfar_float64_reference_gate_accepts_matching_hits():
    power = np.ones((3, 5), dtype=np.float32)
    threshold = np.ones((3, 5), dtype=np.float32)
    hits = np.zeros((3, 5), dtype=np.float32)
    result = audit_branch_maps(_geometry_row(), power, threshold, hits)

    assert result["status"] == "passed"
    assert result["valid_cut_count"] == 9
    assert result["hit_flips"] == 0


def test_r8_cfar_float64_reference_gate_rejects_hit_flip():
    power = np.ones((3, 5), dtype=np.float32)
    threshold = np.ones((3, 5), dtype=np.float32)
    hits = np.zeros((3, 5), dtype=np.float32)
    hits[1, 2] = 1.0
    result = audit_branch_maps(_geometry_row(), power, threshold, hits)

    assert result["status"] == "failed"
    assert result["hit_flips"] == 1


def test_r8_cfar_audit_selects_the_requested_on_branch():
    path = on_branch_manifest_path("/pilot/case", "A1_Current_unknown_error")

    assert str(path) == (
        "/pilot/case/production/A1_Current_unknown_error_ON/branch_manifest.json"
    )


def test_r8_cfar_audit_rejects_unregistered_condition():
    try:
        on_branch_manifest_path("/pilot/case", "A3_Blind")
    except ValueError as exc:
        assert "unknown R8 condition" in str(exc)
    else:
        raise AssertionError("unregistered condition was accepted")


def test_r8_cfar_audit_selects_h0_off_branch():
    path = role_branch_manifest_path("/pilot/case", "A2_Known_error_correction_upper_bound", "OFF")

    assert str(path) == (
        "/pilot/case/production/A2_Known_error_correction_upper_bound_OFF/branch_manifest.json"
    )


def test_r8_cfar_audit_rejects_unsupported_role():
    try:
        role_branch_manifest_path("/pilot/case", "A0_Ideal", "CALIBRATION")
    except ValueError as exc:
        assert "role must be ON or OFF" in str(exc)
    else:
        raise AssertionError("unsupported role was accepted")
