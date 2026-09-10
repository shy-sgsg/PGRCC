"""验证 reusable background 的物理 theta 映射和异常布局拒绝。"""

from __future__ import annotations

import json
import csv
import math
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIMULATOR = ROOT / "build/simulate_stage2_statistical"


def packet(theta_deg: float, packet_bytes: int, counter: int) -> bytes:
    header = bytearray(256)
    struct.pack_into("<I", header, 9, packet_bytes)
    struct.pack_into("<I", header, 20, counter)
    struct.pack_into("<h", header, 218, int(round(theta_deg * 100.0)))
    return bytes(header) + bytes(packet_bytes - len(header))


def scenario(background_dir: Path, output_dir: Path) -> dict:
    return {
        "case_id": "reuse_validation",
        "output_dir": str(output_dir),
        "scene_mode": "full",
        "truth_output": False,
        "waveform": {
            "fc_ghz": 16.0, "bandwidth_mhz": 1.0, "fs_mhz": 2.0,
            "tr_us": 1.0, "prf_hz": 1000.0, "pulse_len": 1,
            "acquired_pulse_len": 1, "pulse_num": 2, "d_chan_m": 0.17,
            "iq_data_type": "int16", "new_protocol_channel_count": 4,
            "new_protocol_read_channel_1": 1, "new_protocol_read_channel_2": 2,
        },
        "range_processing": {
            "range_fft_len": 1, "range_crop_start": 0, "range_crop_len": 1,
            "sample_delay_us": 0.0,
        },
        "channel_geometry": {
            "channel_1": {"name": "ch1", "x_m": -0.085, "y_m": 0.0, "z_m": 0.085},
            "channel_2": {"name": "ch2", "x_m": 0.085, "y_m": 0.0, "z_m": 0.085},
            "channel_3": {"name": "ch3", "x_m": -0.085, "y_m": 0.0, "z_m": -0.085},
            "channel_4": {"name": "ch4", "x_m": 0.085, "y_m": 0.0, "z_m": -0.085},
        },
        "scan": {"scan_min_deg": -1.0, "scan_step_deg": 2.0,
                 "beam_count": 2, "beam_width_deg": 1.0, "beam_index_base": 1},
        "random": {"period_start": 0, "period_count": 1,
                   "beam_start": 2, "beam_count": 1, "random_seed": 1},
        "scene": {
            "mode": "full", "output_signal_domain": "raw_lfm",
            "area_clutter": {"enabled": False},
            "strong_scatterers": {"enabled": False},
            "line_scatterers": {"enabled": False},
            "thermal_noise": {"enabled": False},
        },
        "background_input_dir": str(background_dir),
        "targets": [{
            "target_id": "reuse_validation_target", "enabled": True,
            "init": {"type": "beam_bin_azimuth_offset", "beam_id": 2,
                      "expected_bin": 0, "azimuth_offset_deg": 0.0},
            "motion": {"type": "static"},
            "amplitude": {"type": "snr_db", "snr_db": 0.0},
            "visibility": {"type": "hard_gate", "single_beam_only": True},
        }],
    }


class BackgroundReuseValidationTests(unittest.TestCase):
    def run_case(self, source_packets: bytes, expected_error: str) -> None:
        self.assertTrue(SIMULATOR.is_file(), SIMULATOR)
        with tempfile.TemporaryDirectory(prefix="pgrcc_reuse_test_") as temp:
            root = Path(temp)
            background = root / "background"
            (background / "data").mkdir(parents=True)
            packet_bytes = 256 + 1 * 4 * 2 * 2
            (background / "data/stage2_statistical_newprotocol_period_0000.bin").write_bytes(
                source_packets
            )
            config = root / "scenario.json"
            config.write_text(json.dumps(scenario(background, root / "output")), encoding="utf-8")
            completed = subprocess.run(
                [str(SIMULATOR), "--config", str(config)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn(expected_error, completed.stdout + completed.stderr)

    def test_unmatched_theta_is_rejected(self) -> None:
        packet_bytes = 256 + 1 * 4 * 2 * 2
        self.run_case(packet(-1.0, packet_bytes, 0) * 2,
                      "no beam header matching requested beam")

    def test_partial_beam_group_is_rejected(self) -> None:
        packet_bytes = 256 + 1 * 4 * 2 * 2
        self.run_case(packet(-1.0, packet_bytes, 0),
                      "complete electronic beam groups")

    def test_duplicate_theta_is_rejected(self) -> None:
        packet_bytes = 256 + 1 * 4 * 2 * 2
        self.run_case(packet(-1.0, packet_bytes, 0) * 2 +
                      packet(-1.0, packet_bytes, 2) * 2,
                      "duplicate beam theta headers")

    def test_valid_mapping_artifact(self) -> None:
        mapping = ROOT / "outputs/phase_a_reuse_source_equivalent/reports/background_reuse_mapping.csv"
        if not mapping.is_file():
            self.skipTest(
                "historical phase-A mapping artifact was removed during compact-output cleanup; "
                "the current simulator validation tests remain below"
            )
        with mapping.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        for field in (
            "period_id", "requested_beam_id", "requested_theta_deg",
            "source_group", "source_first_packet", "source_theta_deg",
            "output_first_packet", "output_first_counter",
            "source_first_prt_counter", "output_first_prt_counter",
            "theta_error_deg", "mapping_status",
        ):
            self.assertIn(field, row)
        self.assertEqual(row["source_group"], "14")
        self.assertEqual(row["source_first_packet"], "1792")
        self.assertEqual(row["output_first_packet"], "0")
        self.assertEqual(row["mapping_status"], "matched")
        self.assertLessEqual(float(row["theta_error_deg"]), 0.011)

    def test_three_period_three_beam_mapping_artifact(self) -> None:
        mapping = ROOT / "outputs/phase_a_reuse_14_16_3period/reports/background_reuse_mapping.csv"
        self.assertTrue(mapping.is_file())
        with mapping.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 9)
        self.assertEqual({int(row["period_id"]) for row in rows}, {0, 1, 2})
        self.assertEqual({int(row["requested_beam_id"]) for row in rows}, {14, 15, 16})
        expected_source_groups = {14: "13", 15: "14", 16: "15"}
        expected_output_packet = {14: 0, 15: 128, 16: 256}
        expected_output_counter = {14: 0, 15: 128, 16: 256}
        for row in rows:
            beam = int(row["requested_beam_id"])
            period = int(row["period_id"])
            self.assertEqual(row["source_group"], expected_source_groups[beam])
            self.assertEqual(int(row["output_first_packet"]), expected_output_packet[beam])
            self.assertEqual(
                int(row["output_first_counter"]),
                384 * period + expected_output_counter[beam],
            )
            self.assertEqual(row["mapping_status"], "matched")
            self.assertLessEqual(float(row["theta_error_deg"]), 0.011)

    def test_same_beam_payload_and_target_window_equivalence(self) -> None:
        audit_path = ROOT / "outputs/phase_a_reuse_source_equivalent/phase_audit/phase_input_audit.json"
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        payload = audit["rows"]
        same_beam = next(row for row in payload if row["kind"] == "after_vs_source_requested_beam_payload")
        target_window = next(
            row for row in payload if row["kind"] == "after_minus_source_requested_target_window"
        )
        self.assertGreater(float(same_beam["ch1_coherence"]), 0.99)
        self.assertGreater(float(target_window["coherence"]), 0.999)
        self.assertAlmostEqual(float(target_window["phase_deg"]), -58.63643156267692, places=6)

    def test_historical_impact_matrix_records_corrected_reruns(self) -> None:
        impact = ROOT / "outputs/background_reuse_impact/impact_matrix.csv"
        self.assertTrue(impact.is_file())
        with impact.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        affected = [row for row in rows if row["phase_reuse_bug_affected"] == "yes"]
        self.assertEqual(len(affected), 2)
        self.assertTrue(all(row["need_rerun"] == "no" for row in affected))
        self.assertTrue(all(row["rerun_status"] == "completed" for row in affected))
        self.assertTrue(all(row["rerun_output"] for row in affected))

    def test_coherent_gate_ab_is_transparent(self) -> None:
        values: dict[str, dict[str, float]] = {}
        for gate in ("false", "true"):
            summary = next(
                (ROOT / "outputs/coherent_gate_ab" / f"gate_{gate}" / "result/csi_metrics").glob(
                    "*/csi_metric_tap_summary.csv"
                )
            )
            with summary.open(newline="", encoding="utf-8") as handle:
                row = next(
                    item for item in csv.DictReader(handle)
                    if item["roi_name"] == "active_support_unmasked"
                )
            values[gate] = {
                key: float(row[key])
                for key in ("CA_ROI_dB", "phase_model_weighted_coherence",
                            "phase_model_weighted_circular_rmse_rad")
            }
        for key in values["false"]:
            self.assertTrue(math.isclose(values["false"][key], values["true"][key], abs_tol=1.0e-12))


if __name__ == "__main__":
    unittest.main()
