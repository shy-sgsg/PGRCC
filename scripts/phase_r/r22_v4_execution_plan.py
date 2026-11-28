"""Expand the frozen R22 V4 design into a non-executing run plan."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from scripts.phase_r import run_r22_cuda_pilot as runner
from scripts import run_production_hierarchical_calibration_pilot as calibration


DESIGN_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs/research/phase_r/R22/config/"
    / "r22_v4_causal_output_complete_confirmatory_draft_20261113.json"
)
_PRODUCTION_ROLES = ("OFF", "ON", "TO")
_V4_REGISTERED_TO_RUNNER = {
    "A0": "A0",
    "C0": "C0",
    "FIXED_D3": "R22_R9_Fixed_D3",
    "HUBER_D3_CAUSAL_PREFIX": "HUBER_D3_CAUSAL_PREFIX",
    "M1_CAUSAL_PREFIX": "M1_CAUSAL_PREFIX",
    "M1_BATCH10_DIAGNOSTIC": "M1_BATCH10_DIAGNOSTIC",
    "PK_KNOWN": "PK",
}


def load_v4_design(path: Path = DESIGN_PATH) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("R22 V4 design must be a JSON object")
    return value


def _seed_range(section: Mapping[str, Any], key: str, count_key: str) -> list[int]:
    bounds = section.get(key)
    if (not isinstance(bounds, list) or len(bounds) != 2
            or any(isinstance(item, bool) or not isinstance(item, int) for item in bounds)):
        raise ValueError(f"{key} must contain two integer bounds")
    first, last = bounds
    if first <= 0 or last < first:
        raise ValueError(f"{key} is invalid")
    seeds = list(range(first, last + 1))
    if len(seeds) != section.get(count_key):
        raise ValueError(f"{count_key} does not match {key}")
    return seeds


def _number_list(section: Mapping[str, Any], key: str) -> list[float]:
    values = section.get(key)
    if not isinstance(values, list) or not values:
        raise ValueError(f"{key} must be a non-empty list")
    normalized = [float(value) for value in values]
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{key} contains duplicate values")
    return normalized


def _methods(section: Mapping[str, Any], *, required_ids: set[str]) -> dict[str, dict[str, Any]]:
    rows = section.get("methods")
    if not isinstance(rows, list):
        raise ValueError("methods must be a list")
    methods: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not str(row.get("id") or "").strip():
            raise ValueError("each method requires a non-empty id")
        method_id = str(row["id"])
        if method_id in methods:
            raise ValueError(f"duplicate method id: {method_id}")
        methods[method_id] = dict(row)
    if set(methods) != required_ids:
        raise ValueError(
            f"V4 method set differs from the implementation contract: "
            f"expected={sorted(required_ids)}, actual={sorted(methods)}"
        )
    return methods


def _case_id(phase: str, seed: int, speed: float, snr: float, delay: float,
             target_count: int, period_count: int) -> str:
    return runner.case_identifier(
        phase=phase, seed=seed, speed_mps=speed, snr_db=snr,
        delay_ns=delay, target_count=target_count, period_count=period_count,
    )


def _scientific_slots(
    case: Mapping[str, Any], methods: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    delay = float(case["delay_error_ns"])
    phase = str(case["phase"])
    roles = ("ON", "TO") if phase == "target_present" else ("ON",)
    selected = [
        (method_id, spec) for method_id, spec in methods.items()
        if float(spec.get("delay_ns", delay)) == delay
    ]
    if phase == "target_present" and delay == 0.0:
        selected = [(method_id, spec) for method_id, spec in selected if method_id == "A0"]
    if not selected:
        raise ValueError(f"no registered V4 methods apply to {phase} delay={delay}")
    rows: list[dict[str, Any]] = []
    for method_id, spec in selected:
        diagnostic = method_id == "M1_BATCH10_DIAGNOSTIC"
        for role in roles:
            runner_method_id = _V4_REGISTERED_TO_RUNNER[method_id]
            if method_id == "A0":
                input_role = f"A0_{role}"
                estimator_input_role = None
            elif method_id == "M1_BATCH10_DIAGNOSTIC":
                input_role = "A1_ON"
                estimator_input_role = "same_case_A1_ON_full_period_batch_diagnostic"
            elif method_id in {"HUBER_D3_CAUSAL_PREFIX", "M1_CAUSAL_PREFIX"}:
                input_role = f"A1_{role}_causally_corrected"
                estimator_input_role = "same_case_A1_ON_raw_C1_C2_period_prefix_0_to_k"
            elif method_id == "PK_KNOWN":
                input_role = f"A1_{role}_known_error_corrected"
                estimator_input_role = None
            elif method_id == "FIXED_D3":
                input_role = f"A1_{role}_fixed_D3_corrected"
                estimator_input_role = None
            else:
                input_role = f"A1_{role}"
                estimator_input_role = None
            rows.append({
                "case_id": case["case_id"],
                "phase": phase,
                "seed": case["seed"],
                "speed_mps": case["speed_mps"],
                "delay_error_ns": delay,
                "method_id": method_id,
                "runner_method_id": runner_method_id,
                "method_family": str(spec.get("role") or method_id),
                "mode": "not_applicable",
                "role": role,
                "input_role": input_role,
                "estimator_information_input_role": estimator_input_role,
                "execution_role": "DIAGNOSTIC_ESTIMATE" if diagnostic else role,
                "execution_kind": "offline_estimator_diagnostic" if diagnostic else "production_evaluation",
                "production_required": not diagnostic,
                "comparison_eligibility": (
                    "offline_diagnostic_only" if diagnostic else
                    "known_error_upper_bound" if method_id == "PK_KNOWN" else
                    "registered_candidate"
                ),
                "is_primary_comparison": method_id in {
                    "FIXED_D3", "HUBER_D3_CAUSAL_PREFIX", "M1_CAUSAL_PREFIX",
                },
                "uses_delay_truth": method_id == "PK_KNOWN",
                "truth_use_scope": "evaluator_only" if method_id == "PK_KNOWN" else "none",
                "required_outputs": (
                    ["period_prefix_estimate", "source_input_identity"] if diagnostic else
                    ["detection", "parameter_metrics", "track_manager", "cfar_geometry"]
                    + (["target_complex_response"] if phase == "target_present" and role == "TO" else [])
                    + (["h0_false_alarm_metrics"] if phase == "h0" else [])
                ),
                "required_task_metrics": (
                    ["correct_detection_opportunity", "position_error", "parameter_coverage",
                     "false_detection_and_track_events", "target_response"]
                    if phase == "target_present" else
                    ["valid_cut_count", "detection_count", "cluster_count",
                     "false_confirmed_track_count", "h0_event_rate"]
                ),
            })
    return rows


def _production_templates() -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Enumerate V4 runner calls and role semantics; no binaries are run."""
    templates: list[dict[str, Any]] = []
    per_delay: dict[str, int] = {}
    for delay in (0.0, 4.0):
        selected = runner._v4_calibration_method_ids(delay)
        contracts = [{"method_id": "A0"}]
        if selected:
            contracts.extend(calibration.build_method_contract(
                calibration.load_pilot_config(), method_ids=selected,
            ))
        delay_templates: list[dict[str, Any]] = []
        for method in contracts:
            method_id = str(method["method_id"])
            modes = ("Mode-A", "Mode-B") if method_id in calibration.REFERENCE_FIT_METHOD_IDS else ("not_applicable",)
            for mode in modes:
                for role in _PRODUCTION_ROLES:
                    branch_name = (
                        f"{method_id}_{mode.replace('-', '').lower()}_{role}"
                        if mode != "not_applicable" else f"{method_id}_{role}"
                    )
                    row = {
                        "method_id": method_id, "mode": mode, "role": role,
                        "branch_name_template": branch_name,
                        "execution_condition": f"+{int(delay)}ns",
                        "call_source": "calibration._run_case",
                        "registered_method_ids": [
                            registered for registered, actual in _V4_REGISTERED_TO_RUNNER.items()
                            if actual == method_id
                        ],
                        "classification": "runner_production_branch",
                        "role_policy": (
                            "phase-dependent: registered target-present ON/TO or H0 ON; "
                            "OFF and targetless TO are internal auxiliary branches"
                        ),
                    }
                    delay_templates.append(row)
        per_delay[str(int(delay))] = len(delay_templates)
        templates.extend(delay_templates)
    result = templates
    for method_id, qualification in (
        ("R22_R9_Fixed_D3", "registered_at_plus_four_ns"),
        ("HUBER_D3_CAUSAL_PREFIX", "registered_at_plus_four_ns"),
        ("M1_CAUSAL_PREFIX", "registered_at_plus_four_ns"),
    ):
        for role in _PRODUCTION_ROLES:
            result.append({
                "method_id": method_id, "mode": "not_applicable", "role": role,
                "branch_name_template": f"{method_id}_{role}",
                "call_source": "run_r22_cuda_pilot V4 correction path",
                "execution_condition": "+4ns",
                "qualification_policy": qualification,
                "registered_method_ids": [
                    registered for registered, actual in _V4_REGISTERED_TO_RUNNER.items()
                    if actual == method_id
                ],
                "classification": "production_correction_or_causal_baseline",
                "role_policy": (
                    "registered target-present ON/TO or H0 ON; "
                    "OFF and targetless TO are internal auxiliary branches"
                ),
            })
    return result, per_delay


def build_v4_execution_plan(design: Mapping[str, Any]) -> dict[str, Any]:
    """Expand the DRAFT design and current runner behavior into an auditable plan."""
    if design.get("schema_version") != 1:
        raise ValueError("unsupported V4 schema_version")
    if design.get("execution_authorized") is not False or design.get("cuda_authorized") is not False:
        raise ValueError("V4 frozen design must remain non-executable and CUDA unauthorized")
    production = design.get("production")
    target = design.get("target_present")
    h0 = design.get("h0")
    if not all(isinstance(item, Mapping) for item in (production, target, h0)):
        raise ValueError("V4 production, target_present, and h0 sections are required")
    period_count = int(production.get("period_count", 0))
    target_count = int(target.get("target_count", -1))
    if period_count <= 0 or target_count <= 0 or int(h0.get("target_count", -1)) != 0:
        raise ValueError("V4 target/period counts are invalid")
    target_seeds = _seed_range(target, "seed_range_inclusive", "seed_count")
    h0_seeds = _seed_range(h0, "candidate_seed_range_inclusive", "seed_count")
    collision = sorted(set(target_seeds) & set(h0_seeds))
    if collision:
        raise ValueError(f"target-present and H0 seed ranges overlap: {collision}")
    speeds = _number_list(target, "radial_speed_mps")
    delays = _number_list(target, "delay_error_ns")
    h0_delays = _number_list(h0, "delay_error_ns")
    if delays != h0_delays:
        raise ValueError("target-present and H0 delay conditions differ")
    if int(h0.get("period_count", period_count)) != period_count:
        raise ValueError("H0 period_count differs from Production design")
    target_methods = _methods(target, required_ids={
        "A0", "C0", "FIXED_D3", "HUBER_D3_CAUSAL_PREFIX",
        "M1_CAUSAL_PREFIX", "M1_BATCH10_DIAGNOSTIC", "PK_KNOWN",
    })
    h0_methods = _methods(h0, required_ids=set(target_methods))
    if set(target_methods) != set(h0_methods):
        raise ValueError("target-present and H0 method identities differ")
    if float(target.get("target_snr_db")) != 20.0:
        raise ValueError("V4 target SNR differs from its declared confirmatory condition")

    case_identities: list[dict[str, Any]] = []
    for seed in target_seeds:
        for speed in speeds:
            for delay in delays:
                case_identities.append({
                    "case_id": _case_id("confirmatory", seed, speed, float(target["target_snr_db"]),
                                         delay, target_count, period_count),
                    "phase": "target_present", "runner_phase": "confirmatory",
                    "seed": seed, "speed_mps": speed,
                    "snr_db": float(target["target_snr_db"]), "delay_error_ns": delay,
                    "target_count": target_count, "period_count": period_count,
                    "beam": int(production["beam"]),
                    "area_clutter": dict(production["area_clutter"]),
                    "stage2_roles": ["A0_ON", "A0_OFF", "A0_TO", "A1_ON", "A1_OFF", "A1_TO"],
                    "paired_background_required": True,
                })
    for seed in h0_seeds:
        for delay in h0_delays:
            case_identities.append({
                "case_id": _case_id("h0", seed, 0.0, float(target["target_snr_db"]),
                                     delay, 0, period_count),
                "phase": "h0", "runner_phase": "h0", "seed": seed, "speed_mps": 0.0,
                "snr_db": float(target["target_snr_db"]), "delay_error_ns": delay,
                "target_count": 0, "period_count": period_count,
                "beam": int(production["beam"]),
                "area_clutter": dict(production["area_clutter"]),
                "stage2_roles": ["A0_ON", "A0_OFF", "A0_TO", "A1_ON", "A1_OFF", "A1_TO"],
                "paired_background_required": True,
            })
    ids = [row["case_id"] for row in case_identities]
    if len(ids) != len(set(ids)):
        raise ValueError("V4 case identity collision")
    if len([row for row in case_identities if row["phase"] == "target_present"]) != int(target["case_count"]):
        raise ValueError("target-present case_count differs from expanded design")
    if len([row for row in case_identities if row["phase"] == "h0"]) != int(h0["case_count"]):
        raise ValueError("H0 case_count differs from expanded design")

    registered = [
        slot for case in case_identities
        for slot in _scientific_slots(
            case, target_methods if case["phase"] == "target_present" else h0_methods,
        )
    ]
    registered_counts = {
        phase: sum(row["phase"] == phase for row in registered)
        for phase in ("target_present", "h0")
    }
    if (registered_counts["target_present"] != int(target["production_branch_count"])
            or registered_counts["h0"] != int(h0["production_branch_count"])):
        raise ValueError(
            "registered method-role slot totals differ from frozen design; do not silently change design"
        )

    templates, calls_by_delay = _production_templates()
    stage2_counts_by_delay = {
        "0": {"minimum": 4, "maximum": 6},
        "4": {"minimum": 6, "maximum": 6},
    }
    call_min: dict[str, int] = {"target_present": 0, "h0": 0}
    call_max: dict[str, int] = {"target_present": 0, "h0": 0}
    stage2_total = 0
    stage2_max_total = 0
    for case in case_identities:
        phase, delay = case["phase"], int(case["delay_error_ns"])
        stage2_total += stage2_counts_by_delay[str(delay)]["minimum"]
        stage2_max_total += stage2_counts_by_delay[str(delay)]["maximum"]
        base = calls_by_delay[str(delay)]
        # The batch M1 estimate is diagnostic only. It creates no Production
        # branches. Fixed D3 and the two period-causal methods add three roles
        # each at +4 ns.
        extras = 0 if delay == 0 else 9
        call_min[phase] += base + extras
        call_max[phase] += base + extras
    counts = {
        "target_present": sum(row["phase"] == "target_present" for row in case_identities),
        "h0": sum(row["phase"] == "h0" for row in case_identities),
    }
    declared_total = sum(registered_counts.values())
    diag_total = sum(row["execution_kind"] == "offline_estimator_diagnostic" for row in registered)
    registered_production_counts = {
        phase: sum(
            row["phase"] == phase and row["production_required"]
            for row in registered
        )
        for phase in ("target_present", "h0")
    }
    actual_calls_by_phase = dict(call_min)
    auxiliary_calls_by_phase = {
        phase: actual_calls_by_phase[phase] - registered_production_counts[phase]
        for phase in actual_calls_by_phase
    }
    if any(count < 0 for count in auxiliary_calls_by_phase.values()):
        raise ValueError("registered production slots exceed source-derived runner calls")
    zero_cases = sum(int(row["delay_error_ns"]) == 0 for row in case_identities)
    plus_four_cases = sum(int(row["delay_error_ns"]) == 4 for row in case_identities)
    actual_per_case = {"0": calls_by_delay["0"], "4": calls_by_delay["4"] + 9}
    calls_by_delay_phase = {
        phase: {
            "zero_delay": sum(
                row["phase"] == phase and float(row["delay_error_ns"]) == 0.0
                for row in case_identities
            ) * actual_per_case["0"],
            "plus_four_ns": sum(
                row["phase"] == phase and float(row["delay_error_ns"]) == 4.0
                for row in case_identities
            ) * actual_per_case["4"],
        }
        for phase in ("target_present", "h0")
    }
    for phase, breakdown in calls_by_delay_phase.items():
        breakdown["total"] = sum(breakdown.values())
    return {
        "design_id": design["design_id"], "design_status": design.get("status"),
        "execution_authorized": False, "cuda_authorized": False,
        "case_counts": counts, "case_identities": case_identities,
        "registered_branch_counts": {**registered_counts, "total": declared_total},
        "registered_science_branches": registered,
        "registered_slot_semantics": (
            "frozen-design method/role slots; M1_BATCH10_DIAGNOSTIC slots are estimates only, "
            "not Production branches or ranking observations"
        ),
        "offline_diagnostic_slot_count": diag_total,
        "registered_production_branch_counts": {
            **registered_production_counts,
            "total": sum(registered_production_counts.values()),
        },
        "internal_auxiliary_production_call_counts": {
            **auxiliary_calls_by_phase,
            "total": sum(auxiliary_calls_by_phase.values()),
            "semantics": (
                "source-derived runner branches outside registered method-role ranking slots; "
                "includes OFF support, paired A0 reference at +4ns, and targetless TO calls"
            ),
        },
        "production_branch_templates": templates,
        "production_branch_call_counts": {
            "total": sum(call_min.values()),
            "target_present": call_min["target_present"],
            "h0": call_min["h0"],
            "maximum": sum(call_max.values()),
            "target_present_maximum": call_max["target_present"],
            "h0_maximum": call_max["h0"],
            "registered_production_slots": sum(registered_production_counts.values()),
            "internal_auxiliary_calls": sum(auxiliary_calls_by_phase.values()),
            "zero_delay_case_count": zero_cases,
            "plus_four_ns_case_count": plus_four_cases,
            "per_case_by_delay": actual_per_case,
            "by_phase_and_delay": calls_by_delay_phase,
            "source_contract": (
                "V4-specific run_case selection: A0 only at 0ns; A0+C0+PK at +4ns; "
                "three correction methods add three roles each at +4ns; batch M1 is diagnostic"
            ),
        },
        "stage2_call_counts": {
            "minimum_total": stage2_total, "maximum_total": stage2_max_total,
            "zero_delay_case_calls": stage2_counts_by_delay["0"],
            "plus_four_ns_case_calls": stage2_counts_by_delay["4"],
            "identity_note": (
                "zero-delay A1_OFF/A1_TO are reused only when generated scenario payloads match; "
                "+4 ns materializes six roles"
            ),
        },
        "target_truth_policy": {
            "target_present_sources": ["A0_ON", "A1_ON", "A0_TO", "A1_TO"],
            "identity": ["seed", "period", "target_id", "beam", "pulse"],
            "values": ["range", "Doppler", "visibility", "target_state"],
            "h0": "no target truth or target-only TO response",
        },
        "storage_policy": dict(design.get("storage_policy", {})),
        "not_executed": ["Stage2", "Native CUDA Production", "V4 pilot", "confirmatory matrix"],
    }


def validate_v4_execution_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    cases = plan.get("case_identities")
    slots = plan.get("registered_science_branches")
    if not isinstance(cases, list) or not isinstance(slots, list):
        raise ValueError("V4 plan lacks expanded case/branch identities")
    case_ids = [str(row.get("case_id") or "") for row in cases if isinstance(row, Mapping)]
    if len(case_ids) != len(cases) or not all(case_ids) or len(case_ids) != len(set(case_ids)):
        raise ValueError("V4 plan case identities are missing or duplicated")
    slot_keys = [(
        str(row.get("case_id")), str(row.get("method_id")), str(row.get("mode")),
        str(row.get("role")), str(row.get("execution_role")),
    ) for row in slots if isinstance(row, Mapping)]
    if len(slot_keys) != len(slots) or len(slot_keys) != len(set(slot_keys)):
        raise ValueError("V4 plan branch slot identities are missing or duplicated")
    known_cases = set(case_ids)
    if any(row[0] not in known_cases for row in slot_keys):
        raise ValueError("V4 branch slot references an unknown case")
    if any(bool(row.get("uses_delay_truth")) != (row.get("method_id") == "PK_KNOWN") for row in slots):
        raise ValueError("V4 known-error truth scope is inconsistent")
    if any(row.get("phase") == "h0" and row.get("role") != "ON" for row in slots):
        raise ValueError("H0 registered roles must be ON only")
    if any(row.get("phase") == "h0" and row.get("method_id") == "M1_BATCH10_DIAGNOSTIC"
           and row.get("production_required") for row in slots):
        raise ValueError("H0 batch M1 diagnostic cannot be a Production branch")
    if plan.get("execution_authorized") is not False or plan.get("cuda_authorized") is not False:
        raise ValueError("V4 expansion must remain non-executable")
    return {"case_count": len(cases), "registered_slot_count": len(slots), "status": "passed"}


def expected_v4_production_branch_identities(
    plan: Mapping[str, Any], case_id: str,
) -> list[tuple[str, str, str, str]]:
    """Return source-derived (method, mode, role, branch) keys for one case."""
    cases = plan.get("case_identities")
    templates = plan.get("production_branch_templates")
    if not isinstance(cases, list) or not isinstance(templates, list):
        raise ValueError("V4 plan lacks cases or production branch templates")
    case = next((row for row in cases if isinstance(row, Mapping)
                 and str(row.get("case_id")) == str(case_id)), None)
    if case is None:
        raise ValueError(f"unknown V4 case identity: {case_id}")
    delay = int(float(case["delay_error_ns"]))
    expected_condition = f"+{delay}ns" if delay else "+0ns"
    expected: list[tuple[str, str, str, str]] = []
    for row in templates:
        if not isinstance(row, Mapping):
            raise ValueError("invalid V4 branch template")
        condition = row.get("execution_condition")
        if condition is not None and str(condition) != expected_condition:
            continue
        expected.append((
            str(row.get("method_id") or ""),
            str(row.get("mode") or "not_applicable"),
            str(row.get("role") or ""),
            str(row.get("branch_name_template") or ""),
        ))
    if not expected or any(not all(key) for key in expected):
        raise ValueError(f"V4 case has an invalid or empty Production branch plan: {case_id}")
    if len(expected) != len(set(expected)):
        raise ValueError(f"V4 Production branch plan contains duplicate identities: {case_id}")
    return sorted(expected)


def validate_v4_actual_method_records(
    plan: Mapping[str, Any], case_id: str,
    actual_records: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reject omitted, duplicated, or unregistered Production method records."""
    from collections import Counter

    expected = expected_v4_production_branch_identities(plan, case_id)
    actual: list[tuple[str, str, str, str]] = []
    for row in actual_records:
        if str(row.get("role") or "") not in _PRODUCTION_ROLES:
            continue
        actual.append((
            str(row.get("method_id") or ""),
            str(row.get("mode") or "not_applicable"),
            str(row.get("role") or ""),
            str(row.get("branch_name") or ""),
        ))
    expected_counts, actual_counts = Counter(expected), Counter(actual)
    if expected_counts != actual_counts:
        missing = list((expected_counts - actual_counts).elements())
        unexpected = list((actual_counts - expected_counts).elements())
        raise ValueError(
            f"V4 actual Production branches differ from registered plan for {case_id}: "
            f"missing={missing}, unexpected_or_duplicate={unexpected}"
        )
    return {
        "case_id": case_id,
        "expected_production_branch_count": len(expected),
        "actual_production_branch_count": len(actual),
        "status": "passed",
    }
