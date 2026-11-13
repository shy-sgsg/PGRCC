"""Independent float64 CPU references for the Production GO-CFAR geometry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view


@dataclass(frozen=True)
class CfarGeometry:
    height: int
    width: int
    guard_cells: int
    background_cells: int
    doppler_circular: bool
    exclude_row_start: int = -1
    exclude_row_end: int = -1
    cut_band_start: int = 0
    cut_band_end: int = -1
    cut_band_mode: int = 0

    def __post_init__(self) -> None:
        if self.height <= 0 or self.width <= 0:
            raise ValueError("CFAR map dimensions must be positive")
        if self.guard_cells < 0 or self.background_cells < 1:
            raise ValueError("guard must be nonnegative and background must be positive")
        if self.cut_band_mode not in (0, 1, 2):
            raise ValueError("cut_band_mode must be 0, 1, or 2")
        if self.doppler_circular and self.window_size > self.height:
            raise ValueError("circular Doppler window exceeds row count")

    @property
    def radius(self) -> int:
        return self.guard_cells + self.background_cells

    @property
    def window_size(self) -> int:
        return 2 * self.radius + 1


@dataclass(frozen=True)
class ThresholdDetail:
    row: int
    col: int
    window_bounds: tuple[tuple[int, int, int, int], ...]
    directional_sums: tuple[float, float, float, float]
    directional_means: tuple[float, float, float, float]
    noise_level: float
    threshold: float
    geometry_valid: bool
    valid_cut: bool
    direct_fallback_directions: tuple[str, ...]


@dataclass(frozen=True)
class ThresholdMaps:
    noise_level: np.ndarray
    threshold: np.ndarray
    geometry_valid: np.ndarray
    valid_cut: np.ndarray
    details: dict[tuple[int, int], ThresholdDetail]
    direct_fallback_count: int


def _validate_inputs(power_f32: np.ndarray, geometry: CfarGeometry, alpha: float) -> np.ndarray:
    power = np.asarray(power_f32)
    if power.dtype != np.float32:
        raise ValueError("reference input must be a float32 Production power map")
    if power.shape != (geometry.height, geometry.width):
        raise ValueError("power-map shape does not match CFAR geometry")
    if not np.isfinite(power).all() or np.any(power < 0.0):
        raise ValueError("power map must contain finite nonnegative values")
    if not np.isfinite(alpha) or alpha < 0.0:
        raise ValueError("alpha must be finite and nonnegative")
    return power


def _geometry_masks(geometry: CfarGeometry) -> tuple[np.ndarray, np.ndarray]:
    rows = np.arange(geometry.height)
    cols = np.arange(geometry.width)
    radius = geometry.radius
    row_valid = (
        np.ones(geometry.height, dtype=bool)
        if geometry.doppler_circular
        else (rows >= radius) & (rows < geometry.height - radius)
    )
    col_valid = (cols >= radius) & (cols < geometry.width - radius)
    geometry_valid = row_valid[:, None] & col_valid[None, :]

    excluded = np.zeros(geometry.height, dtype=bool)
    if geometry.exclude_row_start >= 0:
        excluded = (rows >= geometry.exclude_row_start) & (rows <= geometry.exclude_row_end)
    in_band = (rows >= geometry.cut_band_start) & (rows <= geometry.cut_band_end)
    cut_enabled = (
        np.ones(geometry.height, dtype=bool)
        if geometry.cut_band_mode == 0
        else in_band if geometry.cut_band_mode == 1 else ~in_band
    )
    valid_cut = geometry_valid & (~excluded & cut_enabled)[:, None]
    return geometry_valid, valid_cut


def _window_bounds(row: int, col: int, geometry: CfarGeometry) -> tuple[tuple[int, int, int, int], ...]:
    g, radius = geometry.guard_cells, geometry.radius
    return (
        (row - radius, row + radius, col - radius, col - g - 1),
        (row - radius, row + radius, col + g + 1, col + radius),
        (row - radius, row - g - 1, col - radius, col + radius),
        (row + g + 1, row + radius, col - radius, col + radius),
    )


def _direct_sums(power: np.ndarray, row: int, col: int, geometry: CfarGeometry) -> tuple[float, ...]:
    g, b, radius = geometry.guard_cells, geometry.background_cells, geometry.radius
    rr = np.arange(row - radius, row + radius + 1)
    if geometry.doppler_circular:
        rr %= geometry.height
    cc = np.arange(col - radius, col + radius + 1)
    left = power[np.ix_(rr, cc[:b])]
    right = power[np.ix_(rr, cc[-b:])]
    top_rows = np.arange(row - radius, row - g)
    bottom_rows = np.arange(row + g + 1, row + radius + 1)
    if geometry.doppler_circular:
        top_rows %= geometry.height
        bottom_rows %= geometry.height
    top = power[np.ix_(top_rows, cc)]
    bottom = power[np.ix_(bottom_rows, cc)]
    return tuple(float(np.sum(values, dtype=np.float64)) for values in (left, right, top, bottom))


def _integral(prefix: np.ndarray, r0: int, c0: np.ndarray, r1: int, c1: np.ndarray) -> np.ndarray:
    return (
        prefix[r1 + 1, c1 + 1]
        - prefix[r0, c1 + 1]
        - prefix[r1 + 1, c0]
        + prefix[r0, c0]
    )


def _integral_with_bound(
    prefix: np.ndarray,
    r0: int,
    c0: np.ndarray,
    r1: int,
    c1: np.ndarray,
    geometry: CfarGeometry,
) -> tuple[np.ndarray, np.ndarray]:
    p00 = prefix[r1 + 1, c1 + 1]
    p10 = prefix[r0, c1 + 1]
    p01 = prefix[r1 + 1, c0]
    p11 = prefix[r0, c0]
    total = p00 - p10 - p01 + p11

    # Each prefix value is produced by one horizontal and one vertical scan.
    # Bound their accumulated roundoff, then include the three subtractions/addition
    # used by rectangle inclusion-exclusion. All input power values are nonnegative.
    eps = np.finfo(np.float64).eps
    scan_ops = geometry.height + geometry.width
    scan_gamma = (scan_ops * eps) / (1.0 - scan_ops * eps)
    rect_gamma = (3.0 * eps) / (1.0 - 3.0 * eps)
    corner_magnitude = np.abs(p00) + np.abs(p10) + np.abs(p01) + np.abs(p11)
    bound = (scan_gamma + rect_gamma) * corner_magnitude
    return total, bound


def _build_integral(power: np.ndarray, geometry: CfarGeometry) -> np.ndarray:
    source = np.asarray(power, dtype=np.float64)
    prefix = np.zeros((geometry.height + 1, geometry.width + 1), dtype=np.float64)
    prefix[1:, 1:] = np.cumsum(np.cumsum(source, axis=1, dtype=np.float64), axis=0, dtype=np.float64)
    return prefix


def _integral_rows(
    prefix: np.ndarray,
    r0: int,
    r1: int,
    c0: np.ndarray,
    c1: np.ndarray,
    geometry: CfarGeometry,
) -> np.ndarray:
    if not geometry.doppler_circular:
        return _integral(prefix, r0, c0, r1, c1)
    while r0 < 0:
        r0 += geometry.height
        r1 += geometry.height
    while r0 >= geometry.height:
        r0 -= geometry.height
        r1 -= geometry.height
    if r1 < geometry.height:
        return _integral(prefix, r0, c0, r1, c1)
    return _integral(prefix, r0, c0, geometry.height - 1, c1) + _integral(
        prefix, 0, c0, r1 - geometry.height, c1
    )


def _integral_rows_with_bound(
    prefix: np.ndarray,
    r0: int,
    r1: int,
    c0: np.ndarray,
    c1: np.ndarray,
    geometry: CfarGeometry,
) -> tuple[np.ndarray, np.ndarray]:
    if not geometry.doppler_circular:
        return _integral_with_bound(prefix, r0, c0, r1, c1, geometry)
    while r0 < 0:
        r0 += geometry.height
        r1 += geometry.height
    while r0 >= geometry.height:
        r0 -= geometry.height
        r1 -= geometry.height
    if r1 < geometry.height:
        return _integral_with_bound(prefix, r0, c0, r1, c1, geometry)
    first, first_bound = _integral_with_bound(
        prefix, r0, c0, geometry.height - 1, c1, geometry
    )
    second, second_bound = _integral_with_bound(
        prefix, 0, c0, r1 - geometry.height, c1, geometry
    )
    eps = np.finfo(np.float64).eps
    total = first + second
    bound = first_bound + second_bound + eps * (np.abs(first) + np.abs(second))
    return total, bound


def _threshold_error_tolerance(noise: np.ndarray | float, alpha: float) -> tuple[np.ndarray, np.ndarray]:
    noise_values = np.asarray(noise, dtype=np.float64)
    noise_tolerance = np.maximum(1.0e-12, 1.0e-8 * np.abs(noise_values))
    threshold = alpha * noise_values
    with np.errstate(over="ignore", invalid="ignore"):
        threshold_f32 = threshold.astype(np.float32)
        half_ulp = 0.5 * np.abs(np.spacing(threshold_f32).astype(np.float64))
    threshold_tolerance = np.minimum(
        half_ulp,
        np.maximum(1.0e-12, 1.0e-8 * np.abs(threshold)),
    )
    return noise_tolerance, threshold_tolerance


def _needs_direct_fallback(
    noise: np.ndarray | float,
    bound: np.ndarray | float,
    count: int,
    alpha: float,
) -> np.ndarray:
    noise_values = np.asarray(noise, dtype=np.float64)
    noise_tolerance, threshold_tolerance = _threshold_error_tolerance(noise_values, alpha)
    mean_error = np.asarray(bound, dtype=np.float64) / count
    threshold_error = alpha * mean_error
    return (mean_error > noise_tolerance) | (threshold_error > threshold_tolerance)


def _direct_direction_sums_for_row(
    power: np.ndarray,
    row: int,
    cols: np.ndarray,
    geometry: CfarGeometry,
    direction: str,
) -> np.ndarray:
    guard, radius = geometry.guard_cells, geometry.radius
    if direction in ("left", "right"):
        rows = np.arange(row - radius, row + radius + 1, dtype=np.int64)
        if geometry.doppler_circular:
            rows %= geometry.height
        offsets = (
            np.arange(-radius, -guard, dtype=np.int64)
            if direction == "left"
            else np.arange(guard + 1, radius + 1, dtype=np.int64)
        )
        col_grid = cols[:, None] + offsets[None, :]
        values = power[np.ix_(rows, col_grid.reshape(-1))]
        values = values.reshape(rows.size, cols.size, offsets.size)
        return np.sum(values, axis=(0, 2), dtype=np.float64)

    offsets = (
        np.arange(-radius, -guard, dtype=np.int64)
        if direction == "top"
        else np.arange(guard + 1, radius + 1, dtype=np.int64)
    )
    row_grid = row + offsets[None, :]
    if geometry.doppler_circular:
        row_grid %= geometry.height
    col_offsets = np.arange(-radius, radius + 1, dtype=np.int64)
    col_grid = cols[:, None] + col_offsets[None, :]
    values = power[row_grid[:, :, None], col_grid[:, None, :]]
    return np.sum(values, axis=(1, 2), dtype=np.float64)


def _details(
    power: np.ndarray,
    geometry: CfarGeometry,
    alpha: float,
    geometry_valid: np.ndarray,
    valid_cut: np.ndarray,
    detail_cuts: Sequence[tuple[int, int]],
    sum_fn: Callable[[int, int], tuple[tuple[float, ...], tuple[str, ...]]],
) -> dict[tuple[int, int], ThresholdDetail]:
    result: dict[tuple[int, int], ThresholdDetail] = {}
    for row, col in detail_cuts:
        if not (0 <= row < geometry.height and 0 <= col < geometry.width):
            raise ValueError(f"detail CUT {(row, col)} lies outside the power map")
        is_geometry_valid = bool(geometry_valid[row, col])
        sums, fallback_directions = (
            sum_fn(row, col) if is_geometry_valid else ((0.0, 0.0, 0.0, 0.0), ())
        )
        counts = (
            geometry.window_size * geometry.background_cells,
            geometry.window_size * geometry.background_cells,
            geometry.background_cells * geometry.window_size,
            geometry.background_cells * geometry.window_size,
        )
        means = tuple(s / n for s, n in zip(sums, counts))
        noise = max(means)
        result[(row, col)] = ThresholdDetail(
            row=row,
            col=col,
            window_bounds=_window_bounds(row, col, geometry),
            directional_sums=tuple(float(s) for s in sums),
            directional_means=tuple(float(m) for m in means),
            noise_level=float(noise),
            threshold=float(alpha * noise),
            geometry_valid=is_geometry_valid,
            valid_cut=bool(valid_cut[row, col]),
            direct_fallback_directions=fallback_directions,
        )
    return result


def threshold_detail_direct_f64(
    power_f32: np.ndarray,
    geometry: CfarGeometry,
    alpha: float,
    row: int,
    col: int,
) -> ThresholdDetail:
    """Compute one CUT's four directional windows by direct float64 summation."""
    power = _validate_inputs(power_f32, geometry, alpha)
    geometry_valid, valid_cut = _geometry_masks(geometry)
    return _details(
        power, geometry, alpha, geometry_valid, valid_cut, ((row, col),),
        lambda r, c: (_direct_sums(power, r, c, geometry), ()),
    )[(row, col)]


def threshold_detail_integral_f64(
    power_f32: np.ndarray,
    geometry: CfarGeometry,
    alpha: float,
    row: int,
    col: int,
) -> ThresholdDetail:
    """Compute one CUT's four directional windows by a float64 integral image."""
    power = _validate_inputs(power_f32, geometry, alpha)
    geometry_valid, valid_cut = _geometry_masks(geometry)
    prefix = _build_integral(power, geometry)
    g, radius = geometry.guard_cells, geometry.radius

    def sum_fn(r: int, c: int) -> tuple[tuple[float, ...], tuple[str, ...]]:
        c0 = np.asarray([c - radius], dtype=np.int64)
        c1 = np.asarray([c + radius], dtype=np.int64)
        windows = (
            _integral_rows_with_bound(prefix, r - radius, r + radius, c0, np.asarray([c - g - 1]), geometry),
            _integral_rows_with_bound(prefix, r - radius, r + radius, np.asarray([c + g + 1]), c1, geometry),
            _integral_rows_with_bound(prefix, r - radius, r - g - 1, c0, c1, geometry),
            _integral_rows_with_bound(prefix, r + g + 1, r + radius, c0, c1, geometry),
        )
        sums = [float(item[0][0]) for item in windows]
        bounds = [float(item[1][0]) for item in windows]
        counts = (geometry.window_size * geometry.background_cells,) * 4
        noise = max(s / n for s, n in zip(sums, counts))
        fallback = tuple(
            name
            for name, bound, count in zip(("left", "right", "top", "bottom"), bounds, counts)
            if bool(_needs_direct_fallback(noise, bound, count, alpha))
        )
        direct = _direct_sums(power, r, c, geometry) if fallback else ()
        direction_names = ("left", "right", "top", "bottom")
        for name in fallback:
            index = direction_names.index(name)
            sums[index] = direct[index]
        return tuple(sums), fallback

    return _details(power, geometry, alpha, geometry_valid, valid_cut, ((row, col),), sum_fn)[(row, col)]


def thresholds_direct_f64(
    power_f32: np.ndarray,
    geometry: CfarGeometry,
    alpha: float,
    detail_cuts: Sequence[tuple[int, int]] = (),
) -> ThresholdMaps:
    """Compute GO-CFAR using direct float64 window reductions, without an integral image."""
    power = _validate_inputs(power_f32, geometry, alpha)
    geometry_valid, valid_cut = _geometry_masks(geometry)
    radius = geometry.radius
    if geometry.doppler_circular:
        row_padded = np.concatenate((power[-radius:], power, power[:radius]), axis=0)
        padded = np.pad(row_padded, ((0, 0), (radius, radius)), mode="constant")
    else:
        padded = np.pad(power, ((radius, radius), (radius, radius)), mode="constant")
    windows = sliding_window_view(padded, (geometry.window_size, geometry.window_size))
    height, width = power.shape
    noise = np.zeros((height, width), dtype=np.float64)
    chunk_rows = 4
    g, b, win = geometry.guard_cells, geometry.background_cells, geometry.window_size
    side_count = win * b
    cap_count = b * win
    for start in range(0, height, chunk_rows):
        stop = min(height, start + chunk_rows)
        block = windows[start:stop]
        left = np.sum(block[:, :, :, :b], axis=(-2, -1), dtype=np.float64) / side_count
        right = np.sum(block[:, :, :, -b:], axis=(-2, -1), dtype=np.float64) / side_count
        top = np.sum(block[:, :, :b, :], axis=(-2, -1), dtype=np.float64) / cap_count
        bottom = np.sum(block[:, :, -b:, :], axis=(-2, -1), dtype=np.float64) / cap_count
        noise[start:stop] = np.maximum(np.maximum(left, right), np.maximum(top, bottom))
    noise[~geometry_valid] = 0.0
    threshold = alpha * noise

    def sum_fn(row: int, col: int) -> tuple[tuple[float, ...], tuple[str, ...]]:
        return _direct_sums(power, row, col, geometry), ()

    details = _details(power, geometry, alpha, geometry_valid, valid_cut, detail_cuts, sum_fn)
    return ThresholdMaps(noise, threshold, geometry_valid, valid_cut, details, 0)


def thresholds_integral_f64(
    power_f32: np.ndarray,
    geometry: CfarGeometry,
    alpha: float,
    detail_cuts: Sequence[tuple[int, int]] = (),
) -> ThresholdMaps:
    """Compute GO-CFAR using a float64 integral image and inclusion-exclusion."""
    power = _validate_inputs(power_f32, geometry, alpha)
    geometry_valid, valid_cut = _geometry_masks(geometry)
    prefix = _build_integral(power, geometry)
    noise = np.zeros(power.shape, dtype=np.float64)
    g, b, radius, win = (
        geometry.guard_cells,
        geometry.background_cells,
        geometry.radius,
        geometry.window_size,
    )
    cols = np.arange(radius, geometry.width - radius, dtype=np.int64)
    direction_names = ("left", "right", "top", "bottom")
    direction_counts = (win * b, win * b, b * win, b * win)
    sums_by_direction = [np.zeros((geometry.height, cols.size), dtype=np.float64) for _ in direction_names]
    bounds_by_direction = [np.zeros((geometry.height, cols.size), dtype=np.float64) for _ in direction_names]
    if cols.size:
        for row in np.flatnonzero(geometry_valid[:, radius] if radius < geometry.width - radius else np.zeros(geometry.height, dtype=bool)):
            row = int(row)
            windows = (
                _integral_rows_with_bound(prefix, row - radius, row + radius, cols - radius, cols - g - 1, geometry),
                _integral_rows_with_bound(prefix, row - radius, row + radius, cols + g + 1, cols + radius, geometry),
                _integral_rows_with_bound(prefix, row - radius, row - g - 1, cols - radius, cols + radius, geometry),
                _integral_rows_with_bound(prefix, row + g + 1, row + radius, cols - radius, cols + radius, geometry),
            )
            for direction, (values, bounds) in enumerate(windows):
                sums_by_direction[direction][row] = values
                bounds_by_direction[direction][row] = bounds

    if cols.size:
        row_indices = np.flatnonzero(geometry_valid[:, radius])
        valid_sums = [values[row_indices] for values in sums_by_direction]
        valid_bounds = [values[row_indices] for values in bounds_by_direction]
        preliminary_means = [values / count for values, count in zip(valid_sums, direction_counts)]
        preliminary_noise = np.maximum.reduce(preliminary_means)
        fallback_masks = [
            _needs_direct_fallback(preliminary_noise, bounds, count, alpha)
            for bounds, count in zip(valid_bounds, direction_counts)
        ]
        direct_fallback_count = 0
        for direction, mask in enumerate(fallback_masks):
            for local_row in np.flatnonzero(np.any(mask, axis=1)):
                row = int(row_indices[local_row])
                local_cols = np.flatnonzero(mask[local_row])
                cols_to_fix = cols[local_cols]
                sums_by_direction[direction][row, local_cols] = _direct_direction_sums_for_row(
                    power, row, cols_to_fix, geometry, direction_names[direction]
                )
                direct_fallback_count += int(local_cols.size)
        final_means = [
            values[row_indices] / count
            for values, count in zip(sums_by_direction, direction_counts)
        ]
        noise[row_indices[:, None], cols[None, :]] = np.maximum.reduce(final_means)
    else:
        direct_fallback_count = 0
    threshold = alpha * noise

    def sum_fn(row: int, col: int) -> tuple[tuple[float, ...], tuple[str, ...]]:
        cc0 = np.asarray([col - radius], dtype=np.int64)
        c1 = np.asarray([col + radius], dtype=np.int64)
        windows = (
            _integral_rows_with_bound(prefix, row - radius, row + radius, cc0, np.asarray([col - g - 1]), geometry),
            _integral_rows_with_bound(prefix, row - radius, row + radius, np.asarray([col + g + 1]), c1, geometry),
            _integral_rows_with_bound(prefix, row - radius, row - g - 1, cc0, c1, geometry),
            _integral_rows_with_bound(prefix, row + g + 1, row + radius, cc0, c1, geometry),
        )
        sums = [float(item[0][0]) for item in windows]
        bounds = [float(item[1][0]) for item in windows]
        counts = (win * b,) * 4
        preliminary_noise = max(value / count for value, count in zip(sums, counts))
        fallback = tuple(
            name
            for name, bound, count in zip(direction_names, bounds, counts)
            if bool(_needs_direct_fallback(preliminary_noise, bound, count, alpha))
        )
        if fallback:
            direct = _direct_sums(power, row, col, geometry)
            for name in fallback:
                index = direction_names.index(name)
                sums[index] = direct[index]
        return tuple(sums), fallback

    details = _details(power, geometry, alpha, geometry_valid, valid_cut, detail_cuts, sum_fn)
    return ThresholdMaps(noise, threshold, geometry_valid, valid_cut, details, direct_fallback_count)
