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


def fetch_tweet_text(tweet_id):
    # Code taken from https://docs.x.com/x-api/posts/get-post-by-id
    response = get_x_client().posts.find_by_id(id=str(tweet_id), tweet_fields=["text"])

    if response.ok:
        return response.data.text
    else:
        print(f"Failed to fetch tweet {tweet_id}")
        print(f"{response.title}: {response.detail}")
        return None


def generate_reply(statement, tone, exclude_tweet_id=None, max_sources=5):
    from derad_agent.runtime.landscape_api import retrieve_statement_landscape

    kwargs = {
        "statement": statement,
        "style": tone
    }
    if exclude_tweet_id is not None:
        kwargs["exclude_tweet_id"] = str(exclude_tweet_id)

    res = retrieve_statement_landscape(**kwargs)
    reply = res.get("reply") or {}
    text = (reply.get("response") or "").strip()

    sources = []
    seen = set()
    for reason in (reply.get("reasons") or []):
        if len(sources) >= max_sources:
            break
        for link in (reason.get("evidence_links") or []):
            if len(sources) >= max_sources:
                break
            if isinstance(link, str) and link.strip() and link.strip() not in seen:
                sources.append(link.strip())
                seen.add(link.strip())

    return {"text": text, "sources": sources or None}
