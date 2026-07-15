import os
from dotenv import load_dotenv
import requests
from keywords import KEYWORDS, KEYWORDS_CASE_SENSITIVE

load_dotenv()
BEARER_TOKEN = os.getenv("X_BEARER_TOKEN")

NUM_TWEETS_PER_CONDITION = 5
MAX_TRIES = 10000


def get_tweets(tweet_ids):
    url = f"https://api.twitter.com/2/tweets/"
    headers = {
        "Authorization": f"Bearer {BEARER_TOKEN}"
    }

    params = {
        "ids": ",".join(tweet_ids[:100]),
        "tweet.fields": "created_at,text,lang"
    }

    response = requests.get(url, headers=headers, params=params)

    if response.status_code == 200:
        response_json = response.json()
        data = []
        errors = []
        if 'data' in response_json:
            data = response_json['data']
        if 'errors' in response_json:
            errors = response_json['errors']
        return data, errors

    print(f"Error: {response.status_code} - {response.text}")
    return None

def filter_tweet(tweet, topic_condition):
    if tweet['lang'] == 'en':
        for keyword in KEYWORDS[topic_condition]:
            if keyword.casefold() in tweet['text'].casefold():
                return True
        for keyword in KEYWORDS_CASE_SENSITIVE[topic_condition]:
            if keyword in tweet['text']:
                return True
    return False

