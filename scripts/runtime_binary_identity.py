"""Small shared helpers for pinning production executable identities."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


class BinaryIdentityMismatch(RuntimeError):
    """Raised when a runtime executable differs from its pinned SHA-256."""


def configured_executable_path(env_name: str, default: Path) -> Path:
    """Resolve an optional isolated-build path while preserving the old default."""

    configured = os.environ.get(env_name, "").strip()
    return Path(configured).expanduser().resolve() if configured else Path(default).resolve()


def assert_artifact_sha256(path: Path, expected_sha256: str, label: str) -> str:
    """Require a file to exist and match the expected binary identity."""

    candidate = Path(path).resolve()
    if not candidate.is_file():
        raise BinaryIdentityMismatch(f"{label} identity mismatch: file is missing: {candidate}")
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if not expected_sha256 or digest != expected_sha256:
        raise BinaryIdentityMismatch(
            f"{label} identity mismatch: expected {expected_sha256 or '<unset>'}, "
            f"actual {digest}, path {candidate}"
        )
    return digest
