#!/usr/bin/env python3
"""Re-run existing challenge inputs to export production P38/CFAR diagnostics.

This runner does not regenerate Stage2 data and never overwrites an existing
challenge or paired-control directory.  It reuses each pass variant's saved
production input XML, redirects ``result_add`` to an independent output tree,
and records the newly exported ``p38_refit_inlier_ratio`` and
``cfar_margin_db`` columns in a compact CSV.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import json
import math
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_model_mismatch_audit import patch_production_xml, run_logged  # noqa: E402
from experiment_provenance import git_provenance  # noqa: E402


DEFAULT_ROOTS = (
    ROOT / "outputs/ai_csi_model_mismatch_m1_rerun",
    ROOT / "outputs/ai_csi_model_mismatch_m2",
    ROOT / "outputs/ai_csi_model_mismatch_m3",
    ROOT / "outputs/ai_csi_model_mismatch_seed2026091012",
    ROOT / "outputs/ai_csi_model_mismatch_seed2026091013",
)


def finite(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resource_snapshot() -> dict[str, str]:
    def run(command: list[str]) -> str:
        completed = subprocess.run(
            command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=False,
        )
        return completed.stdout.strip()

    return {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": platform.node(),
        "nvidia_smi": run(["nvidia-smi"]),
        "free_h": run(["free", "-h"]),
        "df_h_workspace": run(["df", "-h", "."]),
    }


def pass_variants() -> list[Path]:
    variants: list[Path] = []
    seen: set[Path] = set()
    for root in DEFAULT_ROOTS:
        for summary_path in sorted(root.rglob("variant_summary.json")):
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            source = summary_path.parent.resolve()
            if summary.get("status") == "pass" and source not in seen:
                variants.append(source)
                seen.add(source)
    return variants


def source_xml(source: Path) -> Path:
    path = source / "stage2/config/temp_config_stage2_period_0000.xml"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def redirected_xml(source: Path, output: Path) -> Path:
    tree = ET.parse(source_xml(source))
    root = tree.getroot()
    result_node = root.find(".//result_add")
    data_node = root.find(".//GMTI_data_new")
    if result_node is None or data_node is None or not data_node.text:
        raise RuntimeError(f"生产 XML 缺少 result_add/GMTI_data_new：{source}")
    result_dir = output / "stage2/algorithm_result/period_0000"
    result_dir.mkdir(parents=True, exist_ok=True)
    result_node.text = str(result_dir.resolve())
    config_path = output / "stage2/config/production_diagnostic.xml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(config_path, encoding="utf-8", xml_declaration=True)
    patch_production_xml(config_path)
    return config_path


def metric_row(source: Path, output: Path, config_path: Path,
               return_code: int) -> dict[str, Any]:
    detection = output / "stage2/algorithm_result/period_0000/detection_results_GMTI01.csv"
    p38_values: list[float] = []
    cfar_values: list[float] = []
    if detection.is_file():
        with detection.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                p38 = finite(row.get("p38_refit_inlier_ratio"))
                if not math.isfinite(p38):
                    p38 = finite(row.get("p38_raw_inlier_ratio"))
                margin = finite(row.get("cfar_margin_db"))
                if math.isfinite(p38):
                    p38_values.append(p38)
                if math.isfinite(margin):
                    cfar_values.append(margin)
    status = "pass" if return_code == 0 else "gmticore_failed"
    if return_code == 0 and not p38_values:
        status = "missing_p38_inlier_ratio"
    if return_code == 0 and not cfar_values:
        status = "missing_cfar_margin"
    return {
        "source_variant": str(source),
        "output_dir": str(output),
        "config_path": str(config_path),
        "config_sha256": sha256_file(config_path),
        "gmticore_exit_code": return_code,
        "status": status,
        "detection_csv": str(detection),
        "detection_count": len(p38_values),
        "p38_inlier_ratio_mean": float(sum(p38_values) / len(p38_values)) if p38_values else math.nan,
        "p38_inlier_ratio_min": min(p38_values) if p38_values else math.nan,
        "p38_inlier_ratio_max": max(p38_values) if p38_values else math.nan,
        "cfar_margin_db_mean": float(sum(cfar_values) / len(cfar_values)) if cfar_values else math.nan,
        "cfar_margin_db_min": min(cfar_values) if cfar_values else math.nan,
        "cfar_margin_db_max": max(cfar_values) if cfar_values else math.nan,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "source_variant", "output_dir", "config_path", "config_sha256",
        "gmticore_exit_code", "status", "detection_csv", "detection_count",
        "p38_inlier_ratio_mean", "p38_inlier_ratio_min", "p38_inlier_ratio_max",
        "cfar_margin_db_mean", "cfar_margin_db_min", "cfar_margin_db_max",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path,
        default=ROOT / "outputs/ai_csi_model_mismatch_p38_cfar_diagnostics",
    )
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--case", type=Path)
    parser.add_argument("--reuse-existing", action="store_true")
    args = parser.parse_args()
    build_dir = args.build_dir.resolve()
    binary = build_dir / "GMTI_core"
    if not binary.is_file():
        raise SystemExit(f"找不到构建产物：{binary}")
    output_root = args.output_dir.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    # Request the production diagnostic path to download power/threshold maps
    # for the selected beam.  This is an environment-only diagnostic switch;
    # it does not alter the detector or its configured CFAR parameters.
    os.environ["GMTI_CFAR_DUMP_BEAM"] = "1"
    variants = [args.case.resolve()] if args.case else pass_variants()
    if not variants:
        raise SystemExit("没有找到 pass challenge variant")
    before = resource_snapshot()
    rows: list[dict[str, Any]] = []
    for index, source in enumerate(variants, 1):
        relative = source.relative_to(ROOT)
        output = output_root / relative
        summary_path = output / "diagnostic_summary.json"
        if args.reuse_existing and summary_path.is_file():
            row = json.loads(summary_path.read_text(encoding="utf-8"))
        else:
            output.mkdir(parents=True, exist_ok=True)
            config_path = redirected_xml(source, output)
            log_path = output / "gmticore.log"
            print(f"[diagnostic] {index}/{len(variants)} {source} start", flush=True)
            return_code = run_logged(
                [str(binary), str(config_path), "--runtime-mode=debug",
                 "--runtime-diagnostics=on"], log_path,
            )
            row = metric_row(source, output, config_path, return_code)
            summary_path.write_text(
                json.dumps(row, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"[diagnostic] {index}/{len(variants)} {source} {row['status']}", flush=True)
        rows.append(row)
    compact = output_root / "production_diagnostic_metrics.csv"
    write_csv(compact, rows)
    manifest = {
        "schema_version": 1,
        "ai_training": False,
        **git_provenance(ROOT),
        "variant_count": len(variants),
        "resource_snapshot_before": before,
        "resource_snapshot_after": resource_snapshot(),
        "rows": rows,
        "definitions": {
            "p38_inlier_ratio": "mean/min/max finite p38_refit_inlier_ratio over production detection rows",
            "cfar_margin": "mean/min/max finite candidate 10log10(power_map / production CFAR threshold_map) in dB",
            "input_policy": "reuse existing pass variant raw input XML; redirect result_add only; never regenerate or overwrite challenge/control outputs",
        },
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    failed = sum(row.get("status") != "pass" for row in rows)
    print(json.dumps({"output_dir": str(output_root), "variant_count": len(rows), "failed": failed}, ensure_ascii=False))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
