"""
Andrew Shackelford
ashackelford@college.harvard.edu
@shackoverflow

surrender_index_bot.py
A Twitter bot that tracks every live game in the NFL,
and tweets out the "Surrender Index" of every punt
as it happens.

Inspired by SB Nation's Jon Bois @jon_bois.
"""

import argparse
from datetime import datetime, timedelta, timezone
import dateutil.parser
from dateutil import tz
import espn_scraper as espn
import json
import numpy as np
import os
import random
import scipy.stats as stats
from selenium import webdriver
from selenium.webdriver.support.select import Select
import sys
import threading
import time
import tweepy
from twilio.rest import Client

# Due to problems with the NFL api, sometimes plays will be sent down the
# wire twice if they are updated (usually to correct the line of scrimmage
# or game clock). This will cause the play to be tweeted twice, sometimes
# even three times. To fix this, we'll keep a dictionary of every play
# tweeted by every game, and ensure we don't tweet a play that's already
# been tweeted.
tweeted_plays = {}

# The authenticated Tweepy APIs, since they're not passed in the callback.
api, ninety_api = None, None

# Historical surrender indices, so we don't have to reload them each time.
historical_surrender_indices = None

# Whether the bot should tweet out its findings
should_tweet = True

### MISCELLANEOUS HELPER FUNCTIONS ###


def is_punt(play):
    """Determine if a play is a punt.

    Parameters:
    play(dict): The play dictionary.

    Returns:
    bool: Whether the given play object is a punt.
    """
    text = play['text'].lower()
    if 'fake punt' in text:
        return False
    if 'punts' in text:
        return True
    if 'punt is blocked' in text:
        return True
    if 'punt for ' in text:
        return True
    return False


def get_yrdln_int(play):
    """Given a play, get the line of scrimmage as an integer.

    Parameters:
    play(dict): The play dictionary.

    Returns:
    int: The yard line as an integer.
    """
    return play['yardLine']


def get_possessing_team(drive):
    """Given a drive, get the possessing team as an abbreviation.

    Parameters:
    drive(dict): The drive dictionary.

    Returns:
    string: The possessing team's abbreviation.
    """
    return drive['team']['abbreviation']


def get_home_team(game):
    """Given a game, get the home team as an abbreviation.

    Parameters:
    drive(dict): The drive dictionary.

    Returns:
    string: The home team's abbreviation.
    """
    return game['boxscore']['teams'][1]['team']['abbreviation']


def get_away_team(game):
    """Given a game, get the away team as an abbreviation.

    Parameters:
    drive(dict): The drive dictionary.

    Returns:
    string: The home team's abbreviation.
    """
    return game['boxscore']['teams'][0]['team']['abbreviation']


def return_other_team(drive, game):
    """Given a drive and a game, return the abbreviation of the team that does not have possession.

    Parameters:
    drive(dict): The drive dictionary.
    game(dict): The game dictionary.

    Returns:
    string: The abbrevation of the team without possession.
    """

    if get_possessing_team(drive) == get_home_team(game):
        return get_away_team(game)
    else:
        return get_home_team(game)

def get_previous_play(play, drive):
    for idx, play_option in enumerate(drive):
        if get_qtr_num(play) == get_qtr_num(play_option) and get_home_score(play) == get_home_score(play_option) and get_away_score(play) == get_away_score(play_option) and get_time_str(play) == get_time_str(play_option) and play['text'] == play_option['text']:
            return drive[idx-1]
    raise Exception("Unable to find previous play")


### CALCULATION HELPER FUNCTIONS ###


def calc_seconds_from_time_str(time_str):
    """Calculate the integer number of seconds from a time string.

    Parameters:
    time_str(string): A string in format "MM:SS" representing the game clock.

    Returns:
    int: The time string converted to an integer number of seconds.
    """

    minutes, seconds = map(int, time_str.split(":"))
    return minutes * 60 + seconds


def is_postseason(game):
    """Given a game, determine whether or not the game is in the postseason.

    Parameters:
    game(dict): The game dictionary

    Returns:
    bool: Whether or not the game is a postseason game.

    """
    return game['header']['season']['type'] == 3


def get_time_str(play):
    """Given a play, return the game clock as a string.

    Parameters:
    play(dict): The play dictionary

    Returns:
    string: The game clock as a string.
    """
    return play['clock']['displayValue']


def get_qtr_num(play):
    """Given a play, return the quarter as an integer.

    Parameters:
    play(dict): The play dictionary

    Returns:
    int: The quarter as an integer.
    """
    return play['period']['number']


def calc_seconds_since_halftime(play, game):
    """Calculate the number of seconds elapsed since halftime.

    Parameters:
    play(dict): The play dictionary.
    game(dict): The game dictionary.

    Returns:
    int: The number of seconds elapsed since halftime of that play.
    """

    # Regular season games have only one overtime of length 10 minutes
    if not is_postseason(game) and get_qtr_num(play) == 5:
        seconds_elapsed_in_qtr = (10 * 60) - calc_seconds_from_time_str(
            get_time_str(play))
    else:
        seconds_elapsed_in_qtr = (15 * 60) - calc_seconds_from_time_str(
            get_time_str(play))
    return max(seconds_elapsed_in_qtr + (15 * 60) * (get_qtr_num(play) - 3), 0)


def get_home_score(play):
    """Given a play, return the home score as an integer.

    Parameters:
    play(dict): The play dictionary

    Returns:
    int: The home score as an integer.
    """
    return play['home_score']


def get_away_score(play):
    """Given a play, return the away score as an integer.

    Parameters:
    play(dict): The play dictionary

    Returns:
    int: The away score as an integer.
    """
    return play['away_score']


def calc_score_diff(play, drive, game):
    """Calculate the score differential of the team with possession.

    Parameters:
    play(dict): The play dictionary.
    drive(dict): The drive dictionary.
    game(dict): The game dictionary.

    Returns:
    int: The score differential of the team with possession.
    """

    if get_possessing_team(drive) == get_home_team(game):
        return get_home_score(play) - get_away_score(play)
    else:
        return get_away_score(play) - get_home_score(play)


def is_in_opposing_territory(play):
    """Given a play, determine if the line of scrimmage is in opposing territory.
    For the purposes of our calculations, the 50 yard line counts as opposing territory.

    Parameters:
    play(dict): The play dictionary

    Returns:
    bool: Whether or not the play is in opposing territory.
    """
    return play['yardLine'] == play['yardsToEndzone']


### SURRENDER INDEX FUNCTIONS ###


def calc_field_pos_score(play):
    """Calculate the field position score for a play.

    Parameters:
    play(dict): The play dictionary.

    Returns:
    float: The "field position score" for a given play, used to calculate the surrender index.
    """

    try:
        if get_yrdln_int(play) == 50:
            return (1.1)**10.
        if not is_in_opposing_territory(play):
            return max(1., (1.1)**(get_yrdln_int(play) - 40))
        else:
            return (1.2)**(50 - get_yrdln_int(play)) * ((1.1)**(10))
    except BaseException:
        return 0.


def calc_yds_to_go_multiplier(play):
    """Calculate the yards to go multiplier for a play.

    Parameters:
    play(dict): The play dictionary.

    Returns:
    float: The "yards to go multiplier" for a given play, used to calculate the surrender index.
    """

    if play['distance'] >= 10:
        return 0.2
    elif play['distance'] >= 7:
        return 0.4
    elif play['distance'] >= 4:
        return 0.6
    elif play['distance'] >= 2:
        return 0.8
    else:
        return 1.


def calc_score_multiplier(play, drive, game):
    """Calculate the score multiplier for a play.

    Parameters:
    play(dict): The play dictionary.
    drive(dict): The drive dictionary.
    game(dict): The game dictionary.

    Returns:
    float: The "score multiplier" for a given play, used to calculate the surrender index.
    """

    score_diff = calc_score_diff(get_previous_play(play), drive, game)

    if score_diff > 0:
        return 1.
    elif score_diff == 0:
        return 2.
    elif score_diff < -8.:
        return 3.
    else:
        return 4.


def calc_clock_multiplier(play, drive, game):
    """Calculate the clock multiplier for a play.

    Parameters:
    play(dict): The play dictionary.
    drive(dict): The drive dictionary.
    game(dict): The game dictionary.

    Returns:
    float: The "clock multiplier" for a given play, used to calculate the surrender index.
    """

    if calc_score_diff(play, drive, game) <= 0 and get_qtr_num(play) > 2:
        seconds_since_halftime = calc_seconds_since_halftime(play, game)
        return ((seconds_since_halftime * 0.001)**3.) + 1.
    else:
        return 1.


def calc_surrender_index(play, drive, game):
    """Calculate the surrender index for a play.

    Parameters:
    play(dict): The play dictionary.
    drive(dict): The drive dictionary.
    game(dict): The game dictionary.

    Returns:
    float: The surrender index for a given play.
    """

    return calc_field_pos_score(
        play) * calc_yds_to_go_multiplier(play) * calc_score_multiplier(
            play, drive, game) * calc_clock_multiplier(play, drive, game)


### STRING FORMAT FUNCTIONS ###


def get_pretty_time_str(time_str):
    """Take a time string and remove a leading zero if necessary.

    Parameters:
    time_str(string): A string in format "MM:SS" representing the game clock.

    Returns:
    string: The time string with a leading zero removed, if present.
    """

    if time_str[0] == '0':
        return time_str[1:]
    else:
        return time_str


def get_qtr_str(qtr):
    """Given a quarter as an integer, return the quarter as a string, handling overtime correctly.
       e.g. 1 = 1st, 2 = 2nd, 5 = OT, etc.

    Parameters:
    qtr(int): The quater number you wish to convert to an ordinal string.

    Returns:
    string: The quater as an correctly formatted string.
    """

    if qtr < 5:
        return 'the ' + get_num_str(qtr)
    elif qtr == 5:
        return 'OT'
    else:
        return str(qtr - 4) + ' OT'


def get_ordinal_suffix(num):
    """Given a digit, return the correct ordinal suffix.

    Parameters:
    num(int or float): The number you wish to get the ordinal suffix for,
                       based solely on the last digit.

    Returns:
    string: The ordinal suffix.

    """
    last_digit = str(num)[-1]

    if last_digit == '1':
        return 'st'
    elif last_digit == '2':
        return 'nd'
    elif last_digit == '3':
        return 'rd'
    else:
        return 'th'


def get_num_str(num):
    """Given a number, return the number as an ordinal string, handling percentiles correctly.
       e.g. 1 = 1st, 2 = 2nd, 3 = 3rd, etc.

    Parameters:
    num(int or float): The number you wish to convert to an ordinal string.

    Returns:
    string: The integer as an ordinal string.
    """

    rounded_num = int(num)  # round down

    if rounded_num % 100 == 11 or rounded_num % 100 == 12 or rounded_num % 100 == 13:
        return str(rounded_num) + 'th'

    # add more precision for 99th percentile
    if rounded_num == 99:
        if num < 99.9:
            return str(round(num, 1)) + get_ordinal_suffix(round(num, 1))
        elif num < 99.99:
            return str(round(num, 2)) + get_ordinal_suffix(round(num, 2))
        else:
            # round down
            multiplied = int(num * 1000)
            rounded_down = float(multiplied) / 1000
            return str(rounded_down) + get_ordinal_suffix(rounded_down)

    return str(rounded_num) + get_ordinal_suffix(rounded_num)


def pretty_score_str(score_1, score_2):
    """Given two scores, return the scores as a pretty string.
       e.g. "winning 28 to 3"

    Parameters:
    score_1(int): The first score in the string.
    score_2(int): The second score in the string.

    Returns:
    string: The two scores as a pretty string.
    """

    if score_1 > score_2:
        ret_str = 'winning '
    elif score_2 > score_1:
        ret_str = 'losing '
    else:
        ret_str = 'tied '

    ret_str += str(score_1) + ' to ' + str(score_2)
    return ret_str


def get_score_str(play, drive, game):
    """Given a play, return the game score as a pretty string,
       with the possessing team first.
       e.g. "losing 28 to 34"

    Parameters:
    play(dict): The play dictionary.
    drive(dict): The drive dictionary.
    game(dict): The game dictionary.

    Returns:
    string: The game score as a pretty string.
    """

    global scores

    prev_play = get_previous_play(play)
    if get_possessing_team(drive) == get_home_team(game):
        return pretty_score_str(get_home_score(prev_play), get_away_score(prev_play))
    else:
        return pretty_score_str(get_away_score(prev_play), get_home_score(prev_play))


### HISTORY FUNCTIONS ###


def get_game_id(game):
    """Given a play, get the id of that game.

    Parameters:
    game(dict): The game dictionary:

    Returns:
    string: The game id as a string.
    """
    return game['header']['id']


def has_been_tweeted(play, drive, game):
    """Given a play, determine if that play has been tweeted already.

    Parameters:
    play(dict): The play dictionary.
    drive(dict): The drive dictionary.
    game(dict): The game dictionary.

    Returns:
    bool: Whether that play has already been tweeted.
    """

    global tweeted_plays
    game_plays = tweeted_plays.get(get_game_id(game), set())
    for old_play, old_drive in list(game_plays):
        if get_possessing_team(old_drive) == get_possessing_team(
                drive) and get_qtr_num(old_play) == get_qtr_num(play) and abs(
                    calc_seconds_from_time_str(get_time_str(old_play)) -
                    calc_seconds_from_time_str(get_time_str(play))) < 50:
            # Check if the team with possession and quarter are the same, and
            # if the game clock at the start of the play is within 50 seconds.
            return True

    return False


def update_tweeted_plays(play, drive, game):
    """Given a play, update the dictionary of already tweeted plays.

    Parameters:
    play(dict): The play dictionary.
    drive(dict): The drive dictionary.
    game(dict): The game dictionary.
    """

    global tweeted_plays
    game_plays = tweeted_plays.get(get_game_id(game), set())
    game_plays.add((play, drive))
    tweeted_plays[get_game_id(game)] = game_plays


### PERCENTILE FUNCTIONS ###


def load_historical_surrender_indices():
    """Load in saved surrender indices from punts from 2009 to 2019.

    Returns:
    numpy.array: A numpy array containing all loaded surrender indices.
    """

    with open('2009-2019_surrender_indices.npy', 'rb') as f:
        return np.load(f)


def load_current_surrender_indices():
    """Load in saved surrender indices from the current season's past punts.

    Returns:
    numpy.array: A numpy array containing all surrender indices from the current season.
    """

    try:
        with open('current_surrender_indices.npy', 'rb') as f:
            return np.load(f)
    except BaseException:
        return np.array([])


def write_current_surrender_indices(surrender_indices):
    """Write surrender indices from the current season to file.

    Parameters:
    surrender_indices(numpy.array): The numpy array to write to file.
    """

    with open('current_surrender_indices.npy', 'wb') as f:
        np.save(f, surrender_indices)


def calculate_percentiles(surrender_index):
    """Load in past saved surrender indices, calculate the percentiles of the
       given one among current season and all since 2009, then add the given
       one to current season and write back to file.

    Parameters:
    surrender_index(float): The surrender index of a punt.

    Returns:
    float: The percentile of the given surrender index among punts from the current season.
    float: The percentile of the given surrender index among punts since 2009.
    """

    global historical_surrender_indices

    current_surrender_indices = load_current_surrender_indices()
    current_percentile = stats.percentileofscore(current_surrender_indices,
                                                 surrender_index,
                                                 kind='strict')

    all_surrender_indices = np.concatenate(
        (historical_surrender_indices, current_surrender_indices))
    historical_percentile = stats.percentileofscore(all_surrender_indices,
                                                    surrender_index,
                                                    kind='strict')

    current_surrender_indices = np.append(current_surrender_indices,
                                          surrender_index)
    write_current_surrender_indices(current_surrender_indices)

    return current_percentile, historical_percentile


### TWITTER FUNCTIONS ###


def initialize_api():
    """Load in the Twitter credentials and initialize the Tweepy API for both accounts.

    Returns:
    tweepy.API, tweepy.API: Two instances of the Tweepy API: one for the main account,
                            and one for the account that only tweets above 90th percentile.
    """

    with open('credentials.json', 'r') as f:
        credentials = json.load(f)
    auth = tweepy.OAuthHandler(credentials['consumer_key'],
                               credentials['consumer_secret'])
    auth.set_access_token(credentials['access_token'],
                          credentials['access_token_secret'])
    api = tweepy.API(auth)

    auth = tweepy.OAuthHandler(credentials['90_consumer_key'],
                               credentials['90_consumer_secret'])
    auth.set_access_token(credentials['90_access_token'],
                          credentials['90_access_token_secret'])
    ninety_api = tweepy.API(auth)

    auth = tweepy.OAuthHandler(credentials['cancel_consumer_key'],
                               credentials['cancel_consumer_secret'])
    auth.set_access_token(credentials['cancel_access_token'],
                          credentials['cancel_access_token_secret'])
    cancel_api = tweepy.API(auth)

    return api, ninety_api, cancel_api


def initialize_twilio_client():
    """Load in the Twilio credentials and initialize the Twilio API.

    Returns:
    twilio.rest.Client: An instance of the Twilio Client.
    """

    with open('credentials.json', 'r') as f:
        credentials = json.load(f)
    return Client(credentials['twilio_account_sid'],
                  credentials['twilio_auth_token'])


def send_heartbeat_message(should_repeat=True):
    """Send a heartbeat message every 24 hours to confirm the script is still running.
    """
    global twilio_client

    with open('credentials.json', 'r') as f:
        credentials = json.load(f)
    while True:
        message = twilio_client.messages.create(
            body="The Surrender Index script is up and running.",
            from_=credentials['from_phone_number'],
            to=credentials['to_phone_number'])
        if not should_repeat:
            break
        time.sleep(60 * 60 * 24)


def send_error_message(e):
    """Send an error message when an exception occurs.

    Parameters:
    e(Exception): The exception that occurred.

    """

    global twilio_client

    with open('credentials.json', 'r') as f:
        credentials = json.load(f)
        message = twilio_client.messages.create(
            body="The Surrender Index script encountered an exception " +
            str(e) + ".",
            from_=credentials['from_phone_number'],
            to=credentials['to_phone_number'])


def create_tweet_str(play, drive, game, surrender_index, current_percentile,
                     historical_percentile):
    """Given a play, surrender index, and two percentiles, craft a string to tweet.

    Parameters:
    play(nflgame.game.Play): The play object.
    surrender_index(float): The surrender index of the punt.
    current_percentile(float): The percentile of the surrender index in punts from the current season.
    historical_percentile(float): The percentile of the surrender index in punts since 2009.


    Returns:
    string: The string to tweet.
    """

    if get_yrdln_int(play) == 50:
        territory_str = '50'
    elif is_in_opposing_territory(play):
        territory_str = return_other_team(drive, game) + ' ' + str(get_yrdln_int(play))
    else:
        territory_str = get_possessing_team(drive) + ' ' + str(get_yrdln_int(play))

    decided_str = get_possessing_team(drive) + ' decided to punt to ' + return_other_team(drive, game)
    yrdln_str = ' from the ' + territory_str + ' on '
    down_str = get_num_str(play['down']) + ' & ' + str(play['distance'])
    clock_str = ' with ' + get_pretty_time_str(get_time_str(play)) + ' remaining in '
    qtr_str = get_qtr_str(get_qtr_num(play)) + ' while ' + get_score_str(play, drive, game) + '.'

    play_str = decided_str + yrdln_str + down_str + clock_str + qtr_str

    surrender_str = 'With a Surrender Index of ' + str(
        round(surrender_index, 2)
    ) + ', this punt ranks at the ' + get_num_str(
        current_percentile
    ) + ' percentile of cowardly punts of the 2020 season, and the ' + get_num_str(
        historical_percentile) + ' percentile of all punts since 2009.'

    return play_str + '\n\n' + surrender_str


def tweet_play(play, drive, game):
    """Given a play, tweet it (if it hasn't already been tweeted).

    Parameters:
    play(nflgame.game.Play): The play to tweet.
    """

    global api
    global ninety_api
    global cancel_api

    if not has_been_tweeted(play, drive, game):
        surrender_index = calc_surrender_index(play, drive, game)
        current_percentile, historical_percentile = calculate_percentiles(
            surrender_index)
        tweet_str = create_tweet_str(play, drive, game, surrender_index,
                                     current_percentile, historical_percentile)

        print(tweet_str)
        if should_tweet:
            api.update_status(tweet_str)

        # Post the status to the 90th percentile account.
        if current_percentile >= 90. and should_tweet:
            orig_status = ninety_api.update_status(tweet_str)
            thread = threading.Thread(target=handle_cancel,
                                      args=(orig_status._json, tweet_str))
            thread.start()

        update_tweeted_plays(play)


### CANCEL FUNCTIONS ###


def get_driver():
    """Gets a Selenium WebDriver logged into Twitter.

    Returns:
    selenium.WebDriver: A Selenium WebDriver logged into Twitter.
    """
    with open('credentials.json', 'r') as f:
        credentials = json.load(f)
        username = credentials['cancel_email']
        password = credentials['cancel_password']

    if sys.platform.startswith('darwin'):
        driver = webdriver.Chrome('./chromedriver_mac')
    elif sys.platform.startswith('linux'):
        driver = webdriver.Chrome('./chromedriver_linux')
    else:
        raise Exception('No chromedriver found')

    driver.implicitly_wait(10)
    driver.get('https://twitter.com/login')

    username_field = driver.find_element_by_class_name("js-username-field")
    password_field = driver.find_element_by_class_name("js-password-field")
    username_field.send_keys(username)
    password_field.send_keys(password)

    driver.find_element_by_class_name("EdgeButtom--medium").click()
    return driver


def post_reply_poll(link):
    """Posts a reply to the given tweet with a poll asking whether the punt's Surrender Index should be canceled.

    Parameters:
    link(str): A string of the link to the original tweet.
    """
    driver = get_driver()
    driver.get(link)

    driver.find_element_by_xpath("//div[@aria-label='Reply']").click()
    driver.find_element_by_xpath("//div[@aria-label='Add poll']").click()

    driver.find_element_by_name("Choice1").send_keys("Yes")
    driver.find_element_by_name("Choice2").send_keys("No")
    Select(driver.find_element_by_xpath(
        "//select[@aria-label='Days']")).select_by_visible_text("0")
    Select(driver.find_element_by_xpath(
        "//select[@aria-label='Hours']")).select_by_visible_text("1")
    Select(driver.find_element_by_xpath(
        "//select[@aria-label='Minutes']")).select_by_visible_text("0")
    driver.find_element_by_xpath("//div[@aria-label='Tweet text']").send_keys(
        "Should this punt's Surrender Index be canceled?")
    driver.find_element_by_xpath("//div[@data-testid='tweetButton']").click()

    time.sleep(10)
    driver.close()


def check_reply(link):
    """Checks the poll reply to the tweet to count the votes for Yes/No.

    Parameters:
    link(str): A string of the link to the original tweet.

    Returns:
    bool: Whether more than 2/3 of people voted Yes than No. Returns None if an error occurs.
    """
    time.sleep(60 * 60)  # Wait one hour to check reply
    driver = get_driver()
    driver.get(link)

    poll_title = driver.find_element_by_xpath("//*[contains(text(), 'votes')]")
    poll_content = poll_title.find_element_by_xpath("./..")
    poll_result = poll_content.find_elements_by_tag_name("span")
    poll_values = [poll_result[2], poll_result[5]]
    poll_floats = map(lambda x: float(x.get_attribute("innerHTML").strip('%')),
                      poll_values)

    if len(poll_floats) != 2:
        driver.close()
        return None
    else:
        driver.close()
        return poll_floats[0] >= 66.67


def cancel_punt(orig_status, full_text):
    """Cancels a punt, in that it deletes the original tweet, posts a new
       tweet with the same text to the cancel account, and then retweets
       that tweet with the caption "CANCELED" from the original account.

    Parameters:
    orig_status(Dict): A dictionary representing the Status object of the punt that might be canceled.
    """
    global ninety_api
    global cancel_api

    ninety_api.destroy_status(orig_status['id'])
    cancel_status = cancel_api.update_status(full_text)._json
    new_cancel_text = 'CANCELED https://twitter.com/CancelSurrender/status/' + cancel_status[
        'id_str']

    time.sleep(10)
    ninety_api.update_status(new_cancel_text)


def handle_cancel(orig_status, full_text):
    """Handles the cancel functionality for a tweet.
       Should be called in a separate thread so that it does not block the main thread.

    Parameters:
    orig_status(Dict): A dictionary representing the Status object of the punt that might be canceled.
    """

    try:
        orig_link = 'https://twitter.com/surrender_idx90/status/' + orig_status[
            'id_str']
        post_reply_poll(orig_link)
        if check_reply(orig_link):
            cancel_punt(orig_status, full_text)
    except Exception as e:
        print("An error occurred when trying to handle canceling a tweet")
        print(orig_status)
        print(e)
        send_error_message(
            "An error occurred when trying to handle canceling a tweet")


### MAIN FUNCTIONS ###


def live_callback(plays):
    """The callback for nflgame.live.run.
       This callback is called whenever new plays are downloaded by the API.

       This callback tweets any punts contained in the diffs,
       updates the scores for each active game,
       and cleans up any tweeted punts for games that are now completed.

    Parameters:
    active(List[nflgame.game]): A list of each active game.
    completed(List[nflgame.game]): A list of each completed game.
    diffs(List[nflgame.game.GameDiff]): A list of GameDiff objects (one per
                                        game), each of which contains a list
                                        of plays that have occurred since the
                                        last update.
    """

    global tweeted_plays

    for play, drive, game in plays:
        if is_punt(play):
            tweet_play(play, drive, game)


def update_current_year_games():
    global current_year_games
    now = get_now()
    two_months_ago = now - timedelta(days=60)
    scoreboard_urls = espn.get_all_scoreboard_urls("nfl", two_months_ago.year)
    current_year_games = []
    for scoreboard_url in scoreboard_urls:
        data = None
        backoff_time = 1.
        while data == None:
            try:
                data = espn.get_url(scoreboard_url)
            except:
                time.sleep(backoff_time)
                backoff_time *= 2.
        for event in data['content']['sbData']['events']:
            current_year_games.append(event)

def get_now():
    local = tz.gettz()
    return datetime.now(tz=local)


def get_active_game_ids():
    global current_year_games
    global completed_game_ids
    global start_times

    now = get_now()
    active_game_ids = set()

    for game in current_year_games:
        if game['id'] in completed_game_ids:
            # ignore any games that are marked completed (which is done by checking if espn says final)
            continue
        game_time = dateutil.parser.parse(
            game['date']).replace(tzinfo=timezone.utc).astimezone(tz=None)
        print(game_time)
        print(now)
        if game_time - timedelta(minutes=15) < now and game_time + timedelta(hours=6) > now:
            # game should start within 15 minutes and not started more than 6 hours ago
            active_game_ids.add(game['id'])
            start_times[game['id']] = game_time

    return active_game_ids


def clean_games(active_game_ids):
    """Clean any games that are no longer active out of the tweeted plays dict"""
    global tweeted_plays
    game_id_keys = tweeted_plays.keys()
    for game_id in game_id_keys:
        if game_id not in active_game_ids:
            del tweeted_plays[game_id]

def get_unique_str_from_play(play):
    return str(play['period']) + str(play['homeScore']) + str(play(['away_score'])) + play['clock']['displayValue'] + play['text']

def get_new_plays_from_games(old_game, new_game):
    play_intersections = []
    old_plays = set()

    if 'drives' in old_game:
        for drive_type, drives in old_game['drives'].items():
            for drive in drives:
                for play in plays:
                    old_plays.add(get_unique_str_from_play(play))

    if 'drives' in new_game:
        for drive_type, drives in new_game['drives'].items():
            for drive in drives:
                for play in plays:
                    if get_unique_str_from_play(play) not in old_plays:
                        play_intersections.append((play, drive, new_game))

    return play_intersections


def get_new_play_tuples(game_ids):
    global completed_game_ids
    play_tuples = []

    for game_id in game_ids:
        # get game data
        data = None
        backoff_time = 1.
        url = espn.get_game_url("playbyplay", "nfl", int(game_id))
        while data == None:
            try:
                data = espn.get_url(url)
            except:
                time.sleep(backoff_time)
                backoff_time *= 3.
        new_game = data['gamepackageJSON']

        # mark game as completed if necessary
        if new_game['header']['competitions'][0]['status']['type'][
                'completed']:
            completed_game_ids.add(str(new_game['header']['id']))

        # open old game file if it exists
        game_file = "game_data/" + game_id + ".json"
        if os.path.exists(game_file):
            with open(game_file, "r") as f:
                old_data = json.load(f)
                old_game = old_data['gamepackageJSON']
        else:
            old_game = {}

        play_tuples.extend(get_new_plays_from_games(old_game, new_game))

        # write new game file to disk
        with open(game_file, "w") as f:
            json.dump(data, f)

        # wait five seconds (plus/minus some randomness) to avoid sending too many requests
        time.sleep(5. + random.uniform(-2, 2))

    return play_tuples


def download_data_for_active_games():
    active_game_ids = get_active_game_ids()
    if len(active_game_ids) == 0:
        print("No games active. Sleeping for 15 minutes...")
        time.sleep(15)
    clean_games(active_game_ids)
    new_play_tuples = get_new_play_tuples(active_game_ids)
    live_callback(new_play_tuples)


def main():
    """The main function to initialize the API and start the live callback.
    """

    global api
    global ninety_api
    global cancel_api
    global historical_surrender_indices
    global should_tweet
    global sleep_time
    global twilio_client
    global completed_game_ids

    parser = argparse.ArgumentParser(
        description="Run the Surrender Index bot.")
    parser.add_argument('--disableTweeting',
                        action='store_true',
                        dest='disableTweeting')
    args = parser.parse_args()
    should_tweet = not args.disableTweeting

    api, ninety_api, cancel_api = initialize_api()
    historical_surrender_indices = load_historical_surrender_indices()
    twilio_client = initialize_twilio_client()
    sleep_time = 1

    completed_game_ids = set()

    if not os.path.exists("game_data/"):
        os.makedirs("game_data")

    should_continue = True
    while should_continue:
        try:
            # update current year games at 3 AM every day
            send_heartbeat_message(should_repeat=False)
            update_current_year_games()
            now = get_now()
            if now.hour < 3:
                stop_date = now.replace(hour=3,
                                        minute=0,
                                        second=0,
                                        microsecond=0)
            else:
                now += timedelta(days=1)
                stop_date = now.replace(hour=3,
                                        minute=0,
                                        second=0,
                                        microsecond=0)

            while get_now() < stop_date:
                download_data_for_active_games()
                sleep_time = 1.

        except KeyboardInterrupt:
            should_continue = False
        except Exception as e:
            # When an exception occurs: log it, send a message, and sleep for an
            # exponential backoff time
            print("Error occurred:")
            print(e)
            print("Sleeping for " + str(sleep_time) + " minutes")
            send_error_message(e)

            time.sleep(sleep_time * 60)
            sleep_time *= 2


if __name__ == "__main__":
    main()
