from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from scripts.phase_r.r22_v4_evidence import (
    extract_target_complex_response,
    validate_target_complex_response,
)


def _write_tap(branch_root: Path, role: str, value: complex) -> None:
    tap_dir = branch_root / "result" / "csi_metrics" / "run"
    tap_dir.mkdir(parents=True)
    shape = (4, 6)
    real_path = tap_dir / "csi_real.npy"
    imag_path = tap_dir / "csi_imag.npy"
    before_path = tap_dir / "before.npy"
    fa_path = tap_dir / "fa.npy"
    range_path = tap_dir / "range.npy"
    np.save(real_path, np.full(shape, value.real, dtype=np.float32))
    np.save(imag_path, np.full(shape, value.imag, dtype=np.float32))
    np.save(before_path, np.full(shape, abs(value) ** 2 + 1.0, dtype=np.float32))
    np.save(fa_path, np.asarray([-3.0, -2.0, -1.0, 0.0], dtype=np.float32))
    np.save(range_path, np.asarray([100.0, 200.0, 300.0, 400.0, 500.0, 600.0], dtype=np.float32))
    manifest = tap_dir / "csi_roi_manifest.csv"
    fields = (
        "period_id", "rows", "cols", "alignment_mode", "after_definition",
        "p38_k_rad_per_hz", "p38_b_rad", "before_power_path",
        "csi_after_real_path", "csi_after_imag_path", "fa_axis_path", "range_axis_path",
    )
    with manifest.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerow({
            "period_id": 0,
            "rows": shape[0],
            "cols": shape[1],
            "alignment_mode": "row_complex_ls",
            "after_definition": "test CSI output",
            "p38_k_rad_per_hz": 0.0,
            "p38_b_rad": 0.0,
            "before_power_path": before_path.name,
            "csi_after_real_path": real_path.name,
            "csi_after_imag_path": imag_path.name,
            "fa_axis_path": fa_path.name,
            "range_axis_path": range_path.name,
        })


def test_v4_extractor_saves_all_target_cells_roles_and_overlap_state(tmp_path: Path):
    root = tmp_path / "case"
    truth_root = tmp_path / "scene" / "truth"
    truth_root.mkdir(parents=True)
    truth_path = truth_root / "truth_targets_by_beam.csv"
    with truth_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("period_id", "pulse_id", "target_id", "visible", "range_m", "af_total_truth_hz"),
        )
        writer.writeheader()
        writer.writerows([
            {"period_id": 0, "pulse_id": 0, "target_id": "T1", "visible": 1,
             "range_m": 200.0, "af_total_truth_hz": -2.0},
            {"period_id": 0, "pulse_id": 0, "target_id": "T2", "visible": 1,
             "range_m": 300.0, "af_total_truth_hz": -1.0},
        ])

    branch_records = []
    for role, value in (("ON", 4 + 1j), ("OFF", 1 + 0j), ("TO", 2 + 2j)):
        branch_name = f"C0_{role}"
        branch_root = root / "production" / branch_name
        branch_root.mkdir(parents=True)
        (branch_root / "production.xml").write_text("<runtime/>\n", encoding="utf-8")
        (branch_root / "branch_manifest.json").write_text("{}\n", encoding="utf-8")
        result_dir = branch_root / "result"
        result_dir.mkdir()
        _write_tap(branch_root, role, value)
        branch_records.append({
            "branch_name": branch_name,
            "condition": "C0",
            "role": role,
            "status": "passed",
            "production_returncode": 0,
            "production_xml": str(branch_root / "production.xml"),
            "result_dir": str(result_dir),
            "correction_source": "test",
            "applied_delay_ns": 0.0,
            "truth_used_in_estimator": False,
        })

    result = extract_target_complex_response(
        root,
        simulation={"A0_ON": {"output_dir": str(truth_root.parent)}},
        branch_records=branch_records,
        target_count=2,
        period_count=1,
    )

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    with Path(result["response_path"]).open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert result["status"] == "EVALUABLE"
    assert len(rows) == 108
    assert {row["role"] for row in rows} == {"ON", "OFF", "TO"}
    assert {row["target_id"] for row in rows} == {"T1", "T2"}
    assert any(int(row["roi_overlap_count"]) > 1 for row in rows)
    to_row = next(row for row in rows if row["role"] == "TO")
    assert complex(float(to_row["csi_after_real"]), float(to_row["csi_after_imag"])) == 2 + 2j
    assert manifest["response_semantics"]["target_only_complex_response_source"].startswith("TO role")
    assert manifest["response_semantics"]["no_linear_closure_assumption"] is True
    assert len(manifest["period_processing_states"]) == 3
    assert all(
        state["branch_manifest"]["exists"]
        for state in manifest["period_processing_states"]
    )
    assert len(manifest["extractor_identity"]["sha256"]) == 64
    assert manifest["adaptive_role_diagnostic_row_count"] == 2
    assert Path(result["roi_manifest_path"]).is_file()
    verification = validate_target_complex_response(root)
    assert verification["response_row_count"] == 108
    assert verification["period_processing_state_count"] == 3


def test_v4_target_free_extraction_allows_absent_truth_file(tmp_path: Path):
    root = tmp_path / "case"
    truth_root = tmp_path / "scene" / "truth"
    truth_root.mkdir(parents=True)
    branch_records = []
    for role, value in (("ON", 1 + 0j), ("OFF", 1 + 0j), ("TO", 0 + 0j)):
        branch_name = f"C0_{role}"
        branch_root = root / "production" / branch_name
        branch_root.mkdir(parents=True)
        (branch_root / "production.xml").write_text("<runtime/>\n", encoding="utf-8")
        (branch_root / "branch_manifest.json").write_text("{}\n", encoding="utf-8")
        result_dir = branch_root / "result"
        result_dir.mkdir()
        _write_tap(branch_root, role, value)
        branch_records.append({
            "branch_name": branch_name,
            "condition": "C0",
            "role": role,
            "status": "passed",
            "production_returncode": 0,
            "production_xml": str(branch_root / "production.xml"),
            "result_dir": str(result_dir),
            "correction_source": "test",
            "applied_delay_ns": 0.0,
            "truth_used_in_estimator": False,
        })

    result = extract_target_complex_response(
        root,
        simulation={"A0_ON": {"output_dir": str(truth_root.parent)}},
        branch_records=branch_records,
        target_count=0,
        period_count=1,
    )

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    with Path(result["response_path"]).open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert result["status"] == "NOT_APPLICABLE_NO_TARGETS"
    assert result["response_row_count"] == 0
    assert rows == []
    assert manifest["target_truth_expected"] is False
    assert manifest["truth_source"]["exists"] is False
    assert manifest["status_labels"]["EVALUABLE"] is True
    assert manifest["status_labels"]["PERFORMANCE_VERIFIED"] is False
    assert validate_target_complex_response(root)["response_row_count"] == 0
