from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = (ROOT / "include/config_structs.hpp").read_text(encoding="utf-8")
LOADER = (ROOT / "src/loadXML.cpp").read_text(encoding="utf-8")
RUNTIME = (ROOT / "src/runtime_diagnostics.cpp").read_text(encoding="utf-8")
KERNEL = (ROOT / "src/gpu/gpu_kernels.cu").read_text(encoding="utf-8")
PROCESS = (ROOT / "src/processOnePeriod.cpp").read_text(encoding="utf-8")


def test_research_config_has_safe_defaults_without_touching_current_default() -> None:
    assert "bool research_calibration_enable = false" in CONFIG
    assert 'std::string research_calibration_method = "production_current"' in CONFIG
    assert "int research_calibration_min_support =" in CONFIG
    assert "int research_calibration_range_band_bins = 0" in CONFIG
    assert "double research_calibration_robust_phase_threshold_rad =" in CONFIG
    assert 'std::string csi_cancellation_mode = "legacy_min_magnitude"' in CONFIG


def test_loader_parses_and_validates_all_research_modes_strictly() -> None:
    assert "research_calibration_method" in LOADER
    for mode in (
        "production_current",
        "ordinary_subtraction",
        "scc",
        "ddc",
        "robust_ddc",
        "robust_ddc_rb",
    ):
        assert mode in CONFIG
    assert "validateResearchCalibrationConfig" in LOADER
    assert "research_calibration_enable" in LOADER
    assert "research_calibration_robust_phase_threshold_rad" in LOADER
    assert "invalid research calibration configuration" in LOADER


def test_runtime_snapshot_and_tap_provenance_are_explicit() -> None:
    for field in (
        "research_calibration_enable",
        "research_calibration_method",
        "research_calibration_min_support",
        "research_calibration_range_band_bins",
        "research_calibration_robust_phase_threshold_rad",
    ):
        assert field in RUNTIME
        assert f"{field} =" in RUNTIME
    assert "recordProductionCalibrationTap" in RUNTIME
    assert "truth_used_in_estimator" in RUNTIME


def test_shared_cuda_entry_has_disabled_guard_and_single_adapter_tap() -> None:
    entry = KERNEL[KERNEL.index("bool GMTIProcessor::clutter_cancel_38_paper_1_cuda") :]
    assert "if (cfg.research_calibration_enable)" in entry
    assert "research_output_ready" in entry
    assert "applyProductionCalibration" in entry
    assert "cuda_download_sync" in entry
    assert "cudaMemcpyAsync" in entry
    assert "NOT_EVALUABLE" in entry
    assert "return false" in entry

    calls = PROCESS.count("clutter_cancel_38_paper_1_cuda(")
    assert calls == 2, "non-fusion and fusion must share exactly two call sites"
    assert "applyProductionCalibration" not in PROCESS


def test_research_tap_is_after_range_phase_preparation() -> None:
    assert PROCESS.index("cuda_apply_rg_correction_async(phi_fit, Na, Nr)") < PROCESS.index(
        "clutter_cancel_38_paper_1_cuda("
    )
    assert PROCESS.index("cuda_apply_rg_correction_async(phi_fit, Na, Nr)", PROCESS.index(
        "bool GMTIProcessor::processOnePeriodFusionCache"
    )) < PROCESS.index(
        "clutter_cancel_38_paper_1_cuda(", PROCESS.index(
            "bool GMTIProcessor::processOnePeriodFusionCache"
        )
    )
