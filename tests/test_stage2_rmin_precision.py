"""Regression check for the generated Production range-axis origin."""

from __future__ import annotations

import json
import math
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIMULATOR = ROOT / "build/simulate_stage2_statistical"
SCENARIO = ROOT / "simulator/scenarios/beam50_one_targets_continuous.run.json"
C_MPS = 299_792_458.0


def test_generated_rmin_preserves_stage2_physical_range_origin(tmp_path: Path) -> None:
    config = json.loads(SCENARIO.read_text(encoding="utf-8"))
    output_dir = tmp_path / "stage2"
    config["case_id"] = "rmin_precision_regression"
    config["output_dir"] = str(output_dir)
    config["random"]["period_count"] = 1
    config["random"]["beam_start"] = 50
    config["random"]["beam_count"] = 1
    config["random"]["random_seed"] = 1930102
    config["scene"]["area_clutter"]["enabled"] = False
    config["scene"]["line_scatterers"]["enabled"] = False
    config["scene"]["strong_scatterers"]["enabled"] = False
    config["scene"]["thermal_noise"]["enabled"] = False
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(config), encoding="utf-8")

    completed = subprocess.run(
        [str(SIMULATOR), "--config", str(scenario_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    runtime_xml = output_dir / "config/temp_config_stage2_newsystem.xml"
    assert runtime_xml.is_file(), runtime_xml
    root = ET.parse(runtime_xml).getroot()
    actual_origin_m = float(root.findtext(".//Rmin", "nan"))
    waveform = config["waveform"]
    range_processing = config["range_processing"]
    expected_origin_m = 0.5 * C_MPS * (
        range_processing["sample_delay_us"] * 1.0e-6
        + range_processing["range_crop_start"] / (waveform["fs_mhz"] * 1.0e6)
    )
    assert math.isclose(actual_origin_m, expected_origin_m, rel_tol=0.0, abs_tol=1.0e-8)
