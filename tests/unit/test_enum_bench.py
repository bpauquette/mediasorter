import unittest
from unittest.mock import patch

import mediasorter_enum_bench as bench_mod


class EnumerationBenchmarkTests(unittest.TestCase):
    def test_benchmark_report_contains_all_backends(self):
        serial = bench_mod.EnumerationBenchmark("scandir_serial", "C:\\", True, 1.0, 10)
        parallel = bench_mod.EnumerationBenchmark("scandir_parallel", "C:\\", True, 0.5, 10)
        ntfs = bench_mod.EnumerationBenchmark("ntfs_mft_probe", "C:\\", False, 0.01, 0, "denied")
        with patch.object(bench_mod, "benchmark_scandir_directory_count", side_effect=[serial, parallel]), patch.object(
            bench_mod, "benchmark_ntfs_probe", return_value=ntfs
        ):
            report = bench_mod.benchmark_report("C:\\")

        self.assertEqual(report["target"], "C:\\")
        self.assertEqual(report["serial"]["backend_id"], "scandir_serial")
        self.assertEqual(report["parallel"]["backend_id"], "scandir_parallel")
        self.assertEqual(report["ntfs_probe"]["backend_id"], "ntfs_mft_probe")


if __name__ == "__main__":
    unittest.main()
