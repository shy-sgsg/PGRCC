import importlib.util
from pathlib import Path

import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "visualize_track_manager.py"
SPEC = importlib.util.spec_from_file_location("visualize_track_manager", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _frame(result_id, detections, tracks):
    return {
        "result_id": result_id,
        "num_detections": detections,
        "num_tracks": tracks,
    }


def _state(result_id, track_id, age, e):
    return {
        "result_id": result_id,
        "track_id": track_id,
        "age": age,
        "e": e,
    }


def _detection(result_id, det_index):
    return {"result_id": result_id, "det_index": det_index}


def test_append_only_csv_is_split_when_track_ids_restart():
    frames = pd.DataFrame(
        [
            _frame(11, 1, 1),
            _frame(12, 1, 1),
            # A later process writes a larger result_id but reuses track_id=1.
            _frame(18, 1, 1),
            _frame(19, 1, 1),
        ]
    )
    states = pd.DataFrame(
        [
            _state(11, 1, 1, 0.0),
            _state(12, 1, 2, 10.0),
            _state(18, 1, 1, 10000.0),
            _state(19, 1, 2, 10010.0),
        ]
    )
    detections = pd.DataFrame(
        [_detection(result_id, 0) for result_id in (11, 12, 18, 19)]
    )

    frames, detections, states = MODULE.annotate_debug_runs(
        frames, detections, states
    )

    assert states["_run_index"].tolist() == [0, 0, 1, 1]
    selected_frames, selected_detections, selected_states, selected = (
        MODULE.select_debug_run(frames, detections, states, "latest")
    )
    assert selected == 1
    assert selected_frames["result_id"].tolist() == [18, 19]
    assert selected_detections["result_id"].tolist() == [18, 19]
    assert selected_states["e"].tolist() == [10000.0, 10010.0]


def test_append_only_csv_is_split_when_result_id_decreases():
    frames = pd.DataFrame(
        [_frame(13, 1, 1), _frame(14, 1, 1), _frame(13, 1, 1)]
    )
    states = pd.DataFrame(
        [
            _state(13, 1, 1, 0.0),
            _state(14, 1, 2, 1.0),
            _state(13, 1, 1, 5000.0),
        ]
    )
    detections = pd.DataFrame(
        [_detection(13, 0), _detection(14, 0), _detection(13, 0)]
    )

    _, _, states = MODULE.annotate_debug_runs(frames, detections, states)

    assert states["_run_index"].tolist() == [0, 0, 1]


def test_snapshot_row_count_mismatch_is_rejected():
    frames = pd.DataFrame([_frame(11, 2, 1)])
    states = pd.DataFrame([_state(11, 1, 1, 0.0)])
    detections = pd.DataFrame([_detection(11, 0)])

    try:
        MODULE.annotate_debug_runs(frames, detections, states)
    except ValueError as exc:
        assert "track_detections.csv is truncated" in str(exc)
    else:
        raise AssertionError("expected truncated debug CSV to be rejected")


def test_dbs_image_axis_limits_include_padding_and_keep_y_reversed():
    states = pd.DataFrame()
    detections = pd.DataFrame()
    dbs_bg = {"mode": "image", "width": 101, "height": 201}

    xlim, ylim = MODULE.compute_axis_limits_with_dbs(
        states, detections, None, "geo", 0.10, dbs_bg, "union"
    )

    assert xlim == (-10.0, 110.0)
    assert ylim == (220.0, -20.0)


def test_dbs_warp_axis_limits_include_padding():
    states = pd.DataFrame()
    detections = pd.DataFrame()
    dbs_bg = {"mode": "warp", "extent": [100.0, 200.0, 20.0, 60.0]}

    xlim, ylim = MODULE.compute_axis_limits_with_dbs(
        states, detections, None, "geo", 0.10, dbs_bg, "dbs"
    )

    assert xlim == (90.0, 210.0)
    assert ylim == (16.0, 64.0)
