from pathlib import Path

import pytest

from scripts.phase_r.run_r7_minimal_production_matrix import (
    build_case_specs,
    diagnostic_off_required_outputs,
    local_test_echo_specs,
    local_test_period_records,
    period_input_files,
)


def test_period_input_files_returns_the_three_ordered_stage2_periods(tmp_path: Path):
    first = tmp_path / "stage2_statistical_newprotocol_period_0000.bin"
    for index in range(3):
        (tmp_path / f"stage2_statistical_newprotocol_period_{index:04d}.bin").write_bytes(b"x")

    assert period_input_files(first) == [
        tmp_path / "stage2_statistical_newprotocol_period_0000.bin",
        tmp_path / "stage2_statistical_newprotocol_period_0001.bin",
        tmp_path / "stage2_statistical_newprotocol_period_0002.bin",
    ]


def test_period_input_files_fails_closed_when_an_intermediate_period_is_missing(tmp_path: Path):
    first = tmp_path / "period_0000.bin"
    first.write_bytes(b"x")
    (tmp_path / "period_0002.bin").write_bytes(b"x")

    with pytest.raises(FileNotFoundError, match="period_0001.bin"):
        period_input_files(first)


def test_full_matrix_contains_a0_b_and_c_for_off_on_and_to_before_repeat():
    case_ids = {str(spec["case_id"]) for spec in build_case_specs()}

    assert case_ids == {
        "A0_OFF", "A0_ON", "A0_TO",
        "B_OFF", "B_ON", "B_TO",
        "C_OFF", "C_ON", "C_TO",
    }


def test_local_test_echo_specs_assign_unique_result_ids_without_file_offsets(tmp_path: Path):
    paths = [tmp_path / f"period_{index:04d}.bin" for index in range(3)]

    assert local_test_echo_specs(paths) == [
        f"1={paths[0]}",
        f"2={paths[1]}",
        f"3={paths[2]}",
    ]


def test_local_test_period_records_join_forced_id_and_cycle_log_records():
    start_lines = [
        f"[LOCAL-TEST] ===== {index + 1}/3 echo=/data/period_{index:04d}.bin "
        f"result_id={index + 1} ====="
        for index in range(3)
    ]
    cycle_lines = [
        f"[FILE][CYCLE] result_file_id={index + 1} period_index={index} "
        "algorithm_process_ms=10 end_to_end_ms=10 success=true"
        for index in range(3)
    ]
    log = "\n".join(
        line for pair in zip(start_lines, cycle_lines) for line in pair
    )

    assert local_test_period_records(log) == [
        {
            "sequence": index + 1,
            "total": 3,
            "input_path": f"/data/period_{index:04d}.bin",
            "result_id": index + 1,
            "cycle_result_id": index + 1,
            "period_index": index,
            "cycle_success": True,
        }
        for index in range(3)
    ]


def test_diagnostics_off_contract_requires_cycle_results_but_not_diagnostic_sidecars(
    tmp_path: Path,
):
    outputs = diagnostic_off_required_outputs(
        tmp_path / "result",
        tmp_path / "track_debug",
        tmp_path / "runtime.xml",
        tmp_path / "runtime.log",
        period_count=3,
    )

    assert set(outputs) == {
        "detection_results_GMTI01",
        "detection_results_GMTI02",
        "detection_results_GMTI03",
        "track_debug",
        "runtime_xml",
        "runtime_log",
    }
