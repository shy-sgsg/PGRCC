import unittest
from pathlib import Path

from scripts.resource_preflight import (
    assess_resource_preflight,
    parse_meminfo,
    parse_nvidia_query,
)


class ResourcePreflightTests(unittest.TestCase):
    def test_parse_meminfo_converts_kib_to_bytes(self) -> None:
        parsed = parse_meminfo(
            "MemAvailable:       6291456 kB\n"
            "SwapTotal:          1024 kB\n"
            "SwapFree:            256 kB\n")
        self.assertEqual(parsed["MemAvailable"], 6291456 * 1024)
        self.assertEqual(parsed["SwapTotal"], 1024 * 1024)

    def test_parse_nvidia_query_preserves_metrics_and_converts_memory(self) -> None:
        rows = parse_nvidia_query(
            "0, NVIDIA Test, 8192, 16384, 12, P2, 55, 80.5, 1500\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["memory.free"], 8192 * 1024 ** 2)
        self.assertEqual(rows[0]["pstate"], "P2")
        self.assertEqual(rows[0]["temperature.gpu"], 55.0)

    def test_preflight_rejects_low_memory_and_swap_without_swapoff(self) -> None:
        snapshot = {
            "memory_available_bytes": 4 * 1024 ** 3,
            "swap_total_bytes": 8 * 1024 ** 3,
            "swap_used_fraction": 0.95,
            "disk_free_bytes": 20 * 1024 ** 3,
            "gpu": {
                "status": "available",
                "gpu_count": 1,
                "max_free_memory_bytes": 8 * 1024 ** 3,
            },
        }
        result = assess_resource_preflight(snapshot, {
            "require_gpu": True,
            "min_gpu_count": 1,
            "min_mem_available_gib": 6.0,
            "max_swap_used_fraction": 0.80,
            "min_gpu_free_gib": 4.0,
            "min_disk_free_gib": 10.0,
        })
        self.assertEqual(result["status"], "reject")
        self.assertGreaterEqual(len(result["reasons"]), 2)
        self.assertFalse(result["swapoff_performed"])

    def test_preflight_passes_synthetic_healthy_snapshot(self) -> None:
        snapshot = {
            "memory_available_bytes": 8 * 1024 ** 3,
            "swap_total_bytes": 8 * 1024 ** 3,
            "swap_used_fraction": 0.10,
            "disk_free_bytes": 20 * 1024 ** 3,
            "gpu": {
                "status": "available",
                "gpu_count": 1,
                "max_free_memory_bytes": 8 * 1024 ** 3,
            },
        }
        result = assess_resource_preflight(snapshot, {
            "require_gpu": True,
            "min_gpu_count": 1,
            "min_mem_available_gib": 6.0,
            "max_swap_used_fraction": 0.80,
            "min_gpu_free_gib": 4.0,
            "min_disk_free_gib": 10.0,
        })
        self.assertEqual(result["status"], "pass")

    def test_preflight_records_but_does_not_enforce_swap_without_limit(self) -> None:
        snapshot = {
            "memory_available_bytes": 4 * 1024 ** 3,
            "swap_total_bytes": 8 * 1024 ** 3,
            "swap_used_fraction": 0.99,
            "disk_free_bytes": 20 * 1024 ** 3,
            "gpu": {
                "status": "available",
                "gpu_count": 1,
                "max_free_memory_bytes": 2 * 1024 ** 3,
            },
        }
        result = assess_resource_preflight(snapshot, {
            "require_gpu": True,
            "min_gpu_count": 1,
            "min_mem_available_gib": 4.0,
            "max_swap_used_fraction": None,
            "min_gpu_free_gib": 2.0,
            "min_disk_free_gib": 10.0,
        })
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["snapshot"]["swap_used_fraction"], 0.99)


if __name__ == "__main__":
    unittest.main()
