# R7 GO-CFAR compact evidence

This compact package contains the auditable R7 pre/post run manifests, the actual runtime XMLs used for A0/C processing, branch-specific numeric analyses, historical R6 branch-map reanalysis, and the small R6 Production power-map fixture required by the CUDA regression. It does not include large Stage2 IQ inputs, full CFAR/CSI map sets, plots, or per-run logs. Those remain in the local `outputs/phase_r7_cfar_correctness_20261022/` tree and are identified by path/SHA in the manifests.

## Contents

- `manifests/pre_fix_experiment_manifest.json`, `manifests/post_fix_experiment_manifest.json`: binary SHA, input/config SHA, run paths, status, and device record for paired replay. The `post_fix_experiment_manifest.json` copy is authoritative after provenance correction; `post_fix_experiment_manifest_original_unreconciled.json` preserves the original generated manifest for audit.
- `runtime_configs/`: exact runtime XML files, copied byte-for-byte from the R6 Production config snapshots used by R7. B uses the A0 Current runtime config for its matched mode; only its input carries +4 ns error.
- `analysis/r7_pair/`: nine A0/B/C × OFF/ON/TO pre/post numeric comparisons, CSV and full JSON.
- `analysis/r6_history/`: all 36 saved historical R6 branch-period comparisons, CSV and full JSON.
- `csi_metrics/`: the production `csi_metric_tap_summary.csv` from each repaired A0/B/C × OFF/ON/TO run; files retain the original rows and run ID.
- `fixtures/`: a 130×4096 float32 power map plus its actual geometry CSV from the pre-fix R6 seed101 A0_ON dynamic branch. The CUDA selftest uses these to recheck the saved Production case on a clean checkout.
- `metadata_smoke/`: GO branch metadata, geometry CSV, and manifest from one post-matrix A0_ON CUDA smoke using the final diagnostic writer. This verifies metadata serialization only; it is not part of the nine-run numerical matrix or a performance measurement.
- `manifests/post_fix_matrix_3period_final.json` and `manifests/pre_fix_B_matrix_3period.json`: full three-period CUDA execution manifests for the authoritative task-level matrix and legacy B replay.
- `task_metrics_3period/`: the frozen evaluator manifest, summary CSV, and per-case pre/post metrics, one-to-one target association records, and cluster lineage for all 9 A0/B/C × OFF/ON/TO pairs. The package is 2.8 MB and contains no raw IQ or full CFAR/CSI maps.
- `production_runs_3period/`: selected actual runtime XML/logs, per-run `run_manifest` and runtime config dump, timing CSVs, per-period CFAR geometry/numerical/branch-axis records, post-filter cluster lineage, CSI summary/tap sidecars, per-period detections, and TrackManager CSVs. It covers the 9 task pairs, A0 repeat, diagnostics-off control, and pre-fix A0/B/C records. The package is 14 MB; full Stage2 IQ, complete CSI NPY arrays, and CFAR power/threshold maps remain local and are not copied into Git.

Regression fixture SHA-256:

| File | Size | SHA-256 |
|---|---:|---|
| `r6_seed101_a0_on_dynamic_power.f32` | 2,129,920 bytes | `63a48c583e89b59e87571aa6b28a2c2c7aed68e2b635f4805703b0e0533ec531` |
| `r6_seed101_a0_on_dynamic_geometry.csv` | 762 bytes | `2ac1c9231b2aeec2783bd98aaf95c7ddff8a9078d4f4c4b340f57f0d1f070cfe` |
- The five R7 interpretation/implementation reports sit one directory above this package.

## Runtime XML identity

| File | SHA-256 |
|---|---|
| `A0_OFF.xml` | `95c224c7473a756dfc26e0b2ebd1fe30b554a75d20cbfce0f191c74002514e5d` |
| `A0_ON.xml` | `352a69c22edc3f073d1686ad2684a1da3a5c18dbac3af3d22c23219479fef558` |
| `A0_TO.xml` | `d92294399dfd6b4b3b475b00168ac217e6a32c7550491551c32c930b825e0bef` |
| `C_OFF.xml` | `d0ca7ea5da790be9ee632c0fd2a8a2c896c8c3e94426d1630d94bd89332b3456` |
| `C_ON.xml` | `122e1285f4258330648dee321779a763e054a80da175ca7cca95f945acc8c841` |
| `C_TO.xml` | `c5f1be7058433f6e1f09d9b98f7b441592f166469d6e9b95b8ec6d35461f312e` |

CSI summary CSV hashes:

| File | SHA-256 |
|---|---|
| `A0_OFF.csv` | `f21429002c031e9ccb35bfffc3d3d965750a13545eca640a426ff2e3f6bbbd30` |
| `A0_ON.csv` | `61150e985f1f16abcfb506d5c64aea646ea956dbd3065fde61985d9e1b670c0b` |
| `A0_TO.csv` | `2fbda5595c030f547c9d06bbf8112ca7508e484a2c10ad7f8f1d484a33ee150e` |
| `B_OFF.csv` | `94aeae1ce1c499303b03a0de04e8857aaef02a4fee1f3c9d4deb96463e07d0d6` |
| `B_ON.csv` | `57a28e636330b293637e67f66b1792dea33e5e6ab989b8be418c7516ad14b434` |
| `B_TO.csv` | `4bbb5d213b83efe16f3466a3194c1e7f71f7606c312283e88812a4c2750a666c` |
| `C_OFF.csv` | `8939bcca0ba1f1a443af6aa210d58878b4a3aed8576b7801aa75c8d0845d68f2` |
| `C_ON.csv` | `faf24add3c7058661c45d245437bc407e13fdc7ebabff1f441d1f98d4ed8878e` |
| `C_TO.csv` | `ca1ccad8f1ab886baab36f2de8f9564c7e599e223b4123e1f4485ba877a00326` |

All nine early one-period pre/post input SHA pairs and runtime XML SHA pairs match. The old executable SHA is `f65a8aa446754cc79b6417f9275b3a01e73eb85098c6a4f9eaea0cb999c38962`; the one-period numerical matrix executable SHA is `3f58a55330b4afec75e595ce3c6238d5471bbb4f7cda119605c53b4fc141c040`. The pre-existing focused A0_ON output did not record a binary SHA in its own runtime manifest; its matrix-manifest row is corrected to `binary_sha256: null`, and the paired numerical analysis uses the hash-identified `A0_ON_repeat` run. The original generated matrix manifest is preserved under the `*_original_unreconciled.json` name; do not treat its copied A0_ON initial-run SHA as verified.

The authoritative three-period task matrix used Release binary SHA-256 `de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00`, recorded Git revision `04958a996f6a1449fa3d9f158a4765493c85038d`, and matching pre/post runtime XML and three input SHA values for every paired condition. In the same seed-101 scene, A0/C each had 3/3 unique gate-matched detections and B had 0/3; A0/C each associated 2/3 detections to confirmed tracks. This is one-seed feasibility evidence, not an independent-seed or statistical performance result. Per-period evaluator outputs are retained in `task_metrics_3period/`.

## Evidence interpretation

The saved R6 maps contain 471,017 valid CUTs with erroneous nonpositive thresholds across OFF/ON/TO. R7 post-fix threshold maps match float64 references rounded to float32 and all nine compared post-fix branch hit maps have zero decision flips against the references. These are numerical-correctness results, not statistical Pd/Pfa improvements. The subsequent three-period target association and its limits are described in `R7_H0_detection_association_audit.md`; the one-period numerical map files do not provide per-CUT numeric comparison for all periods in the later matrix.

CSI coherence and phase-RMSE values in the direction report are extracted from each file's `active_support_unmasked` row (beam 050, 159,705 cells; status `diagnostic_noise_and_clutter_mixed`). These mixed-support diagnostics are not target-only performance metrics.

Analysis rerun commands (write to a new output directory if the default already exists):

```bash
python3 scripts/phase_r/analyze_r7_cfar_numerics.py --output-dir /tmp/r7_pair_recheck
python3 scripts/phase_r/analyze_r7_cfar_numerics.py --legacy-r6-only --output-dir /tmp/r6_history_recheck
```
