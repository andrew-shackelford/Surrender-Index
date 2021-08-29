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
from base64 import urlsafe_b64encode
import chromedriver_autoinstaller
from datetime import datetime, timedelta, timezone
from dateutil import parser, tz
from email.mime.text import MIMEText
import espn_scraper as espn
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import json
import numpy as np
import os
import pickle
import requests
import scipy.stats as stats
from selenium import webdriver
from selenium.webdriver.support.select import Select
from selenium.common.exceptions import StaleElementReferenceException
from subprocess import Popen, PIPE
import sys
import threading
import time
import tweepy
from twilio.rest import Client
import traceback

# A dictionary of plays that have already been tweeted.
tweeted_plays = None

# A dictionary of the currently active games.
games = {}

# The authenticated Tweepy APIs.
api, ninety_api = None, None

# NPArray of historical surrender indices.
historical_surrender_indices = None

# Whether the bot should tweet out any punts
should_tweet = True

### SELENIUM FUNCTIONS ###


def get_game_driver(headless=True):
    global debug
    global not_headless
    options = webdriver.ChromeOptions()
    if headless and not debug and not not_headless:
        options.add_argument("headless")
    return webdriver.Chrome(options=options)


def get_twitter_driver(link, headless=False):
    with open('credentials.json', 'r') as f:
        credentials = json.load(f)
        email = credentials['cancel_email']
        username = credentials['cancel_username']
        password = credentials['cancel_password']

    driver = get_game_driver(headless=headless)
    driver.implicitly_wait(60)
    driver.get(link)

    driver.find_element_by_xpath("//div[@aria-label='Reply']").click()

    time.sleep(1)
    login_button = driver.find_element_by_xpath("//a[@data-testid='login']")
    time.sleep(1)
    driver.execute_script("arguments[0].click();", login_button)

    email_field = driver.find_element_by_xpath(
        "//input[@name='session[username_or_email]']")
    password_field = driver.find_element_by_xpath(
        "//input[@name='session[password]']")
    email_field.send_keys(email)
    password_field.send_keys(password)
    driver.find_element_by_xpath(
        "//div[@data-testid='LoginForm_Login_Button']").click()

    time.sleep(1)

    if 'email_disabled=true' in driver.current_url:
        username_field = driver.find_element_by_xpath(
            "//input[@name='session[username_or_email]']")
        password_field = driver.find_element_by_xpath(
            "//input[@name='session[password]']")
        username_field.send_keys(username)
        password_field.send_keys(password)
        driver.find_element_by_xpath(
            "//div[@data-testid='LoginForm_Login_Button']").click()

    return driver


### POSSESSION DETERMINATION FUNCTIONS ###


def get_possessing_team(play, game):
    try:
        team_id = play['start']['team']['id']
    except:
        team_id = play['end']['team']['id']
    for team in game['boxscore']['teams']:
        if team['team']['id'] == team_id:
            return team['team']['abbreviation']


### TEAM ABBREVIATION FUNCTIONS ###

def get_home_team(game):
    return game['boxscore']['teams'][1]['team']['abbreviation']


def get_away_team(game):
    return game['boxscore']['teams'][0]['team']['abbreviation']


def return_other_team(game, team):
    return get_away_team(game) if get_home_team(
        game) == team else get_home_team(game)


### GAME INFO FUNCTIONS ###

def is_final(game):
    return game['header']['competitions'][0]['status']['type']['name'] == 'STATUS_FINAL'

def is_postseason(game):
    return game['header']['season']['type'] > 2

### PLAY FUNCTIONS ###

def is_punt(drive):
    return 'punt' in drive['result'].lower()


def get_yrdln_int(play):
    if play['start']['yardLine'] == 50:
        return 50
    return int(play['start']['possessionText'].split(' ')[1])


def get_time_str(play):
    return play['clock']['displayValue']


def get_qtr_num(play):
    return play['period']['number']


def is_in_opposing_territory(play):
    return play['start']['yardsToEndzone'] < 50


def get_dist_num(play):
    return play['start']['distance']


### CALCULATION HELPER FUNCTIONS ###


def calc_seconds_from_time_str(time_str):
    minutes, seconds = map(int, time_str.split(":"))
    return minutes * 60 + seconds


def calc_seconds_since_halftime(play, game):
    # Regular season games have only one overtime of length 10 minutes
    if not is_postseason(game) and get_qtr_num(play) == 5:
        seconds_elapsed_in_qtr = (10 * 60) - calc_seconds_from_time_str(
            get_time_str(play))
    else:
        seconds_elapsed_in_qtr = (15 * 60) - calc_seconds_from_time_str(
            get_time_str(play))
    seconds_since_halftime = max(
        seconds_elapsed_in_qtr + (15 * 60) * (get_qtr_num(play) - 3), 0)
    if debug:
        time_print(("seconds since halftime", seconds_since_halftime))
    return seconds_since_halftime


def calc_score_diff(play, drive, game):
    away, home = play['awayScore'], play['homeScore']
    if get_possessing_team(play, game) == get_home_team(game):
        score_diff = home - away
    else:
        score_diff = away - home
    if debug:
        time_print(("score diff", score_diff))
    return score_diff


### SURRENDER INDEX FUNCTIONS ###


def calc_field_pos_score(play):
    try:
        if play['start']['yardLine'] == 50:
            return (1.1)**10.
        if not is_in_opposing_territory(play):
            return max(1., (1.1)**(get_yrdln_int(play) - 40))
        else:
            return (1.2)**(50 - get_yrdln_int(play)) * ((1.1)**(10))
    except BaseException:
        return 0.


def calc_yds_to_go_multiplier(play):
    dist = get_dist_num(play)
    if dist >= 10:
        return 0.2
    elif dist >= 7:
        return 0.4
    elif dist >= 4:
        return 0.6
    elif dist >= 2:
        return 0.8
    else:
        return 1.


def calc_score_multiplier(prev_play, drive, game):
    score_diff = calc_score_diff(prev_play, drive, game)
    if score_diff > 0:
        return 1.
    elif score_diff == 0:
        return 2.
    elif score_diff < -8.:
        return 3.
    else:
        return 4.


def calc_clock_multiplier(play, prev_play, drive, game):
    if calc_score_diff(prev_play, drive,
                       game) <= 0 and get_qtr_num(play) > 2:
        seconds_since_halftime = calc_seconds_since_halftime(play, game)
        return ((seconds_since_halftime * 0.001)**3.) + 1.
    else:
        return 1.


def calc_surrender_index(play, prev_play, drive, game):
    field_pos_score = calc_field_pos_score(play)
    yds_to_go_mult = calc_yds_to_go_multiplier(play)
    score_mult = calc_score_multiplier(prev_play, drive, game)
    clock_mult = calc_clock_multiplier(play, prev_play, drive, game)

    if debug:
        time_print(play)
        time_print("")
        time_print(("field pos score", field_pos_score))
        time_print(("yds to go mult", yds_to_go_mult))
        time_print(("score mult", score_mult))
        time_print(("clock mult", clock_mult))
    return field_pos_score * yds_to_go_mult * score_mult * clock_mult


### PUNTER FUNCTIONS ###


def find_punters_for_team(team, roster):
    base_link = 'https://www.espn.com/nfl/team/roster/_/name/'
    roster_link = base_link + team
    roster.get(roster_link)
    header = roster.find_element_by_css_selector("div.Special.Teams")
    parents = header.find_elements_by_css_selector(
        "td.Table__TD:not(.Table__TD--headshot)")
    punters = set()
    for parent in parents:
        try:
            ele = parent.find_element_by_class_name("AnchorLink")
            full_name = ele.get_attribute("innerHTML")
            split = full_name.split(" ")
            first_initial_last = full_name[0] + '.' + split[-1]
            punters.add(first_initial_last)
        except BaseException:
            pass
    return punters


def download_punters():
    global punters
    punters = {}
    if os.path.exists('punters.json'):
        file_mod_time = os.path.getmtime('punters.json')
    else:
        file_mod_time = 0.
    if time.time() - file_mod_time < 60 * 60 * 12:
        # if file modified within past 12 hours
        with open('punters.json', 'r') as f:
            punters_list = json.load(f)
            for key, value in punters_list.items():
                punters[key] = set(value)
    else:
        team_abbreviations = [
            'ARI',
            'ATL',
            'BAL',
            'BUF',
            'CAR',
            'CHI',
            'CIN',
            'CLE',
            'DAL',
            'DEN',
            'DET',
            'GB',
            'HOU',
            'IND',
            'JAX',
            'KC',
            'LAC',
            'LAR',
            'LV',
            'MIA',
            'MIN',
            'NE',
            'NO',
            'NYG',
            'NYJ',
            'PHI',
            'PIT',
            'SEA',
            'SF',
            'TB',
            'TEN',
            'WSH',
        ]
        roster = get_game_driver()
        for team in team_abbreviations:
            time_print("Downloading punters for " + team)
            punters[team] = find_punters_for_team(team, roster)
        roster.quit()
        punters_list = {}
        for key, value in punters.items():
            punters_list[key] = list(value)
        with open('punters.json', 'w') as f:
            json.dump(punters_list, f)


### STRING FORMAT FUNCTIONS ###


def get_qtr_str(qtr):
    if qtr <= 4:
        return 'the ' + str(qtr) + get_ordinal_suffix(qtr)
    elif qtr == 5:
        return 'OT'
    elif qtr == 6:
        return '2 OT'
    elif qtr == 7:
        return '3 OT'
    return ''


def get_ordinal_suffix(num):
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
    if score_1 > score_2:
        ret_str = 'winning '
    elif score_2 > score_1:
        ret_str = 'losing '
    else:
        ret_str = 'tied '

    ret_str += str(score_1) + ' to ' + str(score_2)
    return ret_str


def get_score_str(play, game):
    if get_possessing_team(play, game) == get_home_team(game):
        return pretty_score_str(play['homeScore'], play['awayScore'])
    else:
        return pretty_score_str(play['awayScore'], play['homeScore'])


### DELAY OF GAME FUNCTIONS ###


def is_delay_of_game(play, prev_play):
    return 'delay of game' in prev_play['text'].lower(
    ) and get_dist_num(play) - get_dist_num(prev_play) > 0


### HISTORY FUNCTIONS ###


def has_been_tweeted(drive, game_id):
    global tweeted_plays
    game_plays = tweeted_plays.get(game_id, [])
    return drive['id'] in game_plays


def has_been_seen(drive, game_id):
    global seen_plays
    game_plays = seen_plays.get(game_id, [])
    if drive['id'] in game_plays:
        return True
    game_plays.append(drive['id'])
    seen_plays[game_id] = game_plays
    return False


def has_been_final(game_id):
    global final_games
    if game_id in final_games:
        return True
    final_games.add(game_id)
    return False


def load_tweeted_plays_dict():
    global tweeted_plays
    tweeted_plays = {}
    if os.path.exists('tweeted_plays.json'):
        file_mod_time = os.path.getmtime('tweeted_plays.json')
    else:
        file_mod_time = 0.
    if time.time() - file_mod_time < 60 * 60 * 12:
        # if file modified within past 12 hours
        with open('tweeted_plays.json', 'r') as f:
            tweeted_plays = json.load(f)
    else:
        with open('tweeted_plays.json', 'w') as f:
            json.dump(tweeted_plays, f)


def update_tweeted_plays(drive, game_id):
    global tweeted_plays
    game_plays = tweeted_plays.get(game_id, [])
    game_plays.append(drive['id'])
    tweeted_plays[game_id] = game_plays
    with open('tweeted_plays.json', 'w') as f:
        json.dump(tweeted_plays, f)


### PERCENTILE FUNCTIONS ###


def load_historical_surrender_indices():
    with open('1999-2020_surrender_indices.npy', 'rb') as f:
        return np.load(f)


def load_current_surrender_indices():
    try:
        with open('current_surrender_indices.npy', 'rb') as f:
            return np.load(f)
    except BaseException:
        return np.array([])


def write_current_surrender_indices(surrender_indices):
    with open('current_surrender_indices.npy', 'wb') as f:
        np.save(f, surrender_indices)


def calculate_percentiles(surrender_index, should_update_file=True):
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

    if should_update_file:
        current_surrender_indices = np.append(current_surrender_indices,
                                              surrender_index)
        write_current_surrender_indices(current_surrender_indices)

    return current_percentile, historical_percentile


### TWITTER FUNCTIONS ###


def initialize_api():
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


def initialize_gmail_client():
    with open('credentials.json', 'r') as f:
        credentials = json.load(f)
    SCOPES = ['https://www.googleapis.com/auth/gmail.compose']
    email = credentials['gmail_email']
    creds = None
    if os.path.exists("gmail_token.pickle"):
        with open("gmail_token.pickle", "rb") as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'gmail_credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open("gmail_token.pickle", "wb") as token:
            pickle.dump(creds, token)
    return build('gmail', 'v1', credentials=creds)


def initialize_twilio_client():
    with open('credentials.json', 'r') as f:
        credentials = json.load(f)
    return Client(credentials['twilio_account_sid'],
                  credentials['twilio_auth_token'])


def send_message(body):
    global gmail_client
    global twilio_client
    global notify_using_twilio
    with open('credentials.json', 'r') as f:
        credentials = json.load(f)

    if notify_using_twilio:
        message = twilio_client.messages.create(
            body=body,
            from_=credentials['from_phone_number'],
            to=credentials['to_phone_number'])
    elif notify_using_native_mail:
        script = """tell application "Mail"
    set newMessage to make new outgoing message with properties {{visible:false, subject:"{}", sender:"{}", content:"{}"}}
    tell newMessage
        make new to recipient with properties {{address:"{}"}}
    end tell
    send newMessage
end tell
tell application "System Events"
    set visible of application process "Mail" to false
end tell
        """
        formatted_script = script.format(
            body, credentials['gmail_email'], body, credentials['gmail_email'])
        p = Popen('/usr/bin/osascript', stdin=PIPE,
                  stdout=PIPE, encoding='utf8')
        p.communicate(formatted_script)
    else:
        message = MIMEText(body)
        message['to'] = credentials['gmail_email']
        message['from'] = credentials['gmail_email']
        message['subject'] = body
        message_obj = {'raw': urlsafe_b64encode(message.as_bytes()).decode()}
        gmail_client.users().messages().send(userId="me", body=message_obj).execute()


def send_heartbeat_message(should_repeat=True):
    global should_text
    while True:
        if should_text:
            send_message("The Surrender Index script is up and running.")
        if not should_repeat:
            break
        time.sleep(60 * 60 * 24)


def send_error_message(e, body="An error occurred"):
    global should_text
    if should_text:
        send_message(body + ": " + str(e) + ".")


def create_delay_of_game_str(play, drive, game, prev_play,
                             unadjusted_surrender_index,
                             unadjusted_current_percentile,
                             unadjusted_historical_percentile):
    new_territory_str = play['start']['possessionText']
    old_territory_str = prev_play['start']['possessionText']

    penalty_str = "*" + get_possessing_team(
        play,
        game) + " committed a (likely intentional) delay of game penalty, "
    old_yrdln_str = "moving the play from " + prev_play['start']['shortDownDistanceText'] + " at the " + prev_play['start'][
            'possessionText']
    new_yrdln_str = " to " + play['start']['shortDownDistanceText'] + " at the " + play['start']['possessionText'] + ".\n\n"
    index_str = "If this penalty was in fact unintentional, the Surrender Index would be " + str(
        round(unadjusted_surrender_index, 2)) + ", "
    percentile_str = "ranking at the " + get_num_str(
        unadjusted_current_percentile) + " percentile of the 2021 season."

    return penalty_str + old_yrdln_str + new_yrdln_str + index_str + percentile_str


def create_tweet_str(play,
                     prev_play,
                     drive,
                     game,
                     surrender_index,
                     current_percentile,
                     historical_percentile,
                     delay_of_game=False):
    territory_str = play['start']['possessionText']
    asterisk = '*' if delay_of_game else ''

    decided_str = get_possessing_team(
        play, game) + ' decided to punt to ' + return_other_team(
            game, get_possessing_team(play, game))
    yrdln_str = ' from the ' + territory_str + asterisk + ' on '
    down_str = play['start']['shortDownDistanceText'] + asterisk
    clock_str = ' with ' + play['clock']['displayValue'] + ' remaining in '
    qtr_str = get_qtr_str(play['period']['number']) + ' while ' + get_score_str(
        prev_play, game) + '.'

    play_str = decided_str + yrdln_str + down_str + clock_str + qtr_str

    surrender_str = 'With a Surrender Index of ' + str(
        round(surrender_index, 2)
    ) + ', this punt ranks at the ' + get_num_str(
        current_percentile
    ) + ' percentile of cowardly punts of the 2021 season, and the ' + get_num_str(
        historical_percentile) + ' percentile of all punts since 1999.'

    return play_str + '\n\n' + surrender_str


def tweet_play(play, prev_play, drive, game, game_id):
    global api
    global ninety_api
    global cancel_api
    global should_tweet

    delay_of_game = is_delay_of_game(play, prev_play)

    if delay_of_game:
        updated_play = play.copy()
        updated_play['start'] = prev_play['start']
        updated_play['end'] = prev_play['end']
        surrender_index = calc_surrender_index(updated_play, prev_play, drive, game)
        current_percentile, historical_percentile = calculate_percentiles(
            surrender_index)
        unadjusted_surrender_index = calc_surrender_index(
            play, prev_play, drive, game)
        unadjusted_current_percentile, unadjusted_historical_percentile = calculate_percentiles(
            unadjusted_surrender_index, should_update_file=False)
        tweet_str = create_tweet_str(updated_play, prev_play, drive, game,
                                     surrender_index, current_percentile,
                                     historical_percentile, delay_of_game)
    else:
        surrender_index = calc_surrender_index(play, prev_play, drive, game)
        current_percentile, historical_percentile = calculate_percentiles(
            surrender_index)
        tweet_str = create_tweet_str(play, prev_play, drive, game,
                                     surrender_index, current_percentile,
                                     historical_percentile, delay_of_game)

    time_print(tweet_str)

    if delay_of_game:
        delay_of_game_str = create_delay_of_game_str(
            play, drive, game, prev_play, unadjusted_surrender_index,
            unadjusted_current_percentile, unadjusted_historical_percentile)
        time_print(delay_of_game_str)

    if should_tweet:
        status = api.update_status(tweet_str)
        if delay_of_game:
            api.update_status(delay_of_game_str,
                              in_reply_to_status_id=status.id_str)

    # Post the status to the 90th percentile account.
    if current_percentile >= 90. and should_tweet:
        ninety_status = ninety_api.update_status(tweet_str)
        if delay_of_game:
            ninety_api.update_status(
                delay_of_game_str, in_reply_to_status_id=ninety_status.id_str)
        thread = threading.Thread(target=handle_cancel,
                                  args=(ninety_status._json, tweet_str))
        thread.start()

    update_tweeted_plays(drive, game_id)


### CANCEL FUNCTIONS ###


def post_reply_poll(link):
    driver = get_twitter_driver(link)

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
    time.sleep(61 * 60)  # Wait one hour and one minute to check reply
    driver = get_game_driver(headless=False)
    driver.get(link)

    time.sleep(3)

    poll_title = driver.find_element_by_xpath("//*[contains(text(), 'votes')]")
    poll_content = poll_title.find_element_by_xpath("./../../../..")
    poll_result = poll_content.find_elements_by_tag_name("span")
    poll_values = [poll_result[2], poll_result[5]]
    poll_floats = list(
        map(lambda x: float(x.get_attribute("innerHTML").strip('%')),
            poll_values))

    driver.close()
    time_print(("checking poll results: ", poll_floats))
    return poll_floats[0] >= 66.67 if len(poll_floats) == 2 else None


def cancel_punt(orig_status, full_text):
    global ninety_api
    global cancel_api

    ninety_api.destroy_status(orig_status['id'])
    cancel_status = cancel_api.update_status(full_text)._json
    new_cancel_text = 'CANCELED https://twitter.com/CancelSurrender/status/' + cancel_status[
        'id_str']

    time.sleep(10)
    ninety_api.update_status(new_cancel_text)


def handle_cancel(orig_status, full_text):
    try:
        orig_link = 'https://twitter.com/surrender_idx90/status/' + orig_status[
            'id_str']
        post_reply_poll(orig_link)
        if check_reply(orig_link):
            cancel_punt(orig_status, full_text)
    except Exception as e:
        traceback.print_exc()
        time_print("An error occurred when trying to handle canceling a tweet")
        time_print(orig_status)
        time_print(e)
        send_error_message(
            e, "An error occurred when trying to handle canceling a tweet")


### CURRENT GAME FUNCTIONS ###


def time_print(message):
    print(get_current_time_str() + ": " + str(message))


def get_current_time_str():
    return datetime.now().strftime("%b %-d at %-I:%M:%S %p")


def get_now():
    return datetime.now(tz=tz.gettz())


def update_current_year_games():
    global current_year_games
    two_months_ago = get_now() - timedelta(days=60)
    scoreboard_urls = espn.get_all_scoreboard_urls("nfl", two_months_ago.year)
    current_year_games = []

    for scoreboard_url in scoreboard_urls:
        data = None
        backoff_time = 1.
        while data is None:
            try:
                data = espn.get_url(scoreboard_url)
            except BaseException:
                time.sleep(backoff_time)
                backoff_time *= 2.
        for event in data['content']['sbData']['events']:
            current_year_games.append(event)


def get_active_game_ids():
    global current_year_games
    global completed_game_ids

    now = get_now()
    active_game_ids = set()

    for game in current_year_games:
        if game['id'] in completed_game_ids:
            # ignore any games that are marked completed (which is done by
            # checking if ESPN says final)
            continue
        game_time = parser.parse(
            game['date']).replace(tzinfo=timezone.utc).astimezone(tz=None)
        if game_time - timedelta(minutes=15) < now and game_time + timedelta(
                hours=6) > now:
            # game should start within 15 minutes and not started more than 6
            # hours ago
            active_game_ids.add(game['id'])

    return active_game_ids

def download_data_for_active_games():
    global games
    active_game_ids = get_active_game_ids()
    if len(active_game_ids) == 0:
        time_print("No games active. Sleeping for 15 minutes...")
        time.sleep(14 * 60)  # We sleep for another minute in the live callback
    games = {}
    for game_id in active_game_ids:
        base_link = "http://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event="
        game_link = base_link + game_id
        games[game_id] = requests.get(game_link).json()
    live_callback()

### MAIN FUNCTIONS ###


def live_callback():
    global games
    start_time = time.time()
    for game_id, game in games.items():
        time_print('Getting data for game ID ' + game_id)
        if 'drives' in game and 'previous' in game['drives']:

            drives = game['drives']['previous']
            for index, drive in enumerate(drives):
                if 'result' not in drive:
                    continue

                drive_plays = drive['plays']
                if len(drive_plays) < 2:
                    continue

                punt = None
                for index, play in enumerate(drive_plays):
                    if index == 0:
                        continue
                    if 'punt' in play['type']['text'].lower():
                        punt = play
                        prev_play = drive_plays[index - 1]

                if not punt:
                    punt = drive_plays[-1]
                    prev_play = drive_plays[-2]

                if not is_punt(drive):
                    continue

                if has_been_tweeted(drive, game_id):
                    continue

                if not has_been_seen(drive, game_id):
                    continue

                tweet_play(punt, prev_play, drive, game, game_id)

            if is_final(game):
                if has_been_final(game_id):
                    completed_game_ids.add(game_id)

        time_print("Done getting data for game ID " + game_id)
    while (time.time() < start_time + 60):
        time.sleep(1)


def main():
    global api
    global ninety_api
    global cancel_api
    global historical_surrender_indices
    global should_text
    global should_tweet
    global notify_using_native_mail
    global notify_using_twilio
    global final_games
    global debug
    global not_headless
    global clean_immediately
    global disable_final_check
    global sleep_time
    global seen_plays
    global penalty_seen_plays
    global gmail_client
    global twilio_client
    global completed_game_ids

    parser = argparse.ArgumentParser(
        description="Run the Surrender Index bot.")
    parser.add_argument('--disableTweeting',
                        action='store_true',
                        dest='disableTweeting')
    parser.add_argument('--disableNotifications',
                        action='store_true',
                        dest='disableNotifications')
    parser.add_argument('--notifyUsingTwilio',
                        action='store_true',
                        dest='notifyUsingTwilio')
    parser.add_argument('--debug', action='store_true', dest='debug')
    parser.add_argument('--notHeadless', action='store_true', dest='notHeadless')
    parser.add_argument('--disableFinalCheck',
                        action='store_true',
                        dest='disableFinalCheck')
    args = parser.parse_args()
    should_tweet = not args.disableTweeting
    should_text = not args.disableNotifications
    notify_using_twilio = args.notifyUsingTwilio
    notify_using_native_mail = sys.platform == "darwin" and not notify_using_twilio
    debug = args.debug
    not_headless = args.notHeadless
    disable_final_check = args.disableFinalCheck

    print("Tweeting Enabled" if should_tweet else "Tweeting Disabled")

    api, ninety_api, cancel_api = initialize_api()
    historical_surrender_indices = load_historical_surrender_indices()
    sleep_time = 1

    clean_immediately = True
    completed_game_ids = set()
    final_games = set()

    should_continue = True
    while should_continue:
        try:
            chromedriver_autoinstaller.install()

            # update current year games and punters at 5 AM every day
            if notify_using_twilio:
                twilio_client = initialize_twilio_client()
            elif not notify_using_native_mail:
                gmail_client = initialize_gmail_client()
            send_heartbeat_message(should_repeat=False)
            update_current_year_games()
            download_punters()
            load_tweeted_plays_dict()
            seen_plays, penalty_seen_plays = {}, {}

            now = get_now()
            if now.hour < 5:
                stop_date = now.replace(hour=5,
                                        minute=0,
                                        second=0,
                                        microsecond=0)
            else:
                now += timedelta(days=1)
                stop_date = now.replace(hour=5,
                                        minute=0,
                                        second=0,
                                        microsecond=0)

            while get_now() < stop_date:
                start_time = time.time()
                download_data_for_active_games()
                clean_immediately = False
                sleep_time = 1.
        except KeyboardInterrupt:
            should_continue = False
        except Exception as e:
            # When an exception occurs: log it, send a message, and sleep for an
            # exponential backoff time
            traceback.print_exc()
            time_print("Error occurred:")
            time_print(e)
            time_print("Sleeping for " + str(sleep_time) + " minutes")
            send_error_message(e)
            time.sleep(sleep_time * 60)
            sleep_time *= 2


if __name__ == "__main__":
    main()
