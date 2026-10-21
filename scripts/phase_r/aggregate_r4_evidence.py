from __future__ import annotations

import csv
import glob
import hashlib
import json
import math
import os
import re
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


REPO = Path.cwd()
AGGREGATOR = Path(__file__).resolve()
DEFAULT_OUT = "docs/research/phase_r/evidence/r4_e1_e4_e5_seed101_20261022"
parser = argparse.ArgumentParser(description="Aggregate the frozen Phase R4 seed-101 pilot evidence.")
parser.add_argument("--output-dir", default=DEFAULT_OUT, help="new output directory; existing directories are never overwritten")
output_arg = Path(parser.parse_args().output_dir)
OUT = output_arg if output_arg.is_absolute() else REPO / output_arg
ROOTS = [
    "outputs/phase_r_e1_e4_e5_cuda_pilot_seed101_20261022_maps",
    "outputs/phase_r_e1_e4_e5_cuda_pilot_seed101_20261022_retry_v2_snr10_cleanclone",
    "outputs/phase_r_e1_e4_e5_cuda_pilot_seed101_20261022_retry_v2_snr30_cleanclone",
    "outputs/phase_r_e1_e4_e5_cuda_pilot_seed101_20261022_retry_v4_snr10_cleanclone",
    "outputs/phase_r_e1_e4_e5_cuda_pilot_seed101_20261022_retry_v4_snr30_cleanclone",
]
RUNNER = Path("scripts/run_production_hierarchical_calibration_pilot.py")
P5_EVALUATOR = Path("scripts/run_p5_csi_eval.py")
P5_CONFIG = Path("configs/research/production_hierarchical_calibration_delay_pilot.json")


def rel(path: Path | str) -> str:
    return Path(path).resolve().relative_to(REPO.resolve()).as_posix()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def read_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def parse_partial_json_fields(raw: str, keys: tuple[str, ...]) -> dict:
    decoder = json.JSONDecoder()
    result = {}
    for key in keys:
        match = re.search(rf'(?m)^  "{re.escape(key)}"\s*:\s*', raw)
        if not match:
            continue
        try:
            value, _ = decoder.raw_decode(raw, match.end())
        except json.JSONDecodeError:
            continue
        result[key] = value
    return result


def parse_p5_method_role(manifest_path: Path, p5_root: Path):
    parts = manifest_path.parent.relative_to(p5_root).parts
    if len(parts) >= 3 and parts[1] in {"ON", "TO"}:
        method_label, role = parts[0], parts[1]
    else:
        if not parts:
            raise ValueError(f"cannot infer P5 method/role from {manifest_path}")
        method_label = parts[0]
        for candidate_role in ("ON", "TO"):
            suffix = "_" + candidate_role
            if method_label.endswith(suffix):
                method_label = method_label[: -len(suffix)]
                role = candidate_role
                break
        else:
            raise ValueError(f"cannot infer P5 role from {manifest_path}")
    method = method_label.split("_", 1)[0]
    return method, role, method_label


def p5_summary(manifest_path: Path):
    manifest = read_json(manifest_path)
    summary = manifest.get("summary", {})
    csv_path = manifest_path.parent / "csi_metric_summary.csv"
    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8") as handle:
            row = next(csv.DictReader(handle), {})
        for key in (
            "background_roi_count", "target_metric_count",
            "valid_background_roi_count", "valid_target_metric_count",
            "mean_CA_ROI_dB", "mean_SCNR_improvement_dB",
            "mean_target_loss_dB", "residual_peak_count", "status",
        ):
            if key in row and row[key] not in ("", None):
                value = row[key]
                if key == "status":
                    summary[key] = value
                elif key == "residual_peak_count":
                    summary[key] = int(float(value))
                elif key in {
                    "background_roi_count", "target_metric_count",
                    "valid_background_roi_count", "valid_target_metric_count",
                }:
                    summary[key] = int(float(value))
                else:
                    summary[key] = finite(value)
    return manifest, summary


def truth_radial_velocity_summary(truth_path: Path, expected_period_count: int) -> dict:
    by_period = defaultdict(list)
    with truth_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            period = int(row["period_id"])
            velocity = float(row["vr_self_mps"])
            if not math.isfinite(velocity):
                raise ValueError(f"non-finite radial velocity in {truth_path}, period {period}")
            by_period[period].append(velocity)
    expected_periods = set(range(expected_period_count))
    if set(by_period) != expected_periods or any(not values for values in by_period.values()):
        raise ValueError(f"truth periods in {truth_path} do not match {sorted(expected_periods)}")
    means = {str(period): sum(values) / len(values) for period, values in sorted(by_period.items())}
    period_means = list(means.values())
    return {
        "period_count": len(means),
        "period_mean_mps": means,
        "mean_of_period_means_mps": sum(period_means) / len(period_means),
        "min_period_mean_mps": min(period_means),
        "max_period_mean_mps": max(period_means),
    }


def discover_p5_root(case_root: Path) -> Path:
    candidates = [case_root / "evaluation/p5_period_specific", case_root / "evaluated/p5"]
    for candidate in candidates:
        if candidate.is_dir() and next(candidate.rglob("p5_evaluation_manifest.json"), None):
            return candidate
    raise ValueError(f"no P5 evaluation root found in {case_root}; checked {candidates}")


if OUT.exists():
    raise SystemExit(f"refusing to overwrite existing evidence directory: {OUT}")

case_roots = []
for root_rel in ROOTS:
    for manifest_path in sorted((REPO / root_rel / "cases").glob("*/case_manifest.json")):
        case_roots.append((Path(root_rel), manifest_path.parent, manifest_path))

if len(case_roots) != 8:
    raise SystemExit(f"expected 8 valid frozen case manifests; found {len(case_roots)}")

case_keys = []
case_rows: list[dict] = []
branch_rows: list[dict] = []
p5_rows: list[dict] = []
cfar_rows: list[dict] = []
case_records = []
case_p5_rows: dict[str, list[dict]] = {}
root_records = {}

for root_rel, case_root, case_manifest_path in case_roots:
    case_manifest = read_json(case_manifest_path)
    case = case_manifest["case"]
    velocity = float(case["target_velocity_mps"])
    snr = float(case["target_snr_db"])
    seed = int(case["seed"])
    delay = float(case["delay_error_ns"])
    key = (seed, velocity, snr, delay)
    case_keys.append(key)
    case_id = case_manifest["case_id"]
    period_count = int(case["period_count"])
    truth_radial_velocity_path = case_root / "scenes/A0_ON/truth/moving_target_truth.csv"
    truth_radial_velocity = truth_radial_velocity_summary(truth_radial_velocity_path, period_count)
    p5_root = discover_p5_root(case_root)

    run_root = REPO / root_rel
    root_manifest_path = run_root / "manifest.json"
    root_record = root_records.get(root_rel.as_posix())
    if root_record is None:
        root_record = {
            "root": root_rel.as_posix(),
            "manifest": rel(root_manifest_path),
            "manifest_parse_status": "valid",
            "manifest_error": None,
            "status": None,
            "source": None,
            "command": None,
            "gpu_probe_available": None,
            "build": None,
            "config": None,
            "runner": None,
        }
        try:
            run_manifest = read_json(root_manifest_path)
            root_record["status"] = run_manifest.get("status")
            root_record["source"] = run_manifest.get("source")
            root_record["command"] = run_manifest.get("command")
            root_record["gpu_probe_available"] = run_manifest.get("gpu_probe", {}).get("available")
            root_record["build"] = run_manifest.get("build")
            root_record["config"] = run_manifest.get("config")
            root_record["runner"] = run_manifest.get("runner")
        except Exception as exc:
            raw = root_manifest_path.read_text(encoding="utf-8", errors="replace")
            root_record["manifest_parse_status"] = "invalid_truncated_json"
            root_record["manifest_error"] = str(exc)
            partial = parse_partial_json_fields(raw, ("source", "command", "gpu_probe", "build", "config", "runner"))
            if partial.get("source"):
                root_record["source"] = {**partial["source"], "recovered_from_valid_prefix": True}
            root_record["command"] = partial.get("command")
            root_record["gpu_probe_available"] = partial.get("gpu_probe", {}).get("available")
            root_record["build"] = partial.get("build")
            root_record["config"] = partial.get("config")
            root_record["runner"] = partial.get("runner")
            root_record["status"] = "NOT_FINALIZED_ROOT_MANIFEST"
        root_records[root_rel.as_posix()] = root_record

    p5_manifests = sorted(p5_root.rglob("p5_evaluation_manifest.json"))
    expected_p5 = (len(case_manifest["selected_methods"]) + 1) * 2 * period_count
    if len(p5_manifests) != expected_p5:
        raise SystemExit(f"{case_id}: P5 count {len(p5_manifests)} != expected {expected_p5}")
    local_p5 = []
    p5_seen = set()
    for p5_path in p5_manifests:
        manifest, summary = p5_summary(p5_path)
        method, role, method_label = parse_p5_method_role(p5_path, p5_root)
        period = int(manifest.get("period_id", summary.get("period_id", -1)))
        triple = (method, role, period)
        if triple in p5_seen:
            raise SystemExit(f"{case_id}: duplicate P5 grain {triple}")
        p5_seen.add(triple)
        if period < 0 or period >= period_count:
            raise SystemExit(f"{case_id}: invalid P5 period {period}")
        row = {
            "case_id": case_id,
            "seed": seed,
            "configured_ground_speed_input_mps": velocity,
            "target_snr_configured_db": snr,
            "method": method,
            "method_label": method_label,
            "role": role,
            "period_id": period,
            "status": summary.get("status"),
            "background_roi_count": summary.get("background_roi_count"),
            "valid_background_roi_count": summary.get("valid_background_roi_count"),
            "target_metric_count": summary.get("target_metric_count"),
            "valid_target_metric_count": summary.get("valid_target_metric_count"),
            "mean_CA_ROI_dB": finite(summary.get("mean_CA_ROI_dB")),
            "mean_SCNR_improvement_dB": finite(summary.get("mean_SCNR_improvement_dB")),
            "mean_target_loss_dB": finite(summary.get("mean_target_loss_dB")),
            "residual_peak_count": summary.get("residual_peak_count"),
            "source_manifest": rel(p5_path),
            "tap_dir": rel(manifest["tap_dir"]),
            "paired_off_tap_dir": rel(manifest["paired_off_tap_dir"]),
            "truth_dir": rel(manifest["truth_dir"]),
            "config_hash": manifest.get("config_hash"),
        }
        p5_rows.append(row)
        local_p5.append(row)
    if len(p5_seen) != expected_p5:
        raise SystemExit(f"{case_id}: P5 unique grains {len(p5_seen)} != {expected_p5}")
    case_p5_rows[case_id] = local_p5

    production_manifests = sorted((case_root / "production").glob("*/branch_manifest.json"))
    if len(production_manifests) != 15:
        raise SystemExit(f"{case_id}: expected 15 production branches, found {len(production_manifests)}")
    case_branch_rows = []
    for branch_path in production_manifests:
        b = read_json(branch_path)
        m = b.get("metrics") or {}
        branch_name = b.get("branch_name", branch_path.parent.name)
        method = branch_name.split("_", 1)[0]
        role = b.get("role", branch_name.rsplit("_", 1)[-1])
        mode = "Mode-A" if "_modea_" in branch_name else "not_applicable"
        row = {
            "case_id": case_id,
            "seed": seed,
            "configured_ground_speed_input_mps": velocity,
            "target_snr_configured_db": snr,
            "delay_error_ns": delay,
            "method": method,
            "calibration_mode": mode,
            "role": role,
            "branch_name": branch_name,
            "production_status": b.get("production_status", b.get("status")),
            "expected_periods": m.get("expected_periods", b.get("period_count_expected")),
            "truth_visible_periods": m.get("truth_visible_periods"),
            "detection_snapshot_periods": m.get("detection_snapshot_periods"),
            "target_detection_opportunities": m.get("target_detection_opportunity_count"),
            "target_detection_hits": m.get("target_detection_hit_count"),
            "target_detection_pd_point_estimate": m.get("target_detection_pd"),
            "detection_record_count": m.get("detection_record_count"),
            "truth_matched_detection_records": m.get("detection_record_target_match_count"),
            "unmatched_detection_records": m.get("detection_record_false_alarm_count"),
            "unmatched_detection_record_fraction": m.get("detection_record_false_alarm_rate"),
            "track_pd_all_visible_periods": m.get("track_pd_all_visible"),
            "track_pd_after_confirmation": m.get("track_pd_after_confirmation"),
            "matched_protocol_payloads": m.get("matched_protocol_payload_count"),
            "eligible_protocol_payloads": m.get("eligible_protocol_payload_count"),
            "angle_rmse_deg_truth_matched_track_payloads": m.get("angle_rmse_deg"),
            "ground_speed_rmse_mps_truth_matched_track_payloads": m.get("velocity_rmse_mps_ground_speed"),
            "position_rmse_m_truth_matched_track_payloads": m.get("position_rmse_m"),
            "radial_velocity_rmse_mps": None,
            "radial_velocity_rmse_status": "NOT_REPORTED_IN_BRANCH_METRICS",
            "cfar_pfa_status": m.get("cfar_pfa_status"),
            "cluster_false_alarm_status": m.get("cluster_false_alarm_status"),
            "track_audit_status": m.get("track_audit_status", b.get("audit", {}).get("status")),
            "track_audit_total_violations": m.get("track_audit_total_violations", b.get("track_audit_total_violations")),
            "branch_manifest": rel(branch_path),
            "production_log": rel(b["production_log"]) if b.get("production_log") else None,
            "runtime_xml": rel(b["production_xml"]) if b.get("production_xml") else None,
        }
        branch_rows.append(row)
        case_branch_rows.append(row)

    off_cfar_path = case_root / "production/A0_not_applicable_OFF/result/debug/cfar_geometry_diagnostics.csv"
    with off_cfar_path.open(newline="", encoding="utf-8") as handle:
        cfar_data = list(csv.DictReader(handle))
    grouped = defaultdict(list)
    for row in cfar_data:
        grouped[row["branch"]].append(row)
    if set(grouped) != {"union_dynamic_in_band", "union_full_band"}:
        raise SystemExit(f"{case_id}: unexpected CFAR branches {sorted(grouped)}")
    for branch, rows in sorted(grouped.items()):
        valid = sum(int(row["valid_cut_count"]) for row in rows)
        hits = sum(int(row["hit_cut_count"]) for row in rows)
        cfar_rows.append({
            "case_id": case_id,
            "seed": seed,
            "configured_ground_speed_input_mps": velocity,
            "target_snr_configured_db": snr,
            "role": "A0_OFF_H0",
            "detector_branch": branch,
            "period_count": len({int(row["period_id"]) for row in rows}),
            "threshold_test_count": sum(int(row["threshold_test_count"]) for row in rows),
            "valid_cut_count": valid,
            "hit_cut_count_branch_specific": hits,
            "observed_hit_fraction_branch_specific": hits / valid if valid else None,
            "configured_pfa": rows[0]["configured_pfa"],
            "false_counts_status": "|".join(sorted({row["false_counts_status"] for row in rows})),
            "hit_index_hashes_only": "|".join(row["hit_index_hash"] for row in rows),
            "source_csv": rel(off_cfar_path),
        })

    area = case_manifest["area_clutter_gate"]
    triplets = case_manifest.get("causal_triplets", {})
    c0_on = next(row for row in case_branch_rows if row["method"] == "C0" and row["role"] == "ON")
    on_rows = [row for row in case_branch_rows if row["role"] == "ON"]
    p5_statuses = Counter(row["status"] for row in local_p5)
    case_row = {
        "case_id": case_id,
        "seed": seed,
        "configured_ground_speed_input_mps": velocity,
        "truth_radial_velocity_period_means_mps": json.dumps(truth_radial_velocity["period_mean_mps"]),
        "truth_radial_velocity_mean_of_period_means_mps": truth_radial_velocity["mean_of_period_means_mps"],
        "truth_radial_velocity_min_period_mean_mps": truth_radial_velocity["min_period_mean_mps"],
        "truth_radial_velocity_max_period_mean_mps": truth_radial_velocity["max_period_mean_mps"],
        "truth_radial_velocity_period_count": truth_radial_velocity["period_count"],
        "truth_radial_velocity_csv": rel(truth_radial_velocity_path),
        "target_snr_configured_db": snr,
        "delay_error_ns": delay,
        "period_count": period_count,
        "case_status": case_manifest.get("status"),
        "area_gate_status": area.get("status"),
        "platform_height_m": area.get("platform_height_m"),
        "slant_range_window_m": json.dumps(area.get("range_window_m")),
        "ground_range_window_m": json.dumps(area.get("ground_range_window_m")),
        "continuous_area_packets": area.get("continuous_area_packets"),
        "continuous_area_samples": area.get("continuous_area_samples"),
        "area_mean_power_measured": area.get("area_mean_power_measured"),
        "area_rho_lag1_measured": area.get("report_values", {}).get("area_rho_lag1_measured"),
        "production_branch_count": len(case_branch_rows),
        "production_branches_passed": sum(row["production_status"] == "passed" for row in case_branch_rows),
        "on_branches_with_target_hits_equal_opportunities": sum(
            row["target_detection_hits"] == row["target_detection_opportunities"] for row in on_rows
        ),
        "on_branch_count": len(on_rows),
        "C0_ON_target_detection_hits": c0_on["target_detection_hits"],
        "C0_ON_target_detection_opportunities": c0_on["target_detection_opportunities"],
        "C0_ON_track_pd_all_visible_periods": c0_on["track_pd_all_visible_periods"],
        "on_angle_rmse_finite_branches": sum(finite(row["angle_rmse_deg_truth_matched_track_payloads"]) is not None for row in on_rows),
        "on_ground_speed_rmse_finite_branches": sum(finite(row["ground_speed_rmse_mps_truth_matched_track_payloads"]) is not None for row in on_rows),
        "on_position_rmse_finite_branches": sum(finite(row["position_rmse_m_truth_matched_track_payloads"]) is not None for row in on_rows),
        "evidence_gate_causal_triplets": case_manifest.get("evidence_gate", {}).get("causal_triplets"),
        "ideal_triplet_max_abs_error": triplets.get("ideal", {}).get("max_abs_error"),
        "none_triplet_max_abs_error": triplets.get("none", {}).get("max_abs_error"),
        "triplet_sample_count": triplets.get("ideal", {}).get("sample_count"),
        "triplet_tolerance": triplets.get("ideal", {}).get("tolerance"),
        "valid_cut_denominators_gate": case_manifest.get("evidence_gate", {}).get("valid_cut_denominators"),
        "residual_closure_status": case_manifest.get("residual_closure", {}).get("status"),
        "residual_closure_reason": case_manifest.get("residual_closure", {}).get("reason"),
        "p5_root": rel(p5_root),
        "p5_report_count": len(local_p5),
        "p5_status_counts": json.dumps(dict(sorted(p5_statuses.items()))),
        "p5_valid_background_roi_count": sum(int(row["valid_background_roi_count"] or 0) for row in local_p5),
        "p5_background_roi_count": sum(int(row["background_roi_count"] or 0) for row in local_p5),
        "p5_valid_target_metric_count": sum(int(row["valid_target_metric_count"] or 0) for row in local_p5),
        "p5_target_metric_count": sum(int(row["target_metric_count"] or 0) for row in local_p5),
        "case_manifest": rel(case_manifest_path),
        "run_root": root_rel.as_posix(),
    }
    case_rows.append(case_row)

    input_hashes = []
    for role, items in case_manifest.get("inputs", {}).items():
        for item in items:
            input_hashes.append({
                "role": role,
                "path": rel(item["path"]),
                "bytes": item.get("bytes"),
                "sha256": item.get("sha256"),
                "path_exists_at_aggregation": Path(item["path"]).exists(),
            })
    case_records.append({
        "case_id": case_id,
        "case_manifest": rel(case_manifest_path),
        "pair_identity": {
            "seed": case_manifest.get("pair_identity", {}).get("seed"),
            "background_id": rel(case_manifest["pair_identity"]["background_id"]),
            "target_id": case_manifest.get("pair_identity", {}).get("target_id"),
            "delay_error_ns": case_manifest.get("pair_identity", {}).get("delay_error_ns"),
            "production_identity": case_manifest.get("pair_identity", {}).get("production_identity"),
        },
        "selected_methods": case_manifest.get("selected_methods"),
        "selected_calibration_modes": case_manifest.get("selected_calibration_modes"),
        "input_identity": input_hashes,
        "stage2_area_report": rel(area["report_path"]),
        "truth_radial_velocity_csv": rel(truth_radial_velocity_path),
        "truth_radial_velocity_period_means_mps": truth_radial_velocity["period_mean_mps"],
        "runtime_xml_count": len(list(case_root.rglob("production.xml"))),
        "stage2_runtime_xml_count": len(list(case_root.rglob("temp_config_stage2_period_*.xml"))),
    })

if len(set(case_keys)) != 8 or len({(v, s) for _, v, s, _ in case_keys}) != 8:
    raise SystemExit("frozen grid is not eight unique seed/velocity/SNR conditions")
if {round(v, 1) for _, v, _, _ in case_keys} != {0.5, 1.0, 2.0, 4.0} or {s for _, _, s, _ in case_keys} != {10.0, 30.0}:
    raise SystemExit("observed case grid does not match the frozen design")
if len(branch_rows) != 120 or len(p5_rows) != 240 or len(cfar_rows) != 16:
    raise SystemExit(f"unexpected derived row counts: branches={len(branch_rows)}, P5={len(p5_rows)}, CFAR={len(cfar_rows)}")

grouped = defaultdict(list)
for row in p5_rows:
    grouped[(row["case_id"], row["method"], row["role"])].append(row)
method_rows = []
for (case_id, method, role), rows in sorted(grouped.items()):
    numeric = lambda field: [finite(row.get(field)) for row in rows if finite(row.get(field)) is not None]
    case_row = next(row for row in case_rows if row["case_id"] == case_id)
    def mean(field):
        values = numeric(field)
        return sum(values) / len(values) if values else None
    method_rows.append({
        "case_id": case_id,
        "seed": case_row["seed"],
        "configured_ground_speed_input_mps": case_row["configured_ground_speed_input_mps"],
        "target_snr_configured_db": case_row["target_snr_configured_db"],
        "method": method,
        "method_label": rows[0]["method_label"],
        "role": role,
        "period_count": len(rows),
        "period_status_counts": json.dumps(dict(sorted(Counter(row["status"] for row in rows).items()))),
        "valid_background_roi_count": sum(int(row["valid_background_roi_count"] or 0) for row in rows),
        "background_roi_count": sum(int(row["background_roi_count"] or 0) for row in rows),
        "valid_target_metric_count": sum(int(row["valid_target_metric_count"] or 0) for row in rows),
        "target_metric_count": sum(int(row["target_metric_count"] or 0) for row in rows),
        "mean_CA_ROI_dB_valid_periods_only": mean("mean_CA_ROI_dB"),
        "mean_SCNR_improvement_dB_valid_periods_only": mean("mean_SCNR_improvement_dB"),
        "mean_target_loss_dB_valid_periods_only": mean("mean_target_loss_dB"),
        "aggregation_note": "mean of valid period summaries only; one seed; periods are not independent seeds",
    })

OUT.mkdir(parents=True)
write_csv(OUT / "case_summary.csv", case_rows, list(case_rows[0]))
write_csv(OUT / "production_branch_summary.csv", branch_rows, list(branch_rows[0]))
write_csv(OUT / "p5_period_summary.csv", p5_rows, list(p5_rows[0]))
write_csv(OUT / "p5_method_role_summary.csv", method_rows, list(method_rows[0]))
write_csv(OUT / "cfar_off_branch_summary.csv", cfar_rows, list(cfar_rows[0]))

ordered_cases = sorted(case_rows, key=lambda row: (row["configured_ground_speed_input_mps"], row["target_snr_configured_db"]))
labels = [f"{row['configured_ground_speed_input_mps']:g}\n{row['target_snr_configured_db']:g}"
          for row in ordered_cases]
fig, axes = plt.subplots(1, 3, figsize=(17, 5.4), constrained_layout=True)
blue = "#3B6F9C"
gold = "#C79327"
gray = "#5A626B"

ax = axes[0]
x = np.arange(len(ordered_cases))
width = 0.36
bg = [row["p5_valid_background_roi_count"] for row in ordered_cases]
target = [row["p5_valid_target_metric_count"] for row in ordered_cases]
bars_bg = ax.bar(x - width / 2, bg, width, color=blue, label="Valid background ROIs")
bars_target = ax.bar(x + width / 2, target, width, color=gold, label="Valid target metrics")
ax.set_xticks(x, labels)
ax.set_xlabel("configured ground speed v_g (m/s) / target SNR (dB)")
ax.set_ylim(0, 36)
ax.set_ylabel("Valid reports (of 30)")
ax.set_title("P5 metric coverage by case")
ax.grid(axis="y", color="#D9DDE1", linewidth=0.7)
ax.set_axisbelow(True)
for bars in (bars_bg, bars_target):
    for bar in bars:
        value = int(bar.get_height())
        ax.annotate(f"{value}", (bar.get_x() + bar.get_width() / 2, value),
                    xytext=(0, 3), textcoords="offset points", ha="center", va="bottom", fontsize=7)
ax.legend(frameon=False, fontsize=8, loc="upper left")

def method_values(metric):
    result = {}
    for snr in (10.0, 30.0):
        for method in ("C0", "C1", "C2", "C3"):
            match = next(row for row in method_rows
                         if row["configured_ground_speed_input_mps"] == 4.0
                         and row["target_snr_configured_db"] == snr
                         and row["method"] == method and row["role"] == "ON")
            result[(snr, method)] = finite(match.get(metric))
    return result

for ax, metric, title, ylabel in (
    (axes[1], "mean_SCNR_improvement_dB_valid_periods_only", "configured v_g=4 m/s ON: SCNR change", "SCNR after − before (dB)"),
    (axes[2], "mean_CA_ROI_dB_valid_periods_only", "configured v_g=4 m/s ON: background ROI", "10 log10(before / after) (dB)"),
):
    values = method_values(metric)
    xx = np.arange(4)
    bw = 0.34
    v10 = [values[(10.0, method)] for method in ("C0", "C1", "C2", "C3")]
    v30 = [values[(30.0, method)] for method in ("C0", "C1", "C2", "C3")]
    bars10 = ax.bar(xx - bw / 2, v10, bw, color=blue, label="10 dB")
    bars30 = ax.bar(xx + bw / 2, v30, bw, color=gold, label="30 dB")
    ax.axhline(0, color=gray, linewidth=1.0)
    ax.set_xticks(xx, ["C0", "C1", "C2", "C3"])
    ax.set_ylim(bottom=min(v10 + v30) - 0.45, top=max(0.2, max(v10 + v30) + 0.2))
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(axis="y", color="#D9DDE1", linewidth=0.7)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=8)
    for bars in (bars10, bars30):
        for bar in bars:
            value = bar.get_height()
            ax.annotate(f"{value:.2f}", (bar.get_x() + bar.get_width() / 2, value),
                        xytext=(0, -13 if value < 0 else 3), textcoords="offset points",
                        ha="center", va="top" if value < 0 else "bottom", fontsize=7)

fig.suptitle("Phase R4 seed-101 pilot — coverage and evaluable configured v_g=4 m/s P5 values (descriptive only)", fontsize=13)
fig.savefig(OUT / "pilot_metric_coverage.png", dpi=180, bbox_inches="tight")
plt.close(fig)

manifest = {
    "schema_version": 1,
    "stage": "Phase R4 E1/E4/E5 minimal CUDA Production pilot — seed 101",
    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    "status": "completed_with_gaps",
    "decision": "stop_seed_expansion_until_evaluability_gates_are_resolved",
    "evidence_labels": {
        "IMPLEMENTED": "runner supports method/mode subsets and a Production-preceding physical-area fail-closed gate; P5 uses period-specific truth/tap alignment",
        "EXECUTED": "8 valid seed-101 cases × 15 Production branches each, on real CUDA",
        "EVALUABLE": "area injection; ideal/none causal triplets; ON target opportunity hits and matched TrackManager metrics; P5 metrics only where valid; CFAR valid CUT and hit counts branch-wise",
        "PERFORMANCE_VERIFIED": "no; one seed and failed gates preclude verified general performance gains",
    },
    "design": {
        "seed": 101,
        "delay_error_ns": 0.0,
        "configured_ground_speed_input_mps": [0.5, 1.0, 2.0, 4.0],
        "configured_target_snr_db": [10.0, 30.0],
        "period_count_per_case": 3,
        "methods": ["C0", "C1", "C2", "C3"],
        "calibration_modes": ["Mode-A"],
        "roles": {"OFF": "C+N", "ON": "S+C+N", "TO": "S"},
    },
    "source_identity": {
        "production_commit": "69475e3db0c1bd012f247094daef461d15905fff",
        "production_worktree_dirty": False,
        "evidence_aggregator": {"path": rel(AGGREGATOR), "sha256": sha256(AGGREGATOR)},
        "runner": {"path": RUNNER.as_posix(), "sha256": sha256(REPO / RUNNER)},
        "p5_evaluator": {"path": P5_EVALUATOR.as_posix(), "sha256": sha256(REPO / P5_EVALUATOR), "working_tree_modified_for_period_specific_truth": True},
        "p5_config": {"path": P5_CONFIG.as_posix(), "sha256": sha256(REPO / P5_CONFIG)},
        "stage2_binary_sha256": "5dd0ac4e2536b8bcf2508cc53db8a30780eba75f40c025e13ffaa930f8ba7b9b",
        "production_binary_sha256": "f65a8aa446754cc79b6417f9275b3a01e73eb85098c6a4f9eaea0cb999c38962",
    },
    "environment": {
        "gpu": "NVIDIA GeForce RTX 3050 Laptop GPU",
        "driver": "580.178.04",
        "cuda": "13.0",
        "evidence": "run-root manifest nvidia-smi probe; all valid runs skip_cuda=false",
    },
    "area_clutter": {
        "model": "continuous_texture",
        "platform_height_m": 6000.0,
        "ground_height_m": 0.0,
        "slant_range_window_m": [84500.0, 85500.0],
        "ground_range_window_m": [84286.71306914276, 85289.213854977],
        "continuous_area_packets_per_case": 390,
        "continuous_area_samples_per_case": 1952655,
        "measured_mean_power_per_case": 0.0223352779188,
        "rho_lag1_measured": 0.980006534954,
    },
    "metric_directions": {
        "SCNR_improvement_dB": "SCNR_after_dB - SCNR_before_dB; negative means SCNR decreased",
        "CA_ROI_dB": "10*log10(before_mean_power/after_mean_power); negative means ROI background power increased after processing",
        "target_loss_dB": "-target_gain_dB; negative means target power gain",
        "CFAR_hit_fraction": "branch-specific hit_cut_count / valid_cut_count; not empirical Pfa",
        "detection_record_false_alarm_fraction": "unmatched production detection rows / all production detection rows; not CFAR Pfa",
    },
    "gates": {
        "production_branches": "15/15 passed in every valid case",
        "causal_triplets": "ideal and none passed in each case; 18,439,200 samples/case; max abs error range 1.2102e-6 to 1.3278e-6; frozen tolerance 5e-5",
        "valid_cut_denominators": "aggregate NOT_EVALUABLE because two detector branches provide hashes, not a unique hit-cell union",
        "residual_closure": "NOT_EVALUABLE: evaluator requires A0/PK/PKR while frozen method subset ran C0-C3 and omitted PK/PKR",
        "P5_low_speed": "background ROI has no valid cells for v=0.5 and 1 m/s; quality gate unchanged",
        "extension": "seed 202/303 not run because correctness/evaluability gates failed",
    },
    "cases": case_records,
    "input_retention": {
        "manifested_input_file_count": sum(len(record["input_identity"]) for record in case_records),
        "input_files_present_at_aggregation": sum(
            item["path_exists_at_aggregation"] for record in case_records for item in record["input_identity"]
        ),
        "input_files_absent_at_aggregation": sum(
            not item["path_exists_at_aggregation"] for record in case_records for item in record["input_identity"]
        ),
        "f1_f2_numeric_input_power": "NOT_EVALUABLE from retained artifacts; regenerated Stage2 .bin inputs are absent, while path/size/SHA-256 identities remain in case manifests",
    },
    "run_roots": list(root_records.values()),
    "excluded_or_nonprimary_evidence": [
        {"path": "outputs/phase_r_e1_e4_e5_cuda_pilot_seed101_20261022_maps/cases/e1_low_speed__seed_101__v_2__snr_10__delay_0ns", "reason": "partial case directory has no case_manifest; preserved, not counted among the eight completed frozen cases"},
        {"root": "outputs/phase_r_e1_e4_e5_cuda_pilot_seed101_20261022", "reason": "partial initial attempt has no complete case_manifest; preserved as failure evidence"},
        {"root": "outputs/phase_r_e1_e4_e5_cuda_pilot_seed101_20261022_retry1", "reason": "no valid case_manifest; startup/retry root not counted"},
        {"root": "outputs/phase_r_e1_e4_e5_cuda_pilot_seed101_20261022_final", "reason": "duplicate v=0.5 m/s, SNR=10 dB case with no P5 evaluation; 15 production branches preserved but excluded from the frozen 8-case summary"},
        {"path": "outputs/phase_r_e1_e4_e5_cuda_pilot_seed101_20261022_maps/manifest.json", "reason": "truncated/invalid JSON after ENOSPC; individual case manifests and branch evidence remain parseable and are used"},
        {"case": "v=0.5 m/s, SNR=10 dB in maps root", "reason": "one legacy P5 report outside p5_period_specific excluded; exactly 30 period-specific method × role × period reports used"},
    ],
    "files": {
        "case_summary": "case_summary.csv",
        "production_branch_summary": "production_branch_summary.csv",
        "p5_period_summary": "p5_period_summary.csv",
        "p5_method_role_summary": "p5_method_role_summary.csv",
        "cfar_off_branch_summary": "cfar_off_branch_summary.csv",
        "figure_png": "pilot_metric_coverage.png",
    },
    "validation": {
        "valid_case_count": len(case_rows),
        "production_branch_row_count": len(branch_rows),
        "p5_period_row_count": len(p5_rows),
        "cfar_branch_row_count": len(cfar_rows),
        "p5_grain": "case × method × role × period; P5 invalid/missing values remain blank",
        "cfar_grain": "case × A0_OFF × detector branch, periods summed only within that branch",
    },
    "reproduction": {
        "aggregation_command_template": f"python3 {rel(AGGREGATOR)} --output-dir <new-output-directory>",
        "production_runner": RUNNER.as_posix(),
        "config": P5_CONFIG.as_posix(),
        "p5_evaluator": P5_EVALUATOR.as_posix(),
        "case_root_manifests": [record["case_manifest"] for record in case_records],
        "production_commands_and_source_identity": "preserved in each run-root manifest; the maps root manifest is truncated, but its command/source prefix is intact",
        "stage2_and_runtime_configuration": "per-case Stage2 reports, scenario JSONs, period XMLs, branch runtime XMLs, and logs remain under the listed case roots",
    },
}
(OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
print(json.dumps({
    "output": str(OUT),
    "cases": len(case_rows),
    "production_branch_rows": len(branch_rows),
    "p5_period_rows": len(p5_rows),
    "p5_method_role_rows": len(method_rows),
    "cfar_branch_rows": len(cfar_rows),
    "case_coverage": [{
        "configured_ground_speed": row["configured_ground_speed_input_mps"], "snr": row["target_snr_configured_db"],
        "p5_status": row["p5_status_counts"],
        "background": f'{row["p5_valid_background_roi_count"]}/{row["p5_background_roi_count"]}',
        "target": f'{row["p5_valid_target_metric_count"]}/{row["p5_target_metric_count"]}',
    } for row in ordered_cases],
}, indent=2, ensure_ascii=False))
