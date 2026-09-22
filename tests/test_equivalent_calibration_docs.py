"""Contract checks for the Phase-I equivalent/physical calibration boundary."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"
README = ROOT / "README.md"
BOUNDARY_DOC = ROOT / "docs/AI_CSI_39_等效复校准与物理系统校准边界.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_durable_docs_freeze_the_hierarchical_calibration_tokens() -> None:
    combined = "\n".join(read(path) for path in (AGENTS, README, BOUNDARY_DOC))

    required_tokens = (
        "Class-E",
        "Class-P",
        "Class-D",
        "clutter-derived",
        "ai_training=false",
        "router_enabled=false",
        "Phase-II native four-channel freeze",
    )

    for token in required_tokens:
        assert token in combined, token


def test_readme_lists_the_numbered_phase_i_boundary_document() -> None:
    text = read(README)

    assert "docs/AI_CSI_39_等效复校准与物理系统校准边界.md" in text
    assert "Phase-I" in text
    assert "等效复校准" in text
