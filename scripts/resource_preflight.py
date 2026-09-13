#!/usr/bin/env python3
"""Unified resource snapshot and refusal gate for long experiments.

The helper is intentionally read-only.  It never changes swap state, GPU
state, or the filesystem beyond what a caller explicitly does with the
returned record.  Long CUDA runners can persist the record before deciding
whether to start.
"""

from __future__ import annotations

import math
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


GIB = 1024 ** 3


def _bytes_from_kib(value: str | float | int) -> int:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid KiB value: {value!r}") from exc
    if not math.isfinite(numeric) or numeric < 0.0:
        raise ValueError(f"invalid non-negative KiB value: {value!r}")
    return int(round(numeric * 1024.0))


def parse_meminfo(text: str) -> dict[str, int]:
    """Parse the byte-valued fields needed from Linux ``/proc/meminfo``."""

    values: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        fields = raw.strip().split()
        if not fields:
            continue
        try:
            number = float(fields[0])
        except ValueError:
            continue
        unit = fields[1].lower() if len(fields) > 1 else "b"
        multiplier = {"b": 1.0, "kb": 1024.0, "mb": 1024.0 ** 2,
                      "gb": 1024.0 ** 3}.get(unit)
        if multiplier is not None and math.isfinite(number) and number >= 0.0:
            values[key] = int(round(number * multiplier))
    return values


def _number_or_none(value: str) -> float | None:
    token = value.strip().replace("%", "")
    if token.lower() in {"", "n/a", "na", "not supported", "unknown"}:
        return None
    try:
        parsed = float(token)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def parse_nvidia_query(text: str) -> list[dict[str, Any]]:
    """Parse ``nvidia-smi --query-gpu`` CSV output.

    The query used by :func:`collect_resource_snapshot` requests values
    without units, so memory values are converted from MiB to bytes here.
    Missing ``N/A`` values remain ``None`` rather than becoming zero.
    """

    fields = (
        "index", "name", "memory.free", "memory.total", "utilization.gpu",
        "pstate", "temperature.gpu", "power.draw", "clocks.sm",
    )
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != len(fields):
            continue
        row: dict[str, Any] = {}
        for field, value in zip(fields, parts):
            if field == "name" or field == "pstate":
                row[field] = value
            elif field == "index":
                parsed = _number_or_none(value)
                row[field] = int(parsed) if parsed is not None else None
            else:
                parsed = _number_or_none(value)
                row[field] = (int(round(parsed * 1024.0 ** 2))
                              if parsed is not None and field.startswith("memory.")
                              else parsed)
        rows.append(row)
    return rows


def _nvidia_snapshot() -> dict[str, Any]:
    query = (
        "index,name,memory.free,memory.total,utilization.gpu,pstate,"
        "temperature.gpu,power.draw,clocks.sm"
    )
    command = ["nvidia-smi", f"--query-gpu={query}",
               "--format=csv,noheader,nounits"]
    try:
        completed = subprocess.run(
            command, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=False,
        )
    except OSError as exc:
        return {"status": "unavailable", "error": str(exc), "gpus": []}
    rows = parse_nvidia_query(completed.stdout)
    if completed.returncode != 0 or not rows:
        return {
            "status": "unavailable",
            "returncode": completed.returncode,
            "output": completed.stdout.strip(),
            "gpus": rows,
        }
    free_values = [row["memory.free"] for row in rows
                   if isinstance(row.get("memory.free"), (int, float))]
    return {
        "status": "available",
        "returncode": completed.returncode,
        "gpus": rows,
        "gpu_count": len(rows),
        "max_free_memory_bytes": max(free_values) if free_values else None,
    }


def collect_resource_snapshot(root: Path) -> dict[str, Any]:
    """Collect memory, swap, GPU, and disk state without mutating it."""

    root = root.resolve()
    meminfo_path = Path("/proc/meminfo")
    try:
        meminfo = parse_meminfo(meminfo_path.read_text(encoding="utf-8"))
        meminfo_error = None
    except OSError as exc:
        meminfo = {}
        meminfo_error = str(exc)

    swap_total = int(meminfo.get("SwapTotal", 0))
    swap_free = int(meminfo.get("SwapFree", 0))
    swap_used = max(0, swap_total - swap_free)
    swap_fraction = (swap_used / swap_total) if swap_total > 0 else 0.0
    try:
        disk_free = int(shutil.disk_usage(root).free)
        disk_error = None
    except OSError as exc:
        disk_free = None
        disk_error = str(exc)

    gpu = _nvidia_snapshot()
    return {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": platform.node(),
        "root": str(root),
        "memory_available_bytes": meminfo.get("MemAvailable"),
        "swap_total_bytes": swap_total,
        "swap_used_bytes": swap_used,
        "swap_used_fraction": swap_fraction,
        "disk_free_bytes": disk_free,
        "memory_error": meminfo_error,
        "disk_error": disk_error,
        "gpu": gpu,
    }


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def assess_resource_preflight(snapshot: Mapping[str, Any],
                              policy: Mapping[str, Any]) -> dict[str, Any]:
    """Return a pass/reject decision from a snapshot and explicit policy."""

    reasons: list[str] = []
    memory = _finite(snapshot.get("memory_available_bytes"))
    min_memory = float(policy.get("min_mem_available_gib", 0.0)) * GIB
    if memory is None:
        reasons.append("MemAvailable unavailable")
    elif memory < min_memory:
        reasons.append(
            f"MemAvailable below {policy.get('min_mem_available_gib')} GiB")

    # Swap remains part of the snapshot for diagnostics.  A configured limit
    # is optional; ``null``/missing means that swap pressure does not reject
    # the run, as required for the compact Router experiment on this host.
    swap_total = _finite(snapshot.get("swap_total_bytes")) or 0.0
    swap_fraction = _finite(snapshot.get("swap_used_fraction"))
    max_swap_raw = policy.get("max_swap_used_fraction")
    if max_swap_raw is not None:
        max_swap = float(max_swap_raw)
        if swap_fraction is None:
            reasons.append("SwapUsed/SwapTotal unavailable")
        elif swap_total > 0.0 and swap_fraction > max_swap:
            reasons.append(
                f"swap used fraction above {policy.get('max_swap_used_fraction')}")

    gpu = snapshot.get("gpu", {})
    require_gpu = bool(policy.get("require_gpu", True))
    gpu_status = gpu.get("status") if isinstance(gpu, Mapping) else None
    gpu_count = int(gpu.get("gpu_count", 0)) if isinstance(gpu, Mapping) else 0
    min_gpu = float(policy.get("min_gpu_free_gib", 0.0)) * GIB
    gpu_free = _finite(gpu.get("max_free_memory_bytes")) if isinstance(gpu, Mapping) else None
    if require_gpu and gpu_status != "available":
        reasons.append("GPU is not visible")
    elif require_gpu and gpu_count < int(policy.get("min_gpu_count", 1)):
        reasons.append("fewer GPUs than required")
    elif require_gpu and (gpu_free is None or gpu_free < min_gpu):
        reasons.append(
            f"GPU free memory below {policy.get('min_gpu_free_gib')} GiB")

    disk_free = _finite(snapshot.get("disk_free_bytes"))
    min_disk = float(policy.get("min_disk_free_gib", 0.0)) * GIB
    if disk_free is None:
        reasons.append("disk free space unavailable")
    elif disk_free < min_disk:
        reasons.append(
            f"disk free space below {policy.get('min_disk_free_gib')} GiB")

    return {
        "status": "pass" if not reasons else "reject",
        "reasons": reasons,
        "policy": dict(policy),
        "snapshot": dict(snapshot),
        "swapoff_performed": False,
    }
