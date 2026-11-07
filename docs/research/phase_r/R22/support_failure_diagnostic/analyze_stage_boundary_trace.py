#!/usr/bin/env python3
"""Summarize paired ON/OFF/TO closure across the captured R22 S1b stages."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


ROLES = ("ON", "OFF", "TO")
KEY_FIELDS = ("period_id", "beam_id", "stage", "azimuth_index", "range_index")


def complex_value(row: dict[str, str], real: str, imag: str) -> complex:
    return complex(float(row[real]), float(row[imag]))


def l2(values: list[complex]) -> float:
    return math.sqrt(sum(abs(value) ** 2 for value in values))


def relative_l2(residual: list[complex], reference: list[complex]) -> float | None:
    denominator = l2(reference)
    return l2(residual) / denominator if denominator > 0.0 else None


def all_finite(values: list[complex]) -> bool:
    return all(math.isfinite(value.real) and math.isfinite(value.imag) for value in values)


def closure_metric(on: list[complex], off: list[complex], to: list[complex]) -> dict[str, Any]:
    if not all(all_finite(values) for values in (on, off, to)):
        return {"status": "NOT_AVAILABLE", "residual_l2": None,
                "relative_l2_to_on": None}
    residual = [a - b - c for a, b, c in zip(on, off, to)]
    return {
        "status": "EVALUABLE",
        "residual_l2": l2(residual),
        "relative_l2_to_on": relative_l2(residual, on),
    }


def read_trace(path: Path) -> dict[tuple[str, ...], dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError(f"trace has no data rows: {path}")
    output: dict[tuple[str, ...], dict[str, str]] = {}
    for row in rows:
        key = tuple(row[field] for field in KEY_FIELDS)
        if key in output:
            raise ValueError(f"duplicate trace cell {key} in {path}")
        output[key] = row
    return output


def compare_modeb_failure_input(
    on_rows: dict[tuple[str, ...], dict[str, str]], failure_cells_path: Path
) -> dict[str, Any]:
    with failure_cells_path.open(newline="", encoding="utf-8") as stream:
        failed_rows = [
            row for row in csv.DictReader(stream)
            if row["period_id"] == "5" and row["beam_id"] == "50" and
            row["az_index"] == "52" and row["range_start"] == "1065" and
            row["range_end"] == "1197"
        ]
    replay_rows = {
        int(row["range_index"]): row
        for key, row in on_rows.items()
        if key[2] == "csi_post_range_phase_pre_c4_adapter" and
        key[0] == "5" and key[1] == "50" and key[3] == "52" and
        1065 <= int(key[4]) <= 1197
    }
    old_by_range = {int(row["range_index"]): row for row in failed_rows}
    common = set(replay_rows) & set(old_by_range)
    old_fields = ("f1_real", "f1_imag", "f2_real", "f2_imag")
    replay_fields = old_fields
    exact = (
        len(old_by_range) == 133 and len(replay_rows) == 133 and
        set(old_by_range) == set(replay_rows) and
        all(
            tuple(old_by_range[index][field] for field in old_fields) ==
            tuple(replay_rows[index][field] for field in replay_fields)
            for index in common
        )
    )
    supports = sorted({row["adapter_support_count"] for row in failed_rows})
    reasons = sorted({row["group_reason"] for row in failed_rows})
    return {
        "status": "exact_match" if exact else "MISMATCH_OR_INCOMPLETE",
        "source_modeb_failure_cells": str(failure_cells_path),
        "source_rows": len(old_by_range),
        "s1b_pre_adapter_rows": len(replay_rows),
        "common_range_cells": len(common),
        "f1_f2_csv_values_exactly_match": exact,
        "source_failure_support_counts": supports,
        "source_failure_reasons": reasons,
        "interpretation": (
            "An exact match ties the S1b Mode-A ON upstream input to the recorded "
            "Mode-B failure input; it does not make the Mode-A reference-assisted "
            "replay a blind deployment evaluation or prove the failure cause."
        ),
    }


def audit_c4_adapter_linearity(
    trace_root: Path,
    role_rows: dict[str, dict[tuple[str, ...], dict[str, str]]],
) -> dict[str, Any]:
    range_indices = range(1065, 1198)
    expected: dict[str, list[complex]] = {}
    actual: dict[str, list[complex]] = {}
    gamma_by_role: dict[str, complex] = {}
    max_abs_error_by_role: dict[str, float] = {}
    relative_l2_error_by_role: dict[str, float | None] = {}
    for role in ROLES:
        pre = {
            int(key[4]): row for key, row in role_rows[role].items()
            if key[0] == "5" and key[1] == "50" and
            key[2] == "csi_post_range_phase_pre_c4_adapter" and
            key[3] == "52" and 1065 <= int(key[4]) <= 1197
        }
        post = {
            int(key[4]): row for key, row in role_rows[role].items()
            if key[0] == "5" and key[1] == "50" and
            key[2] == "csi_post_c4_adapter" and
            key[3] == "52" and 1065 <= int(key[4]) <= 1197
        }
        gamma_path = (
            trace_root / "production" / f"C4_modea_S1b_{role}" / "result" /
            "production_calibration_reference_gamma.csv"
        )
        if len(pre) != 133 or len(post) != 133 or not gamma_path.is_file():
            return {
                "status": "NOT_EVALUABLE",
                "reason": f"missing_selected_cells_or_gamma:{role}",
            }
        with gamma_path.open(newline="", encoding="utf-8") as stream:
            gamma_rows = [
                row for row in csv.DictReader(stream)
                if row["period_id"] == "5" and row["beam_id"] == "50" and
                row["az_index"] == "52" and row["range_start"] == "1065" and
                row["range_end"] == "1197"
            ]
        if len(gamma_rows) != 1:
            return {
                "status": "NOT_EVALUABLE",
                "reason": f"selected_reference_gamma_count_{role}:{len(gamma_rows)}",
            }
        gamma = complex(
            float(gamma_rows[0]["gamma_real"]),
            float(gamma_rows[0]["gamma_imag"]),
        )
        gamma_by_role[role] = gamma
        role_expected: list[complex] = []
        role_actual: list[complex] = []
        for index in range_indices:
            if index not in pre or index not in post:
                return {
                    "status": "NOT_EVALUABLE",
                    "reason": f"missing_range_cell_{role}:{index}",
                }
            f1 = complex_value(pre[index], "f1_real", "f1_imag")
            f2 = complex_value(pre[index], "f2_real", "f2_imag")
            role_expected.append(f2 - gamma * f1)
            role_actual.append(complex_value(post[index], "output_real", "output_imag"))
        errors = [a - b for a, b in zip(role_expected, role_actual)]
        expected[role] = role_expected
        actual[role] = role_actual
        max_abs_error_by_role[role] = max(abs(value) for value in errors)
        relative_l2_error_by_role[role] = relative_l2(errors, role_actual)

    predicted_closure = [
        on - off - to for on, off, to in zip(
            expected["ON"], expected["OFF"], expected["TO"]
        )
    ]
    actual_closure = [
        on - off - to for on, off, to in zip(
            actual["ON"], actual["OFF"], actual["TO"]
        )
    ]
    closure_delta = [a - b for a, b in zip(predicted_closure, actual_closure)]
    gamma_reference = gamma_by_role["ON"]
    return {
        "status": "EVALUABLE",
        "operator": "F2 - gamma * F1 (production_calibration_adapter.cpp)",
        "cell_count_per_role": len(range_indices),
        "gamma_by_role": {
            role: {"real": value.real, "imag": value.imag}
            for role, value in gamma_by_role.items()
        },
        "max_gamma_abs_difference_from_on": max(
            abs(value - gamma_reference) for value in gamma_by_role.values()
        ),
        "max_abs_formula_error_by_role": max_abs_error_by_role,
        "relative_l2_formula_error_by_role": relative_l2_error_by_role,
        "predicted_output_closure_relative_l2_to_on": relative_l2(
            predicted_closure, actual["ON"]
        ),
        "actual_output_closure_relative_l2_to_on": relative_l2(
            actual_closure, actual["ON"]
        ),
        "predicted_vs_actual_closure_relative_l2_to_on": relative_l2(
            closure_delta, actual["ON"]
        ),
        "interpretation": (
            "The Mode-A adapter applies the same gamma across ON/OFF/TO and its "
            "observed output matches F2-gamma*F1; output nonclosure is therefore "
            "inherited from the pre-adapter inputs within float precision."
        ),
    }


def summarize(
    trace_root: Path, modeb_failure_cells: Path | None = None
) -> dict[str, Any]:
    role_rows: dict[str, dict[tuple[str, ...], dict[str, str]]] = {}
    for role in ROLES:
        path = (
            trace_root / "production" / f"C4_modea_S1b_{role}" / "result" /
            "production_phase_boundary_trace.csv"
        )
        role_rows[role] = read_trace(path)

    keys = set(role_rows["ON"])
    mismatches = {
        role: {
            "missing": len(keys - set(role_rows[role])),
            "extra": len(set(role_rows[role]) - keys),
        }
        for role in ROLES
    }
    if any(item["missing"] or item["extra"] for role, item in mismatches.items()
           if role != "ON"):
        raise ValueError(f"ON/OFF/TO stage cell identities differ: {mismatches}")

    by_stage: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    for key in sorted(keys):
        by_stage[key[2]].append(key)

    summaries: list[dict[str, Any]] = []
    stage_order = (
        "raw_dbs_pre_range_phase",
        "raw_post_range_phase",
        "csi_dbs_pre_range_phase",
        "csi_post_range_phase_pre_c4_adapter",
        "csi_post_c4_adapter",
    )
    for stage in stage_order:
        stage_keys = by_stage.get(stage, [])
        if not stage_keys:
            continue
        rows = {
            role: [role_rows[role][key] for key in stage_keys]
            for role in ROLES
        }
        metrics: dict[str, Any] = {"stage": stage, "cell_count": len(stage_keys)}
        channel_pairs = (
            ("f1", "f1_real", "f1_imag"),
            ("f2", "f2_real", "f2_imag"),
            ("output", "output_real", "output_imag"),
        )
        complex_rows: dict[str, dict[str, list[complex]]] = {}
        for name, real, imag in channel_pairs:
            complex_rows[name] = {
                role: [complex_value(row, real, imag) for row in rows[role]]
                for role in ROLES
            }
        for name in ("f1", "f2", "output"):
            metrics[f"{name}_closure"] = closure_metric(
                complex_rows[name]["ON"],
                complex_rows[name]["OFF"],
                complex_rows[name]["TO"],
            )
        cross = {
            role: [b * a.conjugate() for a, b in zip(
                complex_rows["f1"][role], complex_rows["f2"][role]
            )]
            for role in ROLES
        }
        metrics["f2_conj_f1_nonadditive_difference"] = closure_metric(
            cross["ON"], cross["OFF"], cross["TO"]
        )

        phase_summary: dict[str, Any] = {}
        for role in ROLES:
            phases = [float(row["range_phase_rad"]) for row in rows[role]
                      if math.isfinite(float(row["range_phase_rad"]))]
            slopes = [float(row["p38_slope_rad_per_hz"]) for row in rows[role]
                      if math.isfinite(float(row["p38_slope_rad_per_hz"]))]
            intercepts = [float(row["p38_intercept_rad"]) for row in rows[role]
                          if math.isfinite(float(row["p38_intercept_rad"]))]
            phase_summary[role] = {
                "range_phase_count": len(phases),
                "range_phase_mean_rad": sum(phases) / len(phases) if phases else None,
                "range_phase_min_rad": min(phases) if phases else None,
                "range_phase_max_rad": max(phases) if phases else None,
                "p38_slope_rad_per_hz": slopes[0] if slopes else None,
                "p38_intercept_rad": intercepts[0] if intercepts else None,
                "p38_values_consistent_across_cells": (
                    max(slopes) - min(slopes) <= 1.0e-6 and
                    max(intercepts) - min(intercepts) <= 1.0e-6
                    if slopes and intercepts else None
                ),
            }
        metrics["phase_and_p38"] = phase_summary
        summaries.append(metrics)

    return {
        "trace_root": str(trace_root),
        "roles": list(ROLES),
        "shared_cell_count": len(keys),
        "processing_lineage": {
            "raw_path": [
                "raw_dbs_pre_range_phase",
                "raw_post_range_phase",
            ],
            "csi_path": [
                "csi_dbs_pre_range_phase",
                "csi_post_range_phase_pre_c4_adapter",
                "csi_post_c4_adapter",
            ],
            "boundary_note": (
                "The CSI path reloads original d1/d2 before its own align/FFT/DBS; "
                "raw_post_range_phase is a separate branch, not the parent of "
                "csi_dbs_pre_range_phase."
            ),
        },
        "role_key_mismatches": mismatches,
        "modeb_failure_input_match": (
            compare_modeb_failure_input(role_rows["ON"], modeb_failure_cells)
            if modeb_failure_cells is not None else None
        ),
        "c4_adapter_linearity": audit_c4_adapter_linearity(trace_root, role_rows),
        "closure_definition": (
            "For complex F1/F2 only: ON - OFF - TO; relative L2 denominator is "
            "ON L2. F2*conj(F1) is bilinear and reported as a non-additive "
            "difference, not a closure gate."
        ),
        "stages": summaries,
    }


def markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# R22-S1b 处理阶段闭合审计",
        "",
        "- 对齐单元数：`%s`" % summary["shared_cell_count"],
        "- F1/F2 闭合：`ON - OFF - TO`；相对 L2 分母为 ON 的 L2。",
        "- `F2·conj(F1)` 是双线性量，列出其差值作诊断，不作为闭合门。",
        "- raw 与 CSI 从原始输入分叉，不能串读为同一条处理链。",
        "- 该文件是单 seed、单 beam 的因果诊断，不构成检测性能结论。",
        "",
        "| Path | Stage | Cells | F1 rel-L2 | F2 rel-L2 | F2·conj(F1) difference | Output rel-L2 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    modeb_match = summary.get("modeb_failure_input_match")
    if modeb_match is not None:
        lines.insert(
            3,
            "- Mode-B 失败输入对照：`%s`，逐值相同 %s/133 cells。" % (
                modeb_match["status"],
                modeb_match["common_range_cells"],
            ),
        )
    adapter = summary.get("c4_adapter_linearity")
    if adapter and adapter.get("status") == "EVALUABLE":
        lines.insert(
            4,
            "- C4 线性算子核对：`F2−γF1`，最大公式相对 L2 误差 %s。" % (
                f"{max(adapter['relative_l2_formula_error_by_role'].values()):.3g}"
            ),
        )
    raw_stages = {"raw_dbs_pre_range_phase", "raw_post_range_phase"}
    for item in summary["stages"]:
        def number(value: float | None) -> str:
            return "NOT_AVAILABLE" if value is None else f"{value:.9g}"

        lines.append(
            f"| {'raw' if item['stage'] in raw_stages else 'CSI'} | "
            f"{item['stage']} | {item['cell_count']} | "
            f"{number(item['f1_closure']['relative_l2_to_on'])} | "
            f"{number(item['f2_closure']['relative_l2_to_on'])} | "
            f"{number(item['f2_conj_f1_nonadditive_difference']['relative_l2_to_on'])} | "
            f"{number(item['output_closure']['relative_l2_to_on'])} |"
        )
    lines.append("")
    lines.append("详见相邻 JSON，包含各阶段 range phase 与 P38 拟合值摘要。")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--modeb-failure-cells", type=Path)
    args = parser.parse_args()
    result = summarize(
        args.trace_root,
        args.modeb_failure_cells,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "stage_boundary_closure.json"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "stage_boundary_closure.md").write_text(
        markdown(result), encoding="utf-8"
    )
    print(json.dumps({"status": "passed", "summary": str(json_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
