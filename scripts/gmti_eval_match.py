#!/usr/bin/env python3
"""Truth matching utilities for GMTI detection and track evaluation."""

import math

from gmti_eval_io import to_float, to_int, hypot2


def velocity_truth(row):
    # Detection output reports radial velocity, so compare against the truth
    # radial component when it is available.  Falling back to |(ve,vn)| would
    # mix two different physical quantities and can hide/significantly distort
    # the actual velocity error.
    for key in ("vr_self_mps", "target_vr_self_mps", "v_truth_mps"):
        vr = to_float(row, key)
        if math.isfinite(vr):
            return abs(vr)
    ve = to_float(row, "ve_mps")
    vn = to_float(row, "vn_mps")
    if math.isfinite(ve) and math.isfinite(vn):
        return math.hypot(ve, vn)
    vr = to_float(row, "v_truth_mps")
    return abs(vr) if math.isfinite(vr) else math.nan


def velocity_detection(row):
    for key in ("v_radial_mps", "radial_velocity_mps", "speed"):
        v = to_float(row, key)
        if math.isfinite(v):
            return abs(v)
    return math.nan


def period(row):
    return to_int(row, "period_id", 0)


def beam(row):
    return to_int(row, "beam_id", -999999)


def utc(row):
    for key in ("utc", "utc_mid", "utc_center", "center_utc"):
        v = to_float(row, key)
        if math.isfinite(v):
            return v
    return math.nan


def scan(row):
    return to_int(row, "scan_id", -999999)


def is_mechanical_row(row):
    mode = str(row.get("scan_mode", "")).strip().lower()
    return mode == "mechanical" or "scan_id" in row or "window_id" in row


def mechanical_angle_preference(item, truth):
    """Tie-break overlapping CPIs by physical beam-centre distance, then power."""
    target_az = to_float(truth, "target_azimuth_deg")
    center_az = math.nan
    for key in ("reference_azimuth_deg", "cpi_az_center_deg", "az_center_deg"):
        center_az = to_float(item, key)
        if math.isfinite(center_az):
            break
    if math.isfinite(target_az) and math.isfinite(center_az):
        delta = (target_az - center_az + 180.0) % 360.0 - 180.0
        return abs(delta)
    for key in ("amplitude", "peak_power", "power", "cfar_power"):
        power = to_float(item, key)
        if math.isfinite(power):
            return -1.0e-9 * power
    return 0.0


def range_m(row):
    return to_float(row, "range_m")


def item_position(row):
    # Never use target_*_truth as an item prediction: detection_results.csv
    # intentionally carries those columns for audit, and using them here makes
    # every matched detection appear to have exactly zero position error.
    for e_key, n_key in (
        ("new_e", "new_n"),
        ("final_output_e", "final_output_n"),
        ("e", "n"),
    ):
        e = to_float(row, e_key)
        n = to_float(row, n_key)
        if math.isfinite(e) and math.isfinite(n):
            return e, n
    return math.nan, math.nan


def truth_position(row):
    for e_key, n_key in (
        ("e_mid", "n_mid"),
        ("target_e_truth", "target_n_truth"),
        ("e", "n"),
    ):
        e = to_float(row, e_key)
        n = to_float(row, n_key)
        if math.isfinite(e) and math.isfinite(n):
            return e, n
    return math.nan, math.nan


def truth_id(row, idx):
    return row.get("target_id") or f"truth_{idx}"


def _first_finite(row, keys):
    for key in keys:
        value = to_float(row, key)
        if math.isfinite(value):
            return value
    return math.nan


def azimuth_error_deg(item, truth):
    """Return the absolute interferometric azimuth error in degrees.

    Production detections serialize the selected angle as ``theta_used_deg``;
    Stage2 truth serializes the physical target bearing as ``azimuth_deg``.
    The interferometric solution is 180-degree periodic, so e.g. 139.5 deg
    and -40.5 deg denote the same line of bearing.
    """
    estimated = _first_finite(
        item, ("theta_used_deg", "azimuth_estimate_deg", "theta_estimate_deg"))
    expected = _first_finite(
        truth, ("azimuth_deg", "target_azimuth_deg", "theta_true_deg"))
    if not math.isfinite(estimated) or not math.isfinite(expected):
        return math.nan
    delta = (estimated - expected + 90.0) % 180.0 - 90.0
    return abs(delta)


def candidate_errors(item, truth):
    ie, in_ = item_position(item)
    te, tn = truth_position(truth)
    pos_err = hypot2(ie - te, in_ - tn)
    rng_err = abs(range_m(item) - range_m(truth))
    time_err = abs(utc(item) - utc(truth))
    vel_item = velocity_detection(item)
    vel_truth = velocity_truth(truth)
    vel_err = abs(vel_item - vel_truth) if math.isfinite(vel_item) and math.isfinite(vel_truth) else math.nan
    return {
        "position_error_m": pos_err,
        "range_error_m": rng_err,
        "azimuth_error_deg": azimuth_error_deg(item, truth),
        "time_error_s": time_err,
        "velocity_error_mps": vel_err,
    }


def gate_pass(item, truth, cfg, use_beam=True, use_range=True,
              use_velocity=True, mechanical=False):
    if mechanical:
        if scan(item) != scan(truth):
            return False, "scan"
    elif abs(period(item) - period(truth)) > int(cfg["max_period_diff"]):
        return False, "period"
    if use_beam and abs(beam(item) - beam(truth)) > int(cfg["max_beam_diff"]):
        return False, "beam"
    errs = candidate_errors(item, truth)
    if math.isfinite(errs["time_error_s"]) and errs["time_error_s"] > float(cfg["max_time_diff_s"]):
        return False, "time"
    if use_range and math.isfinite(errs["range_error_m"]) and errs["range_error_m"] > float(cfg["max_range_error_m"]):
        return False, "range"
    if math.isfinite(errs["position_error_m"]) and errs["position_error_m"] > float(cfg["max_position_error_m"]):
        return False, "position"
    if use_velocity and math.isfinite(errs["velocity_error_mps"]) and errs["velocity_error_mps"] > float(cfg["max_velocity_error_mps"]):
        return False, "velocity"
    return True, "pass"


def match_cost(item, truth, cfg, use_range=True, use_velocity=True,
               mechanical=False):
    errs = candidate_errors(item, truth)
    terms = []
    if math.isfinite(errs["position_error_m"]):
        terms.append(errs["position_error_m"] / max(1e-9, float(cfg["max_position_error_m"])))
    if use_range and math.isfinite(errs["range_error_m"]):
        terms.append(errs["range_error_m"] / max(1e-9, float(cfg["max_range_error_m"])))
    if use_velocity and math.isfinite(errs["velocity_error_mps"]):
        terms.append(errs["velocity_error_mps"] / max(1e-9, float(cfg["max_velocity_error_mps"])))
    if math.isfinite(errs["time_error_s"]):
        terms.append(errs["time_error_s"] / max(1e-9, float(cfg["max_time_diff_s"])))
    if mechanical:
        # Keep this a tie-break term: physical localization/time/range remain
        # dominant, while overlapping CPI observations prefer the one whose
        # actual centre angle is closest to the target.
        terms.append(1.0e-6 * mechanical_angle_preference(item, truth))
    return sum(terms) if terms else 1e9


def min_cost_assignment(cost):
    """Return an exact row->column rectangular Hungarian assignment.

    The earlier bit-mask dynamic-programming implementation was only practical
    for very small scenes and pruned the detection set to 24 columns.  That
    silently made 32/64-target evaluation incapable of assigning every truth.
    This shortest-augmenting-path form is O(min(n,m)^2 * max(n,m)), has no
    target-count cut-off, and keeps the evaluator dependency-free.

    Invalid/gated pairs use a large finite cost.  They can be selected while
    completing the rectangular assignment but are filtered by
    ``one_to_one_match`` afterwards.
    """
    n = len(cost)
    m = len(cost[0]) if n else 0
    if n == 0 or m == 0:
        return {}
    if n > m:
        transposed = [[cost[r][c] for r in range(n)] for c in range(m)]
        col_to_row = min_cost_assignment(transposed)
        return {r: c for c, r in col_to_row.items()}

    # 1-based arrays follow the standard Hungarian shortest augmenting path
    # formulation.  Here n <= m, so every row receives a unique column.
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        minv = [math.inf] * (m + 1)
        used = [False] * (m + 1)
        j0 = 0
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = math.inf
            j1 = 0
            for j in range(1, m + 1):
                if used[j]:
                    continue
                raw = cost[i0 - 1][j - 1]
                cij = raw if math.isfinite(raw) else 1e12
                cur = cij - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            if not math.isfinite(delta):
                # All remaining entries are invalid infinities.  Using the
                # same finite sentinel preserves a complete deterministic
                # assignment; the caller will discard those pairs.
                delta = 1e12
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                elif j:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break

    assignment = {}
    for j in range(1, m + 1):
        if p[j] != 0:
            assignment[p[j] - 1] = j - 1
    return assignment


def one_to_one_match(items, truths, cfg, item_id_key, use_beam=True,
                     use_range=True, use_velocity=True, scan_mode="auto"):
    mechanical = scan_mode == "mechanical" or (
        scan_mode == "auto" and
        (any(is_mechanical_row(row) for row in items) or
         any(is_mechanical_row(row) for row in truths)))
    if mechanical:
        use_beam = False
    candidates = []
    for ti, truth in enumerate(truths):
        row = []
        for ii, item in enumerate(items):
            ok, reason = gate_pass(item, truth, cfg, use_beam, use_range,
                                   use_velocity, mechanical)
            row.append(match_cost(item, truth, cfg, use_range, use_velocity,
                                  mechanical) if ok else 1e9)
        candidates.append(row)
    assignment = min_cost_assignment(candidates)
    matches = []
    used_items = set()
    used_truth = set()
    for ti, ii in assignment.items():
        if ti >= len(truths) or ii >= len(items):
            continue
        cost = candidates[ti][ii]
        if not math.isfinite(cost) or cost >= 1e8:
            continue
        errs = candidate_errors(items[ii], truths[ti])
        matches.append({
            "truth_index": ti,
            "truth_id": truth_id(truths[ti], ti),
            "item_index": ii,
            "item_id": items[ii].get(item_id_key, ii),
            "cost": cost,
            **errs,
        })
        used_items.add(ii)
        used_truth.add(ti)
    return matches, used_items, used_truth
