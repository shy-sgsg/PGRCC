"""Contract tests for the immutable Formal-v1 finalization audit."""

from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "docs/AI_CSI_37_ChannelDelay_Finalization_Audit.md"
V1_SOURCE_MANIFEST = ROOT / "outputs/formal_delay_stage1_20260916/manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_formal_v1_audit_labels_development_status_and_provenance() -> None:
    text = AUDIT.read_text(encoding="utf-8")
    assert "Formal-v1" in text
    assert "development formal" in text
    assert "final frozen-paper" in text
    assert "30e0cc4b945dcc63e4e4a92fb4208653b337ca6d07e541b58f1875a61fddcb9b" in text
    assert "106aaacee9c73abba133bad33f34fca4f52ce427" in text
    assert "3ba37d8816cca6ad8f453aa6730197252b5abc74" in text
    assert "must not overwrite" in text or "不覆盖" in text


def test_formal_v1_source_manifest_remains_byte_identical_to_recorded_hash() -> None:
    assert V1_SOURCE_MANIFEST.is_file()
    assert _sha256(V1_SOURCE_MANIFEST) == (
        "30e0cc4b945dcc63e4e4a92fb4208653b337ca6d07e541b58f1875a61fddcb9b"
    )
