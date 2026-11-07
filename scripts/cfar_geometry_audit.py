"""Parse and aggregate production GO-CFAR geometry diagnostics."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


REQUIRED_GEOMETRY_FIELDS = frozenset(
    {
        "schema_version",
        "period_id",
        "beam_id",
        "branch",
        "total_cells",
        "edge_invalid_cells",
        "excluded_cells",
        "cut_band_filtered_cells",
        "valid_cut_count",
        "threshold_test_count",
        "hit_cut_count",
        "hit_index_hash",
        "configured_pfa",
        "cfar_type",
        "alpha",
        "guard_cells",
        "background_cells",
        "doppler_circular",
        "exclude_row_start",
        "exclude_row_end",
        "cut_band_start",
        "cut_band_end",
        "cut_band_mode",
    }
)

DOWNSTREAM_FIELDS = frozenset(
    {
        "false_cut_hit_count",
        "false_cluster_count",
        "protocol_detection_row_count",
        "false_track_count",
    }
)


def _int(row: Mapping[str, Any], field: str) -> int:
    try:
        value = int(row[field])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if value < 0 and field not in {
        "exclude_row_start",
        "exclude_row_end",
    }:
        raise ValueError(f"{field} must be non-negative")
    return value


def validate_geometry_row(row: Mapping[str, Any]) -> dict[str, Any]:
    missing = sorted(REQUIRED_GEOMETRY_FIELDS - set(row))
    if missing:
        raise ValueError(f"missing geometry fields: {', '.join(missing)}")
    if int(row["schema_version"]) != 1:
        raise ValueError(f"unsupported geometry schema_version={row['schema_version']}")
    if not str(row["branch"]).strip():
        raise ValueError("branch must be non-empty")
    for field in (
        "period_id",
        "beam_id",
        "total_cells",
        "edge_invalid_cells",
        "excluded_cells",
        "cut_band_filtered_cells",
        "valid_cut_count",
        "threshold_test_count",
        "hit_cut_count",
        "guard_cells",
        "background_cells",
        "cut_band_start",
        "cut_band_end",
    ):
        _int(row, field)
    if not str(row["cut_band_mode"]).strip():
        raise ValueError("cut_band_mode must be non-empty")
    valid = _int(row, "valid_cut_count")
    threshold = _int(row, "threshold_test_count")
    total = _int(row, "total_cells")
    hits = _int(row, "hit_cut_count")
    if threshold + _int(row, "edge_invalid_cells") != total:
        raise ValueError("threshold_test_count plus edge_invalid_cells must equal total_cells")
    if hits > valid:
        raise ValueError("hit_cut_count cannot exceed valid_cut_count")
    return dict(row)


def aggregate_geometry_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return validated rows with an auditable hit-rate status.

    The downstream fields intentionally remain separate.  A protocol row count
    or a track count is not substituted for a CUT hit count.
    """

    result: list[dict[str, Any]] = []
    for raw in rows:
        row = validate_geometry_row(raw)
        valid = int(row["valid_cut_count"])
        hits = int(row["hit_cut_count"])
        if valid > 0:
            row["hit_rate"] = hits / valid
            row["hit_rate_status"] = "evaluable"
        else:
            row["hit_rate"] = None
            row["hit_rate_status"] = "NOT_EVALUABLE"
        for field in DOWNSTREAM_FIELDS:
            if field in row:
                marker = str(row[field]).strip().upper()
                row[field] = None if marker in {"", "NA", "NOT_EVALUABLE", "UNAVAILABLE"} else int(row[field])
        result.append(row)
    return result


def load_geometry_rows(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        if path.suffix.lower() == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                rows.extend(data)
            elif isinstance(data, dict) and isinstance(data.get("rows"), list):
                rows.extend(data["rows"])
            else:
                raise ValueError(f"{path} does not contain a row list")
        else:
            with path.open(newline="", encoding="utf-8") as stream:
                rows.extend(dict(row) for row in csv.DictReader(stream))
    return aggregate_geometry_rows(rows)
