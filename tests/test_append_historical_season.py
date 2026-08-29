import csv
import gzip
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

from scripts.append_historical_season import (
    append_historical_season,
    resolve_data_path,
)


PBP_COLUMNS = [
    "season",
    "play_type",
    "season_type",
    "qtr",
    "game_seconds_remaining",
    "posteam",
    "defteam",
    "posteam_score",
    "defteam_score",
    "yrdln",
    "ydstogo",
]


class AppendHistoricalSeasonTests(unittest.TestCase):
    def test_default_data_path_is_always_refreshed(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_directory = Path(temporary_directory)
            stale_data_path = cache_directory / "play_by_play_2025.csv.gz"
            stale_data_path.touch()

            with mock.patch(
                "scripts.append_historical_season.download_season",
                return_value=stale_data_path,
            ) as download:
                resolved = resolve_data_path(
                    2025,
                    cache_directory=cache_directory,
                )

            self.assertEqual(resolved, stale_data_path)
            download.assert_called_once_with(2025, stale_data_path)

    def test_appends_only_punts_and_reports_season_counts(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            base_path = directory / "1999-2024_surrender_indices.npy"
            data_path = directory / "play_by_play_2025.csv.gz"
            output_path = directory / "1999-2025_surrender_indices.npy"

            with base_path.open("wb") as base_file:
                np.save(base_file, np.asarray([0.2, 1.0], dtype=np.float64))

            rows = [
                {
                    "season": "2025",
                    "play_type": "punt",
                    "season_type": "REG",
                    "qtr": "1",
                    "game_seconds_remaining": "3500",
                    "posteam": "BUF",
                    "defteam": "NYJ",
                    "posteam_score": "0",
                    "defteam_score": "0",
                    "yrdln": "BUF 40",
                    "ydstogo": "4",
                },
                {
                    "season": "2025",
                    "play_type": "pass",
                    "season_type": "REG",
                    "qtr": "2",
                    "game_seconds_remaining": "1800",
                    "posteam": "BUF",
                    "defteam": "NYJ",
                    "posteam_score": "7",
                    "defteam_score": "0",
                    "yrdln": "BUF 40",
                    "ydstogo": "4",
                },
                {
                    "season": "2025",
                    "play_type": "punt",
                    "season_type": "POST",
                    "qtr": "5",
                    "game_seconds_remaining": "600",
                    "posteam": "NYJ",
                    "defteam": "BUF",
                    "posteam_score": "20",
                    "defteam_score": "20",
                    "yrdln": "BUF 40",
                    "ydstogo": "1",
                },
            ]
            with gzip.open(data_path, "wt", newline="") as data_file:
                writer = csv.DictWriter(data_file, fieldnames=PBP_COLUMNS)
                writer.writeheader()
                writer.writerows(rows)

            counts = append_historical_season(
                base_path, data_path, output_path, season=2025
            )

            self.assertEqual(counts, (2, 2, 1, 1))
            generated = np.load(output_path, allow_pickle=False)
            self.assertEqual(generated.shape, (4,))
            np.testing.assert_array_equal(generated[:2], np.asarray([0.2, 1.0]))
            self.assertTrue(np.isfinite(generated).all())

    def test_rejects_data_from_a_different_season(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            base_path = directory / "base.npy"
            data_path = directory / "play_by_play_2025.csv.gz"
            with base_path.open("wb") as base_file:
                np.save(base_file, np.asarray([1.0], dtype=np.float64))
            row = {
                "season": "2025",
                "play_type": "punt",
                "season_type": "REG",
                "qtr": "1",
                "game_seconds_remaining": "3500",
                "posteam": "BUF",
                "defteam": "NYJ",
                "posteam_score": "0",
                "defteam_score": "0",
                "yrdln": "BUF 40",
                "ydstogo": "4",
            }
            with gzip.open(data_path, "wt", newline="") as data_file:
                writer = csv.DictWriter(data_file, fieldnames=PBP_COLUMNS)
                writer.writeheader()
                writer.writerow(row)

            with self.assertRaisesRegex(ValueError, "Expected season 2026"):
                append_historical_season(
                    base_path,
                    data_path,
                    directory / "unused-output.npy",
                    season=2026,
                )

    def test_rejects_unknown_punt_season_type(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            base_path = directory / "base.npy"
            data_path = directory / "play_by_play_2025.csv.gz"
            with base_path.open("wb") as base_file:
                np.save(base_file, np.asarray([1.0], dtype=np.float64))

            row = {
                "season": "2025",
                "play_type": "punt",
                "season_type": "PRE",
                "qtr": "1",
                "game_seconds_remaining": "3500",
                "posteam": "BUF",
                "defteam": "NYJ",
                "posteam_score": "0",
                "defteam_score": "0",
                "yrdln": "BUF 40",
                "ydstogo": "4",
            }
            with gzip.open(data_path, "wt", newline="") as data_file:
                writer = csv.DictWriter(data_file, fieldnames=PBP_COLUMNS)
                writer.writeheader()
                writer.writerow(row)

            with self.assertRaisesRegex(ValueError, "Unexpected punt season_type"):
                append_historical_season(
                    base_path,
                    data_path,
                    directory / "output.npy",
                    season=2025,
                )


if __name__ == "__main__":
    unittest.main()
