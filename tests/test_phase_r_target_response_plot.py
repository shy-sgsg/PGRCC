import csv
import math
import struct
import subprocess
import sys
from pathlib import Path


def test_idealized_target_response_plot_exports_checked_curve(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts/phase_r/plot_idealized_target_response.py"
    assert script.is_file(), "the reproducible Phase R response-plot generator is missing"

    result = subprocess.run(
        [sys.executable, str(script), "--output-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    csv_path = tmp_path / "idealized_target_response.csv"
    png_path = tmp_path / "idealized_target_response.png"
    assert csv_path.is_file()
    assert png_path.is_file()
    curve_bytes = csv_path.read_bytes()
    assert b"\r\n" not in curve_bytes
    assert b"\n" in curve_bytes

    with csv_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))

    assert len(rows) == 361
    assert list(rows[0]) == ["phase_difference_rad", "normalized_output_amplitude"]
    assert math.isclose(float(rows[0]["phase_difference_rad"]), 0.0, abs_tol=1e-12)
    assert math.isclose(float(rows[0]["normalized_output_amplitude"]), 0.0, abs_tol=1e-12)
    assert math.isclose(float(rows[180]["phase_difference_rad"]), math.pi / 2, abs_tol=1e-12)
    assert math.isclose(
        float(rows[180]["normalized_output_amplitude"]), math.sqrt(0.5), abs_tol=1e-12
    )
    assert math.isclose(float(rows[-1]["phase_difference_rad"]), math.pi, abs_tol=1e-12)
    assert math.isclose(float(rows[-1]["normalized_output_amplitude"]), 1.0, abs_tol=1e-12)
    amplitudes = [float(row["normalized_output_amplitude"]) for row in rows]
    assert all(left <= right for left, right in zip(amplitudes, amplitudes[1:]))

    image = png_path.read_bytes()
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", image[16:24])
    assert width >= 900
    assert height >= 500
