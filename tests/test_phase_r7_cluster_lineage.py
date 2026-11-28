from scripts.phase_r.evaluate_r7_task_metrics import evaluate_case, join_cluster_lineage


def test_final_detection_joins_cluster_lineage_by_exact_period_beam_row_and_column():
    clusters = [
        {"period_id": "0", "beam_id": "50", "cluster_index": "0",
         "cluster_row": "50", "cluster_col": "100", "selected": "1",
         "raw_detection": "1"},
        {"period_id": "0", "beam_id": "50", "cluster_index": "1",
         "cluster_row": "51", "cluster_col": "200", "selected": "1",
         "raw_detection": "0"},
        {"period_id": "0", "beam_id": "50", "cluster_index": "2",
         "cluster_row": "52", "cluster_col": "300", "selected": "0",
         "raw_detection": "0"},
    ]
    detections = [
        {"det_id": "7", "period_id": "0", "beam_id": "50",
         "row": "50", "col": "100"},
        # A one-cell-nearby output must not be attached to the rejected peak.
        {"det_id": "8", "period_id": "0", "beam_id": "50",
         "row": "52", "col": "301"},
    ]

    joined = join_cluster_lineage(clusters, detections)

    assert joined["clusters"][0]["final_detection_ids"] == ["7"]
    assert joined["clusters"][1]["final_detection_ids"] == []
    assert joined["clusters"][2]["final_detection_ids"] == []
    assert joined["unjoined_detection_ids"] == ["8"]
    assert joined["clusters"][0]["lineage_stage"] == "final_detection"
    assert joined["clusters"][1]["lineage_stage"] == "selected_no_raw_detection"
    assert joined["clusters"][2]["lineage_stage"] == "rejected_before_selection"


def test_same_row_and_column_from_another_period_or_beam_does_not_join():
    clusters = [
        {"period_id": "1", "beam_id": "50", "cluster_index": "0",
         "cluster_row": "50", "cluster_col": "100", "selected": "1",
         "raw_detection": "1"}
    ]
    detections = [
        {"det_id": "7", "period_id": "0", "beam_id": "50",
         "row": "50", "col": "100"},
        {"det_id": "8", "period_id": "1", "beam_id": "51",
         "row": "50", "col": "100"},
    ]

    joined = join_cluster_lineage(clusters, detections)

    assert joined["clusters"][0]["final_detection_ids"] == []
    assert joined["unjoined_detection_ids"] == ["7", "8"]


def test_duplicate_cluster_lineage_key_fails_instead_of_guessing():
    cluster = {"period_id": "0", "beam_id": "50", "cluster_index": "0",
               "cluster_row": "50", "cluster_col": "100", "selected": "1",
               "raw_detection": "1"}

    try:
        join_cluster_lineage([cluster, dict(cluster, cluster_index="1")], [])
    except ValueError as error:
        assert "duplicate" in str(error).lower()
    else:
        raise AssertionError("duplicate cluster coordinates must fail closed")


def test_beam_summaries_preserve_zero_cluster_beams_and_validate_row_counts():
    result = evaluate_case(
        mode="ON",
        truths=[{
            "period_id": "0", "beam_id": "50", "target_id": "t0", "visible": "1",
            "range_bin": "100", "af_total_truth_hz": "-150", "e_mid": "0", "n_mid": "0",
            "ve_mps": "0", "vn_mps": "0", "vr_self_mps": "0", "azimuth_deg": "0",
        }],
        final_detections=[{
            "det_id": "0", "period_id": "0", "beam_id": "50", "range_bin": "100",
            "row": "50", "col": "100", "fd_ctr_unwrapped": "0", "prf_hz": "1300",
        }],
        lineage_rows=[
            {"record_type": "beam_summary", "period_id": "0", "beam_id": "50",
             "postfilter_cluster_count": "1", "selected_candidate_count": "1",
             "raw_detection_count": "1"},
            {"record_type": "beam_summary", "period_id": "0", "beam_id": "51",
             "postfilter_cluster_count": "0", "selected_candidate_count": "0",
             "raw_detection_count": "0"},
            {"record_type": "cluster", "period_id": "0", "beam_id": "50",
             "cluster_index": "0", "cluster_row": "50", "cluster_col": "100",
             "selected": "1", "raw_detection": "1"},
        ],
        doppler_rows=130,
    )

    assert result["lineage"]["status"] == "complete"
    assert result["lineage"]["beam_summary_count"] == 2
    assert result["lineage"]["postfilter_clusters"] == 1
