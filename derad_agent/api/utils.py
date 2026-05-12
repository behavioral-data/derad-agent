from xdk import Client
from xdk.oauth1_auth import OAuth1

from derad_agent.utils import get_secret


oauth1 = OAuth1(
    api_key=get_secret("x_api_key"),
    api_secret=get_secret("x_api_secret"),
    access_token=get_secret("x_access_token"),
    access_token_secret=get_secret("x_access_token_secret")
)

client = Client(auth=oauth1)


def post_reply(parent_id, reply_text):
    # Code taken from https://docs.x.com/x-api/posts/manage-tweets/quickstart
    response = client.posts.create(
        text=reply_text,
        reply={"in_reply_to_tweet_id": str(parent_id)}
    )

    if response.ok:
        reply_id = response.data.id
        print(f"Created reply: {reply_id}")
        return reply_id
    else:
        print("Failed to create reply")
        print(f"{response.title}: {response.detail}")
        return -1
