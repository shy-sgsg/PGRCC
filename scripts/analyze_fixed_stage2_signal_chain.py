#!/usr/bin/env python3
"""Audit signal/background levels and CSI ROI ratios for fixed Stage2 data.

This is analysis-only: it reads the existing truth, generation statistics and
saved CSI before/after power maps.  It never invokes the simulator.
"""

import argparse
import csv
import glob
import json
import math
from pathlib import Path

import numpy as np


def read_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def db(value):
    return 10.0 * math.log10(value) if value > 0 else math.nan


def finite_stats(values):
    data = np.asarray([x for x in values if math.isfinite(x)], dtype=float)
    if not len(data):
        return {"count": 0}
    return {
        "count": int(len(data)), "mean": float(data.mean()),
        "median": float(np.median(data)), "std": float(data.std(ddof=1)),
        "minimum": float(data.min()), "maximum": float(data.max()),
        "p05": float(np.percentile(data, 5)), "p95": float(np.percentile(data, 95)),
    }


def roi_stats(power, row, col):
    row_lo, row_hi = max(0, row - 16), min(power.shape[0], row + 17)
    col_lo, col_hi = max(0, col - 20), min(power.shape[1], col + 21)
    window = np.asarray(power[row_lo:row_hi, col_lo:col_hi], dtype=float)
    rr, cc = np.indices(window.shape)
    row0, col0 = row - row_lo, col - col_lo
    target = window[(np.abs(rr - row0) <= 2) & (np.abs(cc - col0) <= 2)]
    background = window[(np.abs(rr - row0) > 6) | (np.abs(cc - col0) > 6)]
    background_mean = float(np.mean(background))
    target_peak = float(np.max(target))
    target_mean = float(np.mean(target))
    return {
        "target_peak_power": target_peak,
        "target_roi_mean_power": target_mean,
        "background_mean_power": background_mean,
        "peak_to_background_db": db(target_peak / background_mean),
        "roi_mean_to_background_db": db(target_mean / background_mean),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--system-config", required=True, type=Path)
    parser.add_argument("--maps-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    config = json.loads(args.system_config.read_text(encoding="utf-8"))
    physical = config["design_contract"]["physical_calibration"]
    truth = [row for row in read_csv(args.dataset / "truth/truth_targets_by_beam.csv")
             if row.get("visible") == "1"]
    pulse_truth = [row for row in read_csv(args.dataset / "truth/truth_pulse.csv")
                   if row.get("injection_enabled") == "1"]
    generation = read_csv(args.dataset / "reports/period_raw_generation_stats.csv")

    pulse_width = float(physical["pulse_width_s"])
    bandwidth = float(physical["signal_bandwidth_hz"])
    time_bandwidth_product = pulse_width * bandwidth
    sampling_rate = float(config["waveform"]["fs_mhz"]) * 1.0e6
    discrete_chirp_samples = pulse_width * sampling_rate
    pulses = int(physical["pulse_count"])
    noise_power = float(config["scene"]["thermal_noise"]["noise_power"])
    quantization_noise_power = 1.0 / 6.0  # two rounded int16 components, 1/12 each
    configured_post_compression_scnr_db = float(
        config["design_contract"]["target_post_compression_snr_db"])
    target_amplitudes = np.asarray(
        [float(row["target_amplitude"]) for row in pulse_truth], dtype=float)
    # Use the values recorded by the generator itself.  The injector removes
    # sqrt(Tr*fs), not sqrt(Tr*Br), when mapping raw-LFM amplitude to its
    # configured compressed-cell SCNR.  Keeping BT and Tr*fs separate avoids
    # a hidden 10*log10(fs/Br)=0.792 dB error in the raw-domain audit.
    local_background_rms = np.asarray(
        [float(row["local_background_rms"]) for row in pulse_truth], dtype=float)
    local_clutter_power = np.maximum(0.0, local_background_rms ** 2 - noise_power)
    signal_power = target_amplitudes ** 2

    rows = []
    for item in truth:
        period = int(float(item["period_id"]))
        beam = int(float(item["beam_id"]))
        col = int(float(item["range_bin"]))
        af_total = float(item["af_total_truth_hz"])
        metric_dirs = glob.glob(str(
            args.maps_root / f"algorithm_result/period_{period:04d}/csi_metrics/*"))
        if not metric_dirs:
            raise SystemExit(f"missing CSI metrics for period {period}")
        metric_dir = Path(max(metric_dirs, key=lambda path: Path(path).stat().st_mtime))
        stem = f"period{period:03d}_beam{beam:03d}"
        fa_axis = np.load(metric_dir / f"{stem}_fa_axis_hz.npy")
        # The truth total Doppler can span multiple PRFs; select the nearest
        # circularly equivalent bin on the production recentered axis.
        row = int(np.argmin(np.abs((fa_axis - af_total + 650.0) % 1300.0 - 650.0)))
        before = np.load(metric_dir / f"{stem}_before_power.npy", mmap_mode="r")
        after = np.load(metric_dir / f"{stem}_after_power.npy", mmap_mode="r")
        before_stats = roi_stats(before, row, col)
        after_stats = roi_stats(after, row, col)
        rows.append({
            "period_id": period, "target_id": item["target_id"], "beam_id": beam,
            "truth_range_bin": col, "truth_row_file": item["row_truth"],
            "measured_row_from_production_fa_axis": row,
            "truth_af_total_hz": af_total,
            "production_fa_axis_hz": float(fa_axis[row]),
            **{f"before_{key}": value for key, value in before_stats.items()},
            **{f"after_{key}": value for key, value in after_stats.items()},
            "target_peak_change_db": db(
                after_stats["target_peak_power"] / before_stats["target_peak_power"]),
            "local_background_change_db": db(
                before_stats["background_mean_power"] /
                after_stats["background_mean_power"]),
        })

    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "target_csi_roi_signal_background.csv").open(
            "w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    raw_total_rms = np.asarray([float(row["rms_complex"]) for row in generation])
    raw_total_power = raw_total_rms ** 2
    chain = {
        "definitions": {
            "configured_target_scnr": "target / local (clutter + thermal noise)",
            "thermal_snr": "target / thermal noise only",
            "scr": "target / clutter only",
            "cnr": "clutter / thermal noise",
            "csi_roi_ratio": "saved production power in 5x5 target ROI / guarded 33x41 local background",
        },
        "raw_domain_amplitude": {
            "target_chirp_amplitude": finite_stats(target_amplitudes),
            "local_background_complex_rms": finite_stats(local_background_rms),
            "local_clutter_complex_rms": finite_stats(np.sqrt(local_clutter_power)),
            "thermal_noise_complex_rms": math.sqrt(noise_power),
            "thermal_noise_component_sigma": math.sqrt(noise_power / 2.0),
            "int16_rounding_complex_rms_approx": math.sqrt(quantization_noise_power),
            "file_total_complex_rms_20_channel_period_combinations": finite_stats(raw_total_rms),
        },
        "raw_domain_power": {
            "target_power": finite_stats(signal_power),
            "local_clutter_power": finite_stats(local_clutter_power),
            "thermal_noise_power_configured": noise_power,
            "int16_rounding_noise_power_approx": quantization_noise_power,
            "file_total_power_20_channel_period_combinations": finite_stats(raw_total_power),
        },
        "processing_gains": {
            "time_bandwidth_product_physical": time_bandwidth_product,
            "physical_pulse_compression_gain_db": db(time_bandwidth_product),
            "discrete_chirp_sample_count": discrete_chirp_samples,
            "simulator_numeric_point_to_background_gain_db": db(discrete_chirp_samples),
            "coherent_pulse_count": pulses,
            "coherent_integration_gain_db": db(pulses),
        },
        "configured_physical_ratios_db": {
            "thermal_snr_pre_compression_radar_equation": physical["pre_compression_cnr_db"],
            "thermal_snr_post_compression_radar_equation": physical["post_compression_thermal_snr_db"],
            "thermal_snr_post_coherent_radar_equation": physical["post_coherent_integration_snr_db"],
            "target_local_background_scnr_post_compression_injection": configured_post_compression_scnr_db,
            "target_local_background_scnr_post_coherent_ideal":
                configured_post_compression_scnr_db + db(pulses),
        },
        "generated_local_ratio_db": {
            "thermal_snr_raw": finite_stats(10.0 * np.log10(signal_power / noise_power)),
            "scr_raw": finite_stats(10.0 * np.log10(signal_power / local_clutter_power)),
            "cnr_raw": finite_stats(10.0 * np.log10(local_clutter_power / noise_power)),
            "scnr_raw": finite_stats(10.0 * np.log10(
                signal_power / (local_clutter_power + noise_power))),
            "thermal_snr_post_compression": finite_stats(
                10.0 * np.log10(signal_power / noise_power) + db(discrete_chirp_samples)),
            "scr_post_compression": finite_stats(
                10.0 * np.log10(signal_power / local_clutter_power) + db(discrete_chirp_samples)),
            "cnr_post_compression": finite_stats(
                10.0 * np.log10(local_clutter_power / noise_power)),
            "scnr_post_compression": finite_stats(
                10.0 * np.log10(signal_power / (local_clutter_power + noise_power)) +
                db(discrete_chirp_samples)),
            "scnr_post_coherent_ideal": finite_stats(
                10.0 * np.log10(signal_power / (local_clutter_power + noise_power)) +
                db(discrete_chirp_samples) + db(pulses)),
        },
        "measured_csi_roi_db": {
            "before_peak_to_background": finite_stats(
                [row["before_peak_to_background_db"] for row in rows]),
            "before_roi_mean_to_background": finite_stats(
                [row["before_roi_mean_to_background_db"] for row in rows]),
            "after_peak_to_background": finite_stats(
                [row["after_peak_to_background_db"] for row in rows]),
            "after_roi_mean_to_background": finite_stats(
                [row["after_roi_mean_to_background_db"] for row in rows]),
            "target_peak_change_after_csi": finite_stats(
                [row["target_peak_change_db"] for row in rows]),
            "local_background_improvement_after_csi": finite_stats(
                [row["local_background_change_db"] for row in rows]),
        },
    }
    (args.output_dir / "signal_chain_summary.json").write_text(
        json.dumps(chain, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(chain, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
