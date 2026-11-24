from __future__ import annotations

import copy
import hashlib
import json
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


def test_draft_registry_is_exact_paired_and_default_denied(tmp_path):
    registry = _registry(tmp_path)
    assert registry["case_count"] == 240
    assert registry["batch_count"] == 120
    assert registry["design_status"] == "DRAFT_NOT_FROZEN"
    assert registry["execution_authorized"] is False
    assert registry["cuda_authorized"] is False
    assert registry["registry_sha256"] == executor.registry_digest(registry)
    contract = registry["frozen_thresholds"]["confirmatory_statistical_contract"]
    assert contract["position_scheme"] is None
    assert contract["minimum_common_support_per_speed"] is None
    assert contract["track_protection_policy"] is None
    assert contract["position_interval_method"] == "bootstrap_t"
    assert contract["position_interval_confidence"] == 0.95
    assert all(len(batch["case_ids"]) == 2 for batch in registry["batches"])
    assert all(batch["delay_error_ns"] == [0.0, 4.0] for batch in registry["batches"])
    assert {case["phase"] for case in registry["cases"]} == {"target_present", "h0"}

    checkpoint = executor.create_checkpoint(registry)
    assert checkpoint["execution_status"] == "NOT_AUTHORIZED"
    assert set(case["status"] for case in checkpoint["cases"].values()) == {"PLANNED"}
    default_deny = executor.authorization_template(registry)
    default_deny["authorization_type"] = executor.AUTHORIZATION_TYPE
    default_deny["authorization_sha256"] = executor.authorization_digest(default_deny)
    with pytest.raises(ValueError, match="approval"):
        executor.validate_confirmatory_authorization(
            default_deny, registry, requested_batch_ids=[registry["batches"][0]["batch_id"]],
            output_root=registry["output_root"], now=datetime.now(timezone.utc),
        )


def _authorized_registry_and_record(tmp_path: Path):
    design = json.loads(DESIGN.read_text())
    design["status"] = "FROZEN"
    design["execution_authorized"] = True
    design["cuda_authorized"] = True
    design["primary_comparison"]["position_mde_m"] = 3.0
    design["primary_comparison"]["position_scheme"] = "A"
    design["primary_comparison"]["minimum_common_support_per_speed"] = 1
    design["primary_comparison"]["track_protection_policy"] = "DESCRIPTIVE_ONLY"
    design["primary_comparison"]["detection_coverage_noninferiority_margin_pp"] = 2.0
    design["h0"]["seed_event_rate_upper_limit"] = 0.1
    design["h0"]["false_confirmed_track_rate_upper_limit"] = 0.1
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
        "max_archive_bytes_by_case_schema": {
            "target_present_0ns": 100,
            "target_present_4ns": 100,
            "h0_0ns": 100,
            "h0_4ns": 100,
        },
        "peak_temporary_bytes": 1000,
    }
    resource_evidence_path = tmp_path / "resource_evidence.json"
    resource_evidence_path.write_text(json.dumps(resource_evidence), encoding="utf-8")
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
        "resource_reserve_bytes": 100,
        "minimum_free_bytes": 1350,
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
        "archive_path": str(tmp_path / "case.zip"),
        "archive_sha256": "c" * 64,
        "archive_manifest_sha256": "d" * 64,
        "archive_profile": "r22_v4_output_complete",
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
    saved = json.loads((tmp_path / "run" / "confirmatory_checkpoint.json").read_text())
    assert saved["execution_status"] == "NOT_AUTHORIZED"


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
        executor.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10**12, used=0, free=10**12),
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

    def fake_archive(case_root, archive_path, **kwargs):
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = {"archive_profile": "r22_v4_output_complete", "included_files": [],
                    "required_v4_members": []}
        manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
        run_manifest = (Path(case_root) / "r22_run_manifest.json").read_bytes()
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("ARCHIVE_MANIFEST.json", manifest_bytes)
            archive.writestr("r22_run_manifest.json", run_manifest)
        return {
            "archive_path": str(archive_path.resolve()),
            "archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            "archive_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "archive_profile": "r22_v4_output_complete",
            "archive_bytes": archive_path.stat().st_size,
        }

    monkeypatch.setattr(executor.runner, "run_case", fake_run_case)
    monkeypatch.setattr(executor, "archive_case_directory", fake_archive)
    monkeypatch.setattr(executor, "verify_case_archive", lambda *args, **kwargs: {"crc_verified": True})

    first = executor.execute_authorized_batches(
        registry_path=registry_path,
        authorization_path=authorization_path,
        batch_ids=selected,
        stage2_binary=stage2,
        production_binary=production,
    )
    assert first["status"] == "AUTHORIZED_BATCHES_COMPLETE"
    assert len(calls) == 2
    checkpoint_path = output / "confirmatory_checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    selected_cases = registry["batches"][0]["case_ids"]
    assert {checkpoint["cases"][case_id]["status"] for case_id in selected_cases} == {"EVALUATED"}
    assert sum(row["status"] == "PLANNED" for row in checkpoint["cases"].values()) == 238
    assert all(not path.exists() for path, _, _ in calls)

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
