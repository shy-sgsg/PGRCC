"""Contract checks for the frozen Stage-1.1 feature boundaries."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FLAGS = {
    "ai_training": False,
    "router_enabled": False,
    "native_four_channel_stap": False,
}

NEW_CONFIGS = (
    ROOT / "configs/research/channel_delay_stage1_formal.json",
    ROOT / "configs/research/channel_delay_stage1_materiality.json",
)
NEW_MANIFESTS = (
    ROOT / "outputs/fractional_delay_inverse_closure_20260917/closure_manifest.json",
    ROOT / "outputs/formal_evidence/stage1_delay_v2_reanalysis_20260917/reanalysis_manifest.json",
    ROOT / "outputs/formal_evidence/stage1_delay_hierarchical_v1_exploratory_20260917/hierarchical_manifest.json",
)
NEW_DOCS = (
    ROOT / "docs/AI_CSI_36_单一系统误差确定性自校准阶段报告.md",
    ROOT / "docs/AI_CSI_38_ChannelDelay_Final_Formal_Conclusion.md",
    ROOT / "docs/literature/ChannelDelay_Calibration_Novelty_Audit.md",
    ROOT / "docs/experiments/ChannelDelay_Hardware_Validation_Protocol.md",
    ROOT / "docs/papers/Paper1_ChannelDelay_SelfCalibration_Outline.md",
)


def test_new_stage1_configs_and_manifests_freeze_all_control_flags() -> None:
    for path in (*NEW_CONFIGS, *NEW_MANIFESTS):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert {key: payload.get(key) for key in FLAGS} == FLAGS, path


def test_new_stage1_docs_repeat_the_same_control_flags() -> None:
    expected = tuple(f"{key}=false" for key in FLAGS)
    for path in NEW_DOCS:
        text = path.read_text(encoding="utf-8")
        assert all(value in text for value in expected), path
