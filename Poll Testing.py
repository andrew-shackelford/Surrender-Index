#!/usr/bin/env python
# coding: utf-8

# In[1]:


from selenium import webdriver
from selenium.webdriver.support.select import Select
import tweepy
import json
import random
import time
import threading
import sys


# In[2]:


def get_driver():
    with open('credentials.json', 'r') as f:
        credentials = json.load(f)
        username = credentials['cancel_username']
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


# In[3]:


def get_cancel_api():
    with open('credentials.json', 'r') as f:
        credentials = json.load(f)
    auth = tweepy.OAuthHandler(
        credentials['cancel_consumer_key'], credentials['cancel_consumer_secret'])
    auth.set_access_token(
        credentials['cancel_access_token'], credentials['cancel_access_token_secret'])
    return tweepy.API(auth)


# In[4]:


def post_test_tweet():
    cancel_api = get_cancel_api()
    return cancel_api.update_status("this is a test tweet " + str(random.random()))._json
    return 'https://twitter.com/CancelSurrender/status/' + status._json['id_str']


# In[5]:


def post_cancel_tweet(text):
    cancel_api = get_cancel_api()
    return cancel_api.update_status(text)


# In[6]:


def post_reply_poll(link):
    driver = get_driver()
    driver.get(link)
    
    driver.find_element_by_xpath("//div[@aria-label='Reply']").click()
    driver.find_element_by_xpath("//div[@aria-label='Add poll']").click()
    
    driver.find_element_by_name("Choice1").send_keys("Yes")
    driver.find_element_by_name("Choice2").send_keys("No")
    Select(driver.find_element_by_xpath("//select[@aria-label='Days']")).select_by_visible_text("0")
    Select(driver.find_element_by_xpath("//select[@aria-label='Hours']")).select_by_visible_text("0")
    Select(driver.find_element_by_xpath("//select[@aria-label='Minutes']")).select_by_visible_text("5")
    driver.find_element_by_xpath("//div[@aria-label='Tweet text']").send_keys("Should this punt's Surrender Index be canceled?")
    driver.find_element_by_xpath("//div[@data-testid='tweetButton']").click()
    
    time.sleep(5)
    driver.close()


# In[7]:


def check_reply(link):
    time.sleep(5 * 60)
    driver = get_driver()
    driver.get(link)
    
    poll_title = driver.find_element_by_xpath("//*[contains(text(), 'Should this punt')]")
    tweet_content = poll_title.find_element_by_xpath("./..").find_element_by_xpath("./..")
    poll_result = tweet_content.find_elements_by_xpath("div")[3]
    poll_values = poll_result.find_elements_by_tag_name("span")
    poll_integers = []
    for ele in poll_values:
        poll_integers.append(int(ele.get_attribute("innerHTML").strip('%')))
    if len(poll_integers) != 2:
        driver.close()
        return None
    else:
        driver.close()
        return poll_integers[0] > poll_integers[1]


# In[8]:


def cancel_punt(orig_status):
    cancel_api = get_cancel_api()
    cancel_api.destroy_status(orig_status['id'])
    cancel_status = cancel_api.update_status(orig_status['text'] + "new tweet")._json
    new_cancel_text = "CANCELED " + 'https://twitter.com/CancelSurrender/status/' + cancel_status['id_str']
    cancel_api.update_status(new_cancel_text)    


# In[9]:


def main():
    orig_status = post_test_tweet()
    orig_link = 'https://twitter.com/CancelSurrender/status/' + orig_status['id_str']
    time.sleep(1)
    post_reply_poll(orig_link)
    result = check_reply(orig_link)
    print(result)
    if result:
        time.sleep(10)
        cancel_punt(orig_status)


# In[10]:

if __name__ == "__main__":
	main()

