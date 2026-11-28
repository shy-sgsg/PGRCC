import math

import numpy as np
import pytest


def test_complex_map_metrics_report_relative_error_and_wrapped_phase():
    from scripts.phase_r.analyze_r6_stagewise_divergence import complex_map_metrics

    reference = np.array([[1 + 0j, 0 + 1j]], dtype=np.complex64)
    candidate = np.array([[1 + 1j, 0 + 2j]], dtype=np.complex64)

    result = complex_map_metrics(reference, candidate)

    assert result["shape"] == "1x2"
    assert result["finite_count"] == 2
    assert result["max_abs_error"] == pytest.approx(1.0)
    assert result["relative_complex_rms"] == pytest.approx(1.0)
    assert result["phase_rms_rad"] == pytest.approx(math.pi / (4 * math.sqrt(2)))
    assert result["power_relative_rms"] == pytest.approx(math.sqrt(5.0))


def test_complex_map_metrics_fail_closed_on_shape_or_nonfinite_values():
    from scripts.phase_r.analyze_r6_stagewise_divergence import complex_map_metrics

    with pytest.raises(ValueError, match="same shape"):
        complex_map_metrics(np.ones((1, 2), dtype=np.complex64), np.ones((2,), dtype=np.complex64))

    with pytest.raises(ValueError, match="non-finite"):
        complex_map_metrics(
            np.array([1 + 0j, complex(float("nan"), 0)], dtype=np.complex64),
            np.array([1 + 0j, 1 + 0j], dtype=np.complex64),
        )
