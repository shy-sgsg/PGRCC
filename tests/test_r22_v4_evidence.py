from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from scripts.phase_r.r22_v4_evidence import (
    extract_target_complex_response,
    validate_causal_prefix_lineage,
    validate_target_complex_response,
)


def _causal_lineage_fixture(case_root: Path, *, periods: int = 2):
    methods = ("M1_CAUSAL_PREFIX", "HUBER_D3_CAUSAL_PREFIX")
    identities = []
    for period in range(periods):
        path = case_root / "inputs" / "estimator_on" / f"period_{period:04d}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"on-{period}".encode())
        identities.append({
            "period_id": period, "path": str(path.resolve()), "exists": True,
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })

    estimators = {}
    branches = []
    for method_index, method in enumerate(methods):
        estimates = []
        applied_by_period = []
        for period in range(periods):
            applied = 0.5 + method_index * 0.1 + period * 0.01
            applied_by_period.append(applied)
            estimates.append({
                "method_id": method,
                "period_id": period,
                "prefix_period_ids": list(range(period + 1)),
                "prefix_input_identities": identities[:period + 1],
                "estimator_input_role": "A1_Current_unknown_error_ON_raw_C1_C2_only",
                "estimator_status": "AVAILABLE",
                "estimate_ns": applied,
                "applied_correction_ns": applied,
                "fallback_status": None,
                "truth_used_in_estimator": False,
            })
        estimators[method] = estimates

        for role in ("ON", "OFF", "TO"):
            corrections = []
            for period, applied in enumerate(applied_by_period):
                source = case_root / "inputs" / role / f"period_{period:04d}.bin"
                if role == "ON":
                    source = Path(identities[period]["path"])
                source.parent.mkdir(parents=True, exist_ok=True)
                if not source.exists():
                    source.write_bytes(f"{role}-{period}".encode())
                destination = case_root / "corrected" / method / role / f"period_{period:04d}.bin"
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(f"corrected-{method}-{role}-{period}".encode())
                source_identity = {
                    "path": str(source.resolve()), "exists": True,
                    "bytes": source.stat().st_size,
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
                corrected_identity = {
                    "path": str(destination.resolve()), "exists": True,
                    "bytes": destination.stat().st_size,
                    "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                }
                operator = {
                    "status": "passed",
                    "correction_model": "inverse_stage2_zero_extended_linear_interpolator",
                    "input_path": source_identity["path"],
                    "output_path": corrected_identity["path"],
                    "delta_tau_ns": applied,
                    "input_bytes": source_identity["bytes"],
                    "output_bytes": corrected_identity["bytes"],
                    "packets_rewritten": 1,
                }
                corrections.append({
                    "period_id": period,
                    "estimator_status": "AVAILABLE",
                    "estimate_ns": applied,
                    "applied_correction_ns": applied,
                    "fallback_status": None,
                    "source_path": source_identity["path"],
                    "destination_path": corrected_identity["path"],
                    "source_identity": source_identity,
                    "corrected_identity": corrected_identity,
                    "operator_audit": operator,
                })
            branches.append({
                "branch_name": f"{method}_{role}",
                "method_id": method,
                "role": role,
                "correction_source": "same_case_ON_raw_C1_C2_causal_prefix",
                "estimator_method_id": method,
                "estimator_input_role": "A1_Current_unknown_error_ON_raw_C1_C2_only",
                "truth_used_in_estimator": False,
                "truth_used_to_apply_correction": False,
                "correction_operator": "estimate_channel_delay.rewrite_float32_protocol_delay",
                "period_corrections": corrections,
            })
    return estimators, branches


def test_causal_prefix_lineage_accepts_period_ordered_estimate_and_corrections(tmp_path: Path):
    estimators, branches = _causal_lineage_fixture(tmp_path)

    result = validate_causal_prefix_lineage(
        estimators, branches, period_count=2, delay_ns=4.0, case_root=tmp_path,
    )

    assert result["status"] == "PASSED"
    assert result["period_count"] == 2
    assert set(result["methods"]) == {"M1_CAUSAL_PREFIX", "HUBER_D3_CAUSAL_PREFIX"}


def test_causal_prefix_lineage_rejects_correction_that_differs_from_estimator(tmp_path: Path):
    estimators, branches = _causal_lineage_fixture(tmp_path)
    branches[0]["period_corrections"][0]["applied_correction_ns"] += 0.25

    with pytest.raises(ValueError, match="causal correction differs from estimator"):
        validate_causal_prefix_lineage(
            estimators, branches, period_count=2, delay_ns=4.0, case_root=tmp_path,
        )


def _write_tap(
    branch_root: Path, role: str, value: complex, *, period_count: int = 1,
) -> None:
    tap_dir = branch_root / "result" / "csi_metrics" / "run"
    tap_dir.mkdir(parents=True, exist_ok=True)
    shape = (4, 6)
    fa_path = tap_dir / "fa.npy"
    range_path = tap_dir / "range.npy"
    np.save(fa_path, np.asarray([-3.0, -2.0, -1.0, 0.0], dtype=np.float32))
    np.save(range_path, np.asarray([100.0, 200.0, 300.0, 400.0, 500.0, 600.0], dtype=np.float32))
    manifest = tap_dir / "csi_roi_manifest.csv"
    fields = (
        "period_id", "rows", "cols", "alignment_mode", "after_definition",
        "p38_k_rad_per_hz", "p38_b_rad", "before_power_path",
        "csi_after_real_path", "csi_after_imag_path", "fa_axis_path", "range_axis_path",
    )
    rows = []
    for period_id in range(period_count):
        period_dir = tap_dir / f"period_{period_id:04d}"
        period_dir.mkdir()
        period_value = value + period_id * (0.125 + 0.0625j)
        real_path = period_dir / "csi_real.npy"
        imag_path = period_dir / "csi_imag.npy"
        before_path = period_dir / "before.npy"
        np.save(real_path, np.full(shape, period_value.real, dtype=np.float32))
        np.save(imag_path, np.full(shape, period_value.imag, dtype=np.float32))
        np.save(before_path, np.full(shape, abs(period_value) ** 2 + 1.0, dtype=np.float32))
        rows.append({
            "period_id": period_id,
            "rows": shape[0],
            "cols": shape[1],
            "alignment_mode": "row_complex_ls",
            "after_definition": "test CSI output",
            "p38_k_rad_per_hz": 0.0,
            "p38_b_rad": 0.0,
            "before_power_path": f"period_{period_id:04d}/{before_path.name}",
            "csi_after_real_path": f"period_{period_id:04d}/{real_path.name}",
            "csi_after_imag_path": f"period_{period_id:04d}/{imag_path.name}",
            "fa_axis_path": fa_path.name,
            "range_axis_path": range_path.name,
        })
    with manifest.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _register_branch(
    case_root: Path, branch_root: Path, *, branch_name: str,
    method_id: str, condition: str, mode: str, role: str,
    case_id: str = "case-1",
) -> dict[str, object]:
    source_xml = case_root / "source_config.xml"
    source_xml.parent.mkdir(parents=True, exist_ok=True)
    if not source_xml.exists():
        source_xml.write_text("<runtime><source>fixture</source></runtime>\n", encoding="utf-8")
    method_config_path = case_root / "method_config.json"
    if not method_config_path.exists():
        method_config_path.write_text('{"fixture":true}\n', encoding="utf-8")
    overrides = {"research_calibration_mode": mode}
    xml_path = branch_root / "production.xml"
    xml_path.write_text(
        f"<runtime><research_calibration_mode>{mode}</research_calibration_mode></runtime>\n",
        encoding="utf-8",
    )
    method_config = {
        "path": method_config_path.relative_to(case_root).as_posix(),
        "bytes": method_config_path.stat().st_size,
        "sha256": hashlib.sha256(method_config_path.read_bytes()).hexdigest(),
    }
    source_identity = {
        "path": source_xml.relative_to(case_root).as_posix(),
        "bytes": source_xml.stat().st_size,
        "sha256": hashlib.sha256(source_xml.read_bytes()).hexdigest(),
    }
    source_configuration_identity = {
        "method_config": method_config,
        "source_xml": source_identity,
        "branch_overrides_sha256": hashlib.sha256(
            json.dumps(overrides, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    runtime_xml_identity = {
        "path": xml_path.relative_to(case_root).as_posix(),
        "bytes": xml_path.stat().st_size,
        "sha256": hashlib.sha256(xml_path.read_bytes()).hexdigest(),
    }
    manifest = {
        "case_id": case_id, "method_id": method_id, "condition": condition,
        "mode": mode, "role": role, "branch_name": branch_name,
        "production_xml": "production.xml", "xml_overrides": overrides,
        "runtime_xml_identity": runtime_xml_identity,
        "source_configuration_identity": source_configuration_identity,
    }
    (branch_root / "branch_manifest.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8",
    )
    return {
        **manifest, "status": "passed", "production_returncode": 0,
        "production_xml": str(xml_path), "runtime_xml_identity": runtime_xml_identity,
        "result_dir": str(branch_root / "result"),
    }


def _simulation_with_truth(
    case_root: Path,
    output_dirs: dict[str, Path],
    *,
    seed: int = 1933001,
    case_id: str = "synthetic-v4-case",
):
    case_root.mkdir(parents=True, exist_ok=True)
    case_manifest_path = case_root / "case_manifest.json"
    if not case_manifest_path.exists():
        case_manifest_path.write_text(json.dumps({
            "case_id": case_id, "case": {"seed": seed},
            "scene_identity": {"seed": seed},
        }), encoding="utf-8")
    result = {}
    for role, output_dir in output_dirs.items():
        copied_output_dir = case_root / "scenes" / role
        copied_truth_dir = copied_output_dir / "truth"
        copied_truth_dir.mkdir(parents=True, exist_ok=True)
        source_truth = Path(output_dir) / "truth" / "truth_targets_by_beam.csv"
        if source_truth.is_file():
            shutil.copy2(source_truth, copied_truth_dir / source_truth.name)
        scenario_path = case_root / "scenes" / "scenarios" / f"{role}.json"
        scenario_path.parent.mkdir(parents=True, exist_ok=True)
        scenario_path.write_text(json.dumps({
            "random": {"random_seed": seed},
            "stage1_scene_identity": {"seed": seed},
        }), encoding="utf-8")
        result[role] = {
            "output_dir": str(copied_output_dir), "scenario": str(scenario_path),
            "scenario_sha256": hashlib.sha256(scenario_path.read_bytes()).hexdigest(),
        }
    return result


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
        result_dir = branch_root / "result"
        result_dir.mkdir()
        _write_tap(branch_root, role, value)
        branch_records.append(_register_branch(
            root, branch_root, branch_name=branch_name, method_id="C0",
            condition="C0", mode="not_applicable", role=role,
        ))

    simulation = _simulation_with_truth(root, {
        name: truth_root.parent for name in ("A0_ON", "A1_ON", "A0_TO", "A1_TO")
    }, case_id="case-1")

    result = extract_target_complex_response(
        root,
        simulation=simulation,
        branch_records=branch_records,
        target_count=2,
        period_count=1,
    )

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    with Path(result["response_path"]).open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert result["status"] == "EVALUABLE"
    assert manifest["schema_version"] == 3
    assert manifest["causal_prefix_lineage_status"] == "NOT_APPLICABLE_NO_CAUSAL_CORRECTION_METHODS"
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
    assert verification["causal_prefix_lineage_status"] == "NOT_APPLICABLE_NO_CAUSAL_CORRECTION_METHODS"


def test_v4_target_free_extraction_allows_absent_truth_file(tmp_path: Path):
    root = tmp_path / "case"
    truth_root = tmp_path / "scene" / "truth"
    truth_root.mkdir(parents=True)
    branch_records = []
    for role, value in (("ON", 1 + 0j), ("OFF", 1 + 0j), ("TO", 0 + 0j)):
        branch_name = f"C0_{role}"
        branch_root = root / "production" / branch_name
        branch_root.mkdir(parents=True)
        result_dir = branch_root / "result"
        result_dir.mkdir()
        _write_tap(branch_root, role, value)
        branch_records.append(_register_branch(
            root, branch_root, branch_name=branch_name, method_id="C0",
            condition="C0", mode="not_applicable", role=role,
        ))

    result = extract_target_complex_response(
        root,
        simulation={},
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
    assert manifest["truth_source"] is None
    assert manifest["status_labels"]["EVALUABLE"] is True
    assert manifest["status_labels"]["PERFORMANCE_VERIFIED"] is False
    assert validate_target_complex_response(root)["response_row_count"] == 0


def test_v4_extractor_keeps_mode_a_and_mode_b_as_distinct_branch_groups(tmp_path: Path):
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
        writer.writerow({"period_id": 0, "pulse_id": 0, "target_id": "T1", "visible": 1,
                         "range_m": 200.0, "af_total_truth_hz": -2.0})

    records = []
    for mode in ("Mode-A", "Mode-B"):
        for role, value in (("ON", 4 + 1j), ("OFF", 1 + 0j), ("TO", 2 + 2j)):
            branch_name = f"C0_{mode.replace('-', '').lower()}_{role}"
            branch_root = root / "production" / branch_name
            branch_root.mkdir(parents=True)
            result_dir = branch_root / "result"
            result_dir.mkdir()
            _write_tap(branch_root, role, value)
            records.append(_register_branch(
                root, branch_root, case_id="case-mode-pair", branch_name=branch_name,
                method_id="C0", condition="C0", mode=mode, role=role,
            ))

    result = extract_target_complex_response(
        root, simulation=_simulation_with_truth(root, {
            name: truth_root.parent for name in ("A0_ON", "A1_ON", "A0_TO", "A1_TO")
        }, case_id="case-mode-pair"),
        branch_records=records, target_count=1, period_count=1,
    )
    with Path(result["response_path"]).open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert {row["mode"] for row in rows} == {"Mode-A", "Mode-B"}
    assert {row["branch_id"] for row in rows} == {
        "C0_modea_ON", "C0_modea_OFF", "C0_modea_TO",
        "C0_modeb_ON", "C0_modeb_OFF", "C0_modeb_TO",
    }


def test_v4_target_free_response_needs_no_truth_or_to_branch(tmp_path: Path):
    root = tmp_path / "case"
    branch_root = root / "production" / "C0_ON"
    branch_root.mkdir(parents=True)
    record = _register_branch(
        root, branch_root, case_id="h0-case", branch_name="C0_ON",
        method_id="C0", condition="C0", mode="not_applicable", role="ON",
    )

    result = extract_target_complex_response(
        root, simulation={}, branch_records=[record], target_count=0, period_count=3,
    )

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert result["status"] == "NOT_APPLICABLE_NO_TARGETS"
    assert result["response_row_count"] == 0
    assert manifest["truth_source"] is None
    assert manifest["production_branches"][0]["role"] == "ON"
    assert manifest["period_processing_states"] == []
    assert validate_target_complex_response(root)["response_row_count"] == 0


def test_v4_target_free_mode_groups_ignore_only_marked_non_applicable_to_failures(
    tmp_path: Path,
):
    root = tmp_path / "h0-case"
    records = []
    for mode in ("Mode-A", "Mode-B"):
        for role in ("ON", "OFF"):
            branch_name = f"A0_{mode.lower().replace('-', '')}_{role}"
            branch_root = root / "production" / branch_name
            branch_root.mkdir(parents=True)
            records.append(_register_branch(
                root, branch_root, case_id="h0-case", branch_name=branch_name,
                method_id="A0", condition="A0", mode=mode, role=role,
            ))
        records.append({
            "case_id": "h0-case",
            "branch_name": f"A0_{mode.lower().replace('-', '')}_TO",
            "method_id": "A0",
            "condition": "A0",
            "mode": mode,
            "role": "TO",
            "status": "NOT_APPLICABLE_NO_TARGETS",
            "v4_observed_production_status": "failed",
            "production_returncode": 1,
            "v4_execution_class": "internal_auxiliary_branch",
            "v4_target_response_not_applicable": True,
            "v4_target_response_not_applicable_reason": (
                "H0 target_count=0; no target-only response source"
            ),
        })

    result = extract_target_complex_response(
        root, simulation={}, branch_records=records, target_count=0, period_count=1,
    )
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))

    assert result["status"] == "NOT_APPLICABLE_NO_TARGETS"
    assert result["response_row_count"] == 0
    assert len(manifest["production_branches"]) == 4
    assert {row["branch_name"] for row in manifest["not_applicable_branches"]} == {
        "A0_modea_TO", "A0_modeb_TO",
    }
    assert {row["observed_production_status"] for row in manifest["not_applicable_branches"]} == {
        "failed",
    }
    assert {tuple(row["required_roles"]) for row in manifest["branch_groups"]} == {
        ("OFF", "ON"),
    }


def test_v4_truth_mapping_conflict_fails_before_creating_response_evidence(tmp_path: Path):
    root = tmp_path / "case"
    simulation = {}
    for name in ("A0_ON", "A1_ON", "A0_TO", "A1_TO"):
        truth_root = tmp_path / name / "truth"
        truth_root.mkdir(parents=True)
        truth_path = truth_root / "truth_targets_by_beam.csv"
        truth_row = {"period_id": 0, "pulse_id": 0, "beam_id": 50,
                     "target_id": "T1", "visible": 1, "target_state": "active",
                     "range_m": 200.0, "af_total_truth_hz": -2.0}
        if name == "A1_TO":
            truth_row["range_m"] = 201.0
        with truth_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=tuple(truth_row))
            writer.writeheader()
            writer.writerow(truth_row)
        simulation[name] = {"output_dir": str(truth_root.parent)}

    simulation = _simulation_with_truth(
        root, {name: Path(record["output_dir"]) for name, record in simulation.items()},
        case_id="truth-conflict",
    )

    with pytest.raises(ValueError, match="truth mapping differs"):
        extract_target_complex_response(
            root, simulation=simulation, branch_records=[], target_count=1, period_count=1,
        )

    assert not (root / "r22_v4_output_evidence").exists()


def test_v4_multimethod_multimode_two_target_three_period_response_is_complete(tmp_path: Path):
    root = tmp_path / "case"
    truth_root = tmp_path / "scene" / "truth"
    truth_root.mkdir(parents=True)
    truth_path = truth_root / "truth_targets_by_beam.csv"
    truth_fields = (
        "period_id", "pulse_id", "target_id", "beam_id", "visible",
        "target_state", "range_m", "af_total_truth_hz",
    )
    with truth_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=truth_fields)
        writer.writeheader()
        for period in range(3):
            writer.writerows([
                {"period_id": period, "pulse_id": 0, "target_id": "T1", "beam_id": 50,
                 "visible": 1, "target_state": "active", "range_m": 200.0,
                 "af_total_truth_hz": -2.0},
                {"period_id": period, "pulse_id": 0, "target_id": "T2", "beam_id": 50,
                 "visible": 1, "target_state": "active", "range_m": 300.0,
                 "af_total_truth_hz": -1.0},
            ])

    records = []
    groups = [
        ("C0", "C0_reference", "not_applicable", 1.0),
        ("C4", "C4_causal", "Mode-A", 10.0),
        ("C4", "C4_causal", "Mode-B", 20.0),
    ]
    for method_id, condition, mode, base in groups:
        for role_index, (role, role_value) in enumerate((
            ("ON", 4 + 1j), ("OFF", 1 + 0j), ("TO", 2 + 2j),
        )):
            branch_name = f"{method_id}_{mode.replace('-', '').lower()}_{role}"
            branch_root = root / "production" / branch_name
            branch_root.mkdir(parents=True)
            (branch_root / "result").mkdir()
            value = complex(base, 0.0) + role_value + role_index * 0.25j
            _write_tap(branch_root, role, value, period_count=3)
            records.append(_register_branch(
                root, branch_root, branch_name=branch_name,
                method_id=method_id, condition=condition, mode=mode, role=role,
                case_id="case-multimethod",
            ))

    result = extract_target_complex_response(
        root,
        simulation=_simulation_with_truth(root, {
            name: truth_root.parent for name in ("A0_ON", "A1_ON", "A0_TO", "A1_TO")
        }, case_id="case-multimethod"),
        branch_records=records,
        target_count=2,
        period_count=3,
        case_id="case-multimethod",
    )
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    with Path(result["response_path"]).open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))

    assert result["response_row_count"] == len(rows) == 972
    assert manifest["target_period_roi_count"] == 6
    assert manifest["response_evidence_branch_count"] == 9
    assert len(manifest["period_processing_states"]) == 27
    assert manifest["adaptive_role_diagnostic_row_count"] == 18
    assert {row["period_id"] for row in rows} == {"0", "1", "2"}
    assert {row["target_id"] for row in rows} == {"T1", "T2"}
    assert {(row["method_id"], row["condition"], row["mode"]) for row in rows} == {
        ("C0", "C0_reference", "not_applicable"),
        ("C4", "C4_causal", "Mode-A"),
        ("C4", "C4_causal", "Mode-B"),
    }
    assert validate_target_complex_response(root)["period_processing_state_count"] == 27
