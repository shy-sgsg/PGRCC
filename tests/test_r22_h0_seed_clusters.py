from __future__ import annotations

import importlib

import pytest


def _aggregate():
    try:
        module = importlib.import_module("scripts.phase_r.analyze_r22_h0_seed_clusters")
    except ImportError as exc:
        pytest.fail(f"R22 H0 seed-cluster analyzer is not implemented: {exc}")
    return module.aggregate_h0_rows


def _row(seed: int, delay_ns: float, hits: int, *, method: str = "C0") -> dict[str, str]:
    return {
        "seed": str(seed),
        "delay_ns": str(delay_ns),
        "method_id": method,
        "mode": "not_applicable",
        "role": "OFF",
        "cut_band_mode": "union_full_band",
        "observed_cut_hit_count": str(hits),
        "valid_cut_count": "10",
        "status": "EVALUABLE_DESCRIPTIVE_H0_SEED",
        "h0_scope": "independent_target_free_seed",
    }


def test_review_manifest_paths_do_not_expose_machine_local_roots(tmp_path):
    module = importlib.import_module("scripts.phase_r.analyze_r22_h0_seed_clusters")

    assert module._display_path(module.ROOT / "docs/research/RESEARCH_MASTER_PLAN.md") == (
        "docs/research/RESEARCH_MASTER_PLAN.md"
    )
    assert module._display_path(tmp_path / "machine" / "archive.zip") == (
        "<external>/archive.zip"
    )


def _execution_identity_record():
    return {
        "source_identity": {
            "commit": "a" * 40,
            "files": {"scripts/phase_r/runner.py": {"sha256": "d" * 64}},
        },
        "stage2_binary": {"sha256": "b" * 64},
        "production_binary": {
            "sha256": "c" * 64,
            "backend": "local Native CUDA",
            "use_corex": False,
        },
        "native_cuda_preflight": {"gpu": {"available": True}},
        "status_labels": {
            "IMPLEMENTED": True,
            "EXECUTED": True,
            "EVALUABLE": True,
            "PERFORMANCE_VERIFIED": False,
        },
    }


def test_execution_identity_requires_native_cuda_and_is_consistent():
    module = importlib.import_module("scripts.phase_r.analyze_r22_h0_seed_clusters")
    baseline = module._validate_execution_identity(_execution_identity_record())

    assert baseline["production_binary_sha256"] == "c" * 64
    assert baseline["use_corex"] is False
    assert module._validate_execution_identity(_execution_identity_record(), baseline) == baseline

    changed = _execution_identity_record()
    changed["production_binary"]["sha256"] = "e" * 64
    with pytest.raises(ValueError, match="execution identity differs"):
        module._validate_execution_identity(changed, baseline)


@pytest.mark.parametrize(
    "section, changes, message",
    [
        ("production_binary", {"backend": "CPU"}, "local Native CUDA"),
        ("production_binary", {"use_corex": True}, "USE_COREX=false"),
        ("native_cuda_preflight", {"gpu": {"available": False}}, "CUDA GPU preflight"),
        ("status_labels", {"EVALUABLE": False}, "EVALUABLE"),
    ],
)
def test_execution_identity_rejects_unqualified_runtime(section, changes, message):
    module = importlib.import_module("scripts.phase_r.analyze_r22_h0_seed_clusters")
    record = _execution_identity_record()
    record[section].update(changes)

    with pytest.raises(ValueError, match=message):
        module._validate_execution_identity(record)


def test_h0_ci_and_delay_difference_resample_paired_seed_rates():
    rows = [
        _row(1, 0, 1), _row(2, 0, 9),
        _row(1, 4, 2), _row(2, 4, 8),
    ]

    result = _aggregate()(
        rows, expected_seeds=[1, 2], cut_bands=("union_full_band",), resamples=10_000,
    )
    by_delay = {row["delay_ns"]: row for row in result["per_condition"]}
    paired = result["paired_delay"][0]

    assert by_delay[0.0]["mean_seed_fraction"] == pytest.approx(0.5)
    assert by_delay[0.0]["ci95_low"] == pytest.approx(0.1)
    assert by_delay[0.0]["ci95_high"] == pytest.approx(0.9)
    assert paired["mean_delta_plus4_minus0"] == pytest.approx(0.0)
    assert paired["ci95_low"] == pytest.approx(-0.1)
    assert paired["ci95_high"] == pytest.approx(0.1)
    assert paired["n_seed"] == 2


def test_h0_incomplete_seed_set_has_no_complete_confidence_interval():
    result = _aggregate()(
        [_row(1, 0, 1), _row(2, 0, 9)],
        expected_seeds=[1, 2, 3],
        cut_bands=("union_full_band",),
        resamples=100,
    )

    summary = result["per_condition"][0]
    assert summary["status"] == "INCOMPLETE"
    assert summary["n_seed"] == 2
    assert summary["mean_seed_fraction"] == pytest.approx(0.5)
    assert summary["ci95_low"] is None
    assert summary["ci95_high"] is None


def test_zero_delay_inapplicable_method_does_not_get_a_paired_comparison():
    rows = [_row(seed, 4, hits, method="P1") for seed, hits in ((1, 2), (2, 4))]

    result = _aggregate()(
        rows,
        expected_seeds=[1, 2],
        not_applicable_zero_delay={"P1"},
        resamples=100,
    )

    zero = next(row for row in result["per_condition"] if row["delay_ns"] == 0.0)
    assert zero["status"] == "NOT_APPLICABLE_ZERO_DELAY_REFERENCE"
    assert zero["n_seed"] == 0
    assert result["paired_delay"][0]["status"] == "NOT_APPLICABLE_ZERO_DELAY_REFERENCE"
    assert result["paired_delay"][0]["mean_delta_plus4_minus0"] is None


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"role": "ON"}, "role OFF"),
        ({"valid_cut_count": "0"}, "valid_cut_count must be positive"),
        ({"observed_cut_hit_count": "11"}, "hit count cannot exceed valid CUT count"),
    ],
)
def test_h0_aggregator_rejects_invalid_seed_rows(changes, message):
    row = _row(1, 0, 1)
    row.update(changes)

    with pytest.raises(ValueError, match=message):
        _aggregate()([row], expected_seeds=[1])


def test_h0_aggregator_rejects_duplicate_seed_rows():
    rows = [_row(1, 0, 1), _row(1, 0, 2)]

    with pytest.raises(ValueError, match="duplicate H0 seed row"):
        _aggregate()(rows, expected_seeds=[1])
