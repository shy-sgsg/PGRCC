from __future__ import annotations

import json
import hashlib
import zipfile
from pathlib import Path

import pytest

from scripts.phase_r import r22_experiment_matrix as matrix


def test_case_archive_hashes_audit_files_before_removing_case_tree(tmp_path: Path):
    archive_case = getattr(matrix, "archive_case_directory", None)
    assert callable(archive_case), "compact case archiving is not implemented"

    raw_root = tmp_path / "raw"
    case_root = raw_root / "screening" / "case-a" / "attempt_001"
    case_root.mkdir(parents=True)
    (case_root / "stage2_report.md").write_text("area packets: 1300\n", encoding="utf-8")
    (case_root / "production.xml").write_text("<runtime/>\n", encoding="utf-8")
    (case_root / "task_metrics.csv").write_text("hits,opportunities\n7,10\n", encoding="utf-8")
    (case_root / "per_case_plot.png").write_bytes(b"derived image")
    archive_path = tmp_path / "archives" / "case-a.zip"

    result = archive_case(case_root, archive_path, raw_root=raw_root)

    assert not case_root.exists()
    assert archive_path.is_file()
    assert result["included_file_count"] == 3
    assert result["excluded_file_count"] == 1
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read("ARCHIVE_MANIFEST.json"))
        assert {item["path"] for item in manifest["included_files"]} == {
            "production.xml", "stage2_report.md", "task_metrics.csv"
        }
        assert manifest["excluded_files"] == [
            {
                "path": "per_case_plot.png",
                "reason": "reproducible_per_case_plot; retain aggregate plots",
                "bytes": len(b"derived image"),
                "sha256": hashlib.sha256(b"derived image").hexdigest(),
            }
        ]
        assert archive.read("task_metrics.csv").decode() == "hits,opportunities\n7,10\n"


@pytest.mark.parametrize("suffix", [".bin", ".npy"])
def test_case_archive_refuses_to_delete_uncompacted_numeric_arrays(tmp_path: Path, suffix: str):
    archive_case = getattr(matrix, "archive_case_directory", None)
    assert callable(archive_case), "compact case archiving is not implemented"

    raw_root = tmp_path / "raw"
    case_root = raw_root / "screening" / "case-a"
    case_root.mkdir(parents=True)
    (case_root / f"remaining{suffix}").write_bytes(b"raw array")
    archive_path = tmp_path / "archives" / "case-a.zip"

    with pytest.raises(ValueError, match="raw array remains"):
        archive_case(case_root, archive_path, raw_root=raw_root)

    assert case_root.is_dir()
    assert (case_root / f"remaining{suffix}").is_file()
    assert not archive_path.exists()


def test_case_archive_refuses_paths_outside_the_declared_raw_root(tmp_path: Path):
    archive_case = getattr(matrix, "archive_case_directory", None)
    assert callable(archive_case), "compact case archiving is not implemented"

    raw_root = tmp_path / "raw"
    case_root = tmp_path / "user_data"
    case_root.mkdir()
    (case_root / "notes.md").write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="outside raw root"):
        archive_case(case_root, tmp_path / "archive.zip", raw_root=raw_root)

    assert (case_root / "notes.md").read_text(encoding="utf-8") == "keep"


def test_case_archive_reuses_verified_archive_after_interrupted_matrix_checkpoint(tmp_path: Path):
    archive_case = getattr(matrix, "archive_case_directory", None)
    assert callable(archive_case), "compact case archiving is not implemented"

    raw_root = tmp_path / "raw"
    case_root = raw_root / "screening" / "case-a"
    case_root.mkdir(parents=True)
    (case_root / "task_metrics.csv").write_text("hits\n8\n", encoding="utf-8")
    archive_path = tmp_path / "archives" / "case-a.zip"

    first = archive_case(case_root, archive_path, raw_root=raw_root, remove_source=False)
    second = archive_case(case_root, archive_path, raw_root=raw_root, remove_source=False)

    assert first["raw_case_removed"] is False
    assert second["raw_case_removed"] is False
    assert second["reused_existing_archive"] is True
    assert second["archive_sha256"] == first["archive_sha256"]
    assert case_root.is_dir()


def test_resume_archive_verifier_checks_profile_manifest_inventory_and_crc(tmp_path: Path):
    path = tmp_path / "resume.zip"
    payload = b"resume evidence\n"
    payload_record = {
        "path": "evidence.txt",
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    manifest_bytes = (json.dumps({
        "archive_profile": "legacy",
        "included_files": [payload_record],
        "required_v4_members": [],
    }, sort_keys=True) + "\n").encode()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("evidence.txt", payload)
        archive.writestr("ARCHIVE_MANIFEST.json", manifest_bytes)

    verified = matrix.verify_case_archive(
        path,
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        expected_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        archive_profile="legacy",
    )
    assert verified["crc_verified"] is True
    assert verified["member_manifest_verified"] is True

    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo("evidence.txt")
        data_offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    with path.open("r+b") as stream:
        stream.seek(data_offset)
        stream.write(bytes([payload[0] ^ 0x01]))
    with pytest.raises(ValueError, match="CRC check failed"):
        matrix.verify_case_archive(
            path,
            expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            expected_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
            archive_profile="legacy",
        )


def test_case_archive_omits_only_reconstructible_long_tables_and_raw_track_debug(tmp_path: Path):
    archive_case = getattr(matrix, "archive_case_directory", None)
    assert callable(archive_case), "compact case archiving is not implemented"

    raw_root = tmp_path / "raw"
    case_root = raw_root / "screening" / "case-b"
    files = {
        "evaluation/task_metrics.csv": "hits,opportunities\n8,10\n",
        "evaluation/target_level_metrics.csv": "target_id,hits\n1,8\n",
        "pipeline/cases/run/case_manifest.json": (
            "{\"case_id\":\"case-b\",\"raw_cleanup\":{\"removed_bytes\":9},"
            "\"r22_status_labels\":{\"EVALUABLE\":true},\"unneeded_payload\":[1,2,3]}\n"
        ),
        "pipeline/cases/run/raw_cleanup.json": "{\"removed_bytes\":9}\n",
        "pipeline/cases/run/references/Mode-A/C4/reference_gamma.csv": "duplicate reference\n",
        "pipeline/cases/run/production/C0/production.xml": "<runtime/>\n",
        "pipeline/cases/run/production/C0/branch_manifest.json": "{\"status\":\"passed\"}\n",
        "pipeline/cases/run/production/C0/gmt_pipe_core.log": "off role log\n",
        "pipeline/cases/run/production/C0/result/debug/cfar_geometry_diagnostics.csv": "valid_cut\n100\n",
        "pipeline/cases/run/production/C0/result/detection_results_GMTI09.csv": "repeated detections\n",
        "pipeline/cases/run/production/C0/result/GMTI09.txt": "repeated stage dump\n",
        "pipeline/cases/run/production/C0_modea_ON/gmt_pipe_core.log": "ON role log\n",
        "pipeline/cases/run/production/C0_modea_ON/result/detection_results_GMTI09.csv": "ON detections\n",
        "pipeline/cases/run/production/C0_modea_ON/result/detection_results_GMTI08.csv": "intermediate detections\n",
        "pipeline/cases/run/production/C0_modea_ON/result/GMTI09.txt": "ON stage dump\n",
        "pipeline/cases/run/production/C4_modea_ON/result/production_calibration_reference_gamma.csv": "ON reference\n",
        "pipeline/cases/run/production/C4_modea_TO/result/production_calibration_reference_gamma.csv": "TO reference\n",
        "evaluation/doppler_opportunity_audit.csv": "long table\n",
        "pipeline/cases/run/scenes/A0_ON/truth/truth_pulse.csv": "pulse truth\n",
        "pipeline/cases/run/scenes/A0_ON/truth/moving_target_truth.csv": "per-pulse truth\n",
        "pipeline/cases/run/scenes/A0_ON/truth/target_injection_amplitudes.csv": "per-pulse amplitude\n",
        "pipeline/cases/run/production/C0/track_debug_runs/run/track_events.csv": "debug events\n",
        "pipeline/cases/run/production/C0/cfar_map.f32": "map bytes",
    }
    for relative, content in files.items():
        path = case_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    archive_path = tmp_path / "archives" / "case-b.zip"
    archive_case(case_root, archive_path, raw_root=raw_root)

    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("ARCHIVE_MANIFEST.json"))
        included = {item["path"] for item in manifest["included_files"]}
        excluded = {item["path"]: item["reason"] for item in manifest["excluded_files"]}
        assert {
            "evaluation/task_metrics.csv",
            "evaluation/target_level_metrics.csv",
            "pipeline/cases/run/production/C0/production.xml",
            "pipeline/cases/run/production/C0/branch_manifest.json",
            "pipeline/cases/run/production/C0/result/debug/cfar_geometry_diagnostics.csv",
            "pipeline/cases/run/production/C0_modea_ON/gmt_pipe_core.log",
            "pipeline/cases/run/production/C0_modea_ON/result/detection_results_GMTI09.csv",
            "pipeline/cases/run/production/C0_modea_ON/result/GMTI09.txt",
            "pipeline/cases/run/production/C4_modea_ON/result/production_calibration_reference_gamma.csv",
            "case_manifest_review_extract.json",
        } <= included
        extract = json.loads(archive.read("case_manifest_review_extract.json"))
        assert extract == {
            "case_id": "case-b",
            "raw_cleanup": {"removed_bytes": 9},
            "r22_status_labels": {"EVALUABLE": True},
        }
        assert set(excluded) == {
            "pipeline/cases/run/production/C0/result/detection_results_GMTI09.csv",
            "pipeline/cases/run/production/C0/result/GMTI09.txt",
            "pipeline/cases/run/production/C0/gmt_pipe_core.log",
            "pipeline/cases/run/production/C0_modea_ON/result/detection_results_GMTI08.csv",
            "pipeline/cases/run/production/C4_modea_TO/result/production_calibration_reference_gamma.csv",
            "pipeline/cases/run/raw_cleanup.json",
            "pipeline/cases/run/references/Mode-A/C4/reference_gamma.csv",
            "pipeline/cases/run/case_manifest.json",
            "evaluation/doppler_opportunity_audit.csv",
            "pipeline/cases/run/scenes/A0_ON/truth/truth_pulse.csv",
            "pipeline/cases/run/scenes/A0_ON/truth/moving_target_truth.csv",
            "pipeline/cases/run/scenes/A0_ON/truth/target_injection_amplitudes.csv",
            "pipeline/cases/run/production/C0/track_debug_runs/run/track_events.csv",
            "pipeline/cases/run/production/C0/cfar_map.f32",
        }
        assert excluded["evaluation/doppler_opportunity_audit.csv"] == (
            "reconstructible_doppler_detail; retain parameter_metrics"
        )
        assert excluded["pipeline/cases/run/production/C0/result/detection_results_GMTI09.csv"] == (
            "repeated_non_on_detection_dump; retain evaluator metrics and ON diagnostics"
        )
        assert excluded["pipeline/cases/run/case_manifest.json"] == (
            "expanded_case_manifest; compact case manifest extract retained"
        )


def _write_v4_archive_case(case_root: Path, *, omit_detection_period: int | None = None) -> None:
    response_root = case_root / "pipeline" / "cases" / "run" / "r22_v4_output_evidence"
    response_root.mkdir(parents=True)
    outputs = {
        "target_complex_response.csv": "role,target_id,csi_after_real,csi_after_imag\nTO,T1,1,2\n",
        "target_roi_manifest.csv": "period_id,target_id,row_start,row_stop_exclusive\n0,T1,1,6\n",
        "adaptive_role_difference_diagnostic.csv": "method_id,relative_l2\nC0,0.5\n",
    }
    file_records = {}
    for name, text in outputs.items():
        path = response_root / name
        path.write_text(text, encoding="utf-8")
        file_records[name] = {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    (response_root / "target_complex_response_manifest.json").write_text(
        json.dumps({
            "status": "EVALUABLE",
            "extractor_identity": {"sha256": "a" * 64},
            "status_labels": {
                "IMPLEMENTED": True, "EXECUTED": True,
                "EVALUABLE": True, "PERFORMANCE_VERIFIED": False,
            },
            "files": file_records,
        }),
        encoding="utf-8",
    )
    source_names = (
        "scripts/phase_r/run_r22_cuda_pilot.py",
        "scripts/phase_r/r22_scenario_factory.py",
        "scripts/phase_r/r22_v4_evidence.py",
        "scripts/phase_r/r22_compact_archive.py",
        "scripts/phase_r/r22_experiment_matrix.py",
        "scripts/phase_r/run_r12_physics_matched_delay.py",
        "scripts/phase_r/run_r21_cuda_pilot.py",
        "scripts/phase_r/r21_task_evaluator.py",
        "scripts/phase_r/r21_historical_task_review.py",
        "scripts/run_delay_stage1_formal.py",
        "scripts/run_production_hierarchical_calibration_pilot.py",
        "scripts/run_track_manager_e2e.py",
    )
    (case_root / "r22_run_manifest.json").write_text(json.dumps({
        "source_identity": {
            "files": {
                name: {"sha256": "a" * 64} for name in source_names
            },
        },
        "stage2_binary": {"sha256": "b" * 64},
        "production_binary": {"sha256": "c" * 64},
        "native_cuda_preflight": {
            "gpu": {
                "available": True,
                "driver_identity": {"driver_versions": ["test-driver"]},
            },
        },
    }), encoding="utf-8")
    pipeline_case = case_root / "pipeline" / "cases" / "run"
    (pipeline_case / "case_manifest.json").write_text(
        json.dumps({"case_id": "v4-case", "raw_cleanup": {"removed_bytes": 0}}),
        encoding="utf-8",
    )
    for role in ("ON", "OFF", "TO"):
        branch_name = f"C0_{role}"
        branch_root = pipeline_case / "production" / branch_name
        result_root = branch_root / "result"
        debug_root = branch_root / "track_debug_runs"
        geometry_root = result_root / "debug"
        result_root.mkdir(parents=True)
        debug_root.mkdir(parents=True)
        geometry_root.mkdir(parents=True)
        (branch_root / "production.xml").write_text("<runtime/>\n", encoding="utf-8")
        (branch_root / "gmt_pipe_core.log").write_text("Production completed\n", encoding="utf-8")
        (debug_root / "track_output_payloads.csv").write_text("frame_id,track_id\n", encoding="utf-8")
        (debug_root / "track_events.csv").write_text("frame_id,event\n", encoding="utf-8")
        (geometry_root / "cfar_geometry_diagnostics.csv").write_text(
            "period_id,valid_cut_count\n0,100\n", encoding="utf-8",
        )
        for period in range(2):
            if period == omit_detection_period:
                continue
            (result_root / f"detection_results_GMTI{period + 1:02d}.csv").write_text(
                "detection_id\n", encoding="utf-8",
            )
        branch_record = {
            "branch_name": branch_name,
            "condition": "C0",
            "role": role,
            "status": "passed",
            "production_returncode": 0,
            "period_count_expected": 2,
            "production_xml": str(branch_root / "production.xml"),
            "production_log": str(branch_root / "gmt_pipe_core.log"),
            "result_dir": str(result_root),
            "track_debug_dir": str(debug_root),
        }
        (branch_root / "branch_manifest.json").write_text(
            json.dumps(branch_record), encoding="utf-8",
        )


def test_v4_archive_profile_keeps_all_detection_and_track_lineage(tmp_path: Path):
    raw_root = tmp_path / "raw"
    case_root = raw_root / "confirmatory" / "case-v4"
    case_root.mkdir(parents=True)
    _write_v4_archive_case(case_root)
    archive_path = tmp_path / "archives" / "case-v4.zip"

    result = matrix.archive_case_directory(
        case_root, archive_path, raw_root=raw_root,
        archive_profile="r22_v4_output_complete", require_complete_output=True,
    )

    assert result["raw_case_removed"] is True
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("ARCHIVE_MANIFEST.json"))
        assert manifest["archive_profile"] == "r22_v4_output_complete"
        for role in ("ON", "OFF", "TO"):
            for period in (1, 2):
                assert f"pipeline/cases/run/production/C0_{role}/result/detection_results_GMTI{period:02d}.csv" in names
            assert f"pipeline/cases/run/production/C0_{role}/track_debug_runs/track_events.csv" in names
            assert f"pipeline/cases/run/production/C0_{role}/track_debug_runs/track_output_payloads.csv" in names
        assert "pipeline/cases/run/r22_v4_output_evidence/target_complex_response.csv" in names
        assert "pipeline/cases/run/r22_v4_output_evidence/adaptive_role_difference_diagnostic.csv" in names
        assert len(manifest["required_v4_members"]) > 0


def test_v4_archive_refuses_complete_status_when_any_detection_period_is_missing(tmp_path: Path):
    raw_root = tmp_path / "raw"
    case_root = raw_root / "confirmatory" / "case-v4-incomplete"
    case_root.mkdir(parents=True)
    _write_v4_archive_case(case_root, omit_detection_period=1)
    archive_path = tmp_path / "archives" / "case-v4-incomplete.zip"

    with pytest.raises(ValueError, match="detection period count mismatch"):
        matrix.archive_case_directory(
            case_root, archive_path, raw_root=raw_root,
            archive_profile="r22_v4_output_complete", require_complete_output=True,
        )
    assert case_root.is_dir()
    assert not archive_path.exists()
