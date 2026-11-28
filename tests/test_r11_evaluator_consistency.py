from __future__ import annotations

import json
from pathlib import Path

from scripts.phase_r.reanalyze_r11_evaluator_consistency import analyze_condition


MATCH_CONFIG = json.loads(
    (Path(__file__).resolve().parents[1] / "configs/eval/localization_match_config.json")
    .read_text(encoding="utf-8")
)


def _truth(**overrides):
    row = {
        "period_id": "0",
        "beam_id": "50",
        "target_id": "target-0",
        "visible": "1",
        "range_bin": "2154",
        "range_m": "1000",
        "af_total_truth_hz": "-170",
        "e_mid": "0",
        "n_mid": "0",
        "utc_mid": "1",
        "ve_mps": "3",
        "vn_mps": "4",
        "vr_self_mps": "2",
        "azimuth_deg": "0",
    }
    row.update({key: str(value) for key, value in overrides.items()})
    return row


def _detection(det_id, row, col, **overrides):
    value = {
        "det_id": str(det_id),
        "period_id": "0",
        "beam_id": "50",
        "range_bin": str(col),
        "row": str(row),
        "col": str(col),
        "range_m": "1000",
        "utc_mid": "1",
        "fd_ctr_unwrapped": "0",
        "prf_hz": "1300",
        "theta_used_deg": "0",
        "new_e": "0",
        "new_n": "0",
        "platform_e": "-1",
        "platform_n": "0",
        "radial_velocity_mps": "2",
        "ambiguity_status": "resolved",
    }
    value.update({key: str(item) for key, item in overrides.items()})
    return value


def _lineage(row, col, *, period=0, beam=50, selected=1, raw=1):
    return {
        "record_type": "cluster",
        "period_id": str(period),
        "beam_id": str(beam),
        "cluster_index": f"{row}-{col}",
        "cluster_row": str(row),
        "cluster_col": str(col),
        "selected": str(selected),
        "raw_detection": str(raw),
    }


def _evaluate(truth, detections, lineage):
    return analyze_condition(
        truths=[truth],
        detections=detections,
        lineage_rows=lineage,
        doppler_axis_rows=[
            {
                "period_id": 0,
                "beam_id": 50,
                "doppler_axis_first_hz": -650,
                "doppler_bin_spacing_hz": 10,
                "doppler_row_count": 130,
                "doppler_axis_uniform": 1,
            }
        ],
        doppler_rows=130,
        match_config=MATCH_CONFIG,
    )


def test_r11_truth_cell_row48_col2154_is_accepted_by_frozen_r8_gate():
    det = _detection("cell-48", 48, 2154)
    result = _evaluate(_truth(), [det], [_lineage(48, 2154)])

    assert result["frozen_r8"]["association"]["matched"] == 1
    assert result["frozen_r8"]["association"]["detection_row"] == 48
    assert result["frozen_r8"]["association"]["range_delta_bins"] == 0
    assert result["frozen_r8"]["association"]["doppler_delta_bins"] == 0


def test_r11_row129_col2155_candidate_is_rejected_49_circular_doppler_bins_away():
    det = _detection("alternate", 129, 2155)
    result = _evaluate(_truth(), [det], [_lineage(129, 2155)])

    pair = result["frozen_r8"]["candidate_pair_evidence"][0]
    assert result["frozen_r8"]["association"]["matched"] == 0
    assert pair["truth_row"] == 48
    assert pair["range_delta_bins"] == 1
    assert pair["doppler_delta_bins"] == 49
    assert pair["doppler_delta_signed_bins"] == -49
    assert pair["gate_result"] == "doppler_gate"


def test_frozen_doppler_gate_accepts_circular_wraparound_at_two_bins():
    det = _detection("wrap", 128, 2154)
    result = _evaluate(_truth(af_total_truth_hz=-650), [det], [_lineage(128, 2154)])

    assert result["frozen_r8"]["association"]["truth_row"] == 0
    assert result["frozen_r8"]["association"]["doppler_delta_bins"] == 2
    assert result["frozen_r8"]["association"]["matched"] == 1


def test_frozen_range_gate_includes_plus_and_minus_two_bins_only():
    for delta in (-2, 2):
        det = _detection(f"range-{delta}", 48, 2154 + delta)
        result = _evaluate(_truth(), [det], [_lineage(48, 2154 + delta)])
        assert result["frozen_r8"]["association"]["matched"] == 1
        assert result["frozen_r8"]["association"]["range_delta_bins"] == 2

    det = _detection("range-plus-three", 48, 2157)
    result = _evaluate(_truth(), [det], [_lineage(48, 2157)])
    assert result["frozen_r8"]["association"]["matched"] == 0
    assert result["frozen_r8"]["candidate_pair_evidence"][0]["gate_result"] == "range_gate"


def test_frozen_gate_never_associates_across_period_or_beam():
    for override in ({"period_id": "1"}, {"beam_id": "51"}):
        det = _detection("mismatch", 48, 2154, **override)
        lineage = [_lineage(48, 2154, period=int(det["period_id"]), beam=int(det["beam_id"]))]
        result = _evaluate(_truth(), [det], lineage)
        assert result["frozen_r8"]["association"]["matched"] == 0


def test_frozen_gate_uses_one_to_one_minimum_residual_for_multiple_candidates():
    best = _detection("best", 48, 2154)
    second = _detection("second", 49, 2154)
    result = _evaluate(
        _truth(), [best, second], [_lineage(48, 2154), _lineage(49, 2154)]
    )

    assert result["frozen_r8"]["metrics"]["unique_correct_detections"] == 1
    assert result["frozen_r8"]["metrics"]["false_associations"] == 1
    assert result["frozen_r8"]["association"]["det_id"] == "best"
    assert result["frozen_r8"]["association"]["normalized_residual"] == 0


def test_geometry_match_is_diagnostic_and_never_replaces_frozen_match():
    frozen_candidate = _detection("frozen", 48, 2154, new_n=500)
    geometry_candidate = _detection("geometry", 129, 2155, new_n=1)
    result = _evaluate(
        _truth(),
        [frozen_candidate, geometry_candidate],
        [_lineage(48, 2154), _lineage(129, 2155)],
    )

    assert result["geometry_diagnostic"]["detection"]["det_id"] == "geometry"
    assert result["frozen_r8"]["association"]["det_id"] == "frozen"
    assert result["frozen_r8"]["matched_detection"]["det_id"] == "frozen"
    assert result["frozen_r8"]["metrics"]["parameter_errors"]["position_2d_rmse_m"] == 500
