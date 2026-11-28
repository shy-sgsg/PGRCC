from __future__ import annotations

import json
import hashlib
import posixpath
import zipfile
import stat
from pathlib import Path

import pytest

from scripts.phase_r import r22_experiment_matrix as matrix
from scripts.phase_r import r22_compact_archive as compact
from scripts.phase_r import r22_sharded_archive as sharded


def test_v4_archive_branch_groups_keep_modes_separate_and_fail_missing_or_duplicate_roles():
    records = [
        {
            "case_id": "case-1", "method_id": "C4", "condition": "C4",
            "mode": mode, "role": role,
            "branch_name": f"C4_{mode.replace('-', '').lower()}_{role}",
            "source_configuration_identity": {},
        }
        for mode in ("Mode-A", "Mode-B") for role in ("ON", "OFF", "TO")
    ]

    groups = compact._v4_branch_group_roles(records, case_id="case-1", target_count=20)

    assert len(groups) == 2
    assert {key[3] for key in groups} == {"Mode-A", "Mode-B"}
    assert all(roles == {"ON", "OFF", "TO"} for roles in groups.values())
    with pytest.raises(ValueError, match="lacks required roles"):
        compact._v4_branch_group_roles(
            [row for row in records if not (row["mode"] == "Mode-A" and row["role"] == "ON")],
            case_id="case-1", target_count=20,
        )
    with pytest.raises(ValueError, match="duplicate V4 Production role"):
        compact._v4_branch_group_roles(
            records + [{**records[0], "branch_name": "another-ON"}],
            case_id="case-1", target_count=20,
        )
    h0_records = [
        {
            **next(row for row in records if row["role"] == role),
            "mode": mode,
            "branch_name": f"H0_{mode}_{role}",
        }
        for mode in ("Mode-A", "Mode-B") for role in ("ON", "OFF")
    ]
    h0_groups = compact._v4_branch_group_roles(
        h0_records, case_id="case-1", target_count=0,
    )
    assert all(roles == {"ON", "OFF"} for roles in h0_groups.values())


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


def _write_v4_archive_case(
    case_root: Path, *, omit_detection_period: int | None = None,
    target_count: int = 20, roles: tuple[str, ...] = ("ON", "OFF", "TO"),
) -> None:
    response_root = case_root / "pipeline" / "cases" / "run" / "r22_v4_output_evidence"
    response_root.mkdir(parents=True)
    outputs = ({
        "target_complex_response.csv": "case_id,condition,method_id,mode,role,target_id,csi_after_real,csi_after_imag\nv4-case,C0,C0,not_applicable,TO,T1,1,2\n",
        "target_roi_manifest.csv": "period_id,target_id,row_start,row_stop_exclusive\n0,T1,1,6\n",
        "adaptive_role_difference_diagnostic.csv": "method_id,relative_l2\nC0,0.5\n",
        "truth_mapping_audit.json": json.dumps({"status": "passed", "roles_compared": ["A0_ON", "A1_ON", "A0_TO", "A1_TO"], "identity_row_count": 1}),
    } if target_count > 0 else {
        "target_complex_response.csv": "case_id,condition,method_id,mode,role,target_id,csi_after_real,csi_after_imag\n",
        "target_roi_manifest.csv": "period_id,target_id,row_start,row_stop_exclusive\n",
        "adaptive_role_difference_diagnostic.csv": "method_id,relative_l2\n",
        "truth_mapping_audit.json": json.dumps({"status": "NOT_APPLICABLE_NO_TARGETS", "identity_row_count": 0}),
    })
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
            "status": "EVALUABLE" if target_count > 0 else "NOT_APPLICABLE_NO_TARGETS",
            "extractor_identity": {"sha256": "a" * 64},
            "target_count_design": target_count,
            "period_count": 2,
            "target_truth_expected": target_count > 0,
            "truth_source": {"exists": True} if target_count > 0 else None,
            "period_processing_states": [],
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
        "scripts/phase_r/r22_v4_case_schema.py",
        "scripts/phase_r/r22_compact_archive.py",
        "scripts/phase_r/r22_experiment_matrix.py",
        "scripts/phase_r/r22_sharded_archive.py",
        "scripts/phase_r/run_r12_physics_matched_delay.py",
        "scripts/phase_r/run_r21_cuda_pilot.py",
        "scripts/phase_r/r21_task_evaluator.py",
        "scripts/phase_r/r21_historical_task_review.py",
        "scripts/run_delay_stage1_formal.py",
        "scripts/run_production_hierarchical_calibration_pilot.py",
        "scripts/run_track_manager_e2e.py",
    )
    pipeline_case = case_root / "pipeline" / "cases" / "run"
    pipeline_case.mkdir(parents=True, exist_ok=True)
    truth_sources = {}
    if target_count > 0:
        truth_row = (
            "period_id,target_id,beam_id,pulse_id,range_m,af_total_truth_hz,visible,target_state\n"
            "0,T1,50,0,100.0,1.0,true,active\n"
        )
        for role in ("A0_ON", "A1_ON", "A0_TO", "A1_TO"):
            truth_path = pipeline_case / "scenes" / role / "truth" / "truth_targets_by_beam.csv"
            truth_path.parent.mkdir(parents=True, exist_ok=True)
            truth_path.write_text(truth_row, encoding="utf-8")
            truth_sources[role] = {
                "path": truth_path.relative_to(pipeline_case).as_posix(),
                "exists": True,
                "bytes": truth_path.stat().st_size,
                "sha256": hashlib.sha256(truth_path.read_bytes()).hexdigest(),
            }
        scenario_sources = {}
        for role in ("A0_ON", "A1_ON", "A0_TO", "A1_TO"):
            scenario_path = pipeline_case / "scenes" / "scenarios" / f"{role}.json"
            scenario_path.parent.mkdir(parents=True, exist_ok=True)
            scenario_path.write_text(json.dumps({
                "random": {"random_seed": 1933001},
                "stage1_scene_identity": {"seed": 1933001},
            }), encoding="utf-8")
            scenario_sources[role] = {
                "path": scenario_path.relative_to(pipeline_case).as_posix(),
                "exists": True, "seed": 1933001,
                "bytes": scenario_path.stat().st_size,
                "sha256": hashlib.sha256(scenario_path.read_bytes()).hexdigest(),
            }
        (response_root / "truth_mapping_audit.json").write_text(json.dumps({
            "status": "passed",
            "roles_compared": ["A0_ON", "A1_ON", "A0_TO", "A1_TO"],
            "identity_row_count": 1,
            "case_seed": 1933001,
            "seeds_by_role": {role: 1933001 for role in truth_sources},
            "sources": truth_sources,
            "scenario_sources": scenario_sources,
        }), encoding="utf-8")
        outputs["truth_mapping_audit.json"] = (response_root / "truth_mapping_audit.json").read_text(encoding="utf-8")
        file_records["truth_mapping_audit.json"] = {
            "bytes": (response_root / "truth_mapping_audit.json").stat().st_size,
            "sha256": hashlib.sha256((response_root / "truth_mapping_audit.json").read_bytes()).hexdigest(),
        }
    method_config_path = pipeline_case / "method_config_snapshot.json"
    method_config_path.write_text('{"fixture":true}\n', encoding="utf-8")
    source_xml_path = pipeline_case / "scenario.xml"
    source_xml_path.write_text("<runtime><source>fixture</source></runtime>\n", encoding="utf-8")
    method_config = {
        "path": method_config_path.name,
        "bytes": method_config_path.stat().st_size,
        "sha256": hashlib.sha256(method_config_path.read_bytes()).hexdigest(),
    }
    source_xml_identity = {
        "path": source_xml_path.name,
        "bytes": source_xml_path.stat().st_size,
        "sha256": hashlib.sha256(source_xml_path.read_bytes()).hexdigest(),
    }
    (case_root / "r22_run_manifest.json").write_text(json.dumps({
        "case_id": "v4-case",
        "design": {"seed": 1933001, "target_count": target_count, "period_count": 2},
        "method_config": method_config,
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
    method_branch_inventory = []
    for role in roles:
        branch_name = f"C0_{role}"
        branch_root = pipeline_case / "production" / branch_name
        result_root = branch_root / "result"
        debug_root = branch_root / "track_debug_runs"
        geometry_root = result_root / "debug"
        result_root.mkdir(parents=True)
        debug_root.mkdir(parents=True)
        geometry_root.mkdir(parents=True)
        xml_fields = {
            "research_calibration_enable": "false",
            "research_calibration_method": "production_current",
            "research_calibration_min_support": "8",
            "research_calibration_range_band_bins": "0",
            "research_calibration_robust_phase_threshold_rad": "0.35",
            "research_calibration_mode": "not_applicable",
        }
        xml_text = "<runtime>" + "".join(
            f"<{name}>{value}</{name}>" for name, value in xml_fields.items()
        ) + "</runtime>\n"
        xml_path = branch_root / "production.xml"
        xml_path.write_text(xml_text, encoding="utf-8")
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
            "case_id": "v4-case",
            "branch_name": branch_name,
            "method_id": "C0",
            "condition": "C0",
            "mode": "not_applicable",
            "role": role,
            "status": "passed",
            "production_returncode": 0,
            "period_count_expected": 2,
            "production_xml": str(branch_root / "production.xml"),
            "production_log": str(branch_root / "gmt_pipe_core.log"),
            "result_dir": str(result_root),
            "track_debug_dir": str(debug_root),
            "xml_overrides": xml_fields,
            "runtime_xml_identity": {
                "path": xml_path.relative_to(pipeline_case).as_posix(),
                "bytes": xml_path.stat().st_size,
                "sha256": hashlib.sha256(xml_path.read_bytes()).hexdigest(),
            },
            "source_configuration_identity": {
                "method_config": method_config,
                "source_xml": source_xml_identity,
                "branch_overrides_sha256": hashlib.sha256(
                    json.dumps(xml_fields, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
            },
        }
        (branch_root / "branch_manifest.json").write_text(
            json.dumps(branch_record), encoding="utf-8",
        )
        method_branch_inventory.append(branch_record)
    dense_csi = pipeline_case / "production" / "C0_ON" / "result" / "csi_metrics" / "tap" / "csi.npy"
    dense_csi.parent.mkdir(parents=True, exist_ok=True)
    dense_csi.write_bytes(b"reproducible synthetic CSI array")
    (pipeline_case / "case_manifest.json").write_text(json.dumps({
        "case_id": "v4-case", "design": {"seed": 1933001, "target_count": target_count, "period_count": 2},
        "scene_identity": {"seed": 1933001}, "case": {"seed": 1933001},
        "method_branches": method_branch_inventory, "raw_cleanup": {"removed_bytes": 0},
    }), encoding="utf-8")
    response_manifest_path = response_root / "target_complex_response_manifest.json"
    response_manifest = json.loads(response_manifest_path.read_text(encoding="utf-8"))
    truth_audit_path = response_root / "truth_mapping_audit.json"
    response_manifest["files"]["truth_mapping_audit.json"] = {
        "bytes": truth_audit_path.stat().st_size,
        "sha256": hashlib.sha256(truth_audit_path.read_bytes()).hexdigest(),
    }
    response_manifest["period_processing_states"] = ([{
        "tap_arrays": {"csi_after_real_path": {
            "path": dense_csi.relative_to(pipeline_case).as_posix(),
            "exists": True, "bytes": dense_csi.stat().st_size,
            "sha256": hashlib.sha256(dense_csi.read_bytes()).hexdigest(),
        }},
    }] if target_count > 0 else [])
    response_manifest_path.write_text(json.dumps(response_manifest), encoding="utf-8")


def test_v4_archive_resolves_pipeline_local_case_id_through_scene_identity(tmp_path: Path):
    raw_root = tmp_path / "raw"
    case_root = raw_root / "engineering" / "case-v4-local-label"
    case_root.mkdir(parents=True)
    _write_v4_archive_case(case_root)

    pipeline_case_manifest_path = case_root / "pipeline" / "cases" / "run" / "case_manifest.json"
    pipeline_manifest = json.loads(pipeline_case_manifest_path.read_text(encoding="utf-8"))
    pipeline_manifest["case_id"] = "run"
    pipeline_manifest["scene_identity"]["case_id"] = "v4-case"
    pipeline_manifest["case"]["scene_identity_override"] = {"case_id": "v4-case"}
    pipeline_case_manifest_path.write_text(json.dumps(pipeline_manifest), encoding="utf-8")

    result = matrix.archive_case_directory(
        case_root,
        tmp_path / "archives" / "case-v4-local-label.zip",
        raw_root=raw_root,
        archive_profile="r22_v4_output_complete",
        require_complete_output=True,
        remove_source=False,
    )

    verification = matrix.verify_case_archive(
        result["archive_path"],
        expected_sha256=result["archive_sha256"],
        expected_manifest_sha256=result["archive_manifest_sha256"],
        archive_profile="r22_v4_output_complete",
        require_complete_output=True,
    )
    assert verification["crc_verified"] is True
    assert case_root.is_dir()


def test_v4_archive_rejects_pipeline_scene_identity_conflicting_with_run_id(tmp_path: Path):
    raw_root = tmp_path / "raw"
    case_root = raw_root / "engineering" / "case-v4-conflicting-scene-id"
    case_root.mkdir(parents=True)
    _write_v4_archive_case(case_root)

    pipeline_case_manifest_path = case_root / "pipeline" / "cases" / "run" / "case_manifest.json"
    pipeline_manifest = json.loads(pipeline_case_manifest_path.read_text(encoding="utf-8"))
    pipeline_manifest["scene_identity"]["case_id"] = "another-run"
    pipeline_manifest["case"]["scene_identity_override"] = {"case_id": "v4-case"}
    pipeline_case_manifest_path.write_text(json.dumps(pipeline_manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="pipeline scene case identity"):
        matrix.archive_case_directory(
            case_root,
            tmp_path / "archives" / "case-v4-conflicting-scene-id.zip",
            raw_root=raw_root,
            archive_profile="r22_v4_output_complete",
            require_complete_output=True,
            remove_source=False,
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
        excluded = {item["path"]: item for item in manifest["excluded_files"]}
        dense_path = "pipeline/cases/run/production/C0_ON/result/csi_metrics/tap/csi.npy"
        assert dense_path not in names
        assert excluded[dense_path]["sha256"] == hashlib.sha256(
            b"reproducible synthetic CSI array"
        ).hexdigest()


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


def test_v4_archive_accepts_h0_without_truth_or_to_and_keeps_unproven_arrays(tmp_path: Path):
    raw_root = tmp_path / "raw"
    case_root = raw_root / "h0" / "case-v4-h0"
    case_root.mkdir(parents=True)
    _write_v4_archive_case(case_root, target_count=0, roles=("ON", "OFF"))
    raw_binary = case_root / "pipeline" / "cases" / "run" / "inputs" / "period.bin"
    raw_binary.parent.mkdir(parents=True)
    raw_binary.write_bytes(b"raw input retained until archive verification")
    raw_array = case_root / "pipeline" / "cases" / "run" / "production" / "C0_ON" / "result" / "csi.npy"
    raw_array.write_bytes(b"reproducible map")
    archive_path = tmp_path / "archives" / "case-v4-h0.zip"

    result = matrix.archive_case_directory(
        case_root, archive_path, raw_root=raw_root, remove_source=False,
        archive_profile="r22_v4_output_complete", require_complete_output=True,
    )

    assert case_root.is_dir()
    assert raw_binary.is_file() and raw_array.is_file()
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("ARCHIVE_MANIFEST.json"))
        assert "pipeline/cases/run/production/C0_ON/result/detection_results_GMTI01.csv" in names
        assert "pipeline/cases/run/production/C0_ON/track_debug_runs/track_output_payloads.csv" in names
        assert "pipeline/cases/run/r22_v4_output_evidence/target_complex_response.csv" in names
        assert "pipeline/cases/run/production/C0_ON/result/csi.npy" in names
        assert "pipeline/cases/run/inputs/period.bin" in names
        excluded = {item["path"]: item for item in manifest["excluded_files"]}
        assert "pipeline/cases/run/inputs/period.bin" not in excluded
        assert "pipeline/cases/run/production/C0_ON/result/csi.npy" not in excluded
    assert result["raw_case_removed"] is False


def test_v4_archive_accepts_declared_h0_to_failure_but_keeps_its_failure_audit(tmp_path: Path):
    raw_root = tmp_path / "raw"
    case_root = raw_root / "h0" / "case-v4-h0-with-to-gap"
    case_root.mkdir(parents=True)
    _write_v4_archive_case(
        case_root, target_count=0, roles=("ON", "OFF", "TO"),
    )
    pipeline_case = case_root / "pipeline" / "cases" / "run"
    to_root = pipeline_case / "production" / "C0_TO"
    case_manifest_path = pipeline_case / "case_manifest.json"
    case_manifest = json.loads(case_manifest_path.read_text(encoding="utf-8"))
    to_record = next(row for row in case_manifest["method_branches"] if row["role"] == "TO")
    to_record.update({
        "status": "NOT_APPLICABLE_NO_TARGETS",
        "production_returncode": 1,
        "v4_execution_class": "internal_auxiliary_branch",
        "v4_target_response_not_applicable": True,
        "v4_target_response_not_applicable_reason": (
            "H0 target_count=0; no target-only response source"
        ),
        "v4_observed_production_status": "failed",
        "production_path_gate": {"status": "failed"},
    })
    (to_root / "branch_manifest.json").write_text(json.dumps(to_record), encoding="utf-8")
    case_manifest_path.write_text(json.dumps(case_manifest), encoding="utf-8")
    for path in list((to_root / "result").rglob("*")):
        if path.is_file():
            path.unlink()
    for path in sorted((to_root / "track_debug_runs").rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    (to_root / "track_debug_runs").rmdir()
    archive_path = tmp_path / "archives" / "case-v4-h0-with-to-gap.zip"

    result = matrix.archive_case_directory(
        case_root, archive_path, raw_root=raw_root, remove_source=False,
        archive_profile="r22_v4_output_complete", require_complete_output=True,
    )

    assert case_root.is_dir()
    assert result["raw_case_removed"] is False
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        assert "pipeline/cases/run/production/C0_TO/branch_manifest.json" in names
        assert "pipeline/cases/run/production/C0_TO/production.xml" in names
        assert "pipeline/cases/run/production/C0_TO/gmt_pipe_core.log" in names
        assert not any("C0_TO/result/detection_results_" in name for name in names)
        record = json.loads(archive.read("pipeline/cases/run/production/C0_TO/branch_manifest.json"))
        assert record["v4_observed_production_status"] == "failed"
        assert record["production_returncode"] == 1


def test_v4_archive_preserves_internal_input_symlinks_without_copying_payloads(tmp_path: Path):
    raw_root = tmp_path / "raw"
    case_root = raw_root / "engineering" / "case-v4-links"
    source = case_root / "inputs" / "r22_v4_corrections" / "M1" / "period_0000.bin"
    alias = case_root / "inputs" / "production" / "M1" / "ON" / "period_0000.bin"
    source.parent.mkdir(parents=True)
    alias.parent.mkdir(parents=True)
    source.write_bytes(b"corrected production input bytes")
    alias.symlink_to(source.resolve())
    archive_path = tmp_path / "archives" / "case-v4-links.zip"

    result = matrix.archive_case_directory(
        case_root, archive_path, raw_root=raw_root, remove_source=False,
        archive_profile="r22_v4_output_complete", require_complete_output=False,
    )

    source_relative = source.relative_to(case_root).as_posix()
    alias_relative = alias.relative_to(case_root).as_posix()
    expected_link_text = posixpath.relpath(source_relative, posixpath.dirname(alias_relative))
    with zipfile.ZipFile(archive_path) as archive:
        link_info = archive.getinfo(alias_relative)
        assert stat.S_ISLNK(link_info.external_attr >> 16)
        assert archive.read(alias_relative).decode("utf-8") == expected_link_text
        manifest = json.loads(archive.read("ARCHIVE_MANIFEST.json"))
        link_record = next(row for row in manifest["symlink_entries"] if row["path"] == alias_relative)
        assert link_record["target_case_path"] == source_relative
        assert link_record["bytes"] == source.stat().st_size
        assert link_record["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
        assert alias_relative not in {row["path"] for row in manifest["included_files"]}
    verification = matrix.verify_case_archive(
        archive_path,
        expected_sha256=result["archive_sha256"],
        expected_manifest_sha256=result["archive_manifest_sha256"],
        archive_profile="r22_v4_output_complete",
    )
    assert verification["crc_verified"] is True
    assert verification["member_manifest_verified"] is True


def test_sharded_v4_archive_checkpoints_verified_members_and_releases_raw_data(tmp_path: Path):
    raw_root = tmp_path / "raw"
    case_root = raw_root / "engineering" / "case-sharded"
    first = case_root / "inputs" / "period_0000.bin"
    second = case_root / "production" / "C0_ON" / "result.csv"
    alias = case_root / "production_inputs" / "period_0000.bin"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    alias.parent.mkdir(parents=True)
    first.write_bytes(b"first-data")
    second.write_bytes(b"second-data")
    alias.symlink_to(first.resolve())
    archive_set = tmp_path / "archives" / "case-sharded.archive-set.json"

    result = sharded.archive_case_directory_sharded(
        case_root,
        archive_set,
        raw_root=raw_root,
        archive_profile="r22_v4_output_complete",
        require_complete_output=False,
        max_shard_uncompressed_bytes=6,
        remove_source=True,
    )

    assert result["status"] == "VERIFIED"
    assert result["shard_count"] == 4
    assert result["raw_case_removed"] is True
    assert not case_root.exists()
    verified = sharded.verify_sharded_case_archive(
        archive_set,
        expected_sha256=result["archive_sha256"],
        expected_manifest_sha256=result["archive_manifest_sha256"],
        archive_profile="r22_v4_output_complete",
        require_complete_output=False,
    )
    assert verified["member_manifest_verified"] is True
    assert verified["crc_verified"] is True
    manifest = json.loads(archive_set.read_text(encoding="utf-8"))
    archived = {
        member
        for shard in manifest["shards"]
        for member in shard["members"]
    }
    assert archived == {
        "ARCHIVE_MANIFEST.json",
        "inputs/period_0000.bin",
        "production/C0_ON/result.csv",
        "production_inputs/period_0000.bin",
    }


def test_sharded_archive_preserves_files_added_after_archive_inventory(tmp_path: Path, monkeypatch):
    raw_root = tmp_path / "raw"
    case_root = raw_root / "engineering" / "case-late-file"
    case_root.mkdir(parents=True)
    (case_root / "included.csv").write_text("verified\n", encoding="utf-8")
    archive_set = tmp_path / "archives" / "case-late-file.archive-set.json"
    real_verify = sharded.verify_sharded_case_archive

    def add_late_file(*args, **kwargs):
        result = real_verify(*args, **kwargs)
        (case_root / "late_user_note.txt").write_text("keep this\n", encoding="utf-8")
        return result

    monkeypatch.setattr(sharded, "verify_sharded_case_archive", add_late_file)
    result = sharded.archive_case_directory_sharded(
        case_root, archive_set, raw_root=raw_root,
        archive_profile="r22_v4_output_complete", require_complete_output=False,
        max_shard_uncompressed_bytes=1024, remove_source=True,
    )

    assert result["raw_case_removed"] is False
    assert (case_root / "late_user_note.txt").read_text(encoding="utf-8") == "keep this\n"
    receipt = json.loads(archive_set.read_text(encoding="utf-8"))["source_cleanup"]
    assert receipt["status"] == "UNINVENTORIED_OR_UNRECOGNIZED_FILES_PRESERVED"
    assert receipt["remaining_file_count"] == 1


def test_sharded_archive_refuses_to_remove_changed_excluded_file(tmp_path: Path, monkeypatch):
    raw_root = tmp_path / "raw"
    case_root = raw_root / "engineering" / "case-mutated-exclusion"
    case_root.mkdir(parents=True)
    (case_root / "included.csv").write_text("verified\n", encoding="utf-8")
    excluded = case_root / "per_case_plot.png"
    excluded.write_bytes(b"original plot")
    archive_set = tmp_path / "archives" / "case-mutated-exclusion.archive-set.json"
    real_verify = sharded.verify_sharded_case_archive

    def mutate_exclusion(*args, **kwargs):
        result = real_verify(*args, **kwargs)
        excluded.write_bytes(b"changed after inventory")
        return result

    monkeypatch.setattr(sharded, "verify_sharded_case_archive", mutate_exclusion)
    with pytest.raises(ValueError, match="excluded source file identity changed"):
        sharded.archive_case_directory_sharded(
            case_root, archive_set, raw_root=raw_root,
            archive_profile="r22_v4_output_complete", require_complete_output=False,
            max_shard_uncompressed_bytes=1024, remove_source=True,
        )

    assert excluded.read_bytes() == b"changed after inventory"


def test_sharded_v4_archive_resumes_after_a_later_shard_write_fails(tmp_path: Path, monkeypatch):
    raw_root = tmp_path / "raw"
    case_root = raw_root / "engineering" / "case-resume"
    case_root.mkdir(parents=True)
    (case_root / "a.bin").write_bytes(b"aaaa")
    (case_root / "b.bin").write_bytes(b"bbbb")
    archive_set = tmp_path / "archives" / "case-resume.archive-set.json"
    real_writer = sharded._write_sharded_archive_part
    calls = {"count": 0}

    def fail_on_second_part(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 3:
            raise OSError("injected failure before third shard publication")
        return real_writer(*args, **kwargs)

    monkeypatch.setattr(sharded, "_write_sharded_archive_part", fail_on_second_part)
    with pytest.raises(OSError, match="before third shard publication"):
        sharded.archive_case_directory_sharded(
            case_root,
            archive_set,
            raw_root=raw_root,
            archive_profile="r22_v4_output_complete",
            require_complete_output=False,
            max_shard_uncompressed_bytes=4,
            remove_source=True,
        )

    checkpoint = json.loads(archive_set.read_text(encoding="utf-8"))
    assert checkpoint["status"] == "ARCHIVING"
    assert checkpoint["shards"][0]["status"] == "VERIFIED"
    assert not (case_root / "a.bin").exists()
    assert (case_root / "b.bin").is_file()

    monkeypatch.setattr(sharded, "_write_sharded_archive_part", real_writer)
    resumed = sharded.archive_case_directory_sharded(
        case_root,
        archive_set,
        raw_root=raw_root,
        archive_profile="r22_v4_output_complete",
        require_complete_output=False,
        max_shard_uncompressed_bytes=4,
        remove_source=True,
    )
    assert resumed["status"] == "VERIFIED"
    assert resumed["resumed_shard_count"] == 2
    assert not case_root.exists()


def test_verified_sharded_archive_compacts_before_full_payload_cleanup(tmp_path: Path):
    raw_root = tmp_path / "raw"
    case_root = raw_root / "h0" / "case-compact"
    case_root.mkdir(parents=True)
    (case_root / "r22_run_manifest.json").write_text('{"phase":"h0"}\n', encoding="utf-8")
    (case_root / "evaluation").mkdir()
    evaluator = case_root / "evaluation" / "task_metrics.csv"
    evaluator.write_text("case_id,hits\ncase-compact,10\n", encoding="utf-8")
    archive_set = tmp_path / "archives" / "case-compact.archive-set.json"
    compact_path = tmp_path / "archives" / "case-compact.compact.zip"

    complete = sharded.archive_case_directory_sharded(
        case_root,
        archive_set,
        raw_root=raw_root,
        archive_profile="r22_v4_output_complete",
        require_complete_output=False,
        max_shard_uncompressed_bytes=32,
        remove_source=True,
    )
    compacted = sharded.compact_sharded_case_archive(
        archive_set, compact_path, cleanup_full_archive=True,
    )

    assert complete["crc_verified"] is True
    assert compacted["status"] == "VERIFIED"
    assert compact_path.is_file()
    assert not Path(str(archive_set) + ".shards").exists()
    receipt = sharded.verify_sharded_archive_receipt(
        archive_set,
        expected_sha256=compacted["archive_set_sha256"],
        expected_manifest_sha256=compacted["full_archive_manifest_sha256"],
        expected_compact_sha256=compacted["archive_sha256"],
    )
    assert receipt["historically_verified_full_archive"] is True
    assert receipt["full_archive_payload_present"] is False
    with zipfile.ZipFile(compact_path, "r") as archive:
        manifest = json.loads(archive.read("ARCHIVE_MANIFEST.json"))
        assert manifest["archive_profile"] == "r22_v4_size_pilot_compact"
        assert "evaluation/task_metrics.csv" in archive.namelist()


def test_compact_archive_cleanup_resumes_from_verified_shard_receipt(tmp_path: Path, monkeypatch):
    raw_root = tmp_path / "raw"
    case_root = raw_root / "h0" / "case-cleanup-resume"
    (case_root / "evaluation").mkdir(parents=True)
    (case_root / "r22_run_manifest.json").write_text('{"phase":"h0"}\n', encoding="utf-8")
    (case_root / "evaluation" / "task_metrics.csv").write_text(
        "case_id,hits\ncase-cleanup-resume,1\n", encoding="utf-8",
    )
    (case_root / "large-a.bin").write_bytes(b"a" * 400)
    (case_root / "large-b.bin").write_bytes(b"b" * 400)
    archive_set = tmp_path / "archives" / "case-cleanup-resume.archive-set.json"
    compact_path = tmp_path / "archives" / "case-cleanup-resume.compact.zip"
    sharded.archive_case_directory_sharded(
        case_root, archive_set, raw_root=raw_root,
        archive_profile="r22_v4_output_complete", require_complete_output=False,
        max_shard_uncompressed_bytes=300, remove_source=True,
    )

    original_unlink = Path.unlink

    def fail_on_second_shard(path, *args, **kwargs):
        if path.name == "part_0001.zip":
            raise OSError("injected interruption during full archive cleanup")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_on_second_shard)
    with pytest.raises(OSError, match="during full archive cleanup"):
        sharded.compact_sharded_case_archive(
            archive_set, compact_path, cleanup_full_archive=True,
        )
    checkpoint = json.loads(archive_set.read_text(encoding="utf-8"))
    assert checkpoint["status"] == "COMPACT_VERIFIED"
    assert checkpoint["full_archive_cleanup"]["status"] == "IN_PROGRESS"
    assert compact_path.is_file()

    monkeypatch.setattr(Path, "unlink", original_unlink)
    resumed = sharded.compact_sharded_case_archive(
        archive_set, compact_path, cleanup_full_archive=True,
    )
    assert resumed["status"] == "VERIFIED"
    receipt = sharded.verify_sharded_archive_receipt(
        archive_set,
        expected_sha256=resumed["archive_set_sha256"],
        expected_manifest_sha256=resumed["full_archive_manifest_sha256"],
        expected_compact_sha256=resumed["archive_sha256"],
    )
    assert receipt["historically_verified_full_archive"] is True


def test_compact_cleanup_recovers_if_first_shard_was_unlinked_before_checkpoint_update(
    tmp_path: Path, monkeypatch,
):
    raw_root = tmp_path / "raw"
    case_root = raw_root / "h0" / "case-first-shard-interruption"
    (case_root / "evaluation").mkdir(parents=True)
    (case_root / "r22_run_manifest.json").write_text('{"phase":"h0"}\n', encoding="utf-8")
    (case_root / "evaluation" / "task_metrics.csv").write_text("case_id,hits\nx,1\n")
    (case_root / "large.bin").write_bytes(b"x" * 800)
    archive_set = tmp_path / "archives" / "first-shard.archive-set.json"
    compact_path = tmp_path / "archives" / "first-shard.compact.zip"
    sharded.archive_case_directory_sharded(
        case_root, archive_set, raw_root=raw_root,
        archive_profile="r22_v4_output_complete", require_complete_output=False,
        max_shard_uncompressed_bytes=300, remove_source=True,
    )

    original_unlink = Path.unlink
    injected = {"done": False}

    def unlink_then_interrupt(path, *args, **kwargs):
        result = original_unlink(path, *args, **kwargs)
        if path.name == "part_0000.zip" and not injected["done"]:
            injected["done"] = True
            raise OSError("simulated process loss after first shard unlink")
        return result

    monkeypatch.setattr(Path, "unlink", unlink_then_interrupt)
    with pytest.raises(OSError, match="after first shard unlink"):
        sharded.compact_sharded_case_archive(archive_set, compact_path, cleanup_full_archive=True)
    interrupted = json.loads(archive_set.read_text(encoding="utf-8"))
    assert interrupted["full_archive_cleanup"]["status"] == "IN_PROGRESS"
    assert not (archive_set.parent / "first-shard.archive-set.json.shards/part_0000.zip").exists()

    monkeypatch.setattr(Path, "unlink", original_unlink)
    resumed = sharded.compact_sharded_case_archive(archive_set, compact_path, cleanup_full_archive=True)
    assert resumed["status"] == "VERIFIED"
    assert json.loads(archive_set.read_text(encoding="utf-8"))["status"] == "COMPACTED_CLEANED"


def test_compact_cleanup_removes_only_owned_orphan_shard_temporaries(tmp_path: Path):
    raw_root = tmp_path / "raw"
    case_root = raw_root / "h0" / "case-orphan-shard-temp"
    (case_root / "evaluation").mkdir(parents=True)
    (case_root / "r22_run_manifest.json").write_text('{"phase":"h0"}\n', encoding="utf-8")
    (case_root / "evaluation" / "task_metrics.csv").write_text("case_id,hits\nx,1\n")
    archive_set = tmp_path / "archives" / "orphan-temp.archive-set.json"
    compact_path = tmp_path / "archives" / "orphan-temp.compact.zip"
    sharded.archive_case_directory_sharded(
        case_root, archive_set, raw_root=raw_root,
        archive_profile="r22_v4_output_complete", require_complete_output=False,
        max_shard_uncompressed_bytes=1024, remove_source=True,
    )
    shard_dir = Path(str(archive_set) + ".shards")
    owned_temp = shard_dir / ".part_0000.zip.interrupted.tmp"
    unrelated = shard_dir / "unrelated.tmp"
    owned_temp.write_bytes(b"partial zip")
    unrelated.write_bytes(b"keep")

    with pytest.raises(OSError):
        sharded.compact_sharded_case_archive(archive_set, compact_path, cleanup_full_archive=True)
    assert not owned_temp.exists()
    assert unrelated.read_bytes() == b"keep"

    unrelated.unlink()
    resumed = sharded.compact_sharded_case_archive(archive_set, compact_path, cleanup_full_archive=True)
    assert resumed["status"] == "VERIFIED"


def test_existing_compact_collision_requires_member_hashes_not_only_crc_and_manifest(
    tmp_path: Path,
):
    raw_root = tmp_path / "raw"
    case_root = raw_root / "h0" / "case-tampered-compact-collision"
    (case_root / "evaluation").mkdir(parents=True)
    (case_root / "r22_run_manifest.json").write_text('{"phase":"h0"}\n', encoding="utf-8")
    (case_root / "evaluation" / "task_metrics.csv").write_text("case_id,hits\nx,1\n")
    archive_set = tmp_path / "archives" / "tampered-collision.archive-set.json"
    compact_path = tmp_path / "archives" / "tampered-collision.compact.zip"
    sharded.archive_case_directory_sharded(
        case_root, archive_set, raw_root=raw_root,
        archive_profile="r22_v4_output_complete", require_complete_output=False,
        max_shard_uncompressed_bytes=1024, remove_source=True,
    )
    sharded.compact_sharded_case_archive(archive_set, compact_path, cleanup_full_archive=False)
    receipt = json.loads(archive_set.read_text(encoding="utf-8"))
    receipt["status"] = "FULL_VERIFIED"
    receipt.pop("compact_archive", None)
    receipt.pop("compact_verified_at_utc", None)
    receipt.pop("full_archive_cleanup", None)
    archive_set.write_text(json.dumps(receipt), encoding="utf-8")

    with zipfile.ZipFile(compact_path, "r") as source:
        members = {name: source.read(name) for name in source.namelist()}
    members["evaluation/task_metrics.csv"] += b"tampered\n"
    with zipfile.ZipFile(compact_path, "w", compression=zipfile.ZIP_DEFLATED) as changed:
        for name, payload in members.items():
            changed.writestr(name, payload)

    with pytest.raises(ValueError, match="compact archive member failed SHA-256 check"):
        sharded.compact_sharded_case_archive(archive_set, compact_path, cleanup_full_archive=True)


def test_v4_archive_hashes_shared_symlink_target_once_for_link_identity(tmp_path: Path, monkeypatch):
    raw_root = tmp_path / "raw"
    case_root = raw_root / "engineering" / "case-shared-link-target"
    source = case_root / "inputs" / "source.bin"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"same large production input")
    aliases = [case_root / "production" / f"branch_{index}" / "input.bin" for index in range(3)]
    for alias in aliases:
        alias.parent.mkdir(parents=True)
        alias.symlink_to(source.resolve())

    original_hash = compact._sha256_file
    hashed_paths: list[Path] = []

    def capture_hash(path: Path) -> str:
        hashed_paths.append(Path(path).resolve())
        return original_hash(path)

    monkeypatch.setattr(compact, "_sha256_file", capture_hash)
    compact.archive_case_directory(
        case_root,
        tmp_path / "archives" / "case-shared-link-target.zip",
        raw_root=raw_root,
        remove_source=False,
        archive_profile="r22_v4_output_complete",
        require_complete_output=False,
    )

    assert hashed_paths.count(source.resolve()) == 2  # file member plus one cached link identity


def test_legacy_archive_rejects_symlinks_and_v4_archive_rejects_external_targets(tmp_path: Path):
    raw_root = tmp_path / "raw"
    case_root = raw_root / "engineering" / "case-links"
    case_root.mkdir(parents=True)
    external = tmp_path / "outside.bin"
    external.write_bytes(b"outside")
    (case_root / "outside-link.bin").symlink_to(external)

    with pytest.raises(ValueError, match="symlink"):
        matrix.archive_case_directory(
            case_root, tmp_path / "legacy.zip", raw_root=raw_root,
            archive_profile="legacy", remove_source=False,
        )
    with pytest.raises(ValueError, match="outside the case root"):
        matrix.archive_case_directory(
            case_root, tmp_path / "v4.zip", raw_root=raw_root,
            archive_profile="r22_v4_output_complete", require_complete_output=False,
            remove_source=False,
        )


def test_v4_confirmatory_resume_requires_a_passing_case_type_schema(tmp_path: Path):
    archive_path = tmp_path / "confirmatory.zip"
    manifest = {
        "archive_profile": "r22_v4_output_complete",
        "case_phase": "confirmatory",
        "required_v4_members": [],
        "included_files": [],
        "excluded_files": [],
        "symlink_entries": [],
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ARCHIVE_MANIFEST.json", manifest_bytes)
    archive_sha = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()

    with pytest.raises(ValueError, match="case-type schema"):
        compact.verify_case_archive(
            archive_path,
            expected_sha256=archive_sha,
            expected_manifest_sha256=manifest_sha,
            archive_profile="r22_v4_output_complete",
            require_complete_output=True,
        )


def test_sharded_v4_archive_manifest_binds_confirmatory_case_schema_identity(
    tmp_path: Path, monkeypatch,
):
    case_root = tmp_path / "raw" / "case-1"
    case_root.mkdir(parents=True)
    (case_root / "r22_run_manifest.json").write_text(
        json.dumps({"phase": "confirmatory"}), encoding="utf-8",
    )
    identity = {
        "case_id": "case-1", "phase": "target_present",
        "target_count": 20, "delay_error_ns": 4.0,
    }
    monkeypatch.setattr(
        compact, "_v4_required_members", lambda _root, _files: ["r22_run_manifest.json"],
    )
    monkeypatch.setattr(
        compact, "_v4_verified_dense_csi_exclusions",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(compact, "_compact_case_manifest_extract", lambda *_args: None)
    monkeypatch.setattr(compact, "_exclusion_reason", lambda *_args: None)
    monkeypatch.setattr(
        compact, "_confirmatory_case_schema",
        lambda *_args: {"status": "PASS", "required_members": ["r22_run_manifest.json"]},
    )

    prepared, _ = sharded._make_archive_manifest(
        case_root,
        archive_profile="r22_v4_output_complete",
        require_complete_output=True,
        case_schema_identity=identity,
    )

    manifest = prepared["archive_manifest"]
    assert manifest["case_schema_identity"] == identity
    assert manifest["case_schema"]["status"] == "PASS"
    assert "r22_run_manifest.json" in manifest["required_v4_members"]
