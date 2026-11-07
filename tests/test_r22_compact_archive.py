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
