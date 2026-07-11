from cache import *
from utils import *
from keywords import KEYWORDS

INPUT_FILEPATH = "tweet-group-misleadingness-dataset/tweet_lean.tsv"
OUTPUT_FILEPATH = "output/output-20.csv"
NOTES_DIRECTORY = "notes-dataset"
CACHE_DIRECTORY = "cache"
CACHE_SIZE_PER_POLARITY = 15000
NUM_TWEETS_PER_CONDITION = 20


# Read community notes output tsv
misleading_tweet_chunks = []
for chunk in pd.read_csv(INPUT_FILEPATH, sep="\t", chunksize=10000, dtype={"tweetId": str}):
    misleading_tweet_chunks.append(chunk[chunk['communityFlagged'] == True])
flagged_notes_df = pd.concat(misleading_tweet_chunks)
print(f"Loaded {len(flagged_notes_df)} misleading tweets")
flagged_notes_df['polarity_abs'] = flagged_notes_df['polarity'].abs()

# Sort df in orders corresponding to different conditions
ids_by_polarity = {
    'negative': flagged_notes_df.sort_values(by='polarity')['tweetId'].tolist()[:CACHE_SIZE_PER_POLARITY],
    'positive': flagged_notes_df.sort_values(by='polarity', ascending=False)['tweetId'].tolist()[:CACHE_SIZE_PER_POLARITY],
    'center': flagged_notes_df.sort_values(by='polarity_abs')['tweetId'].tolist()[:CACHE_SIZE_PER_POLARITY]
}

# Retrieve cached and uncached tweets
tweet_dfs_by_polarity = {}
for polarity_condition, tweet_ids in ids_by_polarity.items():
    print(f"Retrieving tweets with polarity {polarity_condition}")
    tweets_df = get_tweets_from_cache(CACHE_DIRECTORY, tweet_ids)
    tweets_df = pd.merge(tweets_df, flagged_notes_df, how='left', left_on='id', right_on='tweetId')
    tweet_dfs_by_polarity[polarity_condition] = tweets_df

# Get tweets for each condition
output_tweets = []
for topic_condition in KEYWORDS:
    print(f"Searching for tweets on topic {topic_condition}")
    for polarity_condition, df in tweet_dfs_by_polarity.items():
        print(f"Searching for tweets with note polarity {polarity_condition}")
        i, j = 0, 0
        while j < NUM_TWEETS_PER_CONDITION and i < len(df):
            tweet = df.iloc[i].to_dict()
            if filter_tweet(tweet, topic_condition):
                tweet['polarity_condition'] = polarity_condition
                tweet['topic_condition'] = topic_condition
                output_tweets.append(tweet)
                j += 1
            i += 1
        if i >= len(df):
            print("Reached end of retrieved tweets, terminated early")
output_tweets_df = pd.DataFrame(output_tweets)
output_tweet_ids = set([tweet['id'] for tweet in output_tweets])

# print("Joining with note data")
# notes_directory_path = Path(NOTES_DIRECTORY)
# notes_files = [f for f in notes_directory_path.iterdir() if f.is_file()]
# note_chunks = []
# for file in notes_files:
#     for chunk in pd.read_csv(file, sep="\t", chunksize=10000, dtype={"tweetId": str}):
#         note_chunks.append(chunk[chunk['tweetId'].isin(output_tweet_ids)])
# notes_df = pd.concat(note_chunks)

# Output to new csv
# output_df = pd.merge(output_tweets_df, notes_df, how='left', left_on='id', right_on='tweetId')
output_df = output_tweets_df
output_df.to_csv(OUTPUT_FILEPATH, index=False)
print("Success!")
