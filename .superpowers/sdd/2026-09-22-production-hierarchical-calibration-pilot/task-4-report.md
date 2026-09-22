# Task 4 report — production hierarchical calibration delay pilot runner

## Scope and implementation

Task 4 only. The new runner and analyzer orchestrate the existing Stage2
scenario generator, `estimate_channel_delay` fractional-delay rewrite, the
existing production `GMTI_pipe_core`/TrackManager path, and existing debug
audits. No replacement tracker, CUDA implementation, C++ change, Task 5
report, or Task 6 review was added.

Added files:

- `configs/research/production_hierarchical_calibration_delay_pilot.json`
- `scripts/run_production_hierarchical_calibration_pilot.py`
- `scripts/analyze_production_hierarchical_calibration_pilot.py`
- `tests/test_production_hierarchical_calibration_pilot.py`

The frozen branch contract is `C0/C1/C2/C3/C4/P1/P2/PK/PKR`. C0 leaves the
research flag off; C1–C4 select only their corresponding research method; P1
and P2 use target-free OFF phase-vs-fast-frequency delay estimation followed
by raw channel-2 fractional correction; PK/PKR use known delay only as an
evaluator upper bound; P2/PKR select robust DDC-RB after correction. All
branches retain the same GO-CFAR, clustering, localization, TrackManager and
PIPE contract. The input contract records `OFF=C+N`, `ON=S+C+N`, `TO=S`,
shared scene identity, Mode-A OFF-only estimation, and truth-blind Mode-B.

Missing support, valid-CUT denominators, causal triplets, or blind estimates
are represented as `NOT_EVALUABLE`/evidence gaps. They never fall through to
Current. Empirical cell Pfa is defined only as
`hit_cut_count / valid_cut_count` with a positive denominator. Compact
evidence is restricted to the six-file allow-list:
`manifest.json`, `method_contract.csv`, `gamma_recovery.csv`,
`clutter_metrics.csv`, `decision_matrix.csv`, and `report.md`.

## TDD and verification evidence

The initial contract test was run red before the new runner/analyzer existed:

```text
PYTHONPATH=. pytest -q tests/test_production_hierarchical_calibration_pilot.py
```

Result: exit code `1`; `8 failed`, all due to the expected missing new
modules.

The final focused/regression command was:

```text
PYTHONPATH=. pytest -q tests/test_production_hierarchical_calibration_pilot.py tests/test_delay_stage1_contract.py tests/test_delay_stage1_provenance.py tests/test_channel_delay_correction.py tests/test_production_calibration_tap.py
```

Result: exit code `0`; `42 passed in 16.83s`.

Python syntax and JSON validation were run with:

```text
python3 -m py_compile scripts/run_production_hierarchical_calibration_pilot.py scripts/analyze_production_hierarchical_calibration_pilot.py && python3 -m json.tool configs/research/production_hierarchical_calibration_delay_pilot.json >/dev/null
```

Result: exit code `0`.

The contract/skip runner was executed without `PYTHONPATH` using a fresh
temporary output root:

```text
python3 scripts/run_production_hierarchical_calibration_pilot.py --mode pilot --output-root <fresh-temporary-root> --contract
```

Result: exit code `0`. The manifest recorded `status=skip_cuda`,
`algorithmic_results_claimed=false`, and decision
`NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE`; it emitted exactly the six
compact evidence files. Skip/contract mode does not run the simulator or
`GMTI_pipe_core` and must not be interpreted as an algorithm result.

## GPU limitation and pending controller action

The sandbox probe was:

```text
nvidia-smi
```

Result: exit code `9` with
`NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver`.
No real CUDA production pilot was claimed or run in this task. The controller
must later run the targeted pilot on a real CUDA device, preserving an
independent output root and recording the generated manifest/logs, for example:

```text
python3 scripts/run_production_hierarchical_calibration_pilot.py --config configs/research/production_hierarchical_calibration_delay_pilot.json --mode pilot --input-mode local --output-root <independent-gpu-output-root>
```

The first pilot selection is the targeted `0,+2,-2 ns` set with one seed,
velocity, and SNR; it is not a Cartesian full matrix. The formal selection
declares representative `+/-4 ns`, at least three seeds, and two velocities,
with `+/-8 ns` available as an optional configured delay.

## Commit boundary

Only the four Task 4 files above and this report are in scope. Existing
`.scratch/` and `outputs/` content is preserved and must not be staged.
