#!/usr/bin/env python3
"""Run GMTI local-test, summarize per-period timing, audit tracks, and visualize."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    parser = argparse.ArgumentParser(
        description="一键运行 GMTI、逐周期计时、TrackManager 审计和航迹可视化"
    )
    parser.add_argument(
        "echo_files", nargs="*",
        help="回波文件；也支持 result_id=path。可改用 --m68-id-range。",
    )
    parser.add_argument(
        "--m68-id-range", nargs=2, type=int, metavar=("START", "END"),
        help="展开为 <echo-dir>/M68ID<id>.bin，例如 --m68-id-range 11 19",
    )
    parser.add_argument(
        "--echo-dir", type=Path,
        default=Path("/home/shy/AIR/小长/GMTI_Data/Mission068/newdata"),
        help="--m68-id-range 使用的数据目录",
    )
    parser.add_argument("--config", required=True, type=Path, help="GMTI XML 配置")
    parser.add_argument(
        "--binary", type=Path, default=ROOT / "build/GMTI_pipe_core",
        help="GMTI_pipe_core 路径",
    )
    parser.add_argument(
        "--output-root", type=Path,
        default=ROOT / f"outputs/track_evaluation_{timestamp}",
        help="本次独立评估目录；必须不存在",
    )
    parser.add_argument(
        "--track-output-state-source",
        choices=["measurement", "prediction", "kalman_filtered"],
        default="measurement",
    )
    parser.add_argument("--p38-diagnostics-dump", choices=["on", "off"], default="off")
    parser.add_argument(
        "--evaluation-workers", type=int, default=8,
        help="评估诊断模式的波位并发数；实时增强默认关闭时推荐8",
    )
    parser.add_argument("--coord", choices=["en", "geo"], default="geo")
    parser.add_argument("--min-track-length", type=int, default=2)
    parser.add_argument("--visual-format", choices=["png", "pdf", "svg"], default="png")
    parser.add_argument(
        "--make-output-gif", dest="make_output_gif", action="store_true",
        help="生成完整航迹和协议输出航迹 GIF（默认开启）",
    )
    parser.add_argument(
        "--no-gif", dest="make_output_gif", action="store_false",
        help="跳过全部 GIF，只生成静态可视化",
    )
    parser.set_defaults(make_output_gif=True)
    parser.add_argument("--no-dbs-background", action="store_true")
    parser.add_argument(
        "--hash-inputs", action="store_true",
        help="计算大回波文件 SHA-256；默认只记录路径、大小和 mtime",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="复用已存在的 output-root，跳过算法并重新生成报告/可视化",
    )
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_identity(path: Path, hash_content: bool = True) -> dict[str, Any]:
    resolved = path.resolve()
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha256(resolved) if hash_content else None,
        "sha256_status": "computed" if hash_content else "not_requested",
    }


def echo_path(spec: str) -> Path:
    value = spec.split("=", 1)[1] if "=" in spec else spec
    return Path(value).expanduser().resolve()


def write_effective_config(source: Path, output: Path, workers: int) -> None:
    if workers <= 0:
        raise SystemExit("--evaluation-workers 必须大于0")
    tree = ET.parse(source)
    root = tree.getroot()
    params = root.find(".//GMTI_parameter")
    if params is None:
        raise SystemExit(f"XML 缺少 GMTI_parameter: {source}")
    node = params.find("wavepos_parallel_max_workers")
    if node is None:
        node = ET.SubElement(params, "wavepos_parallel_max_workers")
    node.text = str(workers)
    tree.write(output, encoding="utf-8", xml_declaration=True)


def run_logged(command: list[str], log_path: Path) -> tuple[int, float]:
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log:
        log.write("$ " + " ".join(command) + "\n")
        log.flush()
        completed = subprocess.run(
            command,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    return completed.returncode, time.monotonic() - started


def locate_debug_dir(log_path: Path, debug_base: Path) -> Path:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    matches = re.findall(r"^\[LOCAL-TEST\] track_debug=(.+)$", text, flags=re.MULTILINE)
    if matches:
        path = Path(matches[-1].strip()).resolve()
        if path.is_dir():
            return path
    candidates = sorted(
        (path for path in debug_base.glob("local_test_*") if path.is_dir()),
        key=lambda path: path.stat().st_mtime_ns,
    )
    if not candidates:
        raise FileNotFoundError(f"未找到独立 track_debug 目录: {debug_base}")
    return candidates[-1].resolve()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def summarize_timing(timing_path: Path, report_dir: Path) -> dict[str, Any]:
    rows = read_csv(timing_path)
    selected_scopes = {
        "main_total", "end_to_end_total", "gmti_processing", "dbs_processing",
        "detection_result_csv_write", "tracking",
    }
    by_result: dict[str, dict[str, Any]] = {}
    for row in rows:
        scope = row.get("scope_name", "")
        result_id = row.get("result_id", "")
        if scope not in selected_scopes or not result_id:
            continue
        record = by_result.setdefault(
            result_id,
            {
                "result_id": result_id,
                "run_id": row.get("run_id", ""),
                "main_total_ms": "",
                "end_to_end_ms": "",
                "gmti_processing_ms": "",
                "dbs_processing_ms": "",
                "tracking_ms": "",
                "result_write_ms": "",
            },
        )
        column = {
            "main_total": "main_total_ms",
            "end_to_end_total": "end_to_end_ms",
            "gmti_processing": "gmti_processing_ms",
            "dbs_processing": "dbs_processing_ms",
            "tracking": "tracking_ms",
            "detection_result_csv_write": "result_write_ms",
        }[scope]
        record[column] = int(row["elapsed_ms"])

    def result_number(value: str) -> int:
        match = re.search(r"(\d+)$", value)
        return int(match.group(1)) if match else 10**9

    period_rows = sorted(by_result.values(), key=lambda row: result_number(row["result_id"]))
    if not period_rows or any(row["main_total_ms"] == "" for row in period_rows):
        raise ValueError(f"{timing_path} 缺少逐周期 main_total 计时")

    output_csv = report_dir / "period_timing.csv"
    write_csv(
        output_csv,
        [
            "result_id", "run_id", "main_total_ms", "end_to_end_ms", "gmti_processing_ms",
            "dbs_processing_ms", "tracking_ms", "result_write_ms",
        ],
        period_rows,
    )
    totals = [int(row["main_total_ms"]) for row in period_rows]
    summary = {
        "period_count": len(period_rows),
        "main_total_ms": {
            "min": min(totals),
            "max": max(totals),
            "mean": statistics.fmean(totals),
            "median": statistics.median(totals),
            "sum": sum(totals),
        },
        "period_csv": str(output_csv.resolve()),
    }
    e2e_totals = [int(row["end_to_end_ms"]) for row in period_rows
                  if row["end_to_end_ms"] != ""]
    if e2e_totals:
        summary["end_to_end_ms"] = {
            "min": min(e2e_totals),
            "max": max(e2e_totals),
            "mean": statistics.fmean(e2e_totals),
            "median": statistics.median(e2e_totals),
            "sum": sum(e2e_totals),
        }

    os.environ.setdefault(
        "MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "gmti_matplotlib")
    )
    os.environ.setdefault("MPLBACKEND", "Agg")
    import matplotlib
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    labels = [row["result_id"] for row in period_rows]
    fig, ax = plt.subplots(figsize=(max(8.0, len(labels) * 1.1), 5.0))
    bars = ax.bar(labels, totals, color="#3274a1")
    ax.set_title("GMTI Per-period Wall Time")
    ax.set_xlabel("Result period")
    ax.set_ylabel("main_total (ms)")
    ax.grid(axis="y", alpha=0.25)
    for bar, value in zip(bars, totals):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            value,
            str(value),
            ha="center",
            va="bottom",
            fontsize=8,
        )
    fig.tight_layout()
    timing_plot = report_dir / "period_runtime.png"
    fig.savefig(timing_plot, dpi=160)
    plt.close(fig)
    summary["period_plot"] = str(timing_plot.resolve())
    return summary


def summarize_tracks(debug_dir: Path, report_dir: Path) -> dict[str, Any]:
    frames = read_csv(debug_dir / "track_frames.csv")
    states = read_csv(debug_dir / "track_states.csv")
    detections = read_csv(debug_dir / "track_detections.csv")
    payloads = read_csv(debug_dir / "track_output_payloads.csv")

    period_rows: list[dict[str, Any]] = []
    for row in frames:
        period_rows.append(
            {
                "result_id": row.get("result_id", ""),
                "num_detections": row.get("num_detections", "0"),
                "num_tracks": row.get("num_tracks", "0"),
                "num_tentative": row.get("num_tentative", "0"),
                "num_confirmed": row.get("num_confirmed", "0"),
                "num_coasted": row.get("num_coasted", "0"),
                "num_deleted": row.get("num_deleted", "0"),
                "num_matched_tracks": row.get("num_matched_tracks", "0"),
                "num_outputs": row.get("num_outputs", "0"),
            }
        )
    period_csv = report_dir / "track_period_summary.csv"
    write_csv(
        period_csv,
        [
            "result_id", "num_detections", "num_tracks", "num_tentative",
            "num_confirmed", "num_coasted", "num_deleted",
            "num_matched_tracks", "num_outputs",
        ],
        period_rows,
    )

    track_periods: dict[int, set[str]] = {}
    confirmed_ids: set[int] = set()
    output_ids: set[int] = set()
    coasted_ids: set[int] = set()
    for row in states:
        track_id = int(row["track_id"])
        track_periods.setdefault(track_id, set()).add(row["result_id"])
        if row.get("state") == "Confirmed":
            confirmed_ids.add(track_id)
        if row.get("state") == "Coasted":
            coasted_ids.add(track_id)
        if row.get("is_output") == "1":
            output_ids.add(track_id)
    lengths = [len(periods) for periods in track_periods.values()]
    summary = {
        "period_count": len(frames),
        "detection_rows": len(detections),
        "state_rows": len(states),
        "payload_rows": len(payloads),
        "unique_track_count": len(track_periods),
        "confirmed_track_count": len(confirmed_ids),
        "coasted_track_count": len(coasted_ids),
        "output_track_count": len(output_ids),
        "track_length_periods": {
            "min": min(lengths) if lengths else 0,
            "max": max(lengths) if lengths else 0,
            "mean": statistics.fmean(lengths) if lengths else 0.0,
            "median": statistics.median(lengths) if lengths else 0.0,
        },
        "period_csv": str(period_csv.resolve()),
    }
    summary_path = report_dir / "track_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    summary["summary_json"] = str(summary_path.resolve())
    return summary


def first_dbs_pair(result_dir: Path) -> tuple[Path | None, Path | None]:
    images = sorted(result_dir.glob("GMTI*.png"))
    for image in images:
        corner = image.with_suffix(".txt")
        if corner.is_file():
            return image.resolve(), corner.resolve()
    return None, None


def write_markdown_report(
    path: Path,
    algorithm_rc: int,
    algorithm_elapsed_sec: float,
    timing: dict[str, Any],
    tracks: dict[str, Any],
    audit_rc: int,
    visualization_rc: int,
    paths: dict[str, str],
) -> None:
    wall = timing["main_total_ms"]
    lines = [
        "# GMTI 航迹一键评估报告",
        "",
        f"- 算法退出码：{algorithm_rc}",
        f"- 一键脚本观测算法总 wall time：{algorithm_elapsed_sec:.3f} s",
        f"- 周期数：{timing['period_count']}",
        (
            "- 单周期 main_total："
            f"min={wall['min']} ms，median={wall['median']} ms，"
            f"mean={wall['mean']:.1f} ms，max={wall['max']} ms"
        ),
    ]
    if "end_to_end_ms" in timing:
        e2e = timing["end_to_end_ms"]
        lines.append(
            "- 单周期端到端总时间："
            f"min={e2e['min']} ms，median={e2e['median']} ms，"
            f"mean={e2e['mean']:.1f} ms，max={e2e['max']} ms"
        )
    lines.extend([
        f"- 唯一航迹数：{tracks['unique_track_count']}",
        f"- 曾确认航迹数：{tracks['confirmed_track_count']}",
        f"- 实际输出航迹数：{tracks['output_track_count']}",
        f"- 航迹审计退出码：{audit_rc}",
        f"- 可视化退出码：{visualization_rc}",
        "",
        "## 关键产物",
        "",
    ])
    lines.extend(f"- {name}：`{value}`" for name, value in paths.items())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    binary = args.binary.expanduser().resolve()
    config = args.config.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    echo_specs = list(args.echo_files)
    if args.m68_id_range is not None:
        start_id, end_id = args.m68_id_range
        if end_id < start_id:
            raise SystemExit("--m68-id-range 要求 END >= START")
        if echo_specs:
            raise SystemExit("位置回波文件与 --m68-id-range 只能选择一种")
        echo_dir = args.echo_dir.expanduser().resolve()
        echo_specs = [
            f"{result_id}={echo_dir / f'M68ID{result_id}.bin'}"
            for result_id in range(start_id, end_id + 1)
        ]
    if not echo_specs:
        raise SystemExit("请提供回波文件，或使用 --m68-id-range START END")

    echoes = [echo_path(spec) for spec in echo_specs]
    normalized_echo_specs = []
    for spec, path in zip(echo_specs, echoes):
        if "=" in spec:
            forced_id = spec.split("=", 1)[0]
            normalized_echo_specs.append(f"{forced_id}={path}")
        else:
            normalized_echo_specs.append(str(path))

    for required in [binary, config, *echoes]:
        if not required.is_file():
            raise SystemExit(f"缺少输入文件: {required}")
    result_dir = output_root / "algorithm_result"
    debug_base = output_root / "track_debug"
    report_dir = output_root / "reports"
    visualization_dir = output_root / "visualization"
    log_dir = output_root / "logs"
    if args.resume:
        if not output_root.is_dir():
            raise SystemExit(f"--resume 指定的输出目录不存在: {output_root}")
        for path in [result_dir, debug_base, report_dir, visualization_dir, log_dir]:
            path.mkdir(parents=True, exist_ok=True)
    else:
        if output_root.exists():
            raise SystemExit(f"输出目录已存在，为避免覆盖请换一个目录: {output_root}")
        for path in [result_dir, debug_base, report_dir, visualization_dir, log_dir]:
            path.mkdir(parents=True, exist_ok=False)

    effective_config = output_root / "effective_evaluation_config.xml"
    if args.resume:
        if not effective_config.is_file():
            raise SystemExit(f"--resume 缺少有效评估配置: {effective_config}")
    else:
        write_effective_config(config, effective_config, args.evaluation_workers)

    algorithm_log = log_dir / "algorithm.log"
    algorithm_command = [
        str(binary),
        "--runtime-mode", "release",
        "--runtime-diagnostics=on",
        "--result-dir", str(result_dir),
        "--track-debug-dir", str(debug_base),
        "--track-debug-dump", "on",
        "--track-output-state-source", args.track_output_state_source,
        "--p38-diagnostics-dump", args.p38_diagnostics_dump,
        "--local-test", str(effective_config),
        *normalized_echo_specs,
    ]
    if args.resume:
        print(f"[1/4] 复用现有算法产物：{result_dir}", flush=True)
        algorithm_rc = 0
        algorithm_elapsed_sec = 0.0
        if not algorithm_log.is_file():
            raise SystemExit(f"--resume 缺少算法日志: {algorithm_log}")
    else:
        print(f"[1/4] 运行算法，完整控制台写入 {algorithm_log}", flush=True)
        algorithm_rc, algorithm_elapsed_sec = run_logged(algorithm_command, algorithm_log)
        if algorithm_rc != 0:
            print(f"算法失败，退出码={algorithm_rc}，请查看 {algorithm_log}", file=sys.stderr)
            return algorithm_rc

    debug_dir = locate_debug_dir(algorithm_log, debug_base)
    print(f"[2/4] 汇总逐周期耗时和航迹：{debug_dir}", flush=True)
    timing = summarize_timing(result_dir / "timing_metrics.csv", report_dir)
    if args.resume:
        algorithm_elapsed_sec = timing["main_total_ms"]["sum"] / 1000.0
    tracks = summarize_tracks(debug_dir, report_dir)

    audit_path = report_dir / "track_audit.json"
    audit_log = log_dir / "track_audit.log"
    audit_command = [
        sys.executable,
        str(ROOT / "scripts/audit_track_manager_run.py"),
        "--debug-dir", str(debug_dir),
        "--output", str(audit_path),
    ]
    print("[3/4] 执行生产 TrackManager 航迹审计", flush=True)
    audit_rc, _ = run_logged(audit_command, audit_log)

    image, corner = first_dbs_pair(result_dir)
    visualization_log = log_dir / "visualization.log"
    visualization_command = [
        sys.executable,
        str(ROOT / "scripts/visualize_track_manager.py"),
        "--debug-dir", str(debug_dir),
        "--out-dir", str(visualization_dir),
        "--coord", args.coord,
        "--format", args.visual_format,
        "--run-index", "latest",
        "--min-len", str(max(1, args.min_track_length)),
        "--axis-from", "union",
    ]
    if args.make_output_gif:
        visualization_command.append("--make-output-gif")
    else:
        visualization_command.extend(["--no-gif", "--no-output-gif"])
    if args.no_dbs_background or image is None or corner is None:
        visualization_command.append("--no-dbs-bg")
    else:
        visualization_command.extend(
            ["--dbs-image", str(image), "--dbs-corner-file", str(corner)]
        )
    print(f"[4/4] 生成航迹可视化：{visualization_dir}", flush=True)
    visualization_rc, _ = run_logged(visualization_command, visualization_log)

    manifest = {
        "algorithm_command": algorithm_command,
        "algorithm_return_code": algorithm_rc,
        "algorithm_elapsed_sec": algorithm_elapsed_sec,
        "config": file_identity(config),
        "effective_config": file_identity(effective_config),
        "evaluation_workers": args.evaluation_workers,
        "binary": file_identity(binary),
        "echo_files": [
            file_identity(path, hash_content=args.hash_inputs) for path in echoes
        ],
        "result_dir": str(result_dir.resolve()),
        "track_debug_dir": str(debug_dir),
        "reports_dir": str(report_dir.resolve()),
        "visualization_dir": str(visualization_dir.resolve()),
        "timing_summary": timing,
        "track_summary": tracks,
        "audit_return_code": audit_rc,
        "visualization_return_code": visualization_rc,
    }
    manifest_path = output_root / "evaluation_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    report_path = report_dir / "evaluation_report.md"
    write_markdown_report(
        report_path,
        algorithm_rc,
        algorithm_elapsed_sec,
        timing,
        tracks,
        audit_rc,
        visualization_rc,
        {
            "算法日志": str(algorithm_log.resolve()),
            "逐周期耗时 CSV": timing["period_csv"],
            "耗时图": timing["period_plot"],
            "航迹逐周期汇总": tracks["period_csv"],
            "航迹汇总 JSON": tracks["summary_json"],
            "生产航迹审计": str(audit_path.resolve()),
            "可视化目录": str(visualization_dir.resolve()),
            "复现 manifest": str(manifest_path.resolve()),
        },
    )

    print(f"完成：{report_path}", flush=True)
    if audit_rc != 0:
        print(f"航迹审计未通过，请查看 {audit_log}", file=sys.stderr)
    if visualization_rc != 0:
        print(f"可视化失败，请查看 {visualization_log}", file=sys.stderr)
    return 0 if audit_rc == 0 and visualization_rc == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
