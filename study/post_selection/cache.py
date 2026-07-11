import pandas as pd
from pathlib import Path
from utils import *


def get_uncached_tweets(tweet_ids, cached_tweet_ids):
    tweets, errors = [], []
    i = 0
    while i < len(tweet_ids):
        # Collect all uncached tweet IDs
        batch = []
        while len(batch) < 100 and i < len(tweet_ids):
            if tweet_ids[i] not in cached_tweet_ids:
                batch.append(tweet_ids[i])
            i += 1
        # Fetch tweets in batches of 100
        if len(batch) > 0:
            response_tweets, response_errors = get_tweets(batch)
            if response_tweets is not None:
                tweets.extend(response_tweets)
                errors.extend(response_errors)

    print(f"Retrieved {len(tweets)} uncached tweets")
    return pd.DataFrame(tweets), pd.DataFrame(errors)

def get_tweets_from_cache(cache_directory, tweet_ids):
    cache_directory_path = Path(cache_directory)
    cache_filepath = cache_directory_path / "cache.csv"
    error_cache_filepath = cache_directory_path / "error.csv"
    if cache_filepath.exists() and error_cache_filepath.exists():
        # Load tweet and error caches
        cache_df = pd.read_csv(cache_filepath, dtype={'id': str})
        cached_tweet_ids = set(cache_df['id'])
        print(f"Loaded {len(cached_tweet_ids)} unique cached tweets")

        error_cache_df = pd.read_csv(error_cache_filepath, dtype={'value': str})
        error_tweet_ids = set(error_cache_df['value'])
        print(f"Loaded {len(error_tweet_ids)} known errors")

        # Retrieve uncached and cached tweets
        uncached_tweets_df, errors_df = get_uncached_tweets(tweet_ids, cached_tweet_ids | error_tweet_ids)
        cached_tweets_df = cache_df[cache_df['id'].isin(tweet_ids)]
        tweets_df = pd.concat([cached_tweets_df, uncached_tweets_df])

        # Reorder tweets_df to same order as tweet_ids
        retrieved_tweet_ids = set(tweets_df['id'].tolist())
        retrieved_tweet_ids_ordered = [id for id in tweet_ids if id in retrieved_tweet_ids]  # ensure that tweets are returned in same order as tweet_ids
        tweets_df = tweets_df.set_index('id').loc[retrieved_tweet_ids_ordered].reset_index()

        new_cache = pd.concat([cache_df, uncached_tweets_df], ignore_index=True)
        new_error_cache = pd.concat([error_cache_df, errors_df], ignore_index=True)
    else:
        cached_tweet_ids = set()
        tweets_df, errors_df = get_uncached_tweets(tweet_ids, cached_tweet_ids)
        new_cache = tweets_df
        new_error_cache = errors_df

    new_cache.to_csv(cache_filepath, index=False)
    new_error_cache.to_csv(error_cache_filepath, index=False)
    return tweets_df
