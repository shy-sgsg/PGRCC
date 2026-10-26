import subprocess
import sys
from pathlib import Path

from scripts.phase_r.r19_task_evaluator import (
    audit_h0,
    common_success_keys,
    evaluate_targets,
    reference_role,
    rmse_from_samples,
    summarize_track_lineage,
    validate_comparison_identity,
)


def test_r19_snapshot_evaluator_cli_resolves_repo_imports():
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts/phase_r/r19_r18_snapshot_evaluation.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--case-key" in completed.stdout


def test_r19_csv_outputs_use_lf_line_endings(tmp_path):
    from scripts.phase_r.r19_r18_snapshot_evaluation import _write_csv

    path = tmp_path / "result.csv"
    _write_csv(path, [{"metric": "x", "value": 1}])
    assert b"\r" not in path.read_bytes()


def truth(target_id="T0", period_id=0, beam_id=50, **changes):
    row = {
        "target_id": target_id,
        "period_id": period_id,
        "beam_id": beam_id,
        "visible": "1",
        "range_bin": 100,
        "range_m": 10_000.0,
        "e_mid": 0.0,
        "n_mid": 0.0,
        "af_total_truth_hz": -150.0,
    }
    row.update(changes)
    return row


def detection(det_id="D0", period_id=0, beam_id=50, **changes):
    row = {
        "det_id": det_id,
        "period_id": period_id,
        "beam_id": beam_id,
        "range_bin": 100,
        "range_m": 10_000.0,
        "row": 35,
        "new_e": 10.0,
        "new_n": 5.0,
        "fd_ctr_unwrapped": 0.0,
        "prf_hz": 1000.0,
        "radial_velocity_mps": 2.0,
        "ambiguity_status": "resolved",
        "platform_e": -1.0,
        "platform_n": -1.0,
        "af_unwrapped_hz": -150.0,
    }
    row.update(changes)
    return row


def test_single_target_single_detection_matches_once():
    result = evaluate_targets([truth()], [detection()], doppler_rows=100)
    assert result["metrics"]["truth_opportunities"] == 1
    assert result["metrics"]["unique_hits"] == 1
    assert result["metrics"]["truth_opportunity_hit_fraction"] == 1.0
    assert result["parameter_samples"]["position_m"][0]["sample_key"] == "T0/0/50"


def test_detection_matches_with_saved_production_axis_without_frequency_center():
    detection_row = detection(row=50)
    detection_row.pop("fd_ctr_unwrapped")
    axis = {
        "period_id": 0,
        "beam_id": 50,
        "doppler_axis_first_hz": -500.0,
        "doppler_bin_spacing_hz": 10.0,
        "doppler_row_count": 100,
        "doppler_axis_uniform": 1,
    }
    result = evaluate_targets(
        [truth(af_total_truth_hz=0.0)], [detection_row],
        doppler_rows=100, doppler_axis_rows=[axis],
    )
    assert result["metrics"]["unique_hits"] == 1


def test_snapshot_loader_prefers_complete_per_period_production_csvs(tmp_path):
    from scripts.phase_r.r19_r18_snapshot_evaluation import _load_final_detections

    result_dir = tmp_path / "branch" / "result"
    result_dir.mkdir(parents=True)
    (result_dir / "detection_results.csv").write_text(
        "period_id,beam_id,det_id\n2,50,last-only\n", encoding="utf-8",
    )
    (result_dir / "detection_results_GMTI01.csv").write_text(
        "period_id,beam_id,det_id\n0,50,first\n", encoding="utf-8",
    )
    (result_dir / "detection_results_GMTI02.csv").write_text(
        "period_id,beam_id,det_id\n1,50,second\n", encoding="utf-8",
    )
    rows = _load_final_detections(tmp_path / "branch")
    assert [(row["period_id"], row["det_id"]) for row in rows] == [
        ("0", "first"), ("1", "second"),
    ]


def test_r18_release_build_source_is_resolved_from_pinned_provenance():
    from scripts.phase_r.r19_r18_snapshot_evaluation import _build_source_identity

    identity = _build_source_identity({
        "build": {"GMTI_pipe_core": {"sha256": "3328d7c61bc36b6d3b906692e355c46c328150c0399fa457e6f871bcb79abaf0"}},
    })
    assert identity == {
        "commit": "4c6d4d303621324f5e174db0ba1811c5691df2e2",
        "source": "docs/research/phase_r/R18/05_execution_and_quality_audit.md",
    }


def test_analysis_worktree_dirty_flag_reflects_git_status(tmp_path):
    import subprocess

    from scripts.phase_r.r19_r18_snapshot_evaluation import _worktree_dirty

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "R19 test"], check=True)
    (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "baseline"], check=True)
    assert _worktree_dirty(repo) is False
    (repo / "new.txt").write_text("untracked\n", encoding="utf-8")
    assert _worktree_dirty(repo) is True


def test_metric_evaluability_table_keeps_pfa_and_replay_limits_explicit():
    from scripts.phase_r.r19_r18_snapshot_evaluation import _metric_evaluability_rows

    case_row = {
        "case_key": "seed606_v6p7_snr30", "case_id": "case", "seed": "606",
        "target_velocity_mps": "6.7", "target_snr_db": "30", "delay_error_ns": "0",
        "condition": "C0", "role": "ON", "strict_truth_hits": 2,
        "strict_truth_opportunities": 3, "strict_hit_fraction": 2 / 3,
        "cfar_full_hit_count": 10, "cfar_full_valid_cut_count": 100,
        "cfar_full_cut_hit_fraction": 0.1, "postfilter_clusters": 4,
        "final_detection_rows": 4, "truth_unmatched_detection_rows": 2,
        "evaluator_status": "evaluable", "track_metrics": {"status": "evaluable"},
    }
    h0_row = {
        **{k: case_row[k] for k in ("case_key", "case_id", "seed", "target_velocity_mps", "target_snr_db", "delay_error_ns")},
        "condition": "C0", "role": "OFF", "h0_status": "evaluable_descriptive_only",
        "h0_observed_false_hit_fraction": 0.1, "empirical_pfa_status": "NOT_EVALUABLE",
        "cfar_full_hit_count": 10, "cfar_full_valid_cut_count": 100,
    }
    csi_row = {
        **{k: case_row[k] for k in ("case_key", "case_id", "seed", "target_velocity_mps", "target_snr_db", "delay_error_ns")},
        "method": "C2", "mode": "Mode-B", "CA_ROI_dB": -1.0, "valid_cells": 10,
    }
    rows = _metric_evaluability_rows([case_row], [h0_row], [], [], [csi_row])
    by_metric = {row["metric"]: row for row in rows}
    assert by_metric["empirical_Pfa"]["status"] == "NOT_EVALUABLE"
    assert by_metric["target_only_complex_transfer"]["status"] == "REPLAY_REQUIRED"


def test_single_target_multiple_detections_selects_one_and_counts_duplicate():
    result = evaluate_targets(
        [truth()],
        [detection("D1"), detection("D2", range_bin=101, row=36, new_e=5.0)],
        doppler_rows=100,
    )
    assert result["metrics"]["unique_hits"] == 1
    assert result["metrics"]["duplicate_candidate_count"] == 1
    assert result["metrics"]["truth_unmatched_detection_rows"] == 1


def test_multiple_targets_and_candidates_use_global_one_to_one_assignment():
    truths = [truth("T0"), truth("T1", range_bin=102, range_m=10_030.0, e_mid=30.0)]
    detections = [
        detection("D0", range_bin=100, range_m=10_000.0, new_e=5.0),
        detection("D1", range_bin=102, range_m=10_030.0, new_e=28.0),
    ]
    result = evaluate_targets(truths, detections, doppler_rows=100)
    assert result["metrics"]["unique_hits"] == 2
    assert len({row["det_id"] for row in result["associations"] if row["matched"]}) == 2


def test_matching_conflict_does_not_reuse_detection_for_two_truths():
    truths = [truth("T0"), truth("T1", range_bin=101, range_m=10_020.0, e_mid=20.0)]
    result = evaluate_targets(truths, [detection("D0")], doppler_rows=100)
    assert result["metrics"]["unique_hits"] == 1
    assert result["metrics"]["misses"] == 1


def test_unmatched_target_remains_in_opportunity_denominator():
    result = evaluate_targets([truth()], [], doppler_rows=100)
    assert result["metrics"]["truth_opportunities"] == 1
    assert result["metrics"]["misses"] == 1
    assert result["metrics"]["truth_opportunity_hit_fraction"] == 0.0


def test_invalid_range_or_position_cannot_match():
    bad_distance = detection("D0", range_m="nan")
    bad_position = detection("D1", new_e=5000.0)
    result = evaluate_targets([truth()], [bad_distance, bad_position], doppler_rows=100)
    assert result["metrics"]["unique_hits"] == 0
    assert result["metrics"]["invalid_detection_rows"] == 1
    assert result["metrics"]["gated_out_candidate_edges"] >= 1


def test_nan_and_infinity_are_invalid_not_silent_matches():
    result = evaluate_targets(
        [truth(e_mid=float("nan"))],
        [detection(new_n=float("inf"))],
        doppler_rows=100,
    )
    assert result["metrics"]["truth_opportunities"] == 1
    assert result["metrics"]["invalid_truth_opportunities"] == 1
    assert result["metrics"]["unique_hits"] == 0


def test_no_valid_cut_has_no_hit_fraction():
    result = audit_h0(
        hit_count=0, valid_cut_count=0, target_free=True, role="OFF",
        reference_role="C0_FIXED", independent_holdout=False, independent_seed_count=3,
    )
    assert result["cut_hit_fraction"] is None
    assert result["status"] == "NOT_EVALUABLE"


def test_h0_denominator_missing_stays_not_evaluable():
    result = audit_h0(
        hit_count=3, valid_cut_count=None, target_free=True, role="OFF",
        reference_role="C0_FIXED", independent_holdout=False, independent_seed_count=3,
    )
    assert result["h0_observed_false_hit_fraction"] is None
    assert result["statistical_pfa_status"] == "NOT_EVALUABLE"


def test_scene_with_target_cannot_be_relabelled_h0():
    result = audit_h0(
        hit_count=4, valid_cut_count=100, target_free=False, role="ON",
        reference_role="C0_FIXED", independent_holdout=True, independent_seed_count=20,
    )
    assert result["h0_observed_false_hit_fraction"] is None
    assert result["status"] == "NOT_H0_TARGET_PRESENT"


def test_mode_a_off_is_marked_sample_in_reference():
    assert reference_role(mode="Mode-A", estimator_role="OFF", evaluation_role="OFF") == "PAIRED_OFF_REFERENCE_SAMPLE_IN"


def test_mode_b_off_is_marked_on_estimate_counterfactual():
    assert reference_role(mode="Mode-B", estimator_role="ON", evaluation_role="OFF") == "ON_ESTIMATE_OFF_COUNTERFACTUAL"


def test_tentative_first_track_period_is_not_a_confirmed_target_track():
    result = summarize_track_lineage(
        observations=[{"period_id": 0, "beam_id": 50, "track_id": "1", "state": "Tentative",
                       "matched_this_frame": True, "det_id": "D0", "output_payload": False}],
        target_matched_detection_keys=set(), association_audit_available=False,
    )
    assert result["confirmed_track_periods"] == 0
    assert result["false_confirmed_track_count"] == 0


def test_confirmation_and_continuity_follow_production_track_rows():
    observations = [
        {"period_id": p, "beam_id": 50, "track_id": "7", "state": "Confirmed", "matched_this_frame": True,
         "det_id": f"D{p}", "output_payload": True}
        for p in (0, 1, 2)
    ]
    result = summarize_track_lineage(
        observations=observations,
        target_matched_detection_keys={(0, 50, "D0"), (1, 50, "D1"), (2, 50, "D2")},
        association_audit_available=False,
    )
    assert result["target_matched_periods"] == 3
    assert result["unique_target_track_count"] == 1
    assert result["track_continuity"] == 1.0


def test_id_switch_root_cause_is_not_guessed_without_v2_audit():
    observations = [
        {"period_id": 0, "beam_id": 50, "track_id": "7", "state": "Confirmed", "matched_this_frame": True,
         "det_id": "D0", "output_payload": True},
        {"period_id": 1, "beam_id": 50, "track_id": "8", "state": "Confirmed", "matched_this_frame": True,
         "det_id": "D1", "output_payload": True},
    ]
    result = summarize_track_lineage(
        observations=observations,
        target_matched_detection_keys={(0, 50, "D0"), (1, 50, "D1")}, association_audit_available=False,
    )
    assert result["id_switch_status"] == "NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG"


def test_missing_track_association_fields_are_not_evaluable():
    result = summarize_track_lineage(
        observations=[{"period_id": 0, "beam_id": 50, "track_id": "7", "state": "Confirmed"}],
        target_matched_detection_keys=set(), association_audit_available=False,
    )
    assert result["status"] == "NOT_EVALUABLE"


def test_common_success_uses_intersection_without_reweighting_own_success():
    samples = {
        "C0": [{"sample_key": "a", "error": 1.0}, {"sample_key": "b", "error": 2.0}],
        "C1": [{"sample_key": "b", "error": 4.0}, {"sample_key": "c", "error": 8.0}],
    }
    assert common_success_keys(samples) == {"b"}
    assert rmse_from_samples(samples["C0"])["count"] == 2
    assert rmse_from_samples([row for row in samples["C0"] if row["sample_key"] in common_success_keys(samples)])["rmse"] == 2.0


def test_duplicate_rmse_opportunity_is_rejected():
    try:
        rmse_from_samples([{"sample_key": "a", "error": 1.0}, {"sample_key": "a", "error": 2.0}])
    except ValueError as exc:
        assert "duplicate" in str(exc).lower()
    else:
        raise AssertionError("duplicate opportunity must not be counted twice")


def test_ambiguous_radial_velocity_is_not_evaluable():
    result = evaluate_targets(
        [truth()], [detection(ambiguity_status="unresolved")], doppler_rows=100,
    )
    sample = result["associations"][0]
    assert sample["matched"] is True
    assert sample["radial_velocity_status"] == "NOT_EVALUABLE_AMBIGUITY"
    assert sample["radial_velocity_error_mps"] is None


def test_mixed_seed_config_or_binary_identity_is_rejected():
    base = {"seed": 606, "config_sha256": "cfg", "production_binary_sha256": "bin"}
    mixed = dict(base, production_binary_sha256="other")
    try:
        validate_comparison_identity([base, mixed])
    except ValueError as exc:
        assert "identity" in str(exc).lower()
    else:
        raise AssertionError("mixed identities must not be paired")


def test_track_with_target_match_and_unmatched_payload_is_not_false_track():
    observations = [
        {"period_id": 0, "beam_id": 50, "track_id": "7", "state": "Confirmed",
         "matched_this_frame": True, "det_id": "D0", "output_payload": True},
        {"period_id": 1, "beam_id": 50, "track_id": "7", "state": "Confirmed",
         "matched_this_frame": True, "det_id": "D99", "output_payload": True},
    ]
    result = summarize_track_lineage(
        observations=observations,
        target_matched_detection_keys={(0, 50, "D0")}, association_audit_available=False,
    )
    assert result["false_confirmed_track_count"] == 0
    assert result["unmatched_confirmed_payload_count"] == 1
    assert result["target_track_ids"] == ["7"]
