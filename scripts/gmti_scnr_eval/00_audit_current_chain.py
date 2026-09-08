#!/usr/bin/env python3
"""冻结当前正式 GMTI 相扫/GO-CFAR 处理链参数并生成 T0 审查产物。"""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from scnr_eval_lib import GoCfarGeometry, ensure_dir, sha256_file, write_csv


ROOT = Path(__file__).resolve().parents[2]


def xml_values(path: Path) -> dict[str, str]:
    root = ET.parse(path).getroot()
    values: dict[str, str] = {}
    for element in root.iter():
        if element is root or element.text is None:
            continue
        text = element.text.strip()
        if text:
            values[element.tag] = text
    return values


def git_identity() -> dict[str, str]:
    def command(args: list[str]) -> str:
        result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    return {
        "commit": command(["git", "rev-parse", "HEAD"]),
        "status_porcelain": command(["git", "status", "--short", "--untracked-files=all"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", type=Path, default=ROOT / "gmti.xml")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "outputs/gmti_scnr_eval/baseline")
    parser.add_argument("--docs-dir", type=Path,
                        default=ROOT / "docs/gmti_scnr_eval")
    args = parser.parse_args()
    xml = args.xml.resolve()
    if not xml.is_file():
        raise SystemExit(f"找不到正式 XML：{xml}")
    values = xml_values(xml)
    cfar_type = values.get("cfar_type", "").upper()
    split_type = values.get("csi_split_in_band_cfar_type", cfar_type).upper()
    if cfar_type != "GO" or split_type != "GO":
        raise SystemExit("当前专项仅接受正式 GO-CFAR XML；cfar_type 与带内 CFAR 均必须为 GO")
    guard = int(values["cfar_guard_cells"])
    background = int(values["cfar_background_cells"])
    pfa = float(values["pf"])
    geometry = GoCfarGeometry(guard, background)
    alpha = geometry.alpha(pfa)
    output_dir = ensure_dir(args.output_dir.resolve())
    docs_dir = ensure_dir(args.docs_dir.resolve())

    source = {
        "pf": ("src/loadXML.cpp:679", "cfg.pf", "配置的单元级虚警概率参数"),
        "cfar_guard_cells": ("src/loadXML.cpp:1117", "cfg.cfar_guard_cells", "二维保护窗半宽"),
        "cfar_background_cells": ("src/loadXML.cpp:1119", "cfg.cfar_background_cells", "二维背景带厚度"),
        "cfar_type": ("src/loadXML.cpp:1134", "cfg.cfar_type", "CFAR 类型"),
        "cfar_doppler_circular": ("src/loadXML.cpp:1121", "cfg.cfar_doppler_circular", "Doppler 维环形窗口"),
        "pulse_num": ("src/loadXML.cpp:643", "cfg.pulse_num", "每波位脉冲数"),
        "PRF": ("src/loadXML.cpp:670", "cfg.PRF", "脉冲重复频率"),
        "fc": ("src/loadXML.cpp:666", "cfg.fc", "载频（XML 单位 GHz）"),
        "d_chan": ("src/loadXML.cpp:678", "cfg.d_channel", "接收相位中心基线"),
        "scan_step_deg": ("src/loadXML.cpp:1065", "cfg.scan_step_deg", "电子扫描波位间隔"),
        "beam_width_deg": ("src/loadXML.cpp:1067", "cfg.beam_width_deg", "波束宽度"),
        "track_confirm_window": ("src/loadXML.cpp:1003", "cfg.track_confirm_window", "航迹确认滑窗"),
        "track_confirm_hits": ("src/loadXML.cpp:1005", "cfg.track_confirm_hits", "滑窗最少命中数"),
    }
    freeze_rows: list[dict[str, object]] = []
    for key, (source_file, source_function, physical) in source.items():
        value = values.get(key, "")
        freeze_rows.append({
            "parameter_name": key,
            "value": value,
            "unit": {"pf": "1", "cfar_guard_cells": "cell", "cfar_background_cells": "cell",
                     "pulse_num": "pulse", "PRF": "Hz", "fc": "GHz", "d_chan": "m",
                     "scan_step_deg": "deg", "beam_width_deg": "deg"}.get(key, "1"),
            "xml_key": key,
            "source_file": source_file,
            "source_function": source_function,
            "default_value": "见 include/config_structs.hpp",
            "runtime_value": value,
            "physical_meaning": physical,
            "why_used": "当前正式 gmti.xml 的运行时取值",
        })
    freeze_rows.extend([
        {"parameter_name": "go_outer_window_width", "value": geometry.outer_width,
         "unit": "cell", "xml_key": "derived", "source_file": "src/gpu/gpu_kernels.cu:461",
         "source_function": "cfar_detect_kernel", "default_value": "N/A", "runtime_value": geometry.outer_width,
         "physical_meaning": "全 CFAR 窗边长", "why_used": "R=g+b，边长 2R+1"},
        {"parameter_name": "go_alpha_cell_count", "value": geometry.code_total_background_cells,
         "unit": "cell", "xml_key": "derived", "source_file": "src/gpu/gpu_kernels.cu:2266",
         "source_function": "dpca_cfar2_fast_cuda", "default_value": "N/A",
         "runtime_value": geometry.code_total_background_cells,
         "physical_meaning": "源码 α 公式使用的环形背景单元数", "why_used": "严格复现源码"},
        {"parameter_name": "go_directional_strip_cells", "value": geometry.directional_strip_cells,
         "unit": "cell", "xml_key": "derived", "source_file": "src/gpu/gpu_kernels.cu:487",
         "source_function": "cfar_detect_kernel", "default_value": "N/A",
         "runtime_value": geometry.directional_strip_cells,
         "physical_meaning": "每个 GO 方向条带面积", "why_used": "计算最大方向均值"},
        {"parameter_name": "go_alpha", "value": f"{alpha:.15g}", "unit": "1", "xml_key": "derived",
         "source_file": "src/gpu/gpu_kernels.cu:2285", "source_function": "dpca_cfar2_fast_cuda",
         "default_value": "N/A", "runtime_value": f"{alpha:.15g}",
         "physical_meaning": "源码实际门限倍率", "why_used": "N*(pf^(-1/N)-1)"},
    ])
    write_csv(output_dir / "parameter_freeze.csv", freeze_rows)
    write_csv(output_dir / "cfar_parameter_audit.csv", [{
        "cfar_type": "GO", "dimensions": "二维距离-多普勒", "doppler_boundary": "circular",
        "configured_pfa": pfa, "guard_half_width_cells": guard,
        "background_thickness_cells": background, "outer_window_width_cells": geometry.outer_width,
        "ring_cell_count_used_for_alpha": geometry.code_total_background_cells,
        "each_directional_strip_cells": geometry.directional_strip_cells,
        "noise_estimator": "max(mean(left), mean(right), mean(top), mean(bottom))",
        "alpha": alpha, "alpha_formula": "N*(pf^(-1/N)-1), N=ring_cell_count",
        "edge_handling": "Doppler circular; range edge R=g+b cells excluded",
        "absolute_threshold": "无；阈值为 alpha*GO 局部背景估计",
        "secondary_threshold": "聚类 min_points=10 和相位一致性/目标筛选，不属于 CFAR CUT 门限",
        "code_evidence": "src/gpu/gpu_kernels.cu:429-530,2258-2394; src/detectAndLocating.cpp:69-250",
    }])
    runtime = {
        "formal_xml": str(xml), "xml_sha256": sha256_file(xml), "git": git_identity(),
        "cfar": {"type": "GO", "configured_pfa": pfa, "guard_half_width": guard,
                 "background_thickness": background, "alpha": alpha,
                 "ring_cell_count": geometry.code_total_background_cells,
                 "directional_strip_cells": geometry.directional_strip_cells},
        "simulation_target_model": {
            "amplitude": "Stage2 snr_db maps deterministic local-background RMS to point-chirp amplitude",
            "fluctuation": "未实现跨脉冲/跨周期随机 RCS 起伏；载波相位仅由几何路径确定",
            "evidence": "simulator/target_injection/lfm_echo_generator.cpp:210-225,570-600",
        },
    }
    (output_dir / "runtime_config.json").write_text(json.dumps(runtime, ensure_ascii=False, indent=2) + "\n",
                                                   encoding="utf-8")
    report = f"""# 当前 GMTI 相扫处理链与参数审查（T0）

## 结论

当前正式配置 `{xml.name}`（路径：`{xml}`）使用 **二维 GO-CFAR**：

- `pf={pfa:g}`；`g={guard}`；`b={background}`；Doppler 维环形处理；
- 全窗 `({geometry.outer_width}×{geometry.outer_width})`，保护窗 `({geometry.guard_width}×{geometry.guard_width})`；
- 源码用于 α 的环形计数为 `N={geometry.code_total_background_cells}`，`alpha={alpha:.8g}`；
- 实际背景估计为上下左右四个 `{geometry.outer_width}×{background}` 条带均值的最大值。四个条带共享四个角块，因此条件积分必须保留其相关性。

## 已审查生产链

`src/processOnePeriod.cpp` 的正式相扫融合路径依次执行脉压、Doppler/DBS、P38、CSI、GO-CFAR、聚类、target_select、P38/CTDR/运动补偿重定位和 DBS 融合；`src/TrackManager.cpp` 使用 `track_confirm_window={values.get('track_confirm_window','')}`、`track_confirm_hits={values.get('track_confirm_hits','')}` 实现三屏选二确认。

GPU CFAR 位于 `src/gpu/gpu_kernels.cu:429-530`，CPU 回退位于 `src/detectAndLocating.cpp:69-250`；二者均使用同一 GO 定义和 `alpha=N(p_f^{{-1/N}}-1)`。

## Stage2 目标模型

Stage2 的 `snr_db` 并非 RCS 起伏模型：它以目标外的局部原始 LFM RMS 计算确定性点目标 chirp 幅度，并除以脉压相干采样增益；载波相位由确定的通道几何光程给出。未发现 Swerling I/II/III/IV 跨脉冲 RCS 随机起伏。因此本专项的单元级理论使用确定复幅度目标；不同 seed 改变热噪声/杂波随机场而不改变同一 target 的 RCS 起伏模型。

## 产物

- `outputs/gmti_scnr_eval/baseline/parameter_freeze.csv`
- `outputs/gmti_scnr_eval/baseline/cfar_parameter_audit.csv`
- `outputs/gmti_scnr_eval/baseline/runtime_config.json`
"""
    (docs_dir / "00_当前处理链审查.md").write_text(report, encoding="utf-8")
    print(f"[PASS] 已冻结 GO-CFAR 正式参数：{output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
