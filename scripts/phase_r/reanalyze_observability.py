#!/usr/bin/env python3
"""Replay saved Phase-I observables without running a simulator or loading IQ."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
from pathlib import Path
import shutil
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_two_channel_error_observability import (
    CIRCULAR_OBSERVABLE_NAMES,
    _json_safe,
    central_difference_sensitivity,
    classify_observability,
    summarize_zero_control_delta,
)

FFT_ARGMAX_OBSERVABLES = frozenset({
    "clutter_doppler_ridge_hz", "clutter_ridge_vs_angle_slope_hz_per_deg",
})


def reanalyze(manifest: dict) -> dict:
    states = list(manifest["parameters"])
    seeds = list(manifest["seeds"])
    names = list(manifest["observable_names"])
    for label, values in (("parameters", states), ("seeds", seeds), ("observables", names)):
        if not values or len(set(values)) != len(values):
            raise ValueError(f"{label} must be nonempty and unique")
    steps = {state: float(manifest["perturbations"][state]) for state in states}
    if any(not math.isfinite(h) or h <= 0 for h in steps.values()):
        raise ValueError("perturbations must be finite and positive")
    expected = set(itertools.product(states, seeds, ("minus", "zero", "plus")))
    cases = {}
    for record in manifest["cases"]:
        key = (record["parameter"], record["seed"], record["sign"])
        if key not in expected or key in cases:
            raise ValueError(f"unexpected or duplicate case: {key}")
        state, _, sign = key
        want = {"minus": -steps[state], "zero": 0.0, "plus": steps[state]}[sign]
        if record["status"] != "passed" or not math.isclose(
            float(record["value"]), want, rel_tol=1e-12, abs_tol=1e-12,
        ):
            raise ValueError(f"failed or mispaired case: {key}")
        obs = {name: float(record["observables"][name]) for name in names}
        if not all(math.isfinite(v) for v in obs.values()):
            raise ValueError(f"non-finite observables: {key}")
        cases[key] = obs
    if cases.keys() != expected:
        raise ValueError(f"missing cases: {sorted(expected - cases.keys())}")

    legacy, corrected = [], []
    crossings, fft_changes = [], []
    for seed in seeds:
        old = np.empty((len(names), len(states)))
        new = np.empty_like(old)
        for column, state in enumerate(states):
            plus, minus = cases[state, seed, "plus"], cases[state, seed, "minus"]
            delta = central_difference_sensitivity(plus, minus, perturbation=steps[state])
            for row, name in enumerate(names):
                raw_difference = plus[name] - minus[name]
                old[row, column] = raw_difference / (2 * steps[state])
                new[row, column] = delta[name]
                change = {"state": state, "seed": seed, "observable": name,
                          "plus": plus[name], "minus": minus[name],
                          "difference": raw_difference,
                          "legacy_sensitivity": old[row, column],
                          "circular_sensitivity": new[row, column]}
                if name in CIRCULAR_OBSERVABLE_NAMES and abs(raw_difference) > math.pi:
                    crossings.append(change)
                if name in FFT_ARGMAX_OBSERVABLES and raw_difference != 0.0:
                    fft_changes.append(change)
        legacy.append(old)
        corrected.append(new)

    non_fft = [i for i, name in enumerate(names) if name not in FFT_ARGMAX_OBSERVABLES]
    if not non_fft:
        raise ValueError("no non-FFT-argmax observables remain")
    filtered_names = [names[i] for i in non_fft]
    filtered = [matrix[non_fft, :] for matrix in corrected]
    step_vector = np.asarray([steps[state] for state in states])
    variants = {
        "legacy_linear_all": (legacy, names),
        "circular_all": (corrected, names),
        "circular_without_fft_argmax": (filtered, filtered_names),
        "circular_step_scaled_without_fft_argmax": (
            [matrix * step_vector[None, :] for matrix in filtered], filtered_names,
        ),
    }
    analyses = {}
    for variant, (matrices, selected_names) in variants.items():
        scopes = {f"seed_{seed}": matrix for seed, matrix in zip(seeds, matrices)}
        scopes["seed_median"] = np.median(np.stack(matrices), axis=0)
        analyses[variant] = {}
        for scope, matrix in scopes.items():
            report = classify_observability(
                matrix, state_names=states, observable_names=selected_names,
            )
            # Keep quantitative diagnostics, not candidate/separable labels
            # that could be misread as an operational identifiability proof.
            analyses[variant][scope] = {
                key: report[key] for key in (
                    "state_names", "observable_names", "raw_matrix", "scaled_matrix",
                    "observable_row_scales", "classification_column_scales",
                    "scaled_rank", "condition_number", "column_cosine",
                )
            }
            analyses[variant][scope]["singular_values"] = np.linalg.svd(
                report["scaled_matrix"], compute_uv=False,
            ).tolist()
    zero_cases = {
        (state, seed): [cases[state, seed, "zero"][name] for name in names]
        for state in states for seed in seeds
    }
    return {
        "case_count": len(cases), "states": states, "seeds": seeds,
        "perturbations": steps, "phase_wrap_crossings": crossings,
        "fft_argmax_changes": fft_changes,
        "aggregation": "elementwise median of per-seed finite differences, before balancing",
        "normalization": "observable-row L2 norm, then state-column L2 norm; no noise whitening",
        "step_scaled": "multiply native-unit derivative columns by their declared perturbation h before balancing",
        "zero_control": summarize_zero_control_delta(
            zero_cases, observable_names=names, reference_parameter=states[0],
        ),
        "analyses": analyses,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        source = args.manifest.read_bytes()
        result = reanalyze(json.loads(source))
        result["source_manifest_sha256"] = hashlib.sha256(source).hexdigest()
        result["created_at_utc"] = datetime.now(timezone.utc).isoformat()
        result["analysis_environment"] = {"python": sys.version.split()[0], "numpy": np.__version__}
        result["analysis_code_sha256"] = {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (Path(__file__).resolve(), ROOT / "scripts/audit_two_channel_error_observability.py")
        }
        args.output_dir.mkdir(parents=True, exist_ok=False)
    except (KeyError, TypeError, ValueError, OSError) as error:
        parser.error(str(error))
    shutil.copyfile(args.manifest, args.output_dir / "source_manifest.json")
    (args.output_dir / "summary.json").write_text(
        json.dumps(_json_safe(result), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"Reanalyzed {result['case_count']} saved cases into {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
