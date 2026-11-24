from __future__ import annotations

import pytest

from scripts.phase_r.r22_v4_case_schema import validate_v4_case_schema


CASE_ROOT = "pipeline/cases/case-x"
PLUS_FOUR_METHODS = (
    "A0", "C0", "PK", "R22_R9_Fixed_D3",
    "HUBER_D3_CAUSAL_PREFIX", "M1_CAUSAL_PREFIX",
)
TRACK_FILES = (
    "track_association_accepts.csv", "track_detections.csv", "track_events.csv",
    "track_frames.csv", "track_output_payloads.csv", "track_points.csv",
    "track_results.csv", "track_states.csv",
)


def _fixture(*, target_count: int, delay_ns: float, include_auxiliary_roles: bool = False):
    h0 = target_count == 0
    methods = ("A0",) if delay_ns == 0 else PLUS_FOUR_METHODS
    roles = ["ON"] if h0 else ["ON", "TO"]
    if include_auxiliary_roles:
        roles.extend(["OFF", "TO"] if h0 else ["OFF"])
    members = {
        "r22_run_manifest.json",
        f"{CASE_ROOT}/case_manifest.json",
        "evaluation/task_metrics.csv",
        "evaluation/parameter_metrics.csv",
        "evaluation/target_associations.csv",
        "evaluation/track_level_metrics.csv",
    }
    if h0:
        members.update({
            "evaluation/h0_task_metrics.csv",
            "evaluation/h0_false_alarm_metrics.csv",
        })
    else:
        members.update({
            f"{CASE_ROOT}/scenes/A0_ON/truth/truth_targets_by_beam.csv",
            f"{CASE_ROOT}/r22_v4_output_evidence/target_complex_response.csv",
            f"{CASE_ROOT}/r22_v4_output_evidence/target_complex_response_manifest.json",
            f"{CASE_ROOT}/r22_v4_output_evidence/target_roi_manifest.csv",
            f"{CASE_ROOT}/r22_v4_output_evidence/truth_mapping_audit.json",
        })
    members.update({
        f"{CASE_ROOT}/r22_v4_output_evidence/target_complex_response_manifest.json",
        f"{CASE_ROOT}/r22_v4_output_evidence/target_complex_response.csv",
        f"{CASE_ROOT}/r22_v4_output_evidence/target_roi_manifest.csv",
        f"{CASE_ROOT}/r22_v4_output_evidence/adaptive_role_difference_diagnostic.csv",
        f"{CASE_ROOT}/r22_v4_output_evidence/truth_mapping_audit.json",
    })
    branch_roles = []
    for method in methods:
        for role in dict.fromkeys(roles):
            branch = f"{method}_{role}"
            branch_roles.append({"method_id": method, "role": role})
            base = f"{CASE_ROOT}/production/{branch}/"
            members.update({
                base + "branch_manifest.json",
                base + "production.xml",
                base + "gmt_pipe_core.log",
                base + "result/debug/cfar_geometry_diagnostics.csv",
            })
            members.update(
                base + f"result/detection_results_GMTI{period:02d}.csv"
                for period in range(1, 11)
            )
            members.update(
                base + f"track_debug_runs/run/{filename}" for filename in TRACK_FILES
            )
    identity = {
        "case_id": "case-x",
        "phase": "h0" if h0 else "target_present",
        "target_count": target_count,
        "delay_error_ns": delay_ns,
        "period_count": 10,
        "branch_roles": branch_roles,
    }
    payload = {
        "target_truth_association_count": 0 if h0 else 20,
        "target_response_target_count": 0 if h0 else 20,
        "target_response_status": "NOT_APPLICABLE_NO_TARGETS" if h0 else "EVALUABLE",
        "target_response_row_count": 0 if h0 else 400,
        "target_roi_row_count": 0 if h0 else 200,
        "truth_mapping_status": "NOT_APPLICABLE_NO_TARGETS" if h0 else "passed",
    }
    return identity, members, payload


@pytest.mark.parametrize(
    ("target_count", "delay_ns", "schema_name"),
    [
        (20, 0.0, "target_present_0ns"),
        (20, 4.0, "target_present_4ns"),
        (0, 0.0, "h0_0ns"),
        (0, 4.0, "h0_4ns"),
    ],
)
def test_four_case_schemas_validate_required_optional_and_forbidden_members(
    target_count: int, delay_ns: float, schema_name: str,
):
    identity, members, payload = _fixture(target_count=target_count, delay_ns=delay_ns)
    result = validate_v4_case_schema(identity, members, payload_metadata=payload)

    assert result["status"] == "PASS"
    assert result["schema_name"] == schema_name
    assert result["missing_required_member_count"] == 0
    assert result["forbidden_member_count"] == 0
    assert result["expected_method_ids"] == (["A0"] if delay_ns == 0 else sorted(PLUS_FOUR_METHODS))


def test_zero_delay_schema_rejects_correction_branches_and_does_not_require_estimators():
    identity, members, payload = _fixture(target_count=20, delay_ns=0.0)
    members.add(f"{CASE_ROOT}/production/M1_CAUSAL_PREFIX_ON/branch_manifest.json")
    identity["branch_roles"].append({"method_id": "M1_CAUSAL_PREFIX", "role": "ON"})

    with pytest.raises(ValueError, match="zero-delay case contains correction method"):
        validate_v4_case_schema(identity, members, payload_metadata=payload)


def test_h0_schema_requires_no_target_roi_no_truth_associations_and_h0_evaluator():
    identity, members, payload = _fixture(target_count=0, delay_ns=4.0, include_auxiliary_roles=True)
    result = validate_v4_case_schema(identity, members, payload_metadata=payload)
    assert result["auxiliary_roles"] == ["OFF", "TO"]

    with pytest.raises(ValueError, match="H0 case contains target-present truth rows"):
        validate_v4_case_schema(
            identity,
            members | {f"{CASE_ROOT}/scenes/A0_ON/truth/truth_targets_by_beam.csv"},
            payload_metadata=payload,
        )
    with pytest.raises(ValueError, match="H0 target truth association count must be zero"):
        validate_v4_case_schema(identity, members, payload_metadata={
            **payload, "target_truth_association_count": 1,
        })


def test_h0_allows_only_explicitly_empty_not_applicable_target_evidence():
    identity, members, payload = _fixture(target_count=0, delay_ns=0.0)
    result = validate_v4_case_schema(identity, members, payload_metadata=payload)
    assert result["status"] == "PASS"
    assert result["target_response_status"] == "NOT_APPLICABLE_NO_TARGETS"
    with pytest.raises(ValueError, match="H0 target response must be explicitly empty"):
        validate_v4_case_schema(identity, members, payload_metadata={
            **payload, "target_response_row_count": 1,
        })
    with pytest.raises(ValueError, match="H0 truth mapping must be not applicable"):
        validate_v4_case_schema(identity, members, payload_metadata={
            **payload, "truth_mapping_status": "passed",
        })


def test_target_present_schema_requires_truth_roi_and_to_roles():
    identity, members, payload = _fixture(target_count=20, delay_ns=4.0)
    to_branch = next(
        row["method_id"] for row in identity["branch_roles"] if row["role"] == "TO"
    )
    identity["branch_roles"] = [
        row for row in identity["branch_roles"]
        if not (row["method_id"] == to_branch and row["role"] == "TO")
    ]
    members = {
        name for name in members
        if not name.startswith(f"{CASE_ROOT}/production/{to_branch}_TO/")
    }
    with pytest.raises(ValueError, match="target-present method lacks ON/TO role"):
        validate_v4_case_schema(identity, members, payload_metadata=payload)


def test_schema_rejects_missing_detection_period_or_trackmanager_lineage():
    identity, members, payload = _fixture(target_count=20, delay_ns=0.0)
    members.remove(f"{CASE_ROOT}/production/A0_ON/result/detection_results_GMTI10.csv")
    with pytest.raises(ValueError, match="missing required Production member"):
        validate_v4_case_schema(identity, members, payload_metadata=payload)
