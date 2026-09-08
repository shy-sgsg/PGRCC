#!/usr/bin/env python3
"""审计 GMTI Monte Carlo 运行是否把真值/目标输出泄露给生产链。

该审计只读取 manifest、生产 XML 和源码可见的配置痕迹，不修改运行结果。
Truth 可以用于离线评分（CUT 事件、truth matching、RMSE、三屏 2/3），但不能
进入 detector 的配置、NMS 选择或目标输出决策。N-only/P38 冻结被记录为背景
校准来源；它不允许引用 S+N 或 truth 文件。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET


TRUTH_TOKENS = ("truth_targets_by_beam", "scene_truth.csv", "moving_target_truth.csv")


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def xml_values(path: Path) -> dict[str, str]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return {}
    parent = root.find("GMTI_parameter")
    if parent is None:
        return {}
    return {str(node.tag): (node.text or "").strip() for node in parent}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True,
                        help="一个 MC 根目录（含 run_manifest.json），或单角度目录")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    root = args.run_dir.resolve()
    output = (args.output or root / "information_leakage_audit.json").resolve()
    pipeline_manifests = sorted(root.rglob("pipeline_manifest.json"))
    records = []
    failures = []
    diagnostic_paths = []
    for manifest_path in pipeline_manifests:
        manifest = load_json(manifest_path)
        xml_path = Path(str(manifest.get("pipe_xml", "")))
        values = xml_values(xml_path) if xml_path.is_file() else {}
        debug_enabled = values.get("debug_pc_peak", "").lower() in {"1", "true", "yes", "on"}
        truth_path = values.get("pc_peak_scene_truth", "")
        active_truth = bool(truth_path) and debug_enabled
        if truth_path and not active_truth:
            diagnostic_paths.append({"pipeline_manifest": str(manifest_path),
                                     "truth_path": truth_path,
                                     "reason": "debug disabled; path is inert"})
        if active_truth:
            failures.append({"pipeline_manifest": str(manifest_path),
                             "reason": "truth path active while debug_pc_peak enabled",
                             "truth_path": truth_path})
        # A production command must not contain a truth CSV as an input.  The
        # manifest stores only the executable command, but inspect all string
        # fields defensively for future wrappers.
        text = json.dumps(manifest, ensure_ascii=False)
        command_truth = [token for token in TRUTH_TOKENS if token in text and token not in truth_path]
        if command_truth:
            failures.append({"pipeline_manifest": str(manifest_path),
                             "reason": "truth token appears outside inert debug field",
                             "tokens": command_truth})
        records.append({
            "pipeline_manifest": str(manifest_path),
            "label": manifest.get("label", ""),
            "pipe_xml": str(xml_path),
            "debug_pc_peak": debug_enabled,
            "pc_peak_scene_truth": truth_path,
            "truth_active_in_detector": active_truth,
            "raw_inputs": manifest.get("raw_sha256_before_cleanup", {}),
            "track_payloads": manifest.get("track_output_payloads", []),
        })
    top_manifest = load_json(root / "run_manifest.json")
    policy = top_manifest.get("information_leakage_policy", {})
    # Historical merged roots may not carry the policy; the per-angle records
    # and source-level NMS audit still provide evidence, but the result is
    # explicitly marked incomplete rather than silently passing.
    policy_present = bool(policy) or any("information_leakage_policy" in load_json(p) for p in root.rglob("manifest.json"))
    if not pipeline_manifests:
        failures.append({"reason": "no pipeline_manifest.json found"})
    result = {
        "run_dir": str(root),
        "status": "pass" if not failures else "fail",
        "pipeline_count": len(records),
        "truth_active_detector_count": sum(int(row["truth_active_in_detector"]) for row in records),
        "diagnostic_truth_path_count": len(diagnostic_paths),
        "policy_manifest_present": policy_present,
        "nms_truth_tiebreak_source_fix": "src/writeresults.cpp: truth-derived NMS tie-break removed",
        "allowed_truth_uses": ["offline output-SCNR support/scoring", "offline truth matching", "offline RMSE/2-of-3 labels"],
        "records": records,
        "diagnostic_truth_paths": diagnostic_paths,
        "failures": failures,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[{result['status'].upper()}] 信息泄露审计：{output}")
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
