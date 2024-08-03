"""
Andrew Shackelford
andrewshackelford97@gmail.com
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
from requests.adapters import HTTPAdapter, Retry
import scipy.stats as stats
from selenium import webdriver
from selenium.webdriver.support.select import Select
from selenium.common.exceptions import StaleElementReferenceException
from subprocess import Popen, PIPE
import sys
import threading
from selenium.webdriver.chrome.service import Service
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
    service = Service()
    options = webdriver.ChromeOptions()
    if headless and not debug and not not_headless:
        options.add_argument("headless")
    return webdriver.Chrome(service=service, options=options)


def get_twitter_driver(link, headless=False):
    with open('credentials.json', 'r') as f:
        credentials = json.load(f)
        email = credentials['cancel_email']
        username = credentials['cancel_username']
        password = credentials['cancel_password']

    driver = get_game_driver(headless=headless)
    driver.implicitly_wait(10)
    driver.get(link)

    driver.find_element("xpath", "//div[@aria-label='Reply']").click()

    time.sleep(1)
    login_button = driver.find_element("xpath", "//a[@href='/i/flow/login']")
    time.sleep(1)
    driver.execute_script("arguments[0].click();", login_button)

    email_field = driver.find_element("xpath",
        "//input[@autocomplete='username']")
    email_field.send_keys(email)
    driver.find_element("xpath", "//span[.='Next']//..//..").click()

    time.sleep(5)
    
    if "phone number or username" in driver.page_source:
        username_field = driver.find_element("xpath", 
            "//input[@name='text']")
        username_field.send_keys(username)
        driver.find_element("xpath", "//span[.='Next']//..//..").click()

    password_field = driver.find_element("xpath",
        "//input[@name='password']")
    password_field.send_keys(password)
    driver.find_element("xpath", "//div[@data-testid='LoginForm_Login_Button']").click()

    time.sleep(1)
    driver.get(link)
    time.sleep(3)

    return driver

def get_post_driver(headless=False):
    with open('credentials.json', 'r') as f:
        credentials = json.load(f)
        email = credentials['email']
        username = credentials['username']
        password = credentials['password']

    driver = get_game_driver(headless=headless)
    driver.implicitly_wait(15)
    driver.get('https://twitter.com/compose/tweet')

    email_field = driver.find_element("xpath",
        "//input[@autocomplete='username']")
    email_field.send_keys(email)
    driver.find_element("xpath", "//span[.='Next']//..//..").click()

    time.sleep(5)

    if "phone number or username" in driver.page_source:
        username_field = driver.find_element("xpath",
            "//input[@name='text']")
        username_field.send_keys(username)
        driver.find_element("xpath", "//span[.='Next']//..//..").click()

    password_field = driver.find_element("xpath",
        "//input[@name='password']")
    password_field.send_keys(password)
    driver.find_element("xpath", "//div[@data-testid='LoginForm_Login_Button']").click()

    return driver

def send_post_webdriver(text):
    try:
        for _ in range(5):
            try:
                driver = get_post_driver()
                time.sleep(2)
                driver.find_element("xpath", "//div[@aria-label='Tweet text']").send_keys(text)
                time.sleep(1)
                driver.find_element("xpath" ,"//div[@data-testid='tweetButton']").click()
                time.sleep(10)
                return
            except BaseException:
                pass
    except Exception as e:
        traceback.print_exc()
        time_print("An error occurred when trying to post a tweet using webdriver")
        time_print(text)
        time_print(e)
        send_error_message(
            e, "An error occurred when trying to post a tweet using webdriver")

### POSSESSION DETERMINATION FUNCTIONS ###


def get_possessing_team(play, game):
    team_id = play.get('start', {}).get('team', {}).get('id')
    if not team_id:
        team_id = play.get('end', {}).get('team', {}).get('id')
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
    competitions = game.get('header', {}).get('competitions', [])
    if len(competitions) > 0:
        return competitions[0].get('status', {}).get(
            'type', {}).get('name') == 'STATUS_FINAL'
    return None


def is_postseason(game):
    return game['header']['season']['type'] > 2

### PLAY FUNCTIONS ###


def is_punt(drive):
    return 'punt' in drive.get('result', '').lower()


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
    return drive.get('id', '') in game_plays


def has_been_seen(drive, game_id):
    global seen_plays
    game_plays = seen_plays.get(game_id, [])
    if drive.get('id', '') in game_plays:
        return True
    game_plays.append(drive.get('id', ''))
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
    with open('1999-2022_surrender_indices.npy', 'rb') as f:
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
    if np.isnan(current_percentile):
        current_percentile = 100.

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

    api = tweepy.Client(
            bearer_token=credentials['bearer_token'],
            consumer_key=credentials['consumer_key'],
            consumer_secret=credentials['consumer_secret'],
            access_token=credentials['access_token'],
            access_token_secret=credentials['access_token_secret']
        )

    ninety_api = tweepy.Client(
            bearer_token=credentials['90_bearer_token'],
            consumer_key=credentials['90_consumer_key'],
            consumer_secret=credentials['90_consumer_secret'],
            access_token=credentials['90_access_token'],
            access_token_secret=credentials['90_access_token_secret']
        )

    cancel_api = tweepy.Client(
            bearer_token=credentials['cancel_bearer_token'],
            consumer_key=credentials['cancel_consumer_key'],
            consumer_secret=credentials['cancel_consumer_secret'],
            access_token=credentials['cancel_access_token'],
            access_token_secret=credentials['cancel_access_token_secret']
        )

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
    old_yrdln_str = "moving the play from " + \
        prev_play['start']['shortDownDistanceText'] + \
        " at the " + prev_play['start']['possessionText']
    new_yrdln_str = " to " + play['start']['shortDownDistanceText'] + \
        " at the " + play['start']['possessionText'] + ".\n\n"
    index_str = "If this penalty was in fact unintentional, the Surrender Index would be " + \
        str(round(unadjusted_surrender_index, 2)) + ", "
    percentile_str = "ranking at the " + get_num_str(
        unadjusted_current_percentile) + " percentile of the 2023 season."

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
    qtr_str = get_qtr_str(play['period']['number']) + \
        ' while ' + get_score_str(prev_play, game) + '.'

    play_str = decided_str + yrdln_str + down_str + clock_str + qtr_str

    surrender_str = 'With a Surrender Index of ' + str(
        round(surrender_index, 2)
    ) + ', this punt ranks at the ' + get_num_str(
        current_percentile
    ) + ' percentile of cowardly punts of the 2023 season, and the ' + get_num_str(
        historical_percentile) + ' percentile of all punts since 1999.'

    return play_str + '\n\n' + surrender_str


def tweet_play(play, prev_play, drive, game, game_id):
    global api
    global ninety_api
    global cancel_api
    global enable_cancel
    global should_tweet
    global enable_main_account

    delay_of_game = is_delay_of_game(play, prev_play)

    if delay_of_game:
        updated_play = play.copy()
        updated_play['start'] = prev_play['start']
        updated_play['end'] = prev_play['end']
        surrender_index = calc_surrender_index(
            updated_play, prev_play, drive, game)
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

    if should_tweet and enable_main_account:
        if not delay_of_game:
            post_thread = threading.Thread(target=send_post_webdriver,
                          args=(tweet_str,))
            post_thread.start()
        else:
            # if delay of game, use the api anyways so that the reply works
            status = api.create_tweet(text=tweet_str)
        if delay_of_game:
            api.create_tweet(text=delay_of_game_str,
                              in_reply_to_tweet_id=status.data['id'])

    # Post the status to the 90th percentile account.
    if current_percentile >= 90. and should_tweet:
        ninety_status = ninety_api.create_tweet(text=tweet_str)
        if delay_of_game:
            ninety_api.create_tweet(
                text=delay_of_game_str, in_reply_to_tweet_id=ninety_status.data['id'])
        if enable_cancel:
            thread = threading.Thread(target=handle_cancel,
                                      args=(ninety_status, tweet_str))
            thread.start()

    update_tweeted_plays(drive, game_id)


### CANCEL FUNCTIONS ###


def post_reply_poll(link):
    for _ in range(5):
        try:
            driver = get_twitter_driver(link)
            break
        except BaseException:
            pass

    driver.find_element("xpath", "//div[@aria-label='Reply']").click()
    driver.find_element("xpath", "//div[@aria-label='Add poll']").click()

    time.sleep(1)

    driver.find_element("name", "Choice1").send_keys("Yes")
    driver.find_element("name", "Choice2").send_keys("No")

    time.sleep(1)
    Select(driver.find_element("xpath", 
        "//span[.='Days']//..//..//select")).select_by_visible_text("0")
    Select(driver.find_element("xpath",
        "//span[.='Hours']//..//..//select")).select_by_visible_text("1")
    Select(driver.find_element("xpath",
        "//span[.='Minutes']//..//..//select")).select_by_visible_text("0")

    time.sleep(1)
    driver.find_element("xpath", "//div[@aria-label='Tweet text']").send_keys(
        "Should this punt's Surrender Index be canceled?")

    time.sleep(1)
    driver.find_element("xpath" ,"//div[@data-testid='tweetButton']").click()

    time.sleep(10)
    driver.close()


def check_reply(link):
    time.sleep(61 * 60)  # Wait one hour and one minute to check reply
    driver = get_game_driver(headless=False)
    driver.implicitly_wait(15)
    driver.get(link)

    time.sleep(3)

    poll_title = driver.find_element("xpath", "//*[contains(text(), 'votes')]")
    poll_content = poll_title.find_element("xpath", "./../../../..")
    poll_result = poll_content.find_elements("tag name", "span")
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

    ninety_api.delete_tweet(orig_status.data['id'])
    cancel_status = cancel_api.create_tweet(text=full_text)

    time.sleep(10)
    ninety_api.create_tweet(text='CANCELED', quote_tweet_id=cancel_status.data['id'])

def poll_using_tweepy(orig_id):
    cancel_api.create_tweet(text="Should this punt's Surrender Index be canceled?",
                            in_reply_to_tweet_id=orig_id,
                            poll_duration_minutes=60,
                            poll_options=["Yes", "No"])

def handle_cancel(orig_status, full_text):
    global reply_using_tweepy
    if reply_using_tweepy:
        poll_using_tweepy(orig_status.data['id'])
        orig_link = 'https://twitter.com/surrender_idx90/status/' + \
            orig_status.data['id']
        if check_reply(orig_link):
            cancel_punt(orig_status, full_text)
        return
    try:
        orig_link = 'https://twitter.com/surrender_idx90/status/' + \
            orig_status.data['id']
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


def update_current_week_games():
    global current_week_games
    current_week_games = []

    espn_data = requests.get(
        "http://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
        timeout=10).json()
    for event in espn_data['events']:
        current_week_games.append(event)


def get_active_game_ids():
    global current_week_games
    global completed_game_ids

    now = get_now()
    active_game_ids = set()

    for game in current_week_games:
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
        games[game_id] = session.get(game_link, timeout=10).json()

    live_callback()

### MAIN FUNCTIONS ###


def live_callback():
    global games
    start_time = time.time()
    for game_id, game in games.items():
        time_print('Getting data for game ID ' + game_id)
        if 'previous' in game.get('drives', {}):

            drives = game['drives']['previous']
            for index, drive in enumerate(drives):
                if 'result' not in drive:
                    continue

                drive_plays = drive.get('plays', [])
                if len(drive_plays) < 2:
                    continue

                if not is_punt(drive):
                    continue

                if has_been_tweeted(drive, game_id):
                    continue

                if not has_been_seen(drive, game_id):
                    continue

                punt = None
                for index, play in enumerate(drive_plays):
                    if index == 0:
                        continue
                    if 'punt' in play.get('type', {}).get('text', '').lower():
                        punt = play
                        prev_play = drive_plays[index - 1]

                if not punt:
                    punt = drive_plays[-1]
                    prev_play = drive_plays[-2]

                try:
                    tweet_play(punt, prev_play, drive, game, game_id)
                except BaseException as e:
                    traceback.print_exc()
                    time_print("Error occurred:")
                    time_print(e)
                    error_str = "Failed to tweet play from drive " + \
                        drive.get('id', '')
                    time_print(error_str)
                    send_error_message(e, error_str)

            if is_final(game):
                if has_been_final(game_id):
                    completed_game_ids.add(game_id)
    while (time.time() < start_time + 30):
        time.sleep(1)
    print("")


def main():
    global api
    global ninety_api
    global cancel_api
    global historical_surrender_indices
    global should_text
    global should_tweet
    global enable_main_account
    global reply_using_tweepy
    global notify_using_native_mail
    global notify_using_twilio
    global final_games
    global debug
    global not_headless
    global enable_cancel
    global sleep_time
    global seen_plays
    global gmail_client
    global twilio_client
    global completed_game_ids
    global session

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
    parser.add_argument(
        '--notHeadless',
        action='store_true',
        dest='notHeadless')
    parser.add_argument('--disableFinalCheck',
                        action='store_true',
                        dest='disableFinalCheck')
    # Enable the main account (has to post via webdriver since more than 50 punts/day)
    parser.add_argument('--enableMainAccount',
                        action='store_true',
                        dest='enableMainAccount')
    # Disable replying using tweepy (and reply via webdriver instead)
    parser.add_argument('--disableTweepyReply',
                        action='store_true',
                        dest='disableTweepyReply')
    # Disable the cancel account
    parser.add_argument('--disableCancel',
                        action='store_true',
                        dest='disableCancel')
    args = parser.parse_args()
    should_tweet = not args.disableTweeting
    should_text = not args.disableNotifications
    enable_main_account = args.enableMainAccount
    reply_using_tweepy = not args.disableTweepyReply
    enable_cancel = not args.disableCancel
    notify_using_twilio = args.notifyUsingTwilio
    notify_using_native_mail = sys.platform == "darwin" and not notify_using_twilio
    debug = args.debug
    not_headless = args.notHeadless

    print("Tweeting Enabled" if should_tweet else "Tweeting Disabled")
    if should_tweet:
        print("Main account enabled" if enable_main_account else "Main account disabled")
        print("Replying using tweepy" if reply_using_tweepy else "Replying using webdriver")

    api, ninety_api, cancel_api = initialize_api()
    historical_surrender_indices = load_historical_surrender_indices()
    sleep_time = 1

    completed_game_ids = set()
    final_games = set()

    should_continue = True
    while should_continue:
        try:
            chromedriver_autoinstaller.install()

            session = requests.Session()
            retries = Retry(total=5,
                            backoff_factor=0.1,
                            status_forcelist=[ 500, 502, 503, 504 ])
            session.mount('http://', HTTPAdapter(max_retries=retries))

            # update current year games at 5 AM every day
            if notify_using_twilio:
                twilio_client = initialize_twilio_client()
            elif not notify_using_native_mail:
                gmail_client = initialize_gmail_client()
            send_heartbeat_message(should_repeat=False)
            update_current_week_games()
            load_tweeted_plays_dict()
            seen_plays = {}

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
