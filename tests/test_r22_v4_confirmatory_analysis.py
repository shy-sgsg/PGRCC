from __future__ import annotations

import copy
import csv
import gzip
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from scripts.phase_r import r22_v4_confirmatory_executor as executor
from scripts.phase_r import analyze_r22_v4_confirmatory_matrix as analysis


ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / (
    "docs/research/phase_r/R22/config/"
    "r22_v4_causal_output_complete_confirmatory_draft_20261113.json"
)
TEMPLATE = ROOT / "simulator/scenarios/beam50_one_targets_continuous.run.json"
METHOD_CONFIG = ROOT / "docs/research/phase_r/R22/config/r22_method_config_v2_range_roi.json"


def _registry(tmp_path: Path) -> dict:
    design = json.loads(DESIGN.read_text(encoding="utf-8"))
    design["status"] = "FROZEN"
    design["primary_comparison"].update({
        "position_scheme": "A",
        "position_mde_m": 3.0,
        "minimum_common_support_per_speed": 1,
        "detection_coverage_noninferiority_margin_pp": 1.0,
        "track_protection_policy": "DESCRIPTIVE_ONLY",
        "track_coverage_noninferiority_margin_pp": None,
    })
    design["h0"]["seed_event_rate_upper_limit"] = 0.1
    design["h0"]["false_confirmed_track_rate_upper_limit"] = 0.1
    design["statistics"]["coverage_interval_method_selection_status"] = "USER_APPROVED_AND_FROZEN"
    frozen_path = tmp_path / "fixture_design.json"
    frozen_path.write_text(json.dumps(design), encoding="utf-8")
    registry = executor.build_confirmatory_registry(
        design_path=frozen_path,
        scenario_template=TEMPLATE,
        method_config=METHOD_CONFIG,
        output_root=tmp_path / "matrix-output",
    )
    target_case = next(case for case in registry["cases"]
                       if case["phase"] == "target_present" and case["speed_mps"] == 2.5)
    h0_case = next(case for case in registry["cases"]
                   if case["phase"] == "h0" and case["delay_error_ns"] == 0.0)
    wanted = {
        (target_case["phase"], target_case["seed"], target_case["speed_mps"], delay)
        for delay in (0.0, 4.0)
    } | {
        (h0_case["phase"], h0_case["seed"], h0_case["speed_mps"], delay)
        for delay in (0.0, 4.0)
    }
    registry["cases"] = [case for case in registry["cases"] if (
        case["phase"], case["seed"], case["speed_mps"], case["delay_error_ns"]
    ) in wanted]
    registry["case_count"] = len(registry["cases"])
    registry["batch_count"] = 2
    registry["registry_sha256"] = executor.registry_digest(registry)
    return registry


def _csv_bytes(rows: list[dict], fields: list[str]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _write_case_archive(
    case: dict, tmp_path: Path, *, missing_target_association: bool = False,
    missing_h0_track_output: bool = False, missing_target_status: bool = False,
    wrong_association_seed: bool = False,
) -> dict:
    case_id = case["case_id"]
    case_root = f"pipeline/cases/{case_id}"
    phase = case["phase"]
    method_ids = ["A0"] if phase == "h0" and case["delay_error_ns"] == 0.0 else (
        ["C0", "FIXED_D3", "HUBER_D3_CAUSAL_PREFIX", "M1_CAUSAL_PREFIX", "PK_KNOWN"]
        if phase == "h0" else ["M1_CAUSAL_PREFIX", "R22_R9_Fixed_D3"]
    )
    method_alias = {"FIXED_D3": "R22_R9_Fixed_D3", "PK_KNOWN": "PK"}
    branch_rows = []
    members: dict[str, bytes] = {
        "r22_run_manifest.json": json.dumps({
            "case_id": case_id,
            "phase": "confirmatory" if phase == "target_present" else "h0",
            "design": {"seed": case["seed"], "target_count": case["target_count"]},
        }).encode(),
        f"{case_root}/case_manifest.json": json.dumps({"case_id": case_id}).encode(),
    }
    for method in method_ids:
        output_method = method_alias.get(method, method)
        branch_rows.append({
            "case_id": case_id,
            "method_id": output_method,
            "role": "ON",
            "status": "passed",
        })
        members[f"{case_root}/production/{output_method}_ON/branch_manifest.json"] = json.dumps(
            branch_rows[-1],
        ).encode()

    if phase == "target_present":
        truth_fields = ["period_id", "target_id", "beam_id", "visible"]
        truth_rows = [
            {"period_id": period, "target_id": "T1", "beam_id": case["beam"], "visible": "true"}
            for period in range(case["period_count"])
        ]
        for role in ("A0_ON", "A1_ON", "A0_TO", "A1_TO"):
            members[f"{case_root}/scenes/{role}/truth/truth_targets_by_beam.csv"] = _csv_bytes(
                truth_rows, truth_fields,
            )
        target_methods = ["M1_CAUSAL_PREFIX", "R22_R9_Fixed_D3"]
        task_rows = []
        assoc_rows = []
        truth_level_rows = []
        track_rows = []
        for method in target_methods:
            task_rows.append({
                "case_id": case_id, "seed": case["seed"], "method_id": method,
                "mode": "not_applicable", "role": "ON", "status": "EVALUABLE",
            })
            track_rows.append({
                "case_id": case_id, "seed": case["seed"], "method_id": method,
                "mode": "not_applicable", "role": "ON", "status": "EVALUABLE",
                "target_confirmed_unique_period_count": 8 if method.startswith("M1") else 4,
            })
            for period in range(case["period_count"]):
                if not (missing_target_status and method == "M1_CAUSAL_PREFIX"
                        and period == case["period_count"] - 1):
                    truth_level_rows.append({
                        "case_id": case_id, "seed": case["seed"], "method_id": method,
                        "mode": "not_applicable", "role": "ON", "period_id": period,
                        "beam_id": case["beam"], "target_id": "T1", "status": "EVALUABLE",
                    })
                if not (missing_target_association and method == "M1_CAUSAL_PREFIX"
                        and period == case["period_count"] - 1):
                    assoc_rows.append({
                        "case_id": case_id,
                        "seed": case["seed"] + int(wrong_association_seed and method == "M1_CAUSAL_PREFIX"
                                                     and period == 0),
                        "method_id": method,
                        "mode": "not_applicable", "role": "ON", "period_id": period,
                        "beam_id": case["beam"], "target_id": "T1", "matched": "true",
                        "position_error_m": "2.0" if method == "M1_CAUSAL_PREFIX" else "4.0",
                    })
        members["evaluation/task_metrics.csv"] = _csv_bytes(
            task_rows, ["case_id", "seed", "method_id", "mode", "role", "status"],
        )
        members["evaluation/target_associations.csv"] = _csv_bytes(
            assoc_rows, ["case_id", "seed", "method_id", "mode", "role", "period_id", "beam_id", "target_id", "matched", "position_error_m"],
        )
        members["evaluation/target_level_metrics.csv"] = _csv_bytes(
            truth_level_rows, ["case_id", "seed", "method_id", "mode", "role", "period_id", "beam_id", "target_id", "status"],
        )
        members["evaluation/track_level_metrics.csv"] = _csv_bytes(
            track_rows, ["case_id", "seed", "method_id", "mode", "role", "status", "target_confirmed_unique_period_count"],
        )
        members["evaluation/parameter_metrics.csv"] = _csv_bytes([], ["case_id", "status"])
        members["evaluation/h0_task_metrics.csv"] = _csv_bytes([], ["case_id", "status"])
        members["evaluation/h0_false_alarm_metrics.csv"] = _csv_bytes([], ["case_id", "status"])
    else:
        members["evaluation/task_metrics.csv"] = _csv_bytes([], ["case_id", "seed", "method_id", "mode", "role", "status"])
        members["evaluation/target_associations.csv"] = _csv_bytes([], ["case_id", "seed", "method_id", "mode", "role", "period_id", "beam_id", "target_id", "matched", "position_error_m"])
        members["evaluation/target_level_metrics.csv"] = _csv_bytes([], ["case_id", "seed", "method_id", "mode", "role", "period_id", "beam_id", "target_id", "status"])
        members["evaluation/track_level_metrics.csv"] = _csv_bytes([], ["case_id", "seed", "method_id", "mode", "role", "status", "target_confirmed_unique_period_count"])
        members["evaluation/parameter_metrics.csv"] = _csv_bytes([], ["case_id", "status"])
        h0_rows = []
        false_alarm_rows = []
        for method in method_ids:
            output_method = method_alias.get(method, method)
            count = 1 if method in {"M1_CAUSAL_PREFIX", "R22_R9_Fixed_D3"} and case["delay_error_ns"] == 4.0 else 0
            missing_track = missing_h0_track_output and method == "M1_CAUSAL_PREFIX" and case["delay_error_ns"] == 4.0
            h0_rows.append({
                "case_id": case_id, "seed": case["seed"], "method_id": output_method,
                "mode": "not_applicable", "role": "ON",
                "status": "EVALUABLE_DESCRIPTIVE_H0_SEED", "valid_period_count": case["period_count"],
                "h0_event_count": count,
                "false_detection_count": count,
                "false_cluster_count": count,
                "false_confirmed_track_count": "" if missing_track else 0,
                "track_status": "NOT_EVALUABLE_MISSING_OUTPUT" if missing_track else "EVALUABLE",
            })
            false_alarm_rows.append({
                "case_id": case_id, "seed": case["seed"], "method_id": output_method,
                "cut_band_mode": "union_full_band", "role": "ON", "status": "DESCRIPTIVE_ONLY_NOT_STATISTICAL_PFA",
                "valid_cut_count": 1000, "observed_cut_hit_count": 0,
            })
        members["evaluation/h0_task_metrics.csv"] = _csv_bytes(
            h0_rows, ["case_id", "seed", "method_id", "mode", "role", "status", "valid_period_count", "h0_event_count", "false_detection_count", "false_cluster_count", "false_confirmed_track_count", "track_status"],
        )
        members["evaluation/h0_false_alarm_metrics.csv"] = _csv_bytes(
            false_alarm_rows, ["case_id", "seed", "method_id", "cut_band_mode", "role", "status", "valid_cut_count", "observed_cut_hit_count"],
        )

    identity = {
        "case_id": case_id,
        "phase": phase,
        "target_count": case["target_count"],
        "delay_error_ns": case["delay_error_ns"],
    }
    required = sorted(members)
    included = [{
        "path": name,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    } for name, payload in sorted(members.items())]
    schema = {
        "status": "PASS",
        "phase_family": phase,
        "required_members": required,
    }
    manifest = {
        "schema_version": 1,
        "archive_profile": "r22_v4_output_complete",
        "case_phase": "confirmatory" if phase == "target_present" else "h0",
        "case_schema_identity": identity,
        "case_schema_identity_sha256": hashlib.sha256(json.dumps(
            identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest(),
        "case_schema": schema,
        "required_v4_members": required,
        "included_files": included,
        "symlink_entries": [],
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    archive_path = tmp_path / f"{case_id}.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as stream:
        for name, payload in members.items():
            stream.writestr(name, payload)
        stream.writestr("ARCHIVE_MANIFEST.json", manifest_bytes)
    archive_sha = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    return {
        "archive_path": str(archive_path),
        "archive_sha256": archive_sha,
        "archive_manifest_sha256": manifest_sha,
        "archive_profile": "r22_v4_output_complete",
        "case_id": case_id,
    }


def _write_sharded_receipt_and_compact(case: dict, tmp_path: Path) -> dict:
    legacy = _write_case_archive(case, tmp_path)
    with zipfile.ZipFile(legacy["archive_path"], "r") as source:
        full_manifest_bytes = source.read("ARCHIVE_MANIFEST.json")
        full_manifest = json.loads(full_manifest_bytes)
        payloads = {
            item["path"]: source.read(item["path"])
            for item in full_manifest["included_files"]
        }
    set_identity = hashlib.sha256(b"simulated sharded archive-set identity").hexdigest()
    compact_manifest = {
        "schema_version": 1,
        "archive_profile": executor.COMPACT_PROFILE,
        "source_archive_format": executor.sharded.ARCHIVE_SET_FORMAT,
        "source_archive_profile": executor.ARCHIVE_PROFILE,
        "source_archive_set_identity_sha256": set_identity,
        "source_archive_manifest_sha256": legacy["archive_manifest_sha256"],
        "required_v4_members": full_manifest["required_v4_members"],
        "included_files": full_manifest["included_files"],
        "excluded_files": [],
        "symlink_entries": [],
        "included_bytes": sum(item["bytes"] for item in full_manifest["included_files"]),
        "excluded_bytes": 0,
        "symlink_target_bytes": 0,
    }
    compact_manifest_bytes = (
        json.dumps(compact_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    compact_path = tmp_path / f"{case['case_id']}.compact.zip"
    with zipfile.ZipFile(compact_path, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, payload in payloads.items():
            target.writestr(name, payload)
        target.writestr("ARCHIVE_MANIFEST.json", compact_manifest_bytes)
    compact_sha = hashlib.sha256(compact_path.read_bytes()).hexdigest()
    compact_manifest_sha = hashlib.sha256(compact_manifest_bytes).hexdigest()
    receipt = {
        "status": "COMPACTED_CLEANED",
        "archive_profile": executor.ARCHIVE_PROFILE,
        "archive_manifest_sha256": legacy["archive_manifest_sha256"],
        "full_archive_verified_at_utc": "simulated-fixture",
        "archive_manifest": full_manifest,
        "archive_set_identity_sha256": set_identity,
        "archive_bytes": 123,
        "full_archive_cleanup": {
            "status": "REMOVED_AFTER_COMPACT_VERIFICATION", "shards": [],
        },
        "compact_archive": {
            "archive_path": str(compact_path.resolve()),
            "archive_sha256": compact_sha,
            "archive_manifest_sha256": compact_manifest_sha,
            "archive_profile": executor.COMPACT_PROFILE,
        },
    }
    receipt_path = tmp_path / f"{case['case_id']}.archive-set.json.gz"
    with receipt_path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
        zipped.write((json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n").encode())
    return {
        "case_id": case["case_id"],
        "archive_path": str(receipt_path.resolve()),
        "archive_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
        "archive_manifest_sha256": legacy["archive_manifest_sha256"],
        "archive_profile": executor.ARCHIVE_PROFILE,
        "case_schema_identity_sha256": full_manifest["case_schema_identity_sha256"],
        "compact_archive_path": str(compact_path.resolve()),
        "compact_archive_sha256": compact_sha,
        "compact_archive_manifest_sha256": compact_manifest_sha,
        "compact_archive_profile": executor.COMPACT_PROFILE,
    }


def _write_registry_checkpoint(
    tmp_path: Path, *, missing: bool = False, missing_target_status: bool = False,
    wrong_association_seed: bool = False,
) -> tuple[Path, Path]:
    registry = _registry(tmp_path)
    states = {}
    for case in registry["cases"]:
        archive = _write_case_archive(
            case, tmp_path,
            missing_target_association=(missing and case["phase"] == "target_present"
                                        and case["delay_error_ns"] == 4.0),
            missing_h0_track_output=(missing and case["phase"] == "h0"
                                     and case["delay_error_ns"] == 4.0),
            missing_target_status=(missing_target_status and case["phase"] == "target_present"
                                   and case["delay_error_ns"] == 4.0),
            wrong_association_seed=(wrong_association_seed and case["phase"] == "target_present"
                                    and case["delay_error_ns"] == 4.0),
        )
        states[case["case_id"]] = {
            "case_identity_sha256": executor.canonical_sha256(case),
            "status": "VERIFIED",
            "archive": archive,
        }
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text(json.dumps({
        "registry_sha256": registry["registry_sha256"],
        "cases": states,
    }), encoding="utf-8")
    return registry_path, checkpoint_path


def test_confirmatory_analyzer_reads_verified_archives_and_keeps_endpoints_seed_level(tmp_path):
    registry, checkpoint = _write_registry_checkpoint(tmp_path)

    result = analysis.analyze_confirmatory_matrix(
        registry_path=registry, checkpoint_path=checkpoint,
        output_dir=tmp_path / "analysis",
    )

    assert result["analysis_status"] == "EVALUABLE_ARCHIVE_SET"
    assert result["case_count_archive_verified"] == 4
    assert result["h0"]["primary_endpoint"] == executor.H0_PRIMARY_ENDPOINT
    target = result["target_present"]
    assert target["seed_level"]["seed_count_planned"] == 1
    assert target["seed_level"]["seed_count_evaluable"] == 1
    assert target["seed_level"]["seed_results"][0]["status"] == "EVALUABLE"
    assert target["position_interval"]["status"] == "NOT_EVALUABLE_INCOMPLETE_OR_INSUFFICIENT_SEED_SET"
    assert target["detection_coverage_m1_minus_fixed_d3_pp"]["status"] == "NOT_EVALUABLE_INSUFFICIENT_SEEDS"
    assert (tmp_path / "analysis/r22_v4_confirmatory_analysis.json").is_file()


def test_case_reader_resolves_verified_sharded_receipt_to_compact_evaluator_archive(
    tmp_path, monkeypatch,
):
    registry = _registry(tmp_path)
    case = next(row for row in registry["cases"] if row["phase"] == "target_present")
    archive_record = _write_sharded_receipt_and_compact(case, tmp_path)
    monkeypatch.setattr(
        executor.sharded, "verify_sharded_archive_receipt",
        lambda *_args, **_kwargs: {"status": "PASS"},
    )
    state = {"status": "VERIFIED", "archive": archive_record}

    verified = analysis._read_verified_case(case, state, archive_root=None)

    assert verified["archive_path"] == archive_record["compact_archive_path"]
    assert verified["archive_manifest_sha256"] == archive_record["archive_manifest_sha256"]
    assert len(verified["truth_keys"]) == case["period_count"]
    assert len(verified["tables"]["associations"]) > 0


def test_confirmatory_analyzer_fails_closed_on_missing_opportunity_and_track_rows(tmp_path):
    registry, checkpoint = _write_registry_checkpoint(tmp_path, missing=True)

    result = analysis.analyze_confirmatory_matrix(
        registry_path=registry, checkpoint_path=checkpoint,
        output_dir=tmp_path / "analysis",
    )

    target_seed = result["target_present"]["seed_level"]["seed_results"][0]
    assert result["analysis_status"] == "INCONCLUSIVE_REQUIRED_EVALUATION_DATA"
    assert target_seed["status"] == "NOT_EVALUABLE_MISSING_OPPORTUNITY_RECORDS"
    h0_m1 = next(row for row in result["h0"]["methods"]
                 if row["method_id"] == "M1_CAUSAL_PREFIX")
    assert h0_m1["seed_any_event"]["event_seed_count"] == 1
    assert h0_m1["seed_any_event"]["one_sided_95pct_upper_bound"] is not None
    assert h0_m1["false_confirmed_track_event_rate"]["evaluable_seed_count"] == 0
    assert h0_m1["false_confirmed_track_event_rate"]["one_sided_95pct_upper_bound"] is None


def test_confirmatory_analyzer_does_not_infer_missing_truth_status_as_evaluable(tmp_path):
    registry, checkpoint = _write_registry_checkpoint(tmp_path, missing_target_status=True)

    result = analysis.analyze_confirmatory_matrix(
        registry_path=registry, checkpoint_path=checkpoint,
        output_dir=tmp_path / "analysis",
    )

    assert result["analysis_status"] == "INCONCLUSIVE_REQUIRED_EVALUATION_DATA"
    seed = result["target_present"]["seed_level"]["seed_results"][0]
    assert seed["status"] == "DATA_OR_TRUTH_FAILURE"


def test_confirmatory_analyzer_rejects_evaluation_rows_from_another_seed(tmp_path):
    registry, checkpoint = _write_registry_checkpoint(tmp_path, wrong_association_seed=True)

    result = analysis.analyze_confirmatory_matrix(
        registry_path=registry, checkpoint_path=checkpoint,
        output_dir=tmp_path / "analysis",
    )

    assert result["analysis_status"] == "INCONCLUSIVE_MISSING_OR_UNVERIFIED_CASE_DATA"
    assert any("seed does not match registry" in row["reason"]
               for row in result["archive_failures"])


def test_confirmatory_analyzer_rejects_unrecognized_match_values(tmp_path):
    registry, checkpoint = _write_registry_checkpoint(tmp_path)
    checkpoint_value = json.loads(checkpoint.read_text(encoding="utf-8"))
    registry_value = json.loads(registry.read_text(encoding="utf-8"))
    target_case = next(case for case in registry_value["cases"]
                       if case["phase"] == "target_present" and case["delay_error_ns"] == 4.0)
    state = checkpoint_value["cases"][target_case["case_id"]]
    archive_path = Path(state["archive"]["archive_path"])
    with zipfile.ZipFile(archive_path, "r") as source:
        members = {name: source.read(name) for name in source.namelist()}
    table_name = "evaluation/target_associations.csv"
    rows = list(csv.DictReader(io.StringIO(members[table_name].decode("utf-8"))))
    rows[0]["matched"] = "unknown"
    members[table_name] = _csv_bytes(
        rows,
        ["case_id", "seed", "method_id", "mode", "role", "period_id", "beam_id", "target_id", "matched", "position_error_m"],
    )
    manifest = json.loads(members["ARCHIVE_MANIFEST.json"])
    for item in manifest["included_files"]:
        if item["path"] == table_name:
            item["bytes"] = len(members[table_name])
            item["sha256"] = hashlib.sha256(members[table_name]).hexdigest()
    members["ARCHIVE_MANIFEST.json"] = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, payload in members.items():
            target.writestr(name, payload)
    state["archive"]["archive_sha256"] = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    state["archive"]["archive_manifest_sha256"] = hashlib.sha256(
        members["ARCHIVE_MANIFEST.json"],
    ).hexdigest()
    checkpoint.write_text(json.dumps(checkpoint_value), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid boolean evaluation value"):
        analysis.analyze_confirmatory_matrix(
            registry_path=registry, checkpoint_path=checkpoint,
            output_dir=tmp_path / "analysis",
        )
