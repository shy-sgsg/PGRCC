from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "scripts/phase_r/run_r8_multiseed_causal_pilot.py"


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    if not RUNNER_PATH.is_file():
        pytest.skip("R8 runner implementation is not present yet")
    name = "phase_r8_runner_under_test"
    spec = importlib.util.spec_from_file_location(name, RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def test_r8_runner_script_exists() -> None:
    assert RUNNER_PATH.is_file(), f"R8 runner has not been implemented: {RUNNER_PATH}"


def test_invalid_seed_is_rejected_before_running_stage1(runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def unexpected_run(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal called
        called = True
        return {"status": "completed"}

    monkeypatch.setattr(runner.r3_runner, "run_abc_extension", unexpected_run)
    with pytest.raises(ValueError, match="seed must be 202 or 303"):
        runner.run_one_seed(tmp_path / "seed101", 101)
    assert called is False


def test_wrong_production_binary_is_rejected_before_running_stage1(runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stale_binary = ROOT / "build/GMTI_pipe_core"
    assert stale_binary.is_file()
    called = False

    def unexpected_run(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal called
        called = True
        return {"status": "completed"}

    monkeypatch.setattr(runner.r3_runner, "run_abc_extension", unexpected_run)
    with pytest.raises(ValueError, match="Production binary SHA-256 mismatch"):
        runner.run_one_seed(tmp_path / "seed202", 202, stale_binary)
    assert called is False


def test_nonempty_output_root_is_rejected_before_running_stage1(runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output_root = tmp_path / "seed202"
    output_root.mkdir()
    (output_root / "keep.txt").write_text("existing evidence", encoding="utf-8")
    called = False

    def unexpected_run(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal called
        called = True
        return {"status": "completed"}

    monkeypatch.setattr(runner.r3_runner, "run_abc_extension", unexpected_run)
    with pytest.raises(ValueError, match="refusing to overwrite nonempty output root"):
        runner.run_one_seed(output_root, 202)
    assert called is False
    assert (output_root / "keep.txt").read_text(encoding="utf-8") == "existing evidence"


def test_verified_binary_is_used_by_both_production_modules_and_recorded(runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = ROOT / "build_r7/GMTI_pipe_core"
    assert binary.is_file()
    output_root = tmp_path / "seed202"
    observed: dict[str, object] = {}

    def fake_run(output: Path, seed: int) -> dict[str, object]:
        observed["seed"] = seed
        observed["stage1_binary"] = runner.stage1.PIPE.resolve()
        observed["production_binary"] = runner.e2e.PIPE.resolve()
        output.mkdir(parents=True, exist_ok=True)
        (output / "manifest.json").write_text(
            json.dumps({"status": "production_comparisons_executed"}),
            encoding="utf-8",
        )
        return {"status": "production_comparisons_executed", "case_status": "production_comparisons_executed"}

    monkeypatch.setattr(runner.r3_runner, "run_abc_extension", fake_run)
    manifest = runner.run_one_seed(output_root, 202)

    assert observed == {
        "seed": 202,
        "stage1_binary": binary.resolve(),
        "production_binary": binary.resolve(),
    }
    assert manifest["production_binary"]["sha256"] == runner.REQUIRED_PRODUCTION_SHA256
    saved = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))
    assert saved["production_binary"]["path"] == str(binary.resolve())
    assert saved["command"]["argv"][-4:] == [
        "--seed",
        "202",
        "--production-binary",
        str(binary.resolve()),
    ]


def test_completed_upstream_case_is_success_when_all_nine_branches_pass(runner: ModuleType) -> None:
    conditions = {
        condition: {
            "production": {
                role: {
                    "status": "passed",
                    "production_status": "passed",
                    "production_returncode": 0,
                    "production_path_gate": {"status": "passed"},
                }
                for role in ("ON", "OFF", "TO")
            }
        }
        for condition in (
            "A0_Ideal",
            "A1_Current_unknown_error",
            "A2_Known_error_correction_upper_bound",
        )
    }
    manifest = {
        "status": "completed",
        "case_status": "completed",
        "case_result": {"status": "completed", "conditions": conditions},
    }

    assert runner.successful_execution(manifest) is True


def test_completed_upstream_case_is_failure_when_any_branch_failed(runner: ModuleType) -> None:
    conditions = {
        condition: {
            "production": {
                role: {
                    "status": "passed",
                    "production_status": "passed",
                    "production_returncode": 0,
                    "production_path_gate": {"status": "passed"},
                }
                for role in ("ON", "OFF", "TO")
            }
        }
        for condition in (
            "A0_Ideal",
            "A1_Current_unknown_error",
            "A2_Known_error_correction_upper_bound",
        )
    }
    conditions["A1_Current_unknown_error"]["production"]["ON"]["status"] = "failed"
    manifest = {
        "status": "completed",
        "case_status": "completed",
        "case_result": {"status": "completed", "conditions": conditions},
    }

    assert runner.successful_execution(manifest) is False
