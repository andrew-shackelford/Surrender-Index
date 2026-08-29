import unittest

import surrender_index_bot as bot


EXPECTED_TEAMS = {
    "1": "ATL",
    "2": "BUF",
    "3": "CHI",
    "4": "CIN",
    "5": "CLE",
    "6": "DAL",
    "7": "DEN",
    "8": "DET",
    "9": "GB",
    "10": "TEN",
    "11": "IND",
    "12": "KC",
    "13": "LV",
    "14": "LAR",
    "15": "MIA",
    "16": "MIN",
    "17": "NE",
    "18": "NO",
    "19": "NYG",
    "20": "NYJ",
    "21": "PHI",
    "22": "ARI",
    "23": "PIT",
    "24": "LAC",
    "25": "SF",
    "26": "SEA",
    "27": "TB",
    "28": "WSH",
    "29": "CAR",
    "30": "JAX",
    "33": "BAL",
    "34": "HOU",
}


def make_game(*, season_type=2):
    return {
        "header": {"season": {"type": season_type}},
        "boxscore": {
            "teams": [
                {"team": {"id": "2", "abbreviation": "BUF"}},
                {"team": {"id": "20", "abbreviation": "NYJ"}},
            ]
        },
    }


def make_play(
    *,
    team_id="2",
    possession_text="BUF 40",
    yard_line=40,
    distance=4,
    clock="08:00",
    period=4,
    away_score=10,
    home_score=10,
):
    return {
        "start": {
            "team": {"id": team_id},
            "possessionText": possession_text,
            "yardLine": yard_line,
            "distance": distance,
            "shortDownDistanceText": f"4th & {distance}",
        },
        "end": {"team": {"id": team_id}},
        "clock": {"displayValue": clock},
        "period": {"number": period},
        "awayScore": away_score,
        "homeScore": home_score,
        "text": "Punt",
    }


class SurrenderIndexFormulaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The production entry point normally defines this flag. Unit tests call
        # the pure calculation helpers directly, so make the intended default
        # explicit without invoking main() or any external integrations.
        bot.debug = False

    def test_field_position_scores_cover_both_sides_and_midfield(self):
        own_40 = make_play(possession_text="BUF 40", yard_line=40)
        midfield = make_play(possession_text="50", yard_line=50)
        opposing_40 = make_play(possession_text="NYJ 40", yard_line=40)

        self.assertAlmostEqual(bot.calc_field_pos_score(own_40), 1.0)
        self.assertAlmostEqual(bot.calc_field_pos_score(midfield), 1.1**10)
        self.assertAlmostEqual(
            bot.calc_field_pos_score(opposing_40),
            (1.2**10) * (1.1**10),
        )

    def test_yards_to_go_multiplier_boundaries(self):
        expectations = {
            10: 0.2,
            9: 0.4,
            7: 0.4,
            6: 0.6,
            4: 0.6,
            3: 0.8,
            2: 0.8,
            1: 1.0,
        }
        for distance, expected in expectations.items():
            with self.subTest(distance=distance):
                self.assertEqual(
                    bot.calc_yds_to_go_multiplier(make_play(distance=distance)),
                    expected,
                )

    def test_full_formula_combines_field_distance_score_and_clock(self):
        game = make_game()
        play = make_play(
            possession_text="NYJ 40",
            yard_line=40,
            distance=1,
            clock="10:00",
            period=4,
            away_score=9,
            home_score=10,
        )
        expected_field_score = (1.2**10) * (1.1**10)
        expected_clock_multiplier = ((1200 * 0.001) ** 3) + 1
        expected = expected_field_score * 1.0 * 4.0 * expected_clock_multiplier

        self.assertAlmostEqual(
            bot.calc_surrender_index(play, play, {}, game),
            expected,
        )


class TeamAndTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bot.debug = False

    def test_team_id_mapping_matches_all_32_current_teams(self):
        self.assertEqual(bot.teams, EXPECTED_TEAMS)

    def test_possessing_team_falls_back_to_end_state(self):
        play = make_play()
        play["start"].pop("team")

        self.assertEqual(bot.get_possessing_team(play, make_game()), "BUF")

    def test_regular_season_overtime_uses_ten_minute_period(self):
        play = make_play(clock="05:00", period=5)

        self.assertEqual(
            bot.calc_seconds_since_halftime(play, make_game(season_type=2)),
            2100,
        )

    def test_postseason_overtime_uses_fifteen_minute_periods(self):
        first_overtime = make_play(clock="10:00", period=5)
        second_overtime = make_play(clock="15:00", period=6)
        postseason_game = make_game(season_type=3)

        self.assertEqual(
            bot.calc_seconds_since_halftime(first_overtime, postseason_game),
            2100,
        )
        self.assertEqual(
            bot.calc_seconds_since_halftime(second_overtime, postseason_game),
            2700,
        )


class OutputStringTests(unittest.TestCase):
    def test_quarter_and_percentile_formatting(self):
        self.assertEqual(bot.get_qtr_str(1), "the 1st")
        self.assertEqual(bot.get_qtr_str(4), "the 4th")
        self.assertEqual(bot.get_qtr_str(5), "OT")
        self.assertEqual(bot.get_qtr_str(6), "2 OT")
        self.assertEqual(bot.get_qtr_str(7), "3 OT")
        self.assertEqual(bot.get_num_str(11.9), "11th")
        self.assertEqual(bot.get_num_str(21.8), "21st")
        self.assertEqual(bot.get_num_str(99.91), "99.91st")

    def test_tweet_text_describes_play_and_uses_2026_season_label(self):
        play = make_play()
        text = bot.create_tweet_str(
            play,
            play,
            {},
            make_game(),
            surrender_index=12.345,
            current_percentile=91.2,
            historical_percentile=88.8,
        )

        self.assertIn(
            "BUF decided to punt to NYJ from the BUF 40 on 4th & 4 "
            "with 08:00 remaining in the 4th while tied 10 to 10.",
            text,
        )
        self.assertIn("With a Surrender Index of 12.35", text)
        self.assertIn("91st percentile of cowardly punts of the 2026 season", text)
        self.assertIn("88th percentile of all punts since 1999", text)
        self.assertNotIn("2025 season", text)

    def test_delay_explanation_uses_2026_season_label(self):
        play = make_play(possession_text="BUF 35", yard_line=35, distance=9)
        previous_play = make_play(
            possession_text="BUF 40",
            yard_line=40,
            distance=4,
        )
        text = bot.create_delay_of_game_str(
            play,
            {},
            make_game(),
            previous_play,
            unadjusted_surrender_index=1.234,
            unadjusted_current_percentile=75.4,
            unadjusted_historical_percentile=50.0,
        )

        self.assertIn("likely intentional", text)
        self.assertIn("Surrender Index would be 1.23", text)
        self.assertIn("75th percentile of the 2026 season", text)
        self.assertNotIn("2025 season", text)


if __name__ == "__main__":
    unittest.main()
