import copy
import math

from scripts.phase_r.r22_scenario_factory import build_seed_scenario
from scripts.run_track_manager_e2e import _protocol_layout_from_scenario


def _scenario():
    return {
        "case_id": "r22-test",
        "output_dir": "unused",
        "waveform": {
            "fc_ghz": 16.0,
            "fs_mhz": 60.0,
            "pulse_len": 11820,
            "pulse_num": 130,
            "new_protocol_channel_count": 4,
            "new_protocol_read_channel_1": 1,
            "new_protocol_read_channel_2": 2,
            "iq_data_type": "float32",
        },
        "range_processing": {
            "range_crop_start": 3864,
            "range_crop_len": 4096,
            "sample_delay_us": 488.0,
        },
        "scan": {
            "scan_min_deg": -60.0,
            "scan_step_deg": 2.0,
            "beam_count": 61,
            "beam_width_deg": 2.28,
            "beam_index_base": 1,
        },
        "platform": {"speed_mps": 60.0, "height_m": 6000.0, "squint_side": 1},
        "simulation_geometry": {
            "local_x_axis": "north",
            "local_y_axis": "east",
            "platform_heading_source": "velocity",
            "platform_heading_deg": 0.0,
            "beam_theta_offset_deg": 0.0,
        },
        "random": {"random_seed": 1001, "period_start": 0, "period_count": 1,
                   "beam_start": 21, "beam_count": 20},
        "scene": {
            "range_min_m": 82800.0,
            "range_max_m": 93000.0,
            "ground_z_m": 0.0,
            "area_clutter": {"enabled": True, "model": "continuous_texture"},
        },
        "targets": [],
    }


def test_seed_scenario_has_distinct_target_states_and_exact_radial_speed():
    source = _scenario()
    original = copy.deepcopy(source)
    result, manifest = build_seed_scenario(
        source,
        seed=1001,
        target_count=40,
        radial_speed_mps=0.5,
        target_snr_db=20.0,
        period_count=10,
        first_beam=21,
        beam_count=20,
        clutter_range_window_m=(84500.0, 85500.0),
    )

    assert source == original
    assert len(result["targets"]) == 40
    assert len({target["target_id"] for target in result["targets"]}) == 40
    assert result["random"]["random_seed"] == 1001
    assert result["random"]["period_count"] == 10
    assert result["random"]["beam_start"] == 21
    assert result["random"]["beam_count"] == 20
    assert manifest["target_state_substream_count"] == 40

    beam_counts = {}
    states = set()
    for target in result["targets"]:
        init = target["init"]
        beam = init["beam_id"]
        beam_counts[beam] = beam_counts.get(beam, 0) + 1
        assert 21 <= beam <= 40
        assert 680 <= init["expected_bin"] <= 1080
        assert target["amplitude"]["snr_db"] == 20.0
        velocity = target["motion"]
        look = manifest["look_vectors_by_target"][target["target_id"]]
        radial = velocity["ve_mps"] * look["east"] + velocity["vn_mps"] * look["north"]
        assert math.isclose(radial, 0.5, rel_tol=0.0, abs_tol=1e-12)
        states.add((beam, init["expected_bin"], init["azimuth_offset_deg"],
                    velocity["ve_mps"], velocity["vn_mps"]))
    assert set(beam_counts.values()) == {2}
    assert len(states) == 40


def test_same_seed_reproduces_targets_and_new_seed_changes_only_target_substream():
    source = _scenario()
    first, first_manifest = build_seed_scenario(
        source, seed=101, target_count=20, radial_speed_mps=2.5,
        target_snr_db=30.0, period_count=5, first_beam=21, beam_count=20,
        clutter_range_window_m=(84500.0, 85500.0),
    )
    replay, replay_manifest = build_seed_scenario(
        source, seed=101, target_count=20, radial_speed_mps=2.5,
        target_snr_db=30.0, period_count=5, first_beam=21, beam_count=20,
        clutter_range_window_m=(84500.0, 85500.0),
    )
    second, second_manifest = build_seed_scenario(
        source, seed=102, target_count=20, radial_speed_mps=2.5,
        target_snr_db=30.0, period_count=5, first_beam=21, beam_count=20,
        clutter_range_window_m=(84500.0, 85500.0),
    )

    assert first["targets"] == replay["targets"]
    assert first["random"]["random_seed"] == replay["random"]["random_seed"]
    assert first_manifest["target_state_substreams"] == replay_manifest["target_state_substreams"]
    assert first["targets"] != second["targets"]
    assert first["random"]["random_seed"] != second["random"]["random_seed"]
    assert first_manifest["target_state_substreams"] != second_manifest["target_state_substreams"]


def test_target_truth_is_nested_across_density_levels():
    source = _scenario()
    common = {
        "seed": 5501,
        "radial_speed_mps": 2.5,
        "target_snr_db": 20.0,
        "period_count": 5,
        "first_beam": 21,
        "beam_count": 1,
        "clutter_range_window_m": (84500.0, 85500.0),
    }
    three, _ = build_seed_scenario(source, target_count=3, **common)
    twenty, _ = build_seed_scenario(source, target_count=20, **common)
    forty, _ = build_seed_scenario(source, target_count=40, **common)

    assert three["targets"] == twenty["targets"][:3]
    assert twenty["targets"] == forty["targets"][:20]


def test_seed_scenario_rejects_nonphysical_range_window_and_scan_span():
    source = _scenario()
    try:
        build_seed_scenario(
            source, seed=101, target_count=20, radial_speed_mps=0.5,
            target_snr_db=20.0, period_count=5, first_beam=55, beam_count=20,
            clutter_range_window_m=(84500.0, 85500.0),
        )
    except ValueError as exc:
        assert "beam span" in str(exc)
    else:
        raise AssertionError("out-of-range beam span must fail closed")


def test_production_layout_carries_the_complete_multibeam_scan_span():
    scenario, _ = build_seed_scenario(
        _scenario(), seed=101, target_count=20, radial_speed_mps=0.5,
        target_snr_db=20.0, period_count=5, first_beam=21, beam_count=20,
        clutter_range_window_m=(84500.0, 85500.0),
    )

    layout = _protocol_layout_from_scenario(scenario)

    assert layout["beam_id"] == 21
    assert layout["scan_beam_count"] == 20
    assert layout["last_beam_id"] == 40


def test_hierarchical_runner_accepts_unmodified_r22_scene_identity_override():
    from scripts import run_production_hierarchical_calibration_pilot as runner

    identity = {"seed": 7781, "period_count": 5, "delay_error_ns": 4.0, "case_id": "r22-test"}
    case = {"seed": 7781, "scene_identity_override": identity}

    assert runner._scene_identity({}, case, 5) == identity


def test_hierarchical_runner_rejects_scene_identity_override_seed_mismatch():
    from scripts import run_production_hierarchical_calibration_pilot as runner

    case = {"seed": 7781, "scene_identity_override": {"seed": 7782, "period_count": 5}}

    try:
        runner._scene_identity({}, case, 5)
    except ValueError as exc:
        assert "seed does not match" in str(exc)
    else:
        raise AssertionError("scene identity override must not change the case seed")
