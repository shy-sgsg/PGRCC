#!/usr/bin/env python3
"""Run paired target-only and negative controls for M1/M2/M3 outputs.

The existing challenge runs are positive C+N/S+C+N cases.  This companion
runner reuses each case's exact scenario and seed, but writes independent
control directories:

* ``target_only``: Stage2 ``scene.signal_only=true``; used only for target
  signal power retention (after/before) from the production CSI tap.
* ``negative_control``: all targets disabled; used for empirical CFAR cell Pfa.

It never changes an existing challenge directory and never trains AI.
"""

from __future__ import annotations

import argparse
import copy
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_model_mismatch_audit import (  # noqa: E402
    find_single_manifest,
    patch_production_xml,
    run_logged,
)
from experiment_provenance import git_provenance  # noqa: E402


DEFAULT_ROOTS = (
    ROOT / "outputs/ai_csi_model_mismatch_m1_rerun",
    ROOT / "outputs/ai_csi_model_mismatch_m2",
    ROOT / "outputs/ai_csi_model_mismatch_m3",
    ROOT / "outputs/ai_csi_model_mismatch_seed2026091012",
    ROOT / "outputs/ai_csi_model_mismatch_seed2026091013",
)


def pass_variants() -> list[Path]:
    variants: list[Path] = []
    for root in DEFAULT_ROOTS:
        for summary_path in sorted(root.rglob("variant_summary.json")):
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            if summary.get("status") == "pass":
                variants.append(summary_path.parent)
    return variants


def resource_snapshot() -> dict[str, str]:
    """Capture the read-only resource state required for audit provenance."""
    def run(command: list[str]) -> str:
        completed = subprocess.run(command, cwd=ROOT, text=True,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, check=False)
        return completed.stdout.strip()

    return {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": platform.node(),
        "nvidia_smi": run(["nvidia-smi"]),
        "free_h": run(["free", "-h"]),
        "df_h_workspace": run(["df", "-h", "."]),
    }


def control_scenario(source: Path, mode: str, output_root: Path) -> dict[str, Any]:
    scenario = json.loads((source / "scenario.json").read_text(encoding="utf-8"))
    scenario["case_id"] = f"{scenario['case_id']}_{mode}"
    scenario["output_dir"] = str((output_root / "stage2").resolve())
    scenario["truth_output"] = mode == "target_only"
    scenario.setdefault("scene", {})["signal_only"] = mode == "target_only"
    if mode == "negative_control":
        for target in scenario.get("targets", []):
            target["enabled"] = False
    else:
        for target in scenario.get("targets", []):
            target["enabled"] = True
    return scenario


def run_control(source: Path, mode: str, output_dir: Path, build_dir: Path,
                reuse_existing: bool) -> dict[str, Any]:
    summary_path = output_dir / "control_summary.json"
    if reuse_existing and summary_path.is_file():
        return json.loads(summary_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    scenario_path = output_dir / "scenario.json"
    scenario = control_scenario(source, mode, output_dir)
    scenario_path.write_text(
        json.dumps(scenario, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    simulate_log = output_dir / "simulate_stage2.log"
    simulate_rc = run_logged(
        [str(build_dir / "simulate_stage2_statistical"), "--config", str(scenario_path)],
        simulate_log,
    )
    result: dict[str, Any] = {
        "schema_version": 1,
        "mode": mode,
        "source_variant": str(source.resolve()),
        "scenario": str(scenario_path.resolve()),
        "simulate_exit_code": simulate_rc,
        "gmticore_exit_code": None,
        "status": "simulate_failed" if simulate_rc else "pending",
    }
    if simulate_rc == 0:
        xml_path = output_dir / "stage2/config/temp_config_stage2_period_0000.xml"
        if not xml_path.is_file():
            result["status"] = "missing_generated_xml"
        else:
            patch_production_xml(xml_path)
            gmti_log = output_dir / "gmticore.log"
            gmti_rc = run_logged(
                [str(build_dir / "GMTI_core"), str(xml_path),
                 "--runtime-mode=debug", "--runtime-diagnostics=on"],
                gmti_log,
            )
            result["gmticore_exit_code"] = gmti_rc
            result["status"] = "pass" if gmti_rc == 0 else "gmticore_failed"
            if gmti_rc == 0:
                manifest = find_single_manifest(output_dir)
                result["csi_manifest"] = str(manifest.resolve())
                result["gmticore_log"] = str(gmti_log.resolve())
                result["run_manifest"] = str(
                    (output_dir / "stage2/algorithm_result/period_0000/run_manifest.json").resolve()
                )
    summary_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "outputs/ai_csi_model_mismatch_metric_controls")
    parser.add_argument("--case", type=Path,
                        help="已有 challenge variant 目录；省略时运行全部 36 个 pass case")
    parser.add_argument("--mode", choices=("target_only", "negative_control", "both"),
                        default="both")
    parser.add_argument("--reuse-existing", action="store_true")
    args = parser.parse_args()
    build_dir = args.build_dir.resolve()
    for binary in ("simulate_stage2_statistical", "GMTI_core"):
        if not (build_dir / binary).is_file():
            raise SystemExit(f"找不到构建产物：{build_dir / binary}")
    variants = [args.case.resolve()] if args.case else pass_variants()
    if not variants:
        raise SystemExit("没有找到 pass challenge variant")
    modes = ("target_only", "negative_control") if args.mode == "both" else (args.mode,)
    resource_before = resource_snapshot()
    rows: list[dict[str, Any]] = []
    for index, variant in enumerate(variants, 1):
        if not (variant / "scenario.json").is_file():
            raise SystemExit(f"缺少 challenge scenario.json：{variant}")
        relative = variant.relative_to(ROOT)
        # Keep seed/family/level identity in the control path without copying
        # the original raw data or overwriting any existing challenge output.
        for mode in modes:
            output = args.output_dir.resolve() / relative / mode
            print(f"[control] {index}/{len(variants)} {variant.name} {mode} start",
                  flush=True)
            row = run_control(variant, mode, output, build_dir,
                              args.reuse_existing)
            rows.append(row)
            print(f"[control] {index}/{len(variants)} {variant.name} {mode} "
                  f"{row.get('status')}", flush=True)
    manifest = {
        "schema_version": 1,
        "ai_training": False,
        **git_provenance(ROOT),
        "case_count": len(variants),
        "modes": list(modes),
        "resource_snapshot_before": resource_before,
        "resource_snapshot_after": resource_snapshot(),
        "status_counts": {
            status: sum(row.get("status") == status for row in rows)
            for status in sorted({row.get("status", "") for row in rows})
        },
        "rows": rows,
        "definitions": {
            "target_only": "scene.signal_only=true; target amplitude is still normalized from the same generated background packet, and target loss is 10log10(target-only after ROI power / before ROI power)",
            "negative_control": "all targets disabled with the same seed and mismatch; Pfa is CFAR hit cells divided by valid CFAR test cells from the production kernel geometry",
            "production_chain": "each control is processed by the production simulate_stage2_statistical and GMTI_core binaries with runtime diagnostics enabled",
        },
    }
    output_root = args.output_dir.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output_dir": str(output_root),
        "case_count": len(variants),
        "control_count": len(rows),
        "failed": sum(row.get("status") != "pass" for row in rows),
    }, ensure_ascii=False))
    return 0 if all(row.get("status") == "pass" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
