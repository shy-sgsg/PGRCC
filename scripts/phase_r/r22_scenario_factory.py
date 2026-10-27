"""Deterministic R22 Stage2 scenario construction with separate target streams."""

from __future__ import annotations

import copy
import math
import random
from typing import Any, Mapping

C_MPS = 299_792_458.0
_TARGET_STREAM_TAG = 0x52320000


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _axis_vector(axis: str) -> tuple[float, float]:
    vectors = {
        "east": (1.0, 0.0), "track_right": (1.0, 0.0),
        "west": (-1.0, 0.0), "track_left": (-1.0, 0.0),
        "north": (0.0, 1.0), "track_forward": (0.0, 1.0),
        "south": (0.0, -1.0), "track_backward": (0.0, -1.0),
    }
    if axis not in vectors:
        raise ValueError(f"unsupported Stage2 local axis: {axis}")
    return vectors[axis]


def _look_vector(
    *,
    platform_ve: float,
    platform_vn: float,
    theta_deg: float,
    theta_offset_deg: float,
    squint_side: int,
    heading_source: str,
    fixed_heading_deg: float,
) -> tuple[float, float]:
    """Match gmti::sim_geometry::makeAlgorithmLookVectorEN for the frozen geometry."""
    if heading_source == "fixed_angle":
        heading_deg = fixed_heading_deg
    elif heading_source == "velocity":
        heading_deg = math.degrees(math.atan2(platform_vn, platform_ve))
    else:
        raise ValueError(f"unsupported platform_heading_source: {heading_source}")
    side_dir = -90.0 if squint_side == 1 else 90.0
    beam_center_dir = side_dir - (theta_deg + theta_offset_deg)
    azimuth = math.radians(heading_deg - beam_center_dir)
    return math.cos(azimuth), math.sin(azimuth)


def _target_stream_seed(seed: int, target_index: int) -> int:
    return (int(seed) << 32) | (_TARGET_STREAM_TAG + int(target_index))


def _range_permutation_stream_seed(seed: int) -> int:
    return (int(seed) << 32) | (_TARGET_STREAM_TAG ^ 0x5A5A5A5A)


def build_seed_scenario(
    base_scenario: Mapping[str, Any],
    *,
    seed: int,
    target_count: int,
    radial_speed_mps: float,
    target_snr_db: float,
    period_count: int,
    first_beam: int,
    beam_count: int,
    clutter_range_window_m: tuple[float, float],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create one paired-scene base config and an auditable target substream manifest.

    Stage2 continues to use ``seed`` for clutter and packet-addressed noise. Each
    target's initial range, beam offset, and tangential velocity are generated
    from a separate deterministic Python RNG stream keyed by ``(seed, target_id)``.
    Target radial velocity is constructed along the exact Stage2 ENU look vector.
    """
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32:
        raise ValueError("seed must be an integer in [0, 2^32)")
    if isinstance(target_count, bool) or not isinstance(target_count, int) or target_count < 0:
        raise ValueError("target_count must be a non-negative integer")
    if isinstance(period_count, bool) or not isinstance(period_count, int) or period_count <= 0:
        raise ValueError("period_count must be a positive integer")
    if isinstance(first_beam, bool) or not isinstance(first_beam, int) or first_beam <= 0:
        raise ValueError("first_beam must be a positive integer")
    if isinstance(beam_count, bool) or not isinstance(beam_count, int) or beam_count <= 0:
        raise ValueError("beam_count must be a positive integer")
    radial_speed = _finite(radial_speed_mps, "radial_speed_mps")
    snr_db = _finite(target_snr_db, "target_snr_db")
    if radial_speed < 0.0:
        raise ValueError("radial_speed_mps must be non-negative")

    scenario = copy.deepcopy(dict(base_scenario))
    waveform = scenario.get("waveform")
    range_cfg = scenario.get("range_processing")
    scan = scenario.get("scan")
    platform = scenario.get("platform")
    random_cfg = scenario.get("random")
    scene = scenario.get("scene")
    if not all(isinstance(value, Mapping) for value in (waveform, range_cfg, scan, platform, random_cfg, scene)):
        raise ValueError("base scenario must define waveform, range_processing, scan, platform, random and scene")

    beam_index_base = int(scan.get("beam_index_base", 1))
    total_beams = int(scan.get("beam_count", 0))
    if beam_index_base not in (0, 1) or first_beam < beam_index_base:
        raise ValueError("invalid beam index base or first beam")
    last_beam = first_beam + beam_count - 1
    if total_beams <= 0 or last_beam >= beam_index_base + total_beams:
        raise ValueError("beam span exceeds configured scan beam count")
    fs_hz = _finite(waveform.get("fs_mhz"), "waveform.fs_mhz") * 1.0e6
    crop_start = int(range_cfg.get("range_crop_start", -1))
    crop_len = int(range_cfg.get("range_crop_len", 0))
    sample_delay_s = _finite(range_cfg.get("sample_delay_us"), "range_processing.sample_delay_us") * 1.0e-6
    if fs_hz <= 0.0 or crop_start < 0 or crop_len <= 0 or sample_delay_s < 0.0:
        raise ValueError("invalid physical range-axis configuration")
    range_min, range_max = (_finite(value, "clutter_range_window_m") for value in clutter_range_window_m)
    if range_min >= range_max:
        raise ValueError("clutter range window must be increasing")
    scene_range_min = _finite(scene.get("range_min_m"), "scene.range_min_m")
    scene_range_max = _finite(scene.get("range_max_m"), "scene.range_max_m")
    area = scene.get("area_clutter")
    if not isinstance(area, Mapping) or area.get("enabled") is not True or area.get("model") != "continuous_texture":
        raise ValueError("R22 target generation requires enabled continuous_texture area clutter")
    if range_min < scene_range_min or range_max > scene_range_max:
        raise ValueError("target range window must be contained in the generated scene range")
    range_origin_m = 0.5 * C_MPS * (sample_delay_s + crop_start / fs_hz)
    range_bin_spacing_m = 0.5 * C_MPS / fs_hz
    first_range_bin = max(0, math.ceil((range_min - range_origin_m) / range_bin_spacing_m))
    last_range_bin = min(crop_len - 1, math.floor((range_max - range_origin_m) / range_bin_spacing_m))
    available_bins = last_range_bin - first_range_bin + 1
    if available_bins < target_count:
        raise ValueError("clutter-overlap range window has fewer unique bins than targets")

    geometry = scenario.get("simulation_geometry", {})
    if not isinstance(geometry, Mapping):
        raise ValueError("simulation_geometry must be an object")
    local_x_axis = str(geometry.get("local_x_axis", "north"))
    local_y_axis = str(geometry.get("local_y_axis", "east"))
    x_e, x_n = _axis_vector(local_x_axis)
    _axis_vector(local_y_axis)  # Validate the second axis even though platform speed uses local x.
    platform_speed = _finite(platform.get("speed_mps"), "platform.speed_mps")
    platform_ve, platform_vn = platform_speed * x_e, platform_speed * x_n
    scan_min = _finite(scan.get("scan_min_deg"), "scan.scan_min_deg")
    scan_step = _finite(scan.get("scan_step_deg"), "scan.scan_step_deg")
    beam_width = _finite(scan.get("beam_width_deg"), "scan.beam_width_deg")
    if platform_speed <= 0.0 or scan_step <= 0.0 or beam_width <= 0.0:
        raise ValueError("platform speed, scan step and beam width must be positive")
    squint_side = int(geometry.get("squint_side", platform.get("squint_side", 1)))
    if squint_side not in (0, 1):
        raise ValueError("squint_side must be 0 or 1")
    heading_source = str(geometry.get("platform_heading_source", "velocity"))
    fixed_heading = _finite(geometry.get("platform_heading_deg", 0.0), "platform_heading_deg")
    theta_offset = _finite(geometry.get("beam_theta_offset_deg", 0.0), "beam_theta_offset_deg")

    targets: list[dict[str, Any]] = []
    substreams: list[dict[str, Any]] = []
    looks: dict[str, dict[str, float]] = {}
    range_bin_order = list(range(first_range_bin, last_range_bin + 1))
    range_stream_seed = _range_permutation_stream_seed(seed)
    random.Random(range_stream_seed).shuffle(range_bin_order)
    for index in range(target_count):
        target_id = f"R22_T{index + 1:03d}"
        stream_seed = _target_stream_seed(seed, index)
        rng = random.Random(stream_seed)
        beam_id = first_beam + (index % beam_count)
        # Assign bins from a seed-specific permutation whose prefix is stable
        # across target counts. This keeps the 3/20/40-target scenes nested.
        range_bin = range_bin_order[index]
        azimuth_offset = rng.uniform(-min(0.5, beam_width * 0.25), min(0.5, beam_width * 0.25))
        theta = scan_min + (beam_id - beam_index_base) * scan_step
        look_e, look_n = _look_vector(
            platform_ve=platform_ve,
            platform_vn=platform_vn,
            theta_deg=theta,
            theta_offset_deg=theta_offset + azimuth_offset,
            squint_side=squint_side,
            heading_source=heading_source,
            fixed_heading_deg=fixed_heading,
        )
        tangential_speed = rng.uniform(-0.5, 0.5)
        ve = radial_speed * look_e - tangential_speed * look_n
        vn = radial_speed * look_n + tangential_speed * look_e
        targets.append({
            "target_id": target_id,
            "enabled": True,
            "init": {
                "type": "beam_bin_azimuth_offset",
                "beam_id": beam_id,
                "expected_bin": range_bin,
                "azimuth_offset_deg": azimuth_offset,
            },
            "motion": {"type": "enu_velocity", "ve_mps": ve, "vn_mps": vn},
            "amplitude": {"type": "snr_db", "snr_db": snr_db},
            "visibility": {"type": "gaussian"},
        })
        looks[target_id] = {"east": look_e, "north": look_n}
        substreams.append({
            "target_id": target_id,
            "stream_seed": stream_seed,
            "range_bin_rank": index,
            "range_bin_permutation_stream_seed": range_stream_seed,
            "expected_bin": range_bin,
        })

    random_out = scenario["random"]
    assert isinstance(random_out, dict)
    random_out.update({
        "random_seed": seed,
        "period_start": int(random_out.get("period_start", 0)),
        "period_count": period_count,
        "beam_start": first_beam,
        "beam_count": beam_count,
    })
    scenario["targets"] = targets
    scenario["r22_target_state_generation"] = {
        "generator": "scripts/phase_r/r22_scenario_factory.py",
        "generator_version": 2,
        "seed_namespace": "independent per-target streams; not reused by Stage2 clutter or packet-noise RNG",
        "target_count": target_count,
        "radial_speed_mps": radial_speed,
        "target_snr_db": snr_db,
        "period_count": period_count,
        "first_beam": first_beam,
        "beam_count": beam_count,
        "range_window_m": [range_min, range_max],
        "range_origin_m": range_origin_m,
        "range_bin_spacing_m": range_bin_spacing_m,
        "range_bin_bounds": [first_range_bin, last_range_bin],
        "target_state_substreams": substreams,
    }
    manifest = {
        "schema_version": 1,
        "seed": seed,
        "stage2_clutter_noise_seed": seed,
        "target_state_stream_rule": "(seed << 32) | (0x52320000 + zero_based_target_index)",
        "target_state_substream_count": target_count,
        "target_state_substreams": substreams,
        "range_bin_permutation_stream_seed": range_stream_seed,
        "range_bin_assignment_rule": "seed-specific shuffled range-bin permutation; target-count-stable prefix",
        "look_vectors_by_target": looks,
        "configured_radial_speed_mps": radial_speed,
        "target_snr_db": snr_db,
        "period_count": period_count,
        "beam_span": [first_beam, last_beam],
        "range_window_m": [range_min, range_max],
        "range_bin_bounds": [first_range_bin, last_range_bin],
    }
    return scenario, manifest
