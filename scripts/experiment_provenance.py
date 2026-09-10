#!/usr/bin/env python3
"""Small read-only provenance helper shared by formal experiment runners."""

from __future__ import annotations

import subprocess
from pathlib import Path


def git_provenance(root: Path) -> dict[str, object]:
    """Return the commit identity and dirty state for a repo/worktree."""
    root = root.resolve()
    git_real = root / ".git-real"
    if git_real.is_dir():
        prefix = ["git", f"--git-dir={git_real}", f"--work-tree={root}"]
    else:
        prefix = ["git", "-C", str(root)]

    def run(args: list[str]) -> str:
        completed = subprocess.run(
            [*prefix, *args],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        return completed.stdout.strip()

    commit = run(["rev-parse", "HEAD"])
    status = run(["status", "--porcelain", "--untracked-files=all"])
    return {
        "source_commit": commit,
        "worktree_dirty": bool(status),
    }
