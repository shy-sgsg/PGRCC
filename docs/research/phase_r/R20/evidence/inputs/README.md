# R20 compact historical evidence bundle

This Git-tracked bundle contains only the saved R8 seeds 101/202/303 and R12 seed 404 evidence needed by the offline R20 adapter: case/branch manifests, normalized runtime XML/configuration, per-period Production Detection CSVs (byte-identical aggregate duplicates omitted and recorded in the manifest), saved Production Doppler-axis and CFAR geometry diagnostics where present, standalone target truth, moving-target slant-range histories, resolved scene waveform metadata, and ON TrackManager debug CSVs.

Raw protocol IQ, intermediate arrays, maps, binaries, and rebuildable simulator outputs are excluded. The bundle manifest records each copied file size and SHA-256 and keeps the source outputs path as provenance. Input IQ hashes in historical manifests remain declarations unless the corresponding raw file was independently present and hashed; this bundle does not claim raw-IQ replay. R8 seed101 intentionally has no saved Production Doppler axis and remains unevaluable for the strict Doppler-gated target metric.

The R20 adapter reads this bundle directly. Replay entry point: `PYTHONPATH=. python3 scripts/phase_r/r20_harmonized_task_review.py --output-root /tmp/r20-replay`.
