from datetime import datetime, timezone
from cache import *
from utils import *
from keywords import KEYWORDS

TWEETS_FILEPATH = "tweet-group-misleadingness-dataset/tweet_lean.tsv"
NOTES_OUTPUT_FILEPATH = "tweet-group-misleadingness-dataset/note_lean.tsv"
NOTES_DIRECTORY = "notes-dataset"
TIME_CUTOFF = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
OUTPUT_FILEPATH = "output/output-50.csv"
CACHE_DIRECTORY = "cache"
CACHE_SIZE_PER_POLARITY = 15000
NUM_TWEETS_PER_CONDITION = 50


# Read community notes output tsv
print("Reading notes")
note_output_chunks = []
for chunk in pd.read_csv(NOTES_OUTPUT_FILEPATH, sep="\t", chunksize=10000, dtype={'noteId': str, 'tweetId': str}):
    note_output_chunks.append(chunk[
       (chunk['currentStatus'] == 'CURRENTLY_RATED_HELPFUL') &
       (chunk['noteFactor_fn'].notna()) &
       (
           (chunk['misleadingFactualError'] == 1) |
           (chunk['misleadingMissingImportantContext'] == 1)
       )
   ])
helpful_notes_df = pd.concat(note_output_chunks)

# filtered_notes_df = helpful_notes_df
# filtered_notes_df = filtered_notes_df.groupby('tweetId')['noteFactor_fn'].mean().reset_index()
# filtered_notes_df['noteFactor_abs'] = filtered_notes_df['noteFactor_fn'].abs()
# print(f"Loaded {len(filtered_notes_df)} tweets with helpful notes")

# Filter for recent notes
print("Filtering for recent notes")
helpful_note_ids = set(helpful_notes_df['noteId'])
notes_directory_path = Path(NOTES_DIRECTORY)
notes_files = [f for f in notes_directory_path.iterdir() if f.is_file()]
note_chunks = []
for file in notes_files:
    for chunk in pd.read_csv(file, sep="\t", chunksize=10000, dtype={'noteId': str, 'createdAtMillis': int}):
        note_chunks.append(chunk[chunk['noteId'].isin(helpful_note_ids)])
notes_df = pd.concat(note_chunks)[['noteId', 'createdAtMillis']]
filtered_notes_df = pd.merge(helpful_notes_df, notes_df, how='inner', on='noteId')

time_cutoff_millis = int(TIME_CUTOFF.timestamp() * 1000)
filtered_notes_df = filtered_notes_df[filtered_notes_df['createdAtMillis'] >= time_cutoff_millis]
filtered_notes_df = filtered_notes_df.groupby('tweetId')['noteFactor_fn'].mean().reset_index()
filtered_notes_df['noteFactor_abs'] = filtered_notes_df['noteFactor_fn'].abs()
print(f"Loaded {len(filtered_notes_df)} tweets with helpful notes written after time cutoff")

# Sort df in orders corresponding to different conditions
ids_by_polarity = {
    'negative': filtered_notes_df.sort_values(by='noteFactor_fn')['tweetId'].tolist(),
    'positive': filtered_notes_df.sort_values(by='noteFactor_fn', ascending=False)['tweetId'].tolist(),
    'center': filtered_notes_df.sort_values(by='noteFactor_abs')['tweetId'].tolist()
}
for polarity_condition, tweet_ids in ids_by_polarity.items():
    ids_by_polarity[polarity_condition] = tweet_ids[:CACHE_SIZE_PER_POLARITY]

# Retrieve cached and uncached tweets
tweet_dfs_by_polarity = {}
for polarity_condition, tweet_ids in ids_by_polarity.items():
    print(f"Retrieving tweets with polarity {polarity_condition}")
    tweets_df = get_tweets_from_cache(CACHE_DIRECTORY, tweet_ids)
    tweets_df = pd.merge(tweets_df, filtered_notes_df, how='left', left_on='id', right_on='tweetId')
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
output_df = pd.DataFrame(output_tweets)

# Output to new csv
output_df.to_csv(OUTPUT_FILEPATH, index=False)
print("Success!")
