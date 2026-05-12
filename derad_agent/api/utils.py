from derad_agent.llm.config import get_x_client


def post_reply(parent_id, reply_text):
    # Code taken from https://docs.x.com/x-api/posts/manage-tweets/quickstart
    response = get_x_client().posts.create(
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
