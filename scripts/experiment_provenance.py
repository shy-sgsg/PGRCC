#!/usr/bin/env python3
"""Small provenance helper shared by formal experiment runners.

The start and post-run records are intentionally separate.  A run must capture
the source commit and clean-worktree state before it creates any output, then
capture the post-run state after compact evidence has been written.  This
prevents generated evidence from making the *pre-run* provenance appear dirty.
"""

from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def _git_prefix(root: Path) -> list[str]:
    root = root.resolve()
    git_real = root / ".git-real"
    if git_real.is_dir():
        return ["git", f"--git-dir={git_real}", f"--work-tree={root}"]
    return ["git", "-C", str(root)]


def _run_git(root: Path, args: list[str]) -> str:
    completed = subprocess.run(
        [*_git_prefix(root), *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return completed.stdout.strip()


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of one configuration or input file."""
    digest = hashlib.sha256()
    with path.resolve().open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def config_sha256(paths: Iterable[Path]) -> dict[str, str]:
    """Return stable absolute-path -> SHA-256 entries for run configs."""
    output: dict[str, str] = {}
    for raw_path in paths:
        path = raw_path.resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        output[str(path)] = sha256_file(path)
    return output


def git_provenance(root: Path) -> dict[str, object]:
    """Return the commit identity and dirty state for a repo/worktree."""
    root = root.resolve()
    commit = _run_git(root, ["rev-parse", "HEAD"])
    status = _run_git(root, ["status", "--porcelain", "--untracked-files=all"])
    return {
        "source_commit": commit,
        "worktree_dirty": bool(status),
    }


def run_provenance_start(root: Path, config_paths: Iterable[Path] = ()) -> dict[str, object]:
    """Capture provenance before the runner creates or changes output files."""
    git = git_provenance(root)
    return {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_commit": git["source_commit"],
        "source_worktree_dirty_before": bool(git["worktree_dirty"]),
        "config_sha256": config_sha256(config_paths),
    }


def run_provenance_post(root: Path, start: dict[str, object]) -> dict[str, object]:
    """Capture source identity and dirty state after the run's outputs exist."""
    git = git_provenance(root)
    return {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_commit_post_run": git["source_commit"],
        "source_worktree_dirty_after": bool(git["worktree_dirty"]),
        "source_commit_at_start": start.get("source_commit"),
        "source_worktree_dirty_before": start.get("source_worktree_dirty_before"),
    }
