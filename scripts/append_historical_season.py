"""Append one finalized nflverse season to the historical index baseline."""

import argparse
import csv
import gzip
from pathlib import Path
import sys

import numpy as np
import requests


NFLVERSE_PBP_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/pbp/"
    "play_by_play_{season}.csv.gz"
)


def calc_score_diff(play):
    return int(play["posteam_score"]) - int(play["defteam_score"])


def get_yrdln_int(play):
    return int(play["yrdln"].split(" ")[-1])


def calc_seconds_since_halftime(play, year, is_postseason):
    if int(play["qtr"]) <= 4:
        if play["game_seconds_remaining"] == "1e3":
            return 1800 - 1000
        return max(0, 1800 - int(play["game_seconds_remaining"]))

    seconds_per_overtime = 900 if year < 2017 or is_postseason else 600
    if int(play["qtr"]) == 5:
        return 1800 + seconds_per_overtime - int(
            play["game_seconds_remaining"]
        )
    if int(play["qtr"]) == 6:
        return 3600 - int(play["game_seconds_remaining"])
    if int(play["qtr"]) == 7:
        return 4500 - int(play["game_seconds_remaining"])
    return 0


def calc_field_pos_score(play):
    try:
        if "50" in play["yrdln"]:
            return 1.1**10.0
        if play["posteam"] in play["yrdln"]:
            return max(1.0, 1.1 ** (get_yrdln_int(play) - 40))
        return 1.2 ** (50 - get_yrdln_int(play)) * 1.1**10
    except (KeyError, TypeError, ValueError):
        return 0.0


def calc_yds_to_go_multiplier(play):
    distance = int(play["ydstogo"])
    if distance >= 10:
        return 0.2
    if distance >= 7:
        return 0.4
    if distance >= 4:
        return 0.6
    if distance >= 2:
        return 0.8
    return 1.0


def calc_score_multiplier(play):
    score_diff = calc_score_diff(play)
    if score_diff > 0:
        return 1.0
    if score_diff == 0:
        return 2.0
    if score_diff < -8.0:
        return 3.0
    return 4.0


def calc_clock_multiplier(play, year, is_postseason):
    if calc_score_diff(play) <= 0 and int(play["qtr"]) > 2:
        seconds_since_halftime = calc_seconds_since_halftime(
            play, year, is_postseason
        )
        return (seconds_since_halftime * 0.001) ** 3.0 + 1.0
    return 1.0


def calc_surrender_index(play, year, is_postseason):
    return (
        calc_field_pos_score(play)
        * calc_yds_to_go_multiplier(play)
        * calc_score_multiplier(play)
        * calc_clock_multiplier(play, year, is_postseason)
    )


def download_season(season, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = destination.with_suffix(destination.suffix + ".part")
    url = NFLVERSE_PBP_URL.format(season=season)
    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        with temporary_path.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                output.write(chunk)
    temporary_path.replace(destination)
    return destination


def resolve_data_path(
    season,
    supplied_data_path=None,
    cache_directory=Path(".cache/nflverse"),
):
    if supplied_data_path is not None:
        return supplied_data_path

    data_path = cache_directory / f"play_by_play_{season}.csv.gz"
    print(f"Downloading finalized {season} nflverse play-by-play data...")
    return download_season(season, data_path)


def calculate_season_indices(data_path, season):
    surrender_indices = []
    regular_season_punts = 0
    postseason_punts = 0
    with gzip.open(data_path, "rt", newline="") as data_file:
        for play in csv.DictReader(data_file):
            row_season = int(play["season"])
            if row_season != season:
                raise ValueError(
                    f"Expected season {season}, found {row_season} in {data_path}"
                )
            if play["play_type"] != "punt":
                continue
            season_type = play["season_type"]
            if season_type not in {"REG", "POST"}:
                raise ValueError(
                    f"Unexpected punt season_type {season_type!r} in {data_path}"
                )
            is_postseason = season_type == "POST"
            surrender_indices.append(
                calc_surrender_index(play, season, is_postseason)
            )
            if is_postseason:
                postseason_punts += 1
            else:
                regular_season_punts += 1
    return (
        np.asarray(surrender_indices, dtype=np.float64),
        regular_season_punts,
        postseason_punts,
    )


def append_historical_season(base_path, data_path, output_path, season):
    with base_path.open("rb") as base_file:
        historical_indices = np.load(base_file)
    season_indices, regular_punts, postseason_punts = calculate_season_indices(
        data_path, season
    )
    if len(season_indices) == 0:
        raise ValueError(f"No punts found in {data_path}")
    combined_indices = np.concatenate((historical_indices, season_indices))
    if not np.isfinite(combined_indices).all():
        raise ValueError("Generated baseline contains non-finite values")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as output_file:
        np.save(output_file, combined_indices)
    return len(historical_indices), len(season_indices), regular_punts, postseason_punts


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", required=True, type=int)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--data",
        type=Path,
        help="Existing play_by_play_<season>.csv.gz; downloads to .cache if omitted.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    data_path = resolve_data_path(args.season, args.data)

    base_count, season_count, regular_punts, postseason_punts = (
        append_historical_season(
            args.base, data_path, args.output, args.season
        )
    )
    print(
        f"Wrote {args.output}: {base_count} historical + {season_count} "
        f"{args.season} punts ({regular_punts} REG, {postseason_punts} POST) "
        f"= {base_count + season_count} total."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Failed to append historical season: {error}", file=sys.stderr)
        raise
