#!/usr/bin/env python3
"""生成真实 S+C+N GO-CFAR 技术报告的便携 artifact.json 与单文件 HTML。

报告面向首次接触 CFAR 的读者：用原生图表展示理论曲线、三层 Pd、直接输出
SCNR 和测角误差；公式/处理链使用 sandbox HTML block，不依赖 CDN 或图片旁路。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.stats import gamma as gamma_dist
from scipy.stats import ncx2


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SUMMARY = ROOT / "outputs/gmti_scnr_eval/formal/GO-CFAR_真实S+C+N_3seed_report_summary.json"
DEFAULT_THEORY_ROOT = ROOT / "outputs/gmti_scnr_eval/final_go/formal/selected_beams_v8_pf1e6_min6_small3_20db_goalpha/theory"
DEFAULT_ARTIFACT = ROOT / "docs/GO-CFAR_真实S+C+N_技术报告.artifact.json"
DEFAULT_HTML = ROOT / "docs/GO-CFAR_真实S+C+N_技术报告.html"
SOURCE_INDEX = ROOT / "docs/GO-CFAR_真实S+C+N_技术报告_sources.sql"

ALPHA = 13.44951031977817
GUARD = 4
BACKGROUND = 16
N_DIRECTION = BACKGROUND * (2 * GUARD + 2 * BACKGROUND + 1)
PFA = 1.0e-6
LAMBDA_M = 299_792_458.0 / (16.472113076923076e9)
D_CHANNEL_M = 0.17
EDGE_OFFSET_DEG = 0.8


def finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def number(value: object, default: float = float("nan")) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def safe_number(value: object) -> float | None:
    result = number(value)
    return result if math.isfinite(result) else None


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def fmt(value: object, digits: int = 3) -> str:
    result = number(value)
    return f"{result:.{digits}f}" if math.isfinite(result) else "—"


def series_label(row: dict) -> str:
    angle = number(row.get("command_angle_deg"))
    if row.get("case_label") == "right_edge":
        return f"{angle:g}° 右边缘"
    return f"{angle:g}° 中心"


def common_fields(row: dict) -> dict:
    command = number(row.get("command_angle_deg"))
    truth = number(row.get("truth_angle_deg"), command)
    return {
        "series_label": series_label(row),
        "position": "右边缘" if row.get("case_label") == "right_edge" else "波束中心",
        "command_angle_deg": command,
        "truth_angle_deg": truth,
        "angle_label": f"{truth:g}°",
        "input_snr_db": number(row.get("configured_target_snr_db")),
    }


def unit_dataset(rows: list[dict]) -> list[dict]:
    result: list[dict] = []
    for row in rows:
        fields = common_fields(row)
        result.append({
            **fields,
            "hits": int(number(row.get("hits"), 0)),
            "trials": int(number(row.get("trials"), 0)),
            "pd": safe_number(row.get("pd")),
            "wilson_low": safe_number(row.get("wilson_low")),
            "wilson_high": safe_number(row.get("wilson_high")),
            "exact_pd": safe_number(row.get("exact_pd")),
            "direct_cut_scnr_db": safe_number(row.get("cut_ratio_median_db")),
            # 旧诊断字段在个别帧可能为负，保留原值并在图注中明确其“存档观测”含义。
            "stored_excess_scnr_db": safe_number(row.get("excess_scnr_median_db")),
            "positive_excess_scnr_db": (
                safe_number(row.get("excess_scnr_median_db"))
                if number(row.get("excess_scnr_median_db")) > 0.0 else None
            ),
            "positive_excess_count": int(number(row.get("excess_scnr_count"), 0)),
        })
    return result


def target_dataset(rows: list[dict]) -> list[dict]:
    result: list[dict] = []
    for row in rows:
        fields = common_fields(row)
        result.append({
            **fields,
            "target_hits": int(number(row.get("target_hits"), 0)),
            "target_trials": int(number(row.get("target_trials"), 0)),
            "target_pd": safe_number(row.get("target_pd")),
            "target_low": safe_number(row.get("target_low")),
            "target_high": safe_number(row.get("target_high")),
            "track_hits": int(number(row.get("track_hits"), 0)),
            "track_trials": int(number(row.get("track_trials"), 0)),
            "track_pd": safe_number(row.get("track_pd")),
            "track_low": safe_number(row.get("track_low")),
            "track_high": safe_number(row.get("track_high")),
            "two_of_three_pd": safe_number(row.get("two_of_three_pd")),
        })
    return result


def angle_dataset(rows: list[dict]) -> list[dict]:
    result: list[dict] = []
    for row in rows:
        fields = common_fields(row)
        result.append({
            **fields,
            "matched_count": int(number(row.get("matched_count"), 0)),
            "bias_deg": safe_number(row.get("bias_deg")),
            "rmse_deg": safe_number(row.get("rmse_deg")),
            "p95_abs_deg": safe_number(row.get("p95_abs_deg")),
        })
    return result


def go_theory_dataset(samples: int = 60_000) -> list[dict]:
    """对 M=max(4 个方向均值) 做确定性的中点分位数积分。

    每个方向均值为 Gamma(N_direction, 1/N_direction)，其最大值的分位数为
    Gamma^{-1}(u^(1/4))。用中点规则避免随机 seed，同时让 gamma=0 的结果
    接近已审计的 Pfa。
    """
    u = (np.arange(samples, dtype=np.float64) + 0.5) / samples
    m = gamma_dist.ppf(np.power(u, 0.25), a=N_DIRECTION, scale=1.0 / N_DIRECTION)
    result: list[dict] = []
    for scnr_db in np.arange(-15.0, 40.01, 0.5):
        gamma = 10.0 ** (scnr_db / 10.0)
        conditional = ncx2.sf(2.0 * ALPHA * m, 2.0, 2.0 * gamma)
        result.append({
            "scnr_db": float(scnr_db),
            "pd": float(np.mean(conditional)),
            "gamma_linear": float(gamma),
        })
    return result


def angle_theory_dataset() -> list[dict]:
    result: list[dict] = []
    for command in (0.0, 30.0, 45.0):
        for position, offset in (("波束中心", 0.0), ("右边缘", EDGE_OFFSET_DEG)):
            truth = command + offset
            jacobian = LAMBDA_M / (2.0 * math.pi * D_CHANNEL_M * math.cos(math.radians(truth)))
            label = f"{truth:g}° {position}"
            for scnr_db in np.arange(-5.0, 40.01, 0.5):
                sigma_rad = jacobian / math.sqrt(10.0 ** (scnr_db / 10.0))
                result.append({
                    "series_label": label,
                    "truth_angle_deg": truth,
                    "position": position,
                    "scnr_db": float(scnr_db),
                    "rmse_deg": float(math.degrees(sigma_rad)),
                })
    return result


def jacobian_dataset() -> list[dict]:
    result: list[dict] = []
    for command in (0.0, 30.0, 45.0):
        for position, offset in (("中心", 0.0), ("边缘", EDGE_OFFSET_DEG)):
            truth = command + offset
            jacobian = LAMBDA_M / (2.0 * math.pi * D_CHANNEL_M * math.cos(math.radians(truth)))
            result.append({
                "angle_label": f"{truth:g}° {position}",
                "truth_angle_deg": truth,
                "jacobian_rad_per_rad": jacobian,
                "jacobian_deg_per_unit": math.degrees(jacobian),
            })
    return result


def source_specs() -> list[dict]:
    source_path = rel(SOURCE_INDEX)
    return [{
        "id": "report_source_index",
        "label": "3-seed 实测、GO 理论与复现脚本来源索引",
        "path": source_path,
    }]


def metric_cards(summary: dict, unit: list[dict], target: list[dict], angle: list[dict], pfa: float) -> tuple[list[dict], list[dict]]:
    cases = len(summary.get("cases", []))
    unit_trials = sum(int(row.get("trials", 0)) for row in unit)
    target_trials = sum(int(row.get("target_trials", 0)) for row in target)
    track_trials = sum(int(row.get("track_trials", 0)) for row in target)
    cards = [
        {
            "id": "card_cases",
            "description": "3 个独立 seed × 6 个角度/位置 case；每个 case 3 个连续周期。",
            "dataset": "headline",
            "sourceId": "report_source_index",
            "metrics": [{"label": "真实 S+C+N case", "field": "case_count", "format": "number"}],
        },
        {
            "id": "card_unit_trials",
            "description": "每个注入 SNR 点的 operational unit 事件为 9 帧目标窗口。",
            "dataset": "headline",
            "sourceId": "report_source_index",
            "metrics": [{"label": "unit 试验帧", "field": "unit_trials", "format": "number"}],
        },
        {
            "id": "card_target_trials",
            "description": "目标级事件只数生产 detection 与当前周期 truth 的关联。",
            "dataset": "headline",
            "sourceId": "report_source_index",
            "metrics": [{"label": "目标级试验帧", "field": "target_trials", "format": "number"}],
        },
        {
            "id": "card_track_trials",
            "description": "航迹级分母为每个 seed 的三周期 TrackManager 目标实例。",
            "dataset": "headline",
            "sourceId": "report_source_index",
            "metrics": [{"label": "航迹实例", "field": "track_trials", "format": "number"}],
        },
        {
            "id": "card_alpha",
            "description": "GO 门限 T=alpha×四个方向训练均值的最大值。",
            "dataset": "headline",
            "sourceId": "report_source_index",
            "metrics": [{"label": "GO alpha", "field": "alpha", "format": "number"}],
        },
        {
            "id": "card_pfa",
            "description": "IID directional-max 条件 Pfa 验证，1,000,000 个训练窗。",
            "dataset": "headline",
            "sourceId": "report_source_index",
            "metrics": [{"label": "实测条件 Pfa (×10⁻⁶)", "field": "pfa_e6", "format": "number"}],
        },
    ]
    headline = [{
        "case_count": cases,
        "unit_trials": unit_trials,
        "target_trials": target_trials,
        "track_trials": track_trials,
        "angle_matches": len(angle),
        "alpha": ALPHA,
        "pfa": pfa,
        "pfa_e6": pfa * 1.0e6,
    }]
    return cards, headline


def html_flow() -> str:
    return r'''
<style>
  .flow-wrap{font-family:system-ui,"Noto Sans CJK SC",sans-serif;color:#243047;background:#f7f9fc;border:1px solid #d9e2ef;border-radius:14px;padding:18px}
  .flow-wrap h3{margin:0 0 8px;font-size:18px;color:#16233b}.flow-wrap p{margin:0 0 12px;color:#52627a}
  .flow-svg{display:block;width:100%;height:auto}.flow-box{fill:#fff;stroke:#3468a8;stroke-width:2}.flow-box.alt{stroke:#b17b2c}.flow-box.final{fill:#eef7f0;stroke:#2e7d4f}.flow-arrow{stroke:#65758c;stroke-width:2;fill:none;marker-end:url(#arrow)}
  .flow-title{font-size:15px;font-weight:700;fill:#18263d}.flow-note{font-size:11px;fill:#52627a}
  @media (prefers-color-scheme:dark){.flow-wrap{background:#111827;border-color:#334155;color:#e5edf8}.flow-wrap h3,.flow-title{color:#f8fafc}.flow-wrap p,.flow-note{color:#a9b7cc}.flow-box{fill:#172238;stroke:#7aa7da}.flow-box.alt{stroke:#e0aa52}.flow-box.final{fill:#153124;stroke:#68c78a}.flow-arrow{stroke:#9aaac0}}
</style>
<div class="flow-wrap" role="img" aria-label="真实 S+C+N GO-CFAR 处理链流程图">
  <h3>真实处理链：每一步都可能改变最终 Pd</h3>
  <p>同一个目标，要先成为 CFAR hit，再成为目标检测，最后才能成为当前帧可追溯的 Confirmed 航迹载荷。</p>
  <svg class="flow-svg" viewBox="0 0 1120 190" xmlns="http://www.w3.org/2000/svg">
    <defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#65758c"/></marker></defs>
    <path class="flow-arrow" d="M142 78 H174"/><path class="flow-arrow" d="M302 78 H334"/><path class="flow-arrow" d="M462 78 H494"/><path class="flow-arrow" d="M622 78 H654"/><path class="flow-arrow" d="M782 78 H814"/><path class="flow-arrow" d="M942 78 H974"/>
    <rect class="flow-box" x="8" y="35" rx="10" width="134" height="72"/><text class="flow-title" x="75" y="63" text-anchor="middle">S+C+N 回波</text><text class="flow-note" x="75" y="84" text-anchor="middle">目标+杂波+热噪声</text>
    <rect class="flow-box" x="174" y="35" rx="10" width="128" height="72"/><text class="flow-title" x="238" y="63" text-anchor="middle">脉压 / 距离</text><text class="flow-note" x="238" y="84" text-anchor="middle">range bin</text>
    <rect class="flow-box" x="334" y="35" rx="10" width="128" height="72"/><text class="flow-title" x="398" y="63" text-anchor="middle">Doppler / DBS</text><text class="flow-note" x="398" y="84" text-anchor="middle">速度行</text>
    <rect class="flow-box" x="494" y="35" rx="10" width="128" height="72"/><text class="flow-title" x="558" y="63" text-anchor="middle">P38 / CSI</text><text class="flow-note" x="558" y="84" text-anchor="middle">杂波抑制+相位</text>
    <rect class="flow-box alt" x="654" y="35" rx="10" width="128" height="72"/><text class="flow-title" x="718" y="63" text-anchor="middle">GO-CFAR</text><text class="flow-note" x="718" y="84" text-anchor="middle">CUT &gt; α·max(mean)</text>
    <rect class="flow-box alt" x="814" y="35" rx="10" width="128" height="72"/><text class="flow-title" x="878" y="63" text-anchor="middle">聚类/定位</text><text class="flow-note" x="878" y="84" text-anchor="middle">目标级 Pd</text>
    <rect class="flow-box final" x="974" y="35" rx="10" width="138" height="72"/><text class="flow-title" x="1043" y="63" text-anchor="middle">TrackManager</text><text class="flow-note" x="1043" y="84" text-anchor="middle">Confirmed+命中</text>
    <text class="flow-note" x="560" y="145" text-anchor="middle">本报告把 unit、target、track 三个事件分开统计；不能用上一层 Pd 代替下一层 Pd。</text>
    <text class="flow-note" x="560" y="166" text-anchor="middle">raw BIN 只在 GO/TrackManager 审计通过后删除，保留 CSV、manifest 和日志。</text>
  </svg>
</div>
'''


def html_equations() -> str:
    return r'''
<style>
  .eq-wrap{font-family:system-ui,"Noto Sans CJK SC",sans-serif;color:#243047}.eq-wrap h3{margin:0 0 10px;color:#16233b;font-size:19px}.eq-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.eq-card{border:1px solid #d9e2ef;border-radius:12px;background:#fbfcfe;padding:14px 16px}.eq-card h4{margin:0 0 7px;font-size:15px;color:#3468a8}.eq{font-family:Georgia,"Times New Roman",serif;font-size:20px;line-height:1.45;color:#111d32;letter-spacing:.01em}.eq-small{font-size:14px;line-height:1.6;color:#52627a;margin-top:7px}.frac{display:inline-flex;flex-direction:column;text-align:center;vertical-align:middle;line-height:1.05;margin:0 3px}.frac>span:first-child{border-bottom:1px solid currentColor;padding:0 4px}.frac>span:last-child{padding:1px 4px 0}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.92em}.step-list{margin:12px 0 0;padding-left:22px;color:#40516a}.step-list li{margin:5px 0}.tag{display:inline-block;border-radius:999px;background:#e7f0fb;color:#3468a8;padding:2px 8px;font-size:12px;margin-right:5px}
  @media (max-width:760px){.eq-grid{grid-template-columns:1fr}.eq{font-size:18px}}
  @media (prefers-color-scheme:dark){.eq-wrap{color:#dbe7f7}.eq-wrap h3{color:#f8fafc}.eq-card{border-color:#334155;background:#111827}.eq-card h4{color:#8ab4e8}.eq{color:#f1f5f9}.eq-small,.step-list{color:#a9b7cc}.tag{background:#1d3555;color:#b9d7ff}}
</style>
<div class="eq-wrap">
  <h3>公式从哪里来：先看门限，再看检测概率，最后把相位误差换成角度误差</h3>
  <div class="eq-grid">
    <div class="eq-card"><h4>① 四个方向的训练窗</h4><div class="eq">N<sub>dir</sub> = b(2g + 2b + 1) = 16 × 41 = 656</div><div class="eq-small">每个方向平均 656 个背景功率格；g=4 是 guard 半宽，b=16 是背景厚度。</div></div>
    <div class="eq-card"><h4>② GO 门限</h4><div class="eq">M = max(m<sub>L</sub>, m<sub>R</sub>, m<sub>T</sub>, m<sub>B</sub>)<br/>T = αM = 13.44951031977817 × M</div><div class="eq-small">背景不均匀时，取最吵方向，避免把杂波边缘当作目标。</div></div>
    <div class="eq-card"><h4>③ 单元级 Pd</h4><div class="eq">P<sub>d,cell</sub>(γ) = E<sub>M</sub>[ Q<sub>1</sub>(√(2γ), √(2αM)) ]</div><div class="eq-small">γ=10<sup>SCNR/10</sup>；Q<sub>1</sub> 是 Marcum-Q（等价于非中心卡方右尾）。</div></div>
    <div class="eq-card"><h4>④ 从单元到目标</h4><div class="eq">P<sub>d,target</sub> ≈ P<sub>d,cell</sub> × q<sub>cluster/select</sub></div><div class="eq-small">q 不是拍脑袋设成 1，本报告直接用 production detection 与 truth 的关联统计。</div></div>
    <div class="eq-card"><h4>⑤ 三帧 2/3 事件</h4><div class="eq">P<sub>2/3</sub> = p<sub>1</sub>p<sub>2</sub> + p<sub>1</sub>p<sub>3</sub> + p<sub>2</sub>p<sub>3</sub> − 2p<sub>1</sub>p<sub>2</sub>p<sub>3</sub><br/><span class="mono">iid: 3p² − 2p³</span></div><div class="eq-small">这是“至少两帧检测到”的事件，不等于当前帧已有 Confirmed measurement 载荷。</div></div>
    <div class="eq-card"><h4>⑥ 输出 SCNR 读数</h4><div class="eq">SCNR<sub>CUT</sub> = 10 log<sub>10</sub>(P<sub>CUT</sub>/M)<br/>SCNR<sub>excess</sub> = 10 log<sub>10</sub>(max(P<sub>CUT</sub>−M,0)/M)</div><div class="eq-small">第一式是直接可观测的功率比；第二式只在 CUT 超过背景时有物理“目标超额”意义。</div></div>
    <div class="eq-card"><h4>⑦ 相位到角度</h4><div class="eq">φ = 2πd sinθ / λ + φ<sub>0</sub><br/>θ̂ = asin(λ(φ̂−φ<sub>0</sub>)/(2πd))</div><div class="eq-small">两通道相位差越稳定，反解出的角度越稳定。</div></div>
    <div class="eq-card"><h4>⑧ 测角精度随 SCNR</h4><div class="eq">σ<sub>θ</sub> ≈ <span class="frac"><span>λ</span><span>2πd cosθ</span></span> · <span class="frac"><span>1</span><span>√γ<sub>φ</sub></span></span></div><div class="eq-small">角度越靠近 90°，1/cosθ 越大；因此 45°几何上比 0°更敏感。</div></div>
  </div>
  <ol class="step-list"><li><span class="tag">小白版</span>先把每个背景方向的平均噪声算出来。</li><li><span class="tag">再取最大</span>最吵方向决定门限，所以 α 不是直接乘一个固定噪声。</li><li><span class="tag">分层统计</span>单元过门限只是第一关，聚类/定位/航迹确认还会继续丢失事件。</li><li><span class="tag">横轴纪律</span>理论使用输出 SCNR；实测同时给出注入 SNR 与直接 CUT/M，不能把二者伪装成同一个量。</li></ol>
</div>
'''


def markdown_table(title: str, headers: list[str], rows: list[list[object]]) -> str:
    body = "| " + " | ".join(headers) + " |\n|" + "|".join("---" for _ in headers) + "|\n"
    body += "".join("| " + " | ".join(str(value) for value in row) + " |\n" for row in rows)
    return f"## {title}\n\n{body}"


def build_artifact(summary: dict, theory_csv: list[dict[str, str]], pfa: float) -> dict:
    unit = unit_dataset(summary["unit"])
    target = target_dataset(summary["target_track"])
    angle = angle_dataset(summary["angle"])
    go_theory = go_theory_dataset()
    angle_theory = angle_theory_dataset()
    jacobian = jacobian_dataset()
    scnr = [dict(row) for row in unit]
    for row in scnr:
        row["output_scnr_definition"] = "CUT/M"

    cards, headline = metric_cards(summary, unit, target, angle, pfa)
    sources = source_specs()
    report_date = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    datasets = {
        "headline": headline,
        "unit_curve": unit,
        "target_curve": target,
        "angle_curve": angle,
        "go_theory": go_theory,
        "angle_theory": angle_theory,
        "jacobian": jacobian,
        "scnr_curve": scnr,
        "unit_table": unit,
        "target_table": target,
        "angle_table": angle,
    }

    def line_chart(chart_id: str, title: str, subtitle: str, dataset: str, x_field: str, x_label: str,
                   y_field: str, y_label: str, y_format: str, intent: str = "trend", color: bool = True) -> dict:
        enc = {
            "x": {"field": x_field, "type": "quantitative", "label": x_label},
            "y": {"field": y_field, "type": "quantitative", "label": y_label, "format": y_format},
        }
        if color:
            enc["color"] = {"field": "series_label", "type": "nominal", "label": "角度/位置"}
        return {
            "id": chart_id,
            "title": title,
            "subtitle": subtitle,
            "type": "line",
            "intent": intent,
            "question": title,
            "rationale": "有序 SNR/SCNR 轴上比较多条角度或位置曲线；颜色只编码有意义的角度/位置分组。",
            "dataset": dataset,
            "sourceId": "report_source_index",
            "encodings": enc,
            "xAxisTitle": x_label,
            "yAxisTitle": y_label,
            "valueFormat": y_format,
            "layout": "full",
            "legend": {"position": "bottom", "sort": "labelAsc", "title": "角度/位置"} if color else {},
            "labels": {"values": "endpoints"},
            "comparisonContext": {"unit": y_label, "grain": "每个角度/位置 × SNR 档位"},
        }

    def scatter_chart(chart_id: str, title: str, subtitle: str, dataset: str, x_field: str, x_label: str,
                      y_field: str, y_label: str, y_format: str, intent: str = "relationship") -> dict:
        return {
            "id": chart_id,
            "title": title,
            "subtitle": subtitle,
            "type": "scatter",
            "intent": intent,
            "question": title,
            "rationale": "每个点保持同一目标帧聚合粒度，用散点显示输出读数与 Pd 的关系，不把缺失输出 SCNR 插值成数值。",
            "dataset": dataset,
            "sourceId": "report_source_index",
            "encodings": {
                "x": {"field": x_field, "type": "quantitative", "label": x_label},
                "y": {"field": y_field, "type": "quantitative", "label": y_label, "format": y_format},
                "color": {"field": "series_label", "type": "nominal", "label": "角度/位置"},
                "tooltip": [
                    {"field": "series_label", "type": "nominal", "label": "角度/位置"},
                    {"field": "input_snr_db", "type": "quantitative", "label": "注入 SNR (dB)"},
                ],
            },
            "xAxisTitle": x_label,
            "yAxisTitle": y_label,
            "valueFormat": y_format,
            "layout": "full",
            "legend": {"position": "bottom", "sort": "labelAsc", "title": "角度/位置"},
            "comparisonContext": {"unit": y_label, "grain": "每个角度/位置 × SNR 档位"},
        }

    charts = [
        line_chart("go_unit_theory", "GO 单元级理论 Pd", "横轴是同一 CFAR 单元的输出 SCNR；低 SCNR 区域应接近 Pfa=1e-6。", "go_theory", "scnr_db", "输出 SCNR (dB)", "pd", "单元 Pd", "percent", color=False),
        line_chart("unit_pd_input", "真实 S+C+N：单元级 Pd vs 注入 SNR", "每个点 9 个目标帧试验；实测横轴是场景注入 SNR，不冒充输出 SCNR。", "unit_curve", "input_snr_db", "注入 SNR (dB)", "pd", "unit Pd", "percent"),
        scatter_chart("unit_pd_direct_scnr", "直接 CUT/M 与单元级 Pd", "横轴是生产 GO 功率图可直接计算的 CUT/M；未检测帧没有伪造的目标超额 SCNR。", "unit_curve", "direct_cut_scnr_db", "直接输出 CUT/M (dB)", "pd", "unit Pd", "percent"),
        line_chart("target_pd_input", "真实 S+C+N：目标级 Pd", "目标级事件 = production detection 在当前周期与 truth 正确关联；分母每点 9 帧。", "target_curve", "input_snr_db", "注入 SNR (dB)", "target_pd", "目标 Pd", "percent"),
        line_chart("track_pd_input", "真实 S+C+N：航迹级 Pd", "航迹级事件 = Confirmed + matched_this_frame + measurement/matched_detection 载荷；分母每点 3 个 seed 实例。", "target_curve", "input_snr_db", "注入 SNR (dB)", "track_pd", "航迹 Pd", "percent"),
        line_chart("two_of_three_input", "三帧 2/3 事件 Pd", "这是‘至少两帧检测到’的联合事件，用于和严格的协议航迹输出区分。", "target_curve", "input_snr_db", "注入 SNR (dB)", "two_of_three_pd", "2/3 事件 Pd", "percent"),
        line_chart("cut_scnr_input", "生产 GO 输出：CUT/M vs 注入 SNR", "CUT/M 包含背景在分母中，是当前链路最直接、最完整的输出 SCNR 代理。", "scnr_curve", "input_snr_db", "注入 SNR (dB)", "direct_cut_scnr_db", "CUT/M (dB)", "number"),
        line_chart("stored_excess_scnr_input", "存档诊断字段：超额 SCNR 观测", "仅绘制诊断 CSV 中可用的存档值；个别历史诊断值可为负，不能把它当成无偏目标 SCNR。", "scnr_curve", "input_snr_db", "注入 SNR (dB)", "stored_excess_scnr_db", "存档 SCNR (dB)", "number"),
        line_chart("angle_theory", "测角理论：σθ 与相位 SCNR", "六条曲线对应 0°/30°/45°中心与右边缘；几何因子为 λ/(2πd cosθ)。", "angle_theory", "scnr_db", "相位输出 SCNR (dB)", "rmse_deg", "理论角度 RMSE (°)", "number"),
        scatter_chart("angle_measured", "真实 S+C+N：测角 RMSE", "只使用 production target 输出中有 angle_error 的匹配样本；横轴仍是注入 SNR 代理。", "angle_curve", "input_snr_db", "注入 SNR (dB)", "rmse_deg", "实测 RMSE (°)", "number", intent="relationship"),
        scatter_chart("angle_bias", "真实 S+C+N：测角偏差", "偏差保留正负号；不能只看 RMSE 而忽略 0°中心的系统偏移。", "angle_curve", "input_snr_db", "注入 SNR (dB)", "bias_deg", "偏差 (°)", "number", intent="relationship"),
        {
            "id": "angle_jacobian",
            "title": "0°/30°/45°的几何测角放大因子",
            "subtitle": "bar 越高，单位相位误差折算成角度误差越大；边缘只增加很小的 0.8°几何变化。",
            "type": "bar",
            "intent": "comparison",
            "question": "不同真实角度的 dtheta/dphi 有多大？",
            "rationale": "六个离散角度/位置适合用从零开始的柱状比较，精确显示 1/cos(theta) 的几何放大。",
            "dataset": "jacobian",
            "sourceId": "report_source_index",
            "encodings": {
                "x": {"field": "angle_label", "type": "nominal", "label": "真实角度"},
                "y": {"field": "jacobian_deg_per_unit", "type": "quantitative", "label": "dtheta/dphi (°/rad)", "format": "number"},
            },
            "xAxisTitle": "真实角度",
            "yAxisTitle": "dtheta/dphi (°/rad)",
            "valueFormat": "number",
            "layout": "full",
            "comparisonContext": {"unit": "°/rad", "grain": "六个角度/位置"},
        },
    ]

    unit_rows = [[row["series_label"], row["input_snr_db"], f"{row['hits']}/{row['trials']}", fmt(row["pd"], 3), fmt(row["wilson_low"], 3), fmt(row["wilson_high"], 3), fmt(row["direct_cut_scnr_db"], 2)] for row in unit]
    target_rows = [[row["series_label"], row["input_snr_db"], f"{row['target_hits']}/{row['target_trials']}", fmt(row["target_pd"], 3), f"{row['track_hits']}/{row['track_trials']}", fmt(row["track_pd"], 3), fmt(row["two_of_three_pd"], 3)] for row in target]
    angle_rows = [[row["series_label"], row["input_snr_db"], row["matched_count"], fmt(row["bias_deg"], 3), fmt(row["rmse_deg"], 3), fmt(row["p95_abs_deg"], 3)] for row in angle]

    markdown_blocks = [
        {
            "id": "title_summary",
            "type": "markdown",
            "sourceId": "report_source_index",
            "body": "# 真实 S+C+N GO-CFAR：0°/30°/45°中心与右边缘\n\n## 技术摘要：方向一致，但理论—实测的横轴还需要继续校准\n\n本报告把生产链拆成 **单元级、目标级、航迹级** 三层 Pd，并同时展示 GO 理论、直接 CUT/M 输出、测角理论和真实 S+C+N 实测。3 个 seed 共 18 个 case，结果显示：中心位置的 unit/target Pd 总体随注入 SNR 上升；30.8°和45.8°右边缘仍显著低于中心；航迹级严格事件目前几乎全为 0，说明 TrackManager 关联/确认是独立的损失层。\n\n最重要的结论是：理论曲线的横轴是 **输出 SCNR**，仿真配置的横轴是 **注入 SNR**。本版不把两者强行重命名，避免用漂亮的曲线掩盖真实链路增益、杂波、Doppler 泄漏和 CSI 处理损失。"
        },
        {
            "id": "scope_definitions",
            "type": "markdown",
            "sourceId": "report_source_index",
            "body": "## 数据范围与统计口径\n\n- 场景：Stage2 `compact_statistical` 真实 **S+C+N**（Rayleigh×lognormal 面杂波 + 热噪声 + 目标）。\n- 角度：0°、30°、45°各一个波束中心；另加右侧 `+0.8°` 边缘。未做全场景扫描。\n- GO-CFAR：Pfa=1e-6、guard=4、background=16、alpha=13.44951031977817；min_points=6；3–5 单元局部峰值 +20 dB 小簇恢复。\n- unit 分母：每个 SNR 点 9 个真实目标帧；目标级同为 9 帧；航迹级分母为 3 个 seed 的三周期目标实例。\n- 航迹级只计 `Confirmed + matched_this_frame` 且载荷来自 measurement/matched detection，不能用预测点替代。\n- raw BIN 已在每个 case 的 GO/TrackManager 审计成功后删除；审计 CSV、manifest、日志、scenario 和实际 XML 保留。"
        },
        {"id": "pipeline_heading", "type": "markdown", "body": "## 先看真实处理链：为什么 Pd 会逐层降低\n\n图中每一个框都是一个可能改变信号、门限或事件定义的环节。"
        },
        {"id": "formula_heading", "type": "markdown", "body": "## 理论建模：从 GO 门限到三层 Pd，再到测角 RMSE\n\n下面的公式按照‘噪声估计 → 过门限 → 成为目标 → 进入航迹 → 相位反解角度’的顺序排列。"
        },
        {"id": "go_theory_heading", "type": "markdown", "sourceId": "report_source_index", "body": "## 图 1：GO 单元级理论曲线\n\n理论曲线在低输出 SCNR 区域接近 Pfa；当输出 SCNR 超过 GO 门限对应的有效区域后才快速上升。由于 alpha≈13.45，不能把负十几 dB 的输出 SCNR 误解成高 Pd。"
        },
        {"id": "unit_heading", "type": "markdown", "sourceId": "report_source_index", "body": "## 图 2–3：单元级 Pd 与直接输出 SCNR\n\n左图按场景注入 SNR 画真实曲线，右图把同一批点投到生产功率图可直接观测的 CUT/M；二者形状不一致时，优先检查处理链增益/损失，而不是先改 GO alpha。"
        },
        {"id": "target_heading", "type": "markdown", "sourceId": "report_source_index", "body": "## 图 4–6：目标级、航迹级和 2/3 联合事件\n\n目标级 Pd 比 unit Pd 低，说明聚类、target-select、定位或 truth association 仍会丢事件。2/3 事件有时高于航迹协议 Pd，因为协议还要求 Confirmed 且当前帧有真实 matched detection 载荷。"
        },
        {"id": "scnr_heading", "type": "markdown", "sourceId": "report_source_index", "body": "## 图 7–8：输出 SCNR 的两种读法\n\n`CUT/M` 是所有有效窗口都能计算的直接输出比值；`stored_excess_scnr_db` 是旧诊断字段的存档观测，个别值可能为负，因此只作为审计线索，不能当作已完成无偏 SCNR 标定。"
        },
        {"id": "angle_heading", "type": "markdown", "sourceId": "report_source_index", "body": "## 图 9–12：测角理论、实测 RMSE、偏差和几何因子\n\n理论曲线使用相位输出 SCNR；实测点使用当前 production angle error 的匹配样本，并保留样本数。0°中心低 SNR 存在较大偏差，30°/45°中心高 SNR 点约为 0.04–0.06° RMSE；右边缘 30.8°/45.8°暂无足够匹配点。"
        },
        {"id": "evidence_heading", "type": "markdown", "sourceId": "report_source_index", "body": "## 精确数值表：图上看形状，表里查分母\n\n表格保留 hit/trial、Wilson 区间、直接输出比值和测角样本数，避免只挑看起来成功的点。"
        },
        {"id": "limitations", "type": "markdown", "sourceId": "report_source_index", "body": "## 限制与下一轮迭代\n\n1. 当前理论 GO 曲线使用 IID 训练窗；真实杂波纹理、旁瓣、Doppler 泄漏、CSI 和聚类损失尚未折算成等效 SCNR。\n2. 边缘只测了右侧一个 `+0.8°` 点；当前 hard_gate 在半宽内不施加连续 beam gain 衰减，因此边缘差异应由实测链路通过率解释，不能凭经验补一个高斯损失。\n3. 航迹级样本只有 3 个 seed 实例/点，0 命中并不等于真实概率为 0；下一轮可在仍不超过 5 seed 的前提下扩充。\n4. 直接 CUT/M 已经可以做第一版理论横轴对齐；下一步应把同帧 `scnr_phase_eff_db`、CUT/M、target Pd 和 TrackManager matched detection 写入统一长表，重新拟合 `SCNR_out = f(SNR_in, angle, Doppler, CSI)`。\n\n### 最短复现入口\n\n```bash\npython3 scripts/gmti_scnr_eval/12_generate_html_report.py\n```\n\n报告输入、曲线和审计根目录见来源索引与报告末尾的源信息。"
        },
    ]

    blocks: list[dict] = []
    blocks.extend(markdown_blocks[:2])
    blocks.append({"id": "headline_metrics", "type": "metric-strip", "cardIds": [card["id"] for card in cards]})
    blocks.extend(markdown_blocks[2:])
    blocks.insert(4, {"id": "pipeline_diagram", "type": "html", "body": html_flow()})
    blocks.insert(6, {"id": "formula_sheet", "type": "html", "body": html_equations()})

    chart_order = [
        "go_unit_theory", "unit_pd_input", "unit_pd_direct_scnr",
        "target_pd_input", "track_pd_input", "two_of_three_input",
        "cut_scnr_input", "stored_excess_scnr_input",
        "angle_theory", "angle_measured", "angle_bias", "angle_jacobian",
    ]
    heading_to_charts = {
        "go_theory_heading": ["go_unit_theory"],
        "unit_heading": ["unit_pd_input", "unit_pd_direct_scnr"],
        "target_heading": ["target_pd_input", "track_pd_input", "two_of_three_input"],
        "scnr_heading": ["cut_scnr_input", "stored_excess_scnr_input"],
        "angle_heading": ["angle_theory", "angle_measured", "angle_bias", "angle_jacobian"],
    }
    # 在文字标题后插入对应图表，保持“结论→定义→图表→解释”的阅读顺序。
    reordered: list[dict] = []
    for block in blocks:
        reordered.append(block)
        for chart_id in heading_to_charts.get(block.get("id"), []):
            reordered.append({"id": f"block_{chart_id}", "type": "chart", "chartId": chart_id, "layout": "full"})
    blocks = reordered
    # 证据表放在精确数值表标题之后。
    for index, block in enumerate(blocks):
        if block.get("id") == "evidence_heading":
            blocks[index + 1:index + 1] = [
                {"id": "unit_table_block", "type": "table", "tableId": "unit_table", "layout": "full"},
                {"id": "target_table_block", "type": "table", "tableId": "target_table", "layout": "full"},
                {"id": "angle_table_block", "type": "table", "tableId": "angle_table", "layout": "full"},
            ]
            break

    tables = [
        {
            "id": "unit_table", "title": "单元级 GO Pd（3-seed 聚合）", "subtitle": "每点 9 个目标帧；Pd=unit hits/trials。",
            "dataset": "unit_table", "sourceId": "report_source_index", "density": "dense", "layout": "full",
            "defaultSort": {"field": "input_snr_db", "direction": "asc"},
            "columns": [
                {"field": "series_label", "label": "角度/位置", "type": "text"},
                {"field": "input_snr_db", "label": "注入 SNR (dB)", "format": "number"},
                {"field": "hits", "label": "hit", "format": "number"},
                {"field": "trials", "label": "trial", "format": "number"},
                {"field": "pd", "label": "unit Pd", "format": "percent"},
                {"field": "wilson_low", "label": "Wilson 下界", "format": "percent"},
                {"field": "wilson_high", "label": "Wilson 上界", "format": "percent"},
                {"field": "direct_cut_scnr_db", "label": "CUT/M (dB)", "format": "number"},
            ],
        },
        {
            "id": "target_table", "title": "目标级与航迹级 Pd（3-seed 聚合）", "subtitle": "航迹分母为 3 个 seed 实例；2/3 是联合事件。",
            "dataset": "target_table", "sourceId": "report_source_index", "density": "dense", "layout": "full",
            "defaultSort": {"field": "input_snr_db", "direction": "asc"},
            "columns": [
                {"field": "series_label", "label": "角度/位置", "type": "text"},
                {"field": "input_snr_db", "label": "注入 SNR (dB)", "format": "number"},
                {"field": "target_hits", "label": "目标 hit", "format": "number"},
                {"field": "target_trials", "label": "目标 trial", "format": "number"},
                {"field": "target_pd", "label": "目标 Pd", "format": "percent"},
                {"field": "track_hits", "label": "航迹 hit", "format": "number"},
                {"field": "track_trials", "label": "航迹 trial", "format": "number"},
                {"field": "track_pd", "label": "航迹 Pd", "format": "percent"},
                {"field": "two_of_three_pd", "label": "2/3 Pd", "format": "percent"},
            ],
        },
        {
            "id": "angle_table", "title": "测角误差（只有实际匹配样本）", "subtitle": "没有 production angle error 的点不插入 0 误差。",
            "dataset": "angle_table", "sourceId": "report_source_index", "density": "dense", "layout": "full",
            "defaultSort": {"field": "input_snr_db", "direction": "asc"},
            "columns": [
                {"field": "series_label", "label": "角度/位置", "type": "text"},
                {"field": "input_snr_db", "label": "注入 SNR (dB)", "format": "number"},
                {"field": "matched_count", "label": "匹配数", "format": "number"},
                {"field": "bias_deg", "label": "偏差 (°)", "format": "number", "movement": True},
                {"field": "rmse_deg", "label": "RMSE (°)", "format": "number"},
                {"field": "p95_abs_deg", "label": "|误差| P95 (°)", "format": "number"},
            ],
        },
    ]

    manifest = {
        "version": 1,
        "surface": "report",
        "title": "真实 S+C+N GO-CFAR 三角度中心/边缘技术报告",
        "description": "生产 GO-CFAR + TrackManager 链路的理论建模与 3-seed S+C+N 实测对照。",
        "generatedAt": report_date,
        "cards": cards,
        "charts": charts,
        "tables": tables,
        "sources": sources,
        "blocks": blocks,
    }
    return {
        "surface": "report",
        "manifest": manifest,
        "snapshot": {"version": 1, "generatedAt": report_date, "status": "ready", "datasets": datasets},
        "sources": sources,
        "package_info": {"root": ".", "manifestPath": rel(DEFAULT_ARTIFACT), "snapshotPath": rel(DEFAULT_ARTIFACT)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--theory-root", type=Path, default=DEFAULT_THEORY_ROOT)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML)
    args = parser.parse_args()
    summary_path = args.summary.resolve()
    theory_root = args.theory_root.resolve()
    artifact_path = args.artifact.resolve()
    html_path = args.html.resolve()
    if not summary_path.is_file():
        raise SystemExit(f"缺少 3-seed 汇总：{summary_path}")
    if not SOURCE_INDEX.is_file():
        raise SystemExit(f"缺少来源索引：{SOURCE_INDEX}")
    summary = load_json(summary_path)
    pfa_rows = read_csv(theory_root / "go_cfar_pfa_validation.csv")
    pfa = number(pfa_rows[0].get("pfa_conditional_mc"), PFA) if pfa_rows else PFA
    theory_rows = read_csv(theory_root / "go_cfar_cell_theory.csv")
    artifact = build_artifact(summary, theory_rows, pfa)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plugin_root = Path("/home/shy/.codex/plugins/cache/openai-curated-remote/data-analytics/0.2.8-13ceeea1f599")
    delivery = plugin_root / "skills/build-report/scripts/deliver_portable_artifact.mjs"
    command = ["node", str(delivery), "--input", str(artifact_path), "--output", str(html_path)]
    completed = subprocess.run(command, cwd=plugin_root, text=True, capture_output=True, check=False)
    if completed.stdout:
        print(completed.stdout.rstrip())
    if completed.returncode != 0:
        if completed.stderr:
            print(completed.stderr.rstrip())
        raise SystemExit(completed.returncode)
    print(f"[PASS] artifact: {artifact_path}")
    print(f"[PASS] portable HTML: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
