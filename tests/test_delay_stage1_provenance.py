"""Provenance and independent-output contracts for Formal-v2."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _v2_args(output_root: Path):
    from scripts.run_delay_stage1_formal import build_arg_parser

    return build_arg_parser().parse_args(
        [
            "--mode",
            "formal",
            "--evidence-version",
            "formal-v2",
            "--output-root",
            str(output_root),
            "--input-mode",
            "local",
            "--working-point",
            "registered",
            "--delay-errors-ns",
            "0,1,-1,2,-2,4,-4,8,-8",
            "--mc-trials",
            "100",
            "--skip-cuda",
            "--max-cases",
            "1",
        ]
    )


def test_formal_v2_manifest_records_clean_provenance_and_separate_roots(tmp_path: Path, monkeypatch) -> None:
    import scripts.run_delay_stage1_formal as runner

    snapshots = iter(
        [
            {"commit": "frozen", "dirty": False, "dirty_tracked": False, "untracked_count": 0},
            {"commit": "frozen", "dirty": False, "dirty_tracked": False, "untracked_count": 0},
        ]
    )
    monkeypatch.setattr(runner, "_git_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(
        runner,
        "_gpu_status",
        lambda: {"available": False, "command": "nvidia-smi", "returncode": 1, "stderr": "unavailable"},
    )

    output_root = tmp_path / "formal_v2"
    args = _v2_args(output_root)
    manifest = runner.run_stage1_cli(
        args,
        command_args=[
            "--mode",
            "formal",
            "--evidence-version",
            "formal-v2",
            "--output-root",
            str(output_root),
            "--working-point",
            "registered",
            "--delay-errors-ns",
            "0,1,-1,2,-2,4,-4,8,-8",
        ],
    )

    assert manifest["evidence_version"] == "formal-v2"
    assert manifest["source_commit_before"] == "frozen"
    assert manifest["source_commit_after"] == "frozen"
    assert manifest["source_dirty_before"] is False
    assert manifest["source_dirty_after"] is False
    assert manifest["attempt"] == 1
    assert manifest["resume"] is False
    assert set(manifest["input_hashes"]) >= {"config", "template"}
    assert set(manifest["build_identity"]) >= {"simulate_stage2_statistical", "GMTI_pipe_core"}
    assert set(manifest["diagnostic_taps"]) >= {"cfar_geometry", "track_association_v2", "pipe_payload"}
    assert manifest["output_roots"]["formal_v2"] == str(output_root.resolve())
    assert manifest["output_roots"]["formal_v1"] != str(output_root.resolve())
    assert manifest["resolved"]["e2e_delay_errors_ns"] == [0.0, 1.0, -1.0, 2.0, -2.0, 4.0, -4.0, 8.0, -8.0]


def test_compact_analyzer_preserves_v2_provenance_and_protects_v1_root(tmp_path: Path) -> None:
    from scripts.analyze_delay_stage1_formal import build_compact_evidence

    run_root = tmp_path / "run"
    run_root.mkdir()
    manifest_path = run_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "evidence_version": "formal-v2",
                "status": "completed_with_gaps",
                "mode": "formal",
                "command": {"shell": "formal-v2"},
                "config": {},
                "template": {},
                "resolved": {"e2e_delay_errors_ns": [0.0]},
                "cases": [],
                "mc_summary": {"rows": []},
                "ai_training": False,
                "router_enabled": False,
                "native_four_channel_stap": False,
                "source_commit_before": "frozen",
                "source_commit_after": "frozen",
                "source_dirty_before": False,
                "source_dirty_after": False,
                "diagnostic_taps": {"cfar_geometry": [], "track_association_v2": [], "pipe_payload": []},
                "output_roots": {"formal_v1": "v1", "formal_v2": "v2"},
            }
        ),
        encoding="utf-8",
    )
    outputs = build_compact_evidence(manifest_path, tmp_path / "v2_evidence")
    compact = json.loads(outputs["manifest.json"].read_text(encoding="utf-8"))
    assert compact["evidence_version"] == "formal-v2"
    assert compact["source_commit_before"] == "frozen"
    assert compact["source_commit_after"] == "frozen"
    assert compact["source_dirty_before"] is False
    assert compact["source_dirty_after"] is False
    assert "diagnostic_taps" in compact

    with pytest.raises(ValueError, match="immutable Formal-v1"):
        build_compact_evidence(manifest_path, ROOT / "outputs/formal_evidence/stage1_delay")
