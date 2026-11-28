from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import zipfile
from types import SimpleNamespace

import pytest

from scripts.phase_r import r22_v4_confirmatory_executor as executor
from scripts.phase_r import run_r22_cuda_pilot as runner


ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / (
    "docs/research/phase_r/R22/config/"
    "r22_v4_causal_output_complete_confirmatory_draft_20261113.json"
)
TEMPLATE = ROOT / "simulator/scenarios/beam50_one_targets_continuous.run.json"
METHOD_CONFIG = ROOT / "docs/research/phase_r/R22/config/r22_method_config_v2_range_roi.json"


def _registry(tmp_path: Path) -> dict:
    return executor.build_confirmatory_registry(
        design_path=DESIGN,
        scenario_template=TEMPLATE,
        method_config=METHOD_CONFIG,
        output_root=tmp_path / "confirmatory-output",
    )


def test_frozen_registry_is_exact_paired_and_default_denied(tmp_path):
    registry = _registry(tmp_path)
    approved_design = json.loads(DESIGN.read_text(encoding="utf-8"))
    assert registry["case_count"] == 240
    assert registry["batch_count"] == 120
    assert registry["design_status"] == "FROZEN"
    assert registry["design_execution_authorized"] is False
    assert registry["design_cuda_authorized"] is False
    assert registry["seed_registry_sha256"] == approved_design["references"]["seed_registry_sha256"]
    assert registry["seed_registry_id"] == "R22_V4_PHASE_H_SEED_REGISTRY_20261126"
    assert registry["execution_authorized"] is False
    assert registry["cuda_authorized"] is False
    assert registry["registry_sha256"] == executor.registry_digest(registry)
    required_runtime_sources = {
        "scripts/phase_r/r22_experiment_matrix.py",
        "scripts/phase_r/r22_sharded_archive.py",
        "scripts/phase_r/r22_v4_evidence.py",
        "scripts/phase_r/r22_scenario_factory.py",
        "scripts/phase_r/r22_v4_identity.py",
        "scripts/estimate_channel_delay.py",
        "scripts/run_track_manager_e2e.py",
    }
    assert required_runtime_sources <= set(registry["runtime_source_identity"])
    thresholds = registry["frozen_thresholds"]
    assert thresholds["position_mde_m"] == 3.0
    assert thresholds["detection_coverage_noninferiority_margin_pp"] == 1.0
    assert thresholds["h0_seed_event_rate_upper_limit"] == 0.1
    assert thresholds["h0_false_confirmed_track_rate_upper_limit"] == 0.1
    contract = registry["frozen_thresholds"]["confirmatory_statistical_contract"]
    assert contract["position_scheme"] == "A"
    assert contract["minimum_common_support_per_speed"] == 10
    assert contract["track_protection_policy"] == "DESCRIPTIVE_ONLY"
    assert contract["position_interval_method"] == "bootstrap_t"
    assert contract["position_interval_confidence"] == 0.95
    assert contract["coverage_interval_method"] == "paired_t"
    assert contract["track_coverage_interval_method"] == "paired_t"
    assert contract["detection_coverage_noninferiority_rule"] == (
        "two-sided paired seed-level t 95% interval lower endpoint for M1-minus-FixedD3 "
        "percentage-point coverage difference must be strictly greater than the negative "
        "approved margin"
    )
    assert contract["coverage_interval_method_selection_status"] == "USER_APPROVED_AND_FROZEN"
    assert contract["bootstrap_replicates"] == 10_000
    assert contract["bootstrap_rng_algorithm"] == "PCG64"
    assert contract["bootstrap_seed"] == 22_029
    assert contract["h0_primary_endpoint"] == executor.H0_PRIMARY_ENDPOINT
    assert all(len(batch["case_ids"]) == 2 for batch in registry["batches"])
    assert all(batch["delay_error_ns"] == [0.0, 4.0] for batch in registry["batches"])
    assert {case["phase"] for case in registry["cases"]} == {"target_present", "h0"}
    assert {case["seed"] for case in registry["cases"] if case["phase"] == "target_present"} == set(
        range(1933001, 1933031)
    )
    assert {case["seed"] for case in registry["cases"] if case["phase"] == "h0"} == set(
        range(1936001, 1936031)
    )

    checkpoint = executor.create_checkpoint(registry)
    assert checkpoint["execution_status"] == "NOT_AUTHORIZED"
    assert set(case["status"] for case in checkpoint["cases"].values()) == {"PLANNED"}
    default_deny = executor.authorization_template(registry)
    assert default_deny["frozen_thresholds"]["confirmatory_statistical_contract"][
        "h0_primary_endpoint"
    ] == executor.H0_PRIMARY_ENDPOINT
    default_deny["authorization_type"] = executor.AUTHORIZATION_TYPE
    default_deny["authorization_sha256"] = executor.authorization_digest(default_deny)
    with pytest.raises(ValueError, match="approval"):
        executor.validate_confirmatory_authorization(
            default_deny, registry, requested_batch_ids=[registry["batches"][0]["batch_id"]],
            output_root=registry["output_root"], now=datetime.now(timezone.utc),
        )


def test_confirmatory_registry_fails_closed_when_seed_registry_digest_differs(tmp_path):
    design = json.loads(DESIGN.read_text(encoding="utf-8"))
    design["references"]["seed_registry_sha256"] = "0" * 64
    changed_design_path = tmp_path / "changed_seed_registry_reference.json"
    changed_design_path.write_text(json.dumps(design), encoding="utf-8")

    with pytest.raises(ValueError, match="seed registry digest"):
        executor.build_confirmatory_registry(
            design_path=changed_design_path,
            scenario_template=TEMPLATE,
            method_config=METHOD_CONFIG,
            output_root=tmp_path / "changed-seed-registry-output",
        )


def _authorized_registry_and_record(tmp_path: Path):
    design = json.loads(DESIGN.read_text())
    design["status"] = "FROZEN"
    design["execution_authorized"] = False
    design["cuda_authorized"] = False
    design["primary_comparison"]["position_mde_m"] = 3.0
    design["primary_comparison"]["position_scheme"] = "A"
    design["primary_comparison"]["minimum_common_support_per_speed"] = 1
    design["primary_comparison"]["track_protection_policy"] = "DESCRIPTIVE_ONLY"
    design["primary_comparison"]["detection_coverage_noninferiority_margin_pp"] = 2.0
    design["h0"]["seed_event_rate_upper_limit"] = 0.1
    design["h0"]["false_confirmed_track_rate_upper_limit"] = 0.1
    design["statistics"]["coverage_interval_method_selection_status"] = "USER_APPROVED_AND_FROZEN"
    frozen_design_path = tmp_path / "synthetic_frozen_design.json"
    frozen_design_path.write_text(json.dumps(design), encoding="utf-8")
    registry = executor.build_confirmatory_registry(
        design_path=frozen_design_path,
        scenario_template=TEMPLATE,
        method_config=METHOD_CONFIG,
        output_root=tmp_path / "confirmatory-output",
    )
    selected = [registry["batches"][0]["batch_id"]]
    case_ids = registry["batches"][0]["case_ids"]
    now = datetime.now(timezone.utc)
    resource_evidence = {
        "status": "PASS",
        "archive_profile": "r22_v4_output_complete",
        "stage2_production_cuda_started": True,
        "retained_archive_profile": "r22_v4_size_pilot_compact",
        "peak_temporary_includes_full_archive": True,
        "project_root": str(tmp_path.resolve()),
        "project_allocated_bytes_at_measurement": 0,
        "project_final_cap_bytes": 20_000_000_000,
        "storage_reserve_bytes": 10_737_418_240,
        "max_retained_case_bytes_by_schema": {
            "target_present_0ns": 100,
            "target_present_4ns": 100,
            "h0_0ns": 100,
            "h0_4ns": 100,
        },
        "peak_temporary_bytes": 1000,
    }
    resource_evidence_path = tmp_path / "resource_evidence.json"
    resource_evidence_path.write_text(json.dumps(resource_evidence), encoding="utf-8")
    projection = executor._resource_projection(
        registry, case_ids, resource_evidence, reserve_bytes=10_737_418_240,
    )
    record = {
        "schema_version": 1,
        "authorization_type": executor.AUTHORIZATION_TYPE,
        "authorization_id": "user-approval-example",
        "status": "AUTHORIZED",
        "user_approved": True,
        "revoked": False,
        "approved_at_utc": (now - timedelta(minutes=1)).isoformat(),
        "expires_at_utc": (now + timedelta(hours=4)).isoformat(),
        "design_sha256": registry["design_sha256"],
        "registry_sha256": registry["registry_sha256"],
        "selected_batch_ids": selected,
        "selected_case_ids": case_ids,
        "max_case_count": len(case_ids),
        "output_root": registry["output_root"],
        "archive_profile": "r22_v4_output_complete",
        "production_backend": "Native CUDA",
        "use_corex": False,
        "gpu_uuid": "GPU-test-uuid",
        "stage2_binary_sha256": "a" * 64,
        "production_binary_sha256": "b" * 64,
        "scenario_template_sha256": registry["scenario_template_sha256"],
        "method_config_sha256": registry["method_config_sha256"],
        "runtime_source_identity": copy.deepcopy(registry["runtime_source_identity"]),
        "runtime_source_identity_sha256": registry["runtime_source_identity_sha256"],
        "runtime_profile_sha256": registry["runtime_profile_sha256"],
        "resource_evidence_path": str(resource_evidence_path),
        "resource_evidence_sha256": hashlib.sha256(resource_evidence_path.read_bytes()).hexdigest(),
        "resource_reserve_bytes": 10_737_418_240,
        "minimum_free_bytes": projection["minimum_free_bytes"],
        "frozen_thresholds": copy.deepcopy(registry["frozen_thresholds"]),
    }
    record["authorization_sha256"] = executor.authorization_digest(record)
    return registry, record, selected


def test_authorization_is_bound_to_exact_registry_batch_and_runtime(tmp_path):
    registry, record, selected = _authorized_registry_and_record(tmp_path)
    accepted = executor.validate_confirmatory_authorization(
        record, registry, requested_batch_ids=selected,
        output_root=registry["output_root"], now=datetime.now(timezone.utc),
        observed_gpu_uuid="GPU-test-uuid",
        stage2_binary_sha256="a" * 64,
        production_binary_sha256="b" * 64,
        archive_profile="r22_v4_output_complete",
    )
    assert accepted["status"] == "AUTHORIZED_FOR_SELECTED_BATCHES"
    assert accepted["case_count"] == 2
    assert accepted["frozen_thresholds"]["confirmatory_statistical_contract"]["position_scheme"] == "A"

    with pytest.raises(ValueError, match="batch"):
        executor.validate_confirmatory_authorization(
            record, registry, requested_batch_ids=["unregistered"],
            output_root=registry["output_root"], now=datetime.now(timezone.utc),
        )
    with pytest.raises(ValueError, match="GPU"):
        executor.validate_confirmatory_authorization(
            record, registry, requested_batch_ids=selected,
            output_root=registry["output_root"], now=datetime.now(timezone.utc),
            observed_gpu_uuid="GPU-other",
        )
    with pytest.raises(ValueError, match="outside the approved output root"):
        executor.validate_confirmatory_authorization(
            record, registry, requested_batch_ids=selected,
            output_root=tmp_path / "different-output", now=datetime.now(timezone.utc),
        )


def test_frozen_h0_primary_endpoint_is_seed_any_event_not_period_fraction(tmp_path):
    registry, _, _ = _authorized_registry_and_record(tmp_path)
    contract = registry["frozen_thresholds"]["confirmatory_statistical_contract"]

    assert contract["h0_primary_endpoint"] == executor.H0_PRIMARY_ENDPOINT
    executor._validate_thresholds(registry["frozen_thresholds"])

    changed = copy.deepcopy(registry["frozen_thresholds"])
    changed["confirmatory_statistical_contract"]["h0_primary_endpoint"] = (
        "seed-clustered proportion of valid beam-50 periods with >=1 frozen postfilter Detection"
    )
    with pytest.raises(ValueError, match="H0 endpoint"):
        executor._validate_thresholds(changed)


@pytest.mark.parametrize("mutation, message", [
    (lambda value: value.update(user_approved=False), "approval"),
    (lambda value: value.update(revoked=True), "revoked"),
    (lambda value: value.update(archive_profile="legacy"), "archive"),
    (lambda value: value.update(production_backend="CoreX"), "Native CUDA"),
    (lambda value: value.update(use_corex=True), "CoreX"),
    (lambda value: value.update(max_case_count=1), "case count"),
])
def test_authorization_tampering_and_pilot_records_are_rejected(tmp_path, mutation, message):
    registry, record, selected = _authorized_registry_and_record(tmp_path)
    mutation(record)
    record["authorization_sha256"] = executor.authorization_digest(record)
    with pytest.raises(ValueError, match=message):
        executor.validate_confirmatory_authorization(
            record, registry, requested_batch_ids=selected,
            output_root=registry["output_root"], now=datetime.now(timezone.utc),
        )

    pilot = {"status": "AUTHORIZED_SINGLE_ENGINEERING_CASE", "phase": "engineering"}
    with pytest.raises(ValueError, match="authorization type"):
        executor.validate_confirmatory_authorization(
            pilot, registry, requested_batch_ids=selected,
            output_root=registry["output_root"], now=datetime.now(timezone.utc),
        )


def test_authorization_expiry_digest_and_frozen_thresholds_are_enforced(tmp_path):
    registry, record, selected = _authorized_registry_and_record(tmp_path)
    with pytest.raises(ValueError, match="expired"):
        executor.validate_confirmatory_authorization(
            record, registry, requested_batch_ids=selected,
            output_root=registry["output_root"],
            now=datetime.fromisoformat(record["expires_at_utc"]) + timedelta(seconds=1),
        )

    corrupted = dict(record)
    corrupted["gpu_uuid"] = "GPU-tampered"
    with pytest.raises(ValueError, match="digest"):
        executor.validate_confirmatory_authorization(
            corrupted, registry, requested_batch_ids=selected,
            output_root=registry["output_root"], now=datetime.now(timezone.utc),
        )

    registry["frozen_thresholds"]["position_mde_m"] = None
    registry["registry_sha256"] = executor.registry_digest(registry)
    record["registry_sha256"] = registry["registry_sha256"]
    record["frozen_thresholds"]["position_mde_m"] = None
    record["authorization_sha256"] = executor.authorization_digest(record)
    with pytest.raises(ValueError, match="threshold"):
        executor.validate_confirmatory_authorization(
            record, registry, requested_batch_ids=selected,
            output_root=registry["output_root"], now=datetime.now(timezone.utc),
        )


def test_authorization_binds_unapproved_or_changed_statistical_contract(tmp_path):
    registry, record, selected = _authorized_registry_and_record(tmp_path)
    registry["frozen_thresholds"]["confirmatory_statistical_contract"]["position_scheme"] = None
    registry["registry_sha256"] = executor.registry_digest(registry)
    record["registry_sha256"] = registry["registry_sha256"]
    record["frozen_thresholds"] = copy.deepcopy(registry["frozen_thresholds"])
    record["authorization_sha256"] = executor.authorization_digest(record)

    with pytest.raises(ValueError, match="statistical contract"):
        executor.validate_confirmatory_authorization(
            record, registry, requested_batch_ids=selected,
            output_root=registry["output_root"], now=datetime.now(timezone.utc),
        )


def test_resource_gate_denies_default_and_checks_disk_gpu_and_archive(tmp_path):
    registry, record, selected = _authorized_registry_and_record(tmp_path)
    denied = executor.resource_gate(
        authorization=None, free_bytes=10**12, required_free_bytes=1024,
        observed_gpu_uuid="GPU-test-uuid",
    )
    assert denied["status"] == "DENIED"

    low_disk = executor.resource_gate(
        authorization=record, free_bytes=100, required_free_bytes=1024,
        observed_gpu_uuid="GPU-test-uuid",
    )
    assert low_disk["status"] == "DENIED"
    wrong_gpu = executor.resource_gate(
        authorization=record, free_bytes=10**12, required_free_bytes=1024,
        observed_gpu_uuid="GPU-other",
    )
    assert wrong_gpu["status"] == "DENIED"
    passed = executor.resource_gate(
        authorization=record, free_bytes=10**12, required_free_bytes=1024,
        observed_gpu_uuid="GPU-test-uuid",
    )
    assert passed["status"] == "READY"
    assert passed["execution_started"] is False


def test_resource_projection_fails_closed_when_retained_archives_exceed_project_cap(tmp_path):
    registry, _record, selected = _authorized_registry_and_record(tmp_path)
    evidence_path = Path(registry["output_root"]).parent / "resource_evidence.json"
    evidence = json.loads(evidence_path.read_text())
    evidence["project_allocated_bytes_at_measurement"] = 19_999_999_999
    with pytest.raises(ValueError, match="final project storage cap"):
        executor._resource_projection(
            registry, registry["batches"][0]["case_ids"], evidence,
            reserve_bytes=10_737_418_240,
        )


def test_resource_projection_rejects_missing_or_changed_frozen_reserve(tmp_path):
    registry, _record, selected = _authorized_registry_and_record(tmp_path)
    evidence_path = Path(registry["output_root"]).parent / "resource_evidence.json"
    evidence = json.loads(evidence_path.read_text())
    for reserve in (0, 10_737_418_239):
        with pytest.raises(ValueError, match="frozen 10 GiB storage reserve"):
            executor._resource_projection(
                registry, registry["batches"][0]["case_ids"], evidence,
                reserve_bytes=reserve,
            )


def test_authorized_case_inventory_covers_all_approved_batches(tmp_path):
    registry, authorization, _selected = _authorized_registry_and_record(tmp_path)
    selected_batches = [row["batch_id"] for row in registry["batches"][:2]]
    selected_cases = [
        case_id for batch in selected_batches
        for case_id in next(row for row in registry["batches"] if row["batch_id"] == batch)["case_ids"]
    ]
    authorization["selected_batch_ids"] = selected_batches
    authorization["selected_case_ids"] = selected_cases
    authorization["max_case_count"] = len(selected_cases)
    authorization["authorization_sha256"] = executor.authorization_digest(authorization)

    assert executor._authorized_case_ids(registry, authorization) == selected_cases
    assert len(selected_cases) > len(registry["batches"][0]["case_ids"])


def test_final_project_cap_is_checked_after_last_case_archive_cleanup(tmp_path, monkeypatch):
    registry, authorization, selected = _authorized_registry_and_record(tmp_path)
    stage2 = tmp_path / "stage2.bin"
    production = tmp_path / "production.bin"
    stage2.write_bytes(b"synthetic stage2 executable identity")
    production.write_bytes(b"synthetic production executable identity")
    authorization["stage2_binary_sha256"] = hashlib.sha256(stage2.read_bytes()).hexdigest()
    authorization["production_binary_sha256"] = hashlib.sha256(production.read_bytes()).hexdigest()
    authorization["authorization_sha256"] = executor.authorization_digest(authorization)
    output = Path(registry["output_root"])
    output.mkdir(parents=True, exist_ok=True)
    registry_path = output / "confirmatory_case_registry.json"
    authorization_path = output / "authorization.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    authorization_path.write_text(json.dumps(authorization), encoding="utf-8")

    monkeypatch.setattr(executor, "_probe_gpu_uuid", lambda: "GPU-test-uuid")
    monkeypatch.setattr(
        executor.shutil, "disk_usage",
        lambda _path: SimpleNamespace(total=10**12, used=0, free=10**12),
    )
    run_calls = []

    def fake_run_case(case_output, **kwargs):
        run_calls.append(Path(case_output))
        Path(case_output).mkdir(parents=True, exist_ok=False)
        result = {
            "implementation_result": "passed",
            "status_labels": {"IMPLEMENTED": True, "EXECUTED": True,
                              "EVALUABLE": True, "PERFORMANCE_VERIFIED": False},
            "evaluation_summary": {"task_rows": 1},
        }
        (Path(case_output) / "r22_run_manifest.json").write_text(json.dumps(result))
        return result

    monkeypatch.setattr(executor.runner, "run_case", fake_run_case)
    monkeypatch.setattr(executor.sharded.compact, "_v4_required_members",
                        lambda _root, _files: ["r22_run_manifest.json"])
    monkeypatch.setattr(executor.sharded.compact, "_v4_verified_dense_csi_exclusions",
                        lambda *_args, **_kwargs: {})
    monkeypatch.setattr(executor.sharded.compact, "_compact_case_manifest_extract",
                        lambda *_args: None)
    monkeypatch.setattr(executor.sharded.compact, "_exclusion_reason", lambda *_args: None)
    monkeypatch.setattr(
        executor.sharded.compact, "_confirmatory_case_schema",
        lambda _root, _members, identity: {
            "status": "PASS", "phase_family": identity["phase"],
            "required_members": ["r22_run_manifest.json"],
        },
    )
    post_case_measurements = {"count": 0}

    def allocated_bytes(_root, *, exclude_subtrees=()):
        if exclude_subtrees:
            return 0
        post_case_measurements["count"] += 1
        return (executor.PROJECT_FINAL_CAP_BYTES - 1
                if post_case_measurements["count"] == 1
                else executor.PROJECT_FINAL_CAP_BYTES + 1)

    monkeypatch.setattr(executor, "_allocated_tree_bytes", allocated_bytes)

    with pytest.raises(RuntimeError, match="actual project allocation exceeds the final cap"):
        executor.execute_authorized_batches(
            registry_path=registry_path, authorization_path=authorization_path,
            batch_ids=selected, stage2_binary=stage2, production_binary=production,
        )

    checkpoint = json.loads((output / "confirmatory_checkpoint.json").read_text())
    assert checkpoint["execution_status"] == "PAUSED_PROJECT_FINAL_CAP"
    assert checkpoint["actual_project_final_storage_check"]["allocated_bytes"] == (
        executor.PROJECT_FINAL_CAP_BYTES + 1
    )
    assert len(run_calls) == 2
    assert all(not path.exists() for path in run_calls)


def test_allocated_tree_measurement_deduplicates_hardlinks_and_excludes_active_case(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    retained = project / "retained.bin"
    retained.write_bytes(b"retained")
    os.link(retained, project / "retained-hardlink.bin")
    active_case = project / "raw" / "case-a" / "attempt_001"
    active_case.mkdir(parents=True)
    (active_case / "large.bin").write_bytes(b"temporary raw data")

    measured = executor._allocated_tree_bytes(project)
    excluded = executor._allocated_tree_bytes(project, exclude_subtrees=(active_case,))
    expected_retained = retained.stat().st_blocks * 512
    expected_full = expected_retained + active_case.stat().st_blocks * 512
    expected_full += (active_case / "large.bin").stat().st_blocks * 512
    expected_full += (active_case.parent).stat().st_blocks * 512
    expected_full += (active_case.parents[1]).stat().st_blocks * 512
    expected_full += (project / "raw").stat().st_blocks * 512
    assert measured == expected_full
    assert excluded == expected_retained + (project / "raw").stat().st_blocks * 512


def test_checkpoint_state_machine_retries_only_technical_failures_and_checks_archive(tmp_path):
    registry = _registry(tmp_path)
    checkpoint = executor.create_checkpoint(registry)
    case_id = registry["cases"][0]["case_id"]
    executor.transition_case(checkpoint, case_id, "RUNNING")
    executor.transition_case(
        checkpoint, case_id, "FAILED", failure_kind="technical", reason="device reset",
    )
    executor.transition_case(checkpoint, case_id, "RUNNING", retry_same_case=True)
    executor.transition_case(checkpoint, case_id, "ARCHIVED", archive={
        "case_id": case_id,
        "case_schema_identity_sha256": "9" * 64,
        "archive_path": str(tmp_path / "case.zip"),
        "archive_sha256": "c" * 64,
        "archive_manifest_sha256": "d" * 64,
        "archive_profile": "r22_v4_output_complete",
        "receipt_compression": "gzip-9",
        "full_archive_payload_present": False,
        "historically_verified_full_archive": True,
        "compact_archive_path": str(tmp_path / "case.compact.zip"),
        "compact_archive_sha256": "e" * 64,
        "compact_archive_manifest_sha256": "f" * 64,
        "compact_archive_profile": "r22_v4_size_pilot_compact",
    })

    verified_calls: list[tuple] = []
    resumed = executor.validate_checkpoint_resume(
        checkpoint, registry,
        archive_verifier=lambda *args, **kwargs: verified_calls.append((args, kwargs)),
    )
    assert resumed["status"] == "RESUME_VALID"
    assert verified_calls

    checkpoint["authorization_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="authorization identity"):
        executor.validate_checkpoint_resume(
            checkpoint, registry, authorization_sha256="b" * 64,
            archive_verifier=lambda *args, **kwargs: None,
        )

    corrupted = copy.deepcopy(checkpoint)
    corrupted["cases"][case_id]["archive"]["archive_sha256"] = "f" * 64
    def verify_expected_identity(path, sha, manifest_sha, profile):
        if sha != "c" * 64:
            raise ValueError("archive identity mismatch")
    with pytest.raises(ValueError, match="archive identity"):
        executor.validate_checkpoint_resume(
            corrupted, registry,
            archive_verifier=verify_expected_identity,
        )

    other = next(row["case_id"] for row in registry["cases"] if row["case_id"] != case_id)
    executor.transition_case(checkpoint, other, "RUNNING")
    executor.transition_case(
        checkpoint, other, "FAILED", failure_kind="algorithm", reason="invalid reference",
    )
    with pytest.raises(ValueError, match="algorithm failure"):
        executor.transition_case(checkpoint, other, "RUNNING", retry_same_case=True)


def test_dry_run_writes_registry_and_checkpoint_without_execution(tmp_path):
    result = executor.dry_run(
        design_path=DESIGN,
        scenario_template=TEMPLATE,
        method_config=METHOD_CONFIG,
        output_root=tmp_path / "run",
    )
    assert result["execution_started"] is False
    assert result["case_count"] == 240
    assert result["status"] == "NOT_AUTHORIZED"
    assert (tmp_path / "run" / "confirmatory_case_registry.json").is_file()
    saved = json.loads((tmp_path / "run" / "confirmatory_dry_run_checkpoint.json").read_text())
    assert saved["execution_status"] == "NOT_AUTHORIZED"
    assert not (tmp_path / "run" / "confirmatory_checkpoint.json").exists()


def test_runner_v4_output_complete_remains_default_deny_without_authorization(tmp_path):
    output = tmp_path / "must-not-be-created"
    with pytest.raises(ValueError, match="authorization"):
        runner.run_case(
            output,
            phase="confirmatory", seed=1933001, speed_mps=0.5, snr_db=20.0,
            delay_ns=0.0, target_count=20, period_count=10,
            first_beam=50, beam_count=1,
            stage2_binary=tmp_path / "stage2",
            production_binary=tmp_path / "production",
            v4_output_complete=True,
        )
    assert not output.exists()


def test_cpu_simulated_batch_execution_archives_checkpoints_and_resumes_without_rerun(
    tmp_path, monkeypatch,
):
    registry, authorization, selected = _authorized_registry_and_record(tmp_path)
    dry_run_result = executor.dry_run(
        design_path=DESIGN,
        scenario_template=TEMPLATE,
        method_config=METHOD_CONFIG,
        output_root=Path(registry["output_root"]),
    )
    assert dry_run_result["status"] == "NOT_AUTHORIZED"
    stage2 = tmp_path / "stage2.bin"
    production = tmp_path / "production.bin"
    stage2.write_bytes(b"synthetic stage2 executable identity")
    production.write_bytes(b"synthetic production executable identity")
    authorization["stage2_binary_sha256"] = hashlib.sha256(stage2.read_bytes()).hexdigest()
    authorization["production_binary_sha256"] = hashlib.sha256(production.read_bytes()).hexdigest()
    authorization["authorization_sha256"] = executor.authorization_digest(authorization)

    output = Path(registry["output_root"])
    output.mkdir(parents=True, exist_ok=True)
    registry_path = output / "confirmatory_case_registry.json"
    authorization_path = output / "authorization.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    authorization_path.write_text(json.dumps(authorization), encoding="utf-8")

    monkeypatch.setattr(executor, "_probe_gpu_uuid", lambda: "GPU-test-uuid")
    free_space = {"bytes": 10**12}
    monkeypatch.setattr(
        executor.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10**12, used=0, free=free_space["bytes"]),
    )
    calls = []

    def fake_run_case(output_root, **kwargs):
        calls.append((Path(output_root), kwargs["seed"], kwargs["delay_ns"]))
        Path(output_root).mkdir(parents=True, exist_ok=False)
        result = {
            "implementation_result": "passed",
            "status_labels": {"IMPLEMENTED": True, "EXECUTED": True, "EVALUABLE": True,
                              "PERFORMANCE_VERIFIED": False},
            "evaluation_summary": {"task_rows": 1},
        }
        (Path(output_root) / "r22_run_manifest.json").write_text(json.dumps(result))
        return result

    monkeypatch.setattr(executor.runner, "run_case", fake_run_case)
    monkeypatch.setattr(
        executor.sharded.compact, "_v4_required_members",
        lambda _root, _files: ["r22_run_manifest.json"],
    )
    monkeypatch.setattr(
        executor.sharded.compact, "_v4_verified_dense_csi_exclusions",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(executor.sharded.compact, "_compact_case_manifest_extract", lambda *_args: None)
    monkeypatch.setattr(executor.sharded.compact, "_exclusion_reason", lambda *_args: None)
    monkeypatch.setattr(
        executor.sharded.compact, "_confirmatory_case_schema",
        lambda _root, _members, identity: {
            "status": "PASS", "phase_family": identity["phase"],
            "required_members": ["r22_run_manifest.json"],
        },
    )

    first_case_id = registry["batches"][0]["case_ids"][0]
    original_transition = executor.transition_case

    def interrupt_after_receipt(checkpoint, case_id, new_status, **kwargs):
        if case_id == first_case_id and new_status == "ARCHIVED":
            raise RuntimeError("simulated stop after compact receipt publication")
        return original_transition(checkpoint, case_id, new_status, **kwargs)

    monkeypatch.setattr(executor, "transition_case", interrupt_after_receipt)
    with pytest.raises(RuntimeError, match="compact receipt publication"):
        executor.execute_authorized_batches(
            registry_path=registry_path,
            authorization_path=authorization_path,
            batch_ids=selected,
            stage2_binary=stage2,
            production_binary=production,
        )
    assert len(calls) == 1
    interrupted_checkpoint = json.loads((output / "confirmatory_checkpoint.json").read_text())
    interrupted_state = interrupted_checkpoint["cases"][first_case_id]
    assert interrupted_state["status"] == "RUNNING"
    interrupted_receipt = output / "archives" / (
        f"{first_case_id}__attempt_001.archive-set.json.gz"
    )
    assert interrupted_receipt.is_file()
    assert (output / "raw" / first_case_id / "attempt_001" / "r22_run_manifest.json").is_file()

    monkeypatch.setattr(executor, "transition_case", original_transition)
    free_space["bytes"] = authorization["minimum_free_bytes"] - 1
    first = executor.execute_authorized_batches(
        registry_path=registry_path,
        authorization_path=authorization_path,
        batch_ids=selected,
        stage2_binary=stage2,
        production_binary=production,
        resume=True,
    )
    assert first["status"] == "AUTHORIZED_BATCHES_COMPLETE"
    assert len(calls) == 2
    checkpoint_path = output / "confirmatory_checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    selected_cases = registry["batches"][0]["case_ids"]
    assert {checkpoint["cases"][case_id]["status"] for case_id in selected_cases} == {"EVALUATED"}
    assert sum(row["status"] == "PLANNED" for row in checkpoint["cases"].values()) == 238
    assert all(not path.exists() for path, _, _ in calls)
    for case_id in selected_cases:
        archived = checkpoint["cases"][case_id]["archive"]
        assert archived["compact_archive_profile"] == "r22_v4_size_pilot_compact"
        assert Path(archived["compact_archive_path"]).is_file()
        assert Path(archived["archive_path"]).name.endswith(".archive-set.json.gz")
        assert not Path(archived["raw_case_path"]).exists()
        assert not Path(str(archived["archive_path"])[:-3]).exists()
        assert executor._default_archive_verifier(
            Path(archived["archive_path"]), archived["archive_sha256"],
            archived["archive_manifest_sha256"], archived["archive_profile"],
        )["compact_archive_verified"] is True

    resumed = executor.execute_authorized_batches(
        registry_path=registry_path,
        authorization_path=authorization_path,
        batch_ids=selected,
        stage2_binary=stage2,
        production_binary=production,
        resume=True,
    )
    assert resumed["status"] == "AUTHORIZED_BATCHES_COMPLETE"
    assert len(calls) == 2
