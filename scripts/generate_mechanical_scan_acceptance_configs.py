#!/usr/bin/env python3
"""Derive deterministic mechanical-scan acceptance configs from production bases."""

import argparse
import copy
import json
from pathlib import Path


def set_output(cfg, run_root, name):
    cfg["case_id"] = name
    cfg["output_dir"] = str(run_root / name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--electronic-base", required=True, type=Path)
    parser.add_argument("--config-dir", required=True, type=Path)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    with args.base.open(encoding="utf-8") as stream:
        base = json.load(stream)
    with args.electronic_base.open(encoding="utf-8") as stream:
        electronic_base = json.load(stream)
    cases = {}

    m1 = copy.deepcopy(base)
    set_output(m1, args.run_root, "mechanical_m1_no_target")
    for target in m1.get("targets", []):
        target["enabled"] = False
    m1["scene"]["thermal_noise"] = {
        "enabled": False, "noise_power": 0.0, "include_target_only": True}
    cases["m1_no_target.json"] = m1

    m2 = copy.deepcopy(base)
    set_output(m2, args.run_root, "mechanical_m2_area_target")
    m2["scene_mode"] = "area_clutter_only"
    m2["scene"]["mode"] = "area_clutter_only"
    m2["scene"]["clutter_amplitude_scale"] = 1.0
    m2["scene"]["area_clutter"] = {
        "enabled": True,
        "model": "continuous_texture",
        "scatterer_count": 0,
        "mean_power": 0.001,
        "texture_sigma": 0.0,
        "spatial_cell_m": 30.0,
        "azimuth_subcell_count": 9,
    }
    cases["m2_area_target.json"] = m2

    m3 = copy.deepcopy(base)
    set_output(m3, args.run_root, "mechanical_m3_offcenter")
    m3["targets"][0]["init"]["azimuth_offset_deg"] = 0.8
    cases["m3_offcenter.json"] = m3

    m4_forward = copy.deepcopy(base)
    set_output(m4_forward, args.run_root, "mechanical_m4_forward")
    cases["m4_forward.json"] = m4_forward
    m4_reverse = copy.deepcopy(base)
    set_output(m4_reverse, args.run_root, "mechanical_m4_reverse")
    m4_reverse["mechanical_scan"]["scan_direction"] = "reverse"
    cases["m4_reverse.json"] = m4_reverse

    m5 = copy.deepcopy(base)
    set_output(m5, args.run_root, "mechanical_m5_paired")
    m5["random"]["random_seed"] = 20260812
    m5["random"]["four_channel_phase_center_mode"] = \
        "paired_same_phase_center"
    m5["scene"]["thermal_noise"] = {
        "enabled": False, "noise_power": 0.0, "include_target_only": True}
    cases["m5_mechanical_paired.json"] = m5
    m5_electronic = copy.deepcopy(electronic_base)
    set_output(m5_electronic, args.run_root, "electronic_m5_paired")
    m5_electronic["scan_mode"] = "electronic"
    cases["m5_electronic_paired.json"] = m5_electronic

    overlap = copy.deepcopy(base)
    set_output(overlap, args.run_root, "mechanical_overlap_130_65")
    overlap["mechanical_scan"]["scan_speed_deg_s"] = 10.0
    overlap["mechanical_scan"]["cpi_step_pulse"] = 65
    cases["overlap_130_65.json"] = overlap

    stationary = copy.deepcopy(base)
    set_output(stationary, args.run_root, "mechanical_stationary_target")
    stationary["targets"][0]["motion"] = {
        "type": "enu_velocity", "ve_mps": 0.0, "vn_mps": 0.0}
    cases["stationary_target.json"] = stationary

    tracking = copy.deepcopy(base)
    set_output(tracking, args.run_root, "mechanical_tracking_3scan")
    tracking["random"]["period_count"] = 3
    cases["tracking_3scan.json"] = tracking

    args.config_dir.mkdir(parents=True, exist_ok=True)
    for name, cfg in cases.items():
        path = args.config_dir / name
        if path.exists() and not args.force:
            raise SystemExit(f"refusing to overwrite {path}; pass --force")
        with path.open("w", encoding="utf-8") as stream:
            json.dump(cfg, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(path)


if __name__ == "__main__":
    main()
