import pytest

from scripts.phase_r.run_r21_cuda_pilot import _has_production_execution, qualify_blind_methods


def _estimate(**overrides):
    value = {
        "status": "ok",
        "method": "M1_Physics_Matched_Delay",
        "delta_tau_ns": 3.75,
        "support_count": 2364,
        "frequency_span_hz": 50.0e6,
        "input_role": "A1_Current_unknown_error_ON_raw_C1_C2_only",
        "truth_used_in_estimator": False,
        "baselines": {
            "D3_Huber_weighted_LS": {"delta_tau_ns": 3.58, "support_count": 2364},
        },
    }
    value.update(overrides)
    return value


def test_blind_method_gate_accepts_only_declared_b_on_raw_inputs():
    result = qualify_blind_methods(_estimate())
    assert result["input_role"] == "A1_Current_unknown_error_ON_raw_C1_C2_only"
    assert result["truth_used_in_estimator"] is False
    assert result["d3_huber"] == {
        "status": "AVAILABLE", "correction_ns": 3.58,
        "source_record": {"delta_tau_ns": 3.58, "support_count": 2364},
        "support_qualified": True,
    }
    assert result["m1"]["correction_ns"] == 3.75


def test_blind_method_gate_rejects_truth_or_other_role_inputs():
    with pytest.raises(ValueError, match="input role"):
        qualify_blind_methods(_estimate(input_role="A0_ON"))
    with pytest.raises(ValueError, match="truth use"):
        qualify_blind_methods(_estimate(truth_used_in_estimator=True))


def test_missing_huber_estimate_is_explicitly_not_available():
    result = qualify_blind_methods(_estimate(baselines={}))
    assert result["d3_huber"] == {
        "status": "NOT_AVAILABLE", "correction_ns": None,
        "source_record": None, "support_qualified": False,
    }
    assert result["m1"]["status"] == "AVAILABLE"


def test_non_finite_blind_estimates_are_not_qualified():
    result = qualify_blind_methods(_estimate(
        status="insufficient_support", delta_tau_ns=float("nan"),
        baselines={"D3_Huber_weighted_LS": {"delta_tau_ns": float("inf")}},
    ))
    assert result["m1"]["status"] == "NOT_AVAILABLE"
    assert result["d3_huber"]["status"] == "NOT_AVAILABLE"


def test_finite_huber_estimate_without_frozen_support_is_not_available():
    result = qualify_blind_methods(_estimate(
        status="insufficient_support", support_count=7, frequency_span_hz=1.0e6,
        baselines={"D3_Huber_weighted_LS": {"delta_tau_ns": 3.58, "support_count": 7}},
    ))
    assert result["d3_huber"]["status"] == "NOT_AVAILABLE"
    assert result["m1"]["status"] == "NOT_AVAILABLE"


def test_huber_support_must_match_the_estimator_input_support():
    result = qualify_blind_methods(_estimate(
        baselines={"D3_Huber_weighted_LS": {"delta_tau_ns": 3.58, "support_count": 2000}},
    ))
    assert result["d3_huber"]["status"] == "NOT_AVAILABLE"


def test_executed_requires_a_production_return_record_not_stage2_simulation():
    assert not _has_production_execution({"simulation": {"A0_ON": {"status": "completed"}}})
    assert _has_production_execution({
        "simulation": {"A0_ON": {"status": "completed"}},
        "conditions": {"B": {"production": {"ON": {"production_returncode": 1}}}},
    })
