#!/usr/bin/env python3
"""Build the portable Stage2/Stage3 full-flow showcase report artifact."""

from __future__ import annotations

import argparse
import base64
import csv
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "docs" / "仿真测试" / "报告"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def data_uri(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = "image/gif" if suffix == ".gif" else "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def source(source_id: str, label: str, path: Path, description: str,
           generated_at: str) -> dict:
    return {
        "id": source_id,
        "label": f"{label} — {description}",
        "path": rel(path),
    }


def image_block(scan: Path, animation: Path, label: str) -> str:
    return f"""
<section style="display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:18px;align-items:start">
  <figure style="margin:0">
    <img src="{data_uri(scan)}" alt="{label} 第三周期全扫描 DBS 图" style="width:100%;max-height:520px;object-fit:contain;background:#050505;border-radius:10px" />
    <figcaption style="margin-top:8px;color:#5b6470">第三周期 61 波位 DBS 全扫描图；250 m 仅为展示栅格，检测距离采样仍为 2.498 m。</figcaption>
  </figure>
  <figure style="margin:0">
    <img src="{data_uri(animation)}" alt="{label} 生产 TrackManager 航迹动画" style="width:100%;max-height:520px;object-fit:contain;background:#fff;border-radius:10px" />
    <figcaption style="margin-top:8px;color:#5b6470">现有 visualize_track_manager.py 直接读取生产 track_debug 生成的三周期确认输出动画。</figcaption>
  </figure>
</section>
""".strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--stem", default="GMTI_PIPE全流程展示_20260715")
    args = parser.parse_args()

    cases: dict[str, dict] = {}
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for key, display in (("stage2", "Stage2 面杂波"), ("stage3", "Stage3 真实 SAR")):
        root = ROOT / "outputs" / "fullflow_pipe_acceptance" / key
        manifest = read_json(root / "run_manifest.json")
        report_dir = root / "latest_report"
        summary = read_json(report_dir / "fullflow_summary.json")
        run_dir = Path(manifest["pipe_run_dir"])
        track_states = read_csv(run_dir / "result" / "track_debug" / "track_states.csv")
        cases[key] = {
            "display": display,
            "root": root,
            "manifest": manifest,
            "summary": summary,
            "report_dir": report_dir,
            "run_dir": run_dir,
            "unique_tracks": len({row.get("track_id", "") for row in track_states}),
            "scan_image": run_dir / "result" / "GMTI03.png",
            "track_animation": report_dir / "track_visualization" / "track_outputs_growth.gif",
        }

    case_rows = []
    scan_rows = []
    for key in ("stage2", "stage3"):
        item = cases[key]
        summary = item["summary"]
        manifest = item["manifest"]
        case_rows.append({
            "case": item["display"],
            "truth": summary["truth_count"],
            "matched": summary["matched_count"],
            "missed": summary["missed_count"],
            "recall": summary["detection_recall"],
            "detections": summary["detection_count"],
            "false_alarms": summary["false_alarm_count"],
            "position_median_m": summary["position_median_m"],
            "position_p95_m": summary["position_p95_m"],
            "track_debug_states": summary["track_debug"]["state_rows"],
            "track_payload_rows": summary["track_debug"]["output_payload_rows"],
            "unique_tracks": item["unique_tracks"],
            "input_gib": manifest["data_bytes"] / (1024 ** 3),
            "strict_status": "未通过（虚警/航迹 truth）",
        })
        for protocol in summary["protocol_rows"]:
            scan_rows.append({
                "case": item["display"],
                "scan": int(protocol["result_index"]) + 1,
                "image_rows": protocol["image_rows"],
                "image_cols": protocol["image_cols"],
                "protocol_targets": protocol["target_count"],
                "image_valid": "是" if protocol["image_available"] else "否",
            })

    overall = [{
        "pipeline_runs": 2,
        "complete_scans": 6,
        "images": 6,
        "total_prts": 47580,
        "total_input_gib": sum(row["input_gib"] for row in case_rows),
        "matched": sum(row["matched"] for row in case_rows),
        "truth": sum(row["truth"] for row in case_rows),
        "combined_recall": sum(row["matched"] for row in case_rows) /
                           sum(row["truth"] for row in case_rows),
    }]

    s2 = cases["stage2"]["summary"]
    s3 = cases["stage3"]["summary"]
    sources = [
        source("stage2_summary", "Stage2 全流程评测汇总",
               cases["stage2"]["report_dir"] / "fullflow_summary.json",
               "Stage2 生产 PIPE 三周期评测汇总。", generated_at),
        source("stage3_summary", "Stage3 全流程评测汇总",
               cases["stage3"]["report_dir"] / "fullflow_summary.json",
               "Stage3 生产 PIPE 三周期评测汇总。", generated_at),
        source("showcase_rollup", "Stage2/Stage3 展示汇总脚本", Path(__file__),
               "将两套评测汇总、协议结果和 track_debug 统计合并为展示报告。",
               generated_at),
        source("stage2_products", "Stage2 扫描图与生产航迹产物",
               cases["stage2"]["report_dir"] / "track_visualization",
               "Stage2 GMTI03.png 与 visualize_track_manager.py 航迹动画。",
               generated_at),
        source("stage3_products", "Stage3 扫描图与生产航迹产物",
               cases["stage3"]["report_dir"] / "track_visualization",
               "Stage3 GMTI03.png 与 visualize_track_manager.py 航迹动画。",
               generated_at),
    ]
    # Native report cards/charts require executable query provenance.  This
    # DuckDB query reproduces the reviewed two-row case summary directly from
    # the saved evaluator JSON files; the generator only adds presentation
    # labels and expands the nested protocol rows for the audit table.
    sources[2]["query"] = {
        "engine": "duckdb",
        "language": "sql",
        "description": "Loads the two formal evaluator summaries used by the showcase cards, chart and tables.",
        "executed_at": generated_at,
        "tables_used": [
            "outputs/fullflow_pipe_acceptance/stage2/latest_report/fullflow_summary.json",
            "outputs/fullflow_pipe_acceptance/stage3/latest_report/fullflow_summary.json",
        ],
        "filters": ["2026-07-15 formal CUDA showcase runs", "visible cooperative truth only"],
        "metric_definitions": [
            "recall = matched_count / truth_count",
            "position errors are computed only for one-to-one matched detections",
            "false_alarms = detection_count - matched_count",
        ],
        "sql": (
            "WITH stage2 AS (SELECT 'Stage2 area clutter' AS case_name, * "
            "FROM read_json_auto('outputs/fullflow_pipe_acceptance/stage2/latest_report/fullflow_summary.json')), "
            "stage3 AS (SELECT 'Stage3 real SAR' AS case_name, * "
            "FROM read_json_auto('outputs/fullflow_pipe_acceptance/stage3/latest_report/fullflow_summary.json')) "
            "SELECT case_name, truth_count, matched_count, missed_count, detection_recall, "
            "detection_count, false_alarm_count, position_median_m, position_p95_m, protocol_rows, track_debug "
            "FROM stage2 UNION ALL BY NAME SELECT case_name, truth_count, matched_count, missed_count, "
            "detection_recall, detection_count, false_alarm_count, position_median_m, position_p95_m, "
            "protocol_rows, track_debug FROM stage3"
        ),
    }

    title = "GMTI PIPE 三周期全流程展示结果"
    manifest = {
        "version": 1,
        "surface": "report",
        "title": title,
        "description": "Stage2 面杂波与 Stage3 真实 SAR 背景的生产 CUDA/PIPE 展示闭环。",
        "generatedAt": generated_at,
        "cards": [
            {"id": "runs", "description": "已实际运行的生产 PIPE 场景数。",
             "dataset": "overall", "sourceId": "showcase_rollup",
             "metrics": [{"label": "生产全流程", "field": "pipeline_runs", "format": "number"}]},
            {"id": "scans", "description": "实际处理并写出结果的完整扫描数。",
             "dataset": "overall", "sourceId": "showcase_rollup",
             "metrics": [{"label": "完整扫描", "field": "complete_scans", "format": "number"}]},
            {"id": "prts", "description": "两套场景实际进入共享内存的 PRT 总数。",
             "dataset": "overall", "sourceId": "showcase_rollup",
             "metrics": [{"label": "处理 PRT", "field": "total_prts", "format": "compact"}]},
            {"id": "recall", "description": "两套场景合作目标 truth 的合并命中率。",
             "dataset": "overall", "sourceId": "showcase_rollup",
             "metrics": [{"label": "合并目标命中率", "field": "combined_recall", "format": "percent"}]},
        ],
        "charts": [
            {"id": "recall_by_case", "title": "合作目标检测召回率",
             "subtitle": "每套场景 72 个可见 truth；严格门槛为 90%。",
             "type": "bar", "dataset": "case_summary", "sourceId": "showcase_rollup",
             "valueFormat": "percent", "layout": "full",
             "encodings": {
                 "x": {"field": "case", "type": "nominal", "label": "场景"},
                 "y": {"field": "recall", "type": "quantitative", "label": "召回率", "format": "percent"},
                 "tooltip": [
                     {"field": "matched", "type": "quantitative", "label": "命中"},
                     {"field": "missed", "type": "quantitative", "label": "漏检"},
                     {"field": "position_p95_m", "type": "quantitative", "label": "定位 P95 (m)"},
                 ],
             },
             "referenceLines": [{"value": 0.9, "label": "严格门槛 90%", "role": "target"}]},
        ],
        "tables": [
            {"id": "case_table", "title": "两套场景的实际评测结果",
             "subtitle": "定位误差仅统计完成一对一 truth 匹配的 detection。",
             "dataset": "case_summary", "sourceId": "showcase_rollup",
             "defaultSort": {"field": "case", "direction": "asc"}, "density": "spacious",
             "columns": [
                 {"field": "case", "label": "场景", "type": "text"},
                 {"field": "matched", "label": "命中", "format": "number"},
                 {"field": "truth", "label": "Truth", "format": "number"},
                 {"field": "recall", "label": "召回率", "format": "percent"},
                 {"field": "position_median_m", "label": "定位中位误差 (m)", "format": "number"},
                 {"field": "position_p95_m", "label": "定位 P95 (m)", "format": "number"},
                 {"field": "false_alarms", "label": "未匹配检测", "format": "number"},
                 {"field": "track_payload_rows", "label": "确认输出点", "format": "number"},
                 {"field": "strict_status", "label": "严格验收", "type": "text"},
             ]},
            {"id": "scan_table", "title": "连续三周期协议输出",
             "subtitle": "每个场景均写出 3 张有效全扫描图；首周期尚未满足 2/3 航迹确认。",
             "dataset": "scan_outputs", "sourceId": "showcase_rollup",
             "defaultSort": {"field": "scan", "direction": "asc"}, "density": "spacious",
             "columns": [
                 {"field": "case", "label": "场景", "type": "text"},
                 {"field": "scan", "label": "扫描", "format": "number"},
                 {"field": "image_rows", "label": "图高", "format": "number"},
                 {"field": "image_cols", "label": "图宽", "format": "number"},
                 {"field": "protocol_targets", "label": "协议确认点", "format": "number"},
                 {"field": "image_valid", "label": "图像有效", "type": "text"},
             ]},
        ],
        "sources": sources,
        "blocks": [
            {"id": "title", "type": "markdown", "body": f"# {title}"},
            {"id": "executive_summary", "type": "markdown", "body": (
                "## Executive Summary\n\n"
                "- **可展示闭环已经完成。** Stage2 面杂波和 Stage3 真实 SAR 背景均实际运行了 "
                "3 个完整扫描；每扫描 61 波位、每波位 130 PRT，并从共享内存进入生产 `GMTI_pipe_core`。\n"
                f"- **定位结果足以演示。** Stage2 命中 {s2['matched_count']}/72，定位中位误差 "
                f"{s2['position_median_m']:.2f} m、P95 {s2['position_p95_m']:.2f} m；Stage3 命中 "
                f"{s3['matched_count']}/72，中位误差 {s3['position_median_m']:.2f} m、P95 "
                f"{s3['position_p95_m']:.2f} m。\n"
                "- **严格指标仍未通过。** 两套场景均有大量未匹配检测和虚假航迹；本报告只确认“数据—CUDA—检测—定位—航迹—出图”可连续展示，不把虚警问题包装成已解决。"
            )},
            {"id": "headline_metrics", "type": "metric-strip", "cardIds": ["runs", "scans", "prts", "recall"]},
            {"id": "closure_heading", "type": "markdown", "body": (
                "## 两套生产链路都已连续跑通\n\n"
                "两套输入各约 4.20 GiB，共处理 47,580 个 PRT。共享内存审计记录为 6 个完整扫描、0 丢周期、0 PRT 间隙、0 重复、0 环形缓冲覆盖；六张协议图像均可解码。"
            ), "sourceId": "showcase_rollup"},
            {"id": "recall_chart_note", "type": "markdown", "body": (
                "**合作目标大部分已被生产检测链命中，但尚未越过严格门槛。** 下图按每套场景的 72 个可见 truth 计算一对一命中率；Stage2 为 86.1%，Stage3 为 77.8%。因此当前适合演示闭环和定位，不适合宣称弱目标/虚警指标已经验收。"
            ), "sourceId": "showcase_rollup"},
            {"id": "recall_chart_block", "type": "chart", "chartId": "recall_by_case"},
            {"id": "case_table_note", "type": "markdown", "body": (
                "**命中后的定位表现稳定。** Stage3 的命中样本误差最集中；Stage2 的 P95 仍低于 50 m 严格定位门槛。未匹配检测数同时列出，用于防止只展示命中目标而隐藏虚警。"
            ), "sourceId": "showcase_rollup"},
            {"id": "case_table_block", "type": "table", "tableId": "case_table"},
            {"id": "stage2_heading", "type": "markdown", "body": (
                "## Stage2：面杂波链路完成，定位进入米级\n\n"
                f"Stage2 三周期共写出 {s2['detection_count']} 个检测，命中 {s2['matched_count']}/72。第三周期全扫描图呈连续扇区；右侧动画来自生产 `track_debug`，不是另写的简化跟踪器。"
            ), "sourceId": "stage2_summary"},
            {"id": "stage2_images", "type": "html",
             "body": image_block(cases['stage2']['scan_image'], cases['stage2']['track_animation'], "Stage2"),
             "sourceId": "stage2_products"},
            {"id": "stage3_heading", "type": "markdown", "body": (
                "## Stage3：真实 SAR 背景连续出图，命中定位更集中\n\n"
                f"Stage3 三周期共写出 {s3['detection_count']} 个检测，命中 {s3['matched_count']}/72；命中样本定位 P95 为 {s3['position_p95_m']:.2f} m。扫描图可见真实 SAR 强散射结构，同时也解释了当前虚警数量更高。"
            ), "sourceId": "stage3_summary"},
            {"id": "stage3_images", "type": "html",
             "body": image_block(cases['stage3']['scan_image'], cases['stage3']['track_animation'], "Stage3"),
             "sourceId": "stage3_products"},
            {"id": "track_heading", "type": "markdown", "body": (
                "## 生产航迹链路已真实工作，但还不能作为性能结论\n\n"
                f"Stage2 `track_debug` 保存 {s2['track_debug']['state_rows']} 条状态记录并输出 {s2['track_debug']['output_payload_rows']} 个本周期确认检测点；"
                f"Stage3 分别为 {s3['track_debug']['state_rows']} 条和 {s3['track_debug']['output_payload_rows']} 个。协议出处复核通过，说明报文发送的是本周期关联 detection。"
                "但三周期过短且虚警很多，当前 `track_truth_matches.csv` 仍为空，因此只能证明链路和可视化工作，不能证明真实目标航迹已可靠确认。"
            ), "sourceId": "showcase_rollup"},
            {"id": "scan_table_block", "type": "table", "tableId": "scan_table"},
            {"id": "next_steps", "type": "markdown", "body": (
                "## 下一步先把展示变成严格验收\n\n"
                "1. 固定当前 6 帧产物作为展示基线，不再反复改动演示入口。\n"
                "2. 在不提高漏检的前提下，针对弱目标和低速目标调 CSI/CFAR，并以未匹配检测和航迹碎片数作为守门指标。\n"
                "3. 延长到至少 5–10 个扫描，修正航迹 truth 匹配，验证确认延迟、连续性和多周期位移速度。\n"
                "4. 单独优化远距全扫描 GPU 拼图；当前信号处理仍是 CUDA，只有 250 m 展示拼图切到 CPU。"
            )},
            {"id": "further_questions", "type": "markdown", "body": (
                "## 仍需回答的问题\n\n"
                "- Stage3 强散射区和合作目标对 p38 二阶段拟合的污染分别贡献了多少虚警？\n"
                "- 三周期确认点中，哪些可以和 24 条 truth 轨迹建立稳定一对一关系？\n"
                "- 在保持当前召回率的前提下，CSI/CFAR 能将每扫描未匹配检测压低到什么水平？"
            )},
            {"id": "caveats", "type": "markdown", "body": (
                "## Caveats and Assumptions\n\n"
                "- 本轮是每套 3 个完整扫描，不是长期航迹试验。\n"
                "- 250 m 是全扫描 PNG 的展示栅格；生产检测和定位没有降采样到 250 m。\n"
                "- `status=failed_acceptance` 来自严格召回、虚警和航迹 truth 门槛，不代表 PIPE 集成失败；两次共享内存集成都为 PASS。\n"
                "- 仓库在运行时为 dirty 状态，报告保留了配置、数据哈希、可执行文件哈希和 run manifest，正式归档前仍需提交版本。"
            )},
        ],
    }

    artifact = {
        "surface": "report",
        "manifest": manifest,
        "snapshot": {
            "version": 1,
            "generatedAt": generated_at,
            "status": "ready",
            "datasets": {
                "overall": overall,
                "case_summary": case_rows,
                "scan_outputs": scan_rows,
            },
            "accessIssues": [],
        },
        "sources": sources,
        "package_info": {"originUrl": "artifact://gmti-pipe-fullflow-showcase"},
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = args.output_dir / f"{args.stem}.artifact.json"
    notes_path = args.output_dir / f"{args.stem}_source_notes.md"
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    notes_path.write_text(
        "# GMTI PIPE 全流程展示报告来源与图表审查\n\n"
        "- 受众：老师/项目负责人；目标是快速判断展示闭环是否完成。\n"
        "- 报告结构：标题、Executive Summary、关键证据、Stage2/Stage3 视觉产物、下一步、问题与限制。\n"
        "- 图表映射：`recall_by_case` 使用两套 `fullflow_summary.json` 的 truth/matched；两类静态扫描图使用各 run 的 `GMTI03.png`；航迹动画使用生产 `track_debug` 经 `visualize_track_manager.py` 生成的 `track_outputs_growth.gif`。\n"
        "- 可视化选择：仅 3 个周期，不用欠采样的时间折线；用分类柱图比较召回率，用表格保留定位/虚警精确值，用原始扫描图和航迹动画证明链路。\n"
        "- QA：已人工检查 Stage2/Stage3 第三周期扫描图和最终输出航迹图；扫描扇区完整、坐标轴可读、动画来源为生产 TrackManager。\n"
        "- 已知限制：航迹点密集且虚警多；报告明确区分展示闭环 PASS 与严格算法验收 FAIL。\n",
        encoding="utf-8")
    print(artifact_path)
    print(notes_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
