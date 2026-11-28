"""Offline replay must preserve circular, seed and discrete-feature semantics."""

import json
import math
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/phase_r/reanalyze_observability.py"


def manifest_fixture():
    names = ["cross_channel_phase_rad", "f1_f2_coherence",
             "clutter_doppler_ridge_hz", "clutter_ridge_vs_angle_slope_hz_per_deg"]
    records = []
    for seed in (11, 22):
        for state in ("channel_delay_error", "servo_angle_error"):
            values = {
                "zero": [3.0, 0.5, 0.0, 0.0],
                "plus": ([-math.pi + 0.02, 0.4, 10.0 if seed == 11 else 0.0, 0.0]
                         if state == "channel_delay_error" else [0.12, 0.6, 0.0, 0.0]),
                "minus": ([math.pi - 0.02, 0.6, 0.0, 0.0]
                          if state == "channel_delay_error" else [0.10, 0.4, 0.0, 0.0]),
            }
            for sign, vector in values.items():
                records.append({
                    "parameter": state, "seed": seed, "sign": sign,
                    "value": {"zero": 0.0, "plus": 0.5, "minus": -0.5}[sign],
                    "status": "passed", "observables": dict(zip(names, vector)),
                })
    return {"parameters": ["channel_delay_error", "servo_angle_error"],
            "seeds": [11, 22], "observable_names": names,
            "perturbations": {"channel_delay_error": 0.5, "servo_angle_error": 0.5},
            "cases": records}


def run_cli(tmp_path, data):
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "result"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--manifest", str(source), "--output-dir", str(output)],
        cwd=ROOT, text=True, capture_output=True,
    )
    return result, source, output


def test_reanalysis_replays_short_arc_and_preserves_seed_differences(tmp_path):
    result, source, output = run_cli(tmp_path, manifest_fixture())
    assert result.returncode == 0, result.stderr
    report = json.loads((output / "summary.json").read_text())
    assert (output / "source_manifest.json").read_bytes() == source.read_bytes()
    corrected = report["analyses"]["circular_all"]
    assert corrected["seed_median"]["raw_matrix"][0] == pytest.approx([0.04, 0.02])
    assert corrected["seed_11"]["raw_matrix"][2][0] == pytest.approx(10.0)
    assert corrected["seed_22"]["raw_matrix"][2][0] == pytest.approx(0.0)
    legacy = report["analyses"]["legacy_linear_all"]["seed_median"]
    assert legacy["raw_matrix"][0][0] == pytest.approx(-2 * math.pi + 0.04)
    filtered = report["analyses"]["circular_without_fft_argmax"]["seed_median"]
    assert filtered["observable_names"] == ["cross_channel_phase_rad", "f1_f2_coherence"]
    assert filtered["scaled_rank"] == 2
    assert len(report["phase_wrap_crossings"]) == 2


@pytest.mark.parametrize("damage", ["missing", "duplicate", "nonfinite", "wrong_step"])
def test_reanalysis_rejects_incomplete_or_mispaired_inputs(tmp_path, damage):
    data = manifest_fixture()
    if damage == "missing":
        data["cases"].pop()
    elif damage == "duplicate":
        data["cases"].append(data["cases"][0])
    elif damage == "nonfinite":
        data["cases"][0]["observables"]["f1_f2_coherence"] = None
    else:
        data["cases"][1]["value"] = 0.25
    result, _, output = run_cli(tmp_path, data)
    assert result.returncode != 0
    assert not output.exists()


def test_reanalysis_preserves_an_existing_result_directory(tmp_path):
    output = tmp_path / "result"
    output.mkdir()
    sentinel = output / "summary.json"
    sentinel.write_text("retained evidence", encoding="utf-8")
    result, _, _ = run_cli(tmp_path, manifest_fixture())
    assert result.returncode != 0
    assert sentinel.read_text() == "retained evidence"
