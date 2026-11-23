from __future__ import annotations

from pathlib import Path

from scripts.phase_r.r22_v4_execution_plan import (
    build_v4_execution_plan,
    expected_v4_production_branch_identities,
    load_v4_design,
    validate_v4_actual_method_records,
)


DESIGN = Path(__file__).resolve().parents[1] / (
    "docs/research/phase_r/R22/config/"
    "r22_v4_causal_output_complete_confirmatory_draft_20261113.json"
)


def test_v4_draft_expands_to_explicit_nonexecuting_case_and_branch_plan():
    plan = build_v4_execution_plan(load_v4_design(DESIGN))

    assert plan["execution_authorized"] is False
    assert plan["cuda_authorized"] is False
    assert len(plan["case_identities"]) == 240
    assert plan["case_counts"] == {"target_present": 180, "h0": 60}
    assert len(plan["registered_science_branches"]) == 1470
    assert plan["registered_branch_counts"] == {
        "target_present": 1260,
        "h0": 210,
        "total": 1470,
    }

    target = [row for row in plan["registered_science_branches"]
              if row["phase"] == "target_present"]
    h0 = [row for row in plan["registered_science_branches"] if row["phase"] == "h0"]
    assert {row["role"] for row in target} == {"ON", "TO"}
    assert {row["role"] for row in h0} == {"ON"}
    assert {row["mode"] for row in plan["production_branch_templates"]} == {"not_applicable"}
    assert {row["role"] for row in plan["production_branch_templates"]} == {
        "ON", "OFF", "TO",
    }
    assert plan["registered_production_branch_counts"] == {
        "target_present": 1080,
        "h0": 180,
        "total": 1260,
    }
    assert plan["production_branch_call_counts"]["total"] == 2520
    assert plan["production_branch_call_counts"]["target_present"] == 1890
    assert plan["production_branch_call_counts"]["h0"] == 630
    assert plan["production_branch_call_counts"]["maximum"] == 2520
    assert plan["production_branch_call_counts"]["internal_auxiliary_calls"] == 1260
    assert plan["production_branch_call_counts"]["per_case_by_delay"] == {"0": 3, "4": 18}
    assert plan["production_branch_call_counts"]["by_phase_and_delay"] == {
        "target_present": {"zero_delay": 270, "plus_four_ns": 1620, "total": 1890},
        "h0": {"zero_delay": 90, "plus_four_ns": 540, "total": 630},
    }
    assert plan["offline_diagnostic_slot_count"] == 210
    assert plan["stage2_call_counts"]["minimum_total"] == 1200
    assert plan["stage2_call_counts"]["maximum_total"] == 1440
    assert plan["stage2_call_counts"]["zero_delay_case_calls"] == {"minimum": 4, "maximum": 6}
    assert plan["stage2_call_counts"]["minimum_total"] == 1200
    assert plan["stage2_call_counts"]["maximum_total"] == 1440
    assert {row["method_id"] for row in plan["production_branch_templates"]} == {
        "A0", "C0", "PK", "R22_R9_Fixed_D3", "HUBER_D3_CAUSAL_PREFIX",
        "M1_CAUSAL_PREFIX",
    }
    assert all(row["mode"] == "not_applicable" for row in plan["production_branch_templates"])
    assert {row["runner_method_id"] for row in plan["registered_science_branches"]} == {
        "A0", "C0", "PK", "R22_R9_Fixed_D3", "HUBER_D3_CAUSAL_PREFIX",
        "M1_CAUSAL_PREFIX", "M1_BATCH10_DIAGNOSTIC",
    }
    diagnostic_rows = [row for row in plan["registered_science_branches"]
                       if row["method_id"] == "M1_BATCH10_DIAGNOSTIC"]
    assert diagnostic_rows and all(row["production_required"] is False for row in diagnostic_rows)
    assert all(row["uses_delay_truth"] is False for row in plan["registered_science_branches"]
               if row["method_id"] != "PK_KNOWN")


def test_v4_actual_method_records_must_match_source_derived_case_plan():
    plan = build_v4_execution_plan(load_v4_design(DESIGN))
    case = next(row for row in plan["case_identities"]
                if row["phase"] == "target_present" and row["delay_error_ns"] == 4.0)
    zero_case = next(row for row in plan["case_identities"]
                     if row["phase"] == "target_present" and row["delay_error_ns"] == 0.0)
    assert zero_case["case_id"].startswith("confirmatory__")
    assert len(expected_v4_production_branch_identities(plan, zero_case["case_id"])) == 3
    expected = expected_v4_production_branch_identities(plan, case["case_id"])
    actual = [
        {"method_id": method, "mode": mode, "role": role, "branch_name": branch}
        for method, mode, role, branch in expected
    ]
    result = validate_v4_actual_method_records(plan, case["case_id"], actual)
    assert result["actual_production_branch_count"] == 18

    with_duplicate = actual + [dict(actual[0])]
    try:
        validate_v4_actual_method_records(plan, case["case_id"], with_duplicate)
    except ValueError as exc:
        assert "unexpected_or_duplicate" in str(exc)
    else:
        raise AssertionError("duplicate actual Production branch was accepted")

    with_unregistered = actual + [{
        "method_id": "C4", "mode": "Mode-A", "role": "ON",
        "branch_name": "C4_modea_ON",
    }]
    try:
        validate_v4_actual_method_records(plan, case["case_id"], with_unregistered)
    except ValueError as exc:
        assert "unexpected_or_duplicate" in str(exc)
    else:
        raise AssertionError("unregistered scientific method was accepted")


def test_v4_plan_rejects_invalid_or_duplicate_seed_ranges():
    design = load_v4_design(DESIGN)
    design["target_present"]["seed_range_inclusive"] = [1933001, 1933001]

    try:
        build_v4_execution_plan(design)
    except ValueError as exc:
        assert "seed_count" in str(exc)
    else:
        raise AssertionError("inconsistent seed range was accepted")
