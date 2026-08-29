import os
from pathlib import Path
import unittest

import numpy as np

import surrender_index_bot as bot


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = REPOSITORY_ROOT / "1999-2025_surrender_indices.npy"
EXPECTED_INDEX_COUNT = 66_146


class HistoricalBaselineTests(unittest.TestCase):
    def test_baseline_has_expected_shape_type_and_values(self):
        self.assertTrue(BASELINE_PATH.is_file(), f"Missing {BASELINE_PATH.name}")

        baseline = np.load(BASELINE_PATH, allow_pickle=False)

        self.assertEqual(baseline.shape, (EXPECTED_INDEX_COUNT,))
        self.assertEqual(baseline.dtype, np.dtype("float64"))
        self.assertTrue(np.isfinite(baseline).all())
        self.assertTrue((baseline > 0).all())

    def test_production_loader_returns_the_2025_baseline(self):
        original_working_directory = Path.cwd()
        try:
            os.chdir(REPOSITORY_ROOT)
            loaded = bot.load_historical_surrender_indices()
        finally:
            os.chdir(original_working_directory)

        expected = np.load(BASELINE_PATH, allow_pickle=False)
        np.testing.assert_array_equal(loaded, expected)


if __name__ == "__main__":
    unittest.main()
