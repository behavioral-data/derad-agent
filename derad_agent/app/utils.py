import re

from derad_agent.llm.config import get_x_client
from derad_agent.llm.config import INDEX_ROOT
from derad_agent.runtime import get_notes_index_dir
from derad_agent.runtime.notes_index import load_notes_index

INDEX = load_notes_index(get_notes_index_dir(INDEX_ROOT))


def fetch_tweet_text(tweet_id):
    # Code taken from https://docs.x.com/x-api/posts/get-post-by-id
    response = get_x_client(tone="default").posts.find_by_id(id=str(tweet_id), tweet_fields=["text"])

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

    tweets: list = []
    notes: list = []
    sources = []
    seen = set()
    for reason in (reply.get("reasons") or []):
        if len(sources) >= max_sources:
            break
        tweets.append(reason.get("tweet_id"))
        notes.append(reason.get("note_id"))
        for link in (reason.get("evidence_links") or []):
            if len(sources) >= max_sources:
                break
            if isinstance(link, str) and link.strip() and link.strip() not in seen:
                sources.append(link.strip())
                seen.add(link.strip())

    return {
        "text": text,
        "sources": sources or None,
        "tweets": tweets or None,
        "notes": notes or None
    }

def post_reply(parent_id, reply_text, tone):
    # Code taken from https://docs.x.com/x-api/posts/manage-tweets/quickstart
    response = get_x_client(tone=tone).posts.create(
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

def generate_notes_html(tweet_ids, note_ids):
    notes_html = ""
    for i, (tweet_id, note_id) in enumerate(zip(tweet_ids, note_ids)):
        tweet_notes = INDEX.notes_by_tweet.get(tweet_id, [])
        note_summaries = dict([(note.get("note_id"), note.get("summary")) for note in tweet_notes])

        if note_id in note_summaries:
            note_text = note_summaries.get(note_id)
            note_text = re.sub(r"(https?://[^\s<>\"]+)", r"""<a href="\1">\1</a>""", note_text)
            notes_html += f"""
                <li>
                    <article class="note-body">
                        <h4>Community Note {i + 1}</h4>
                        <p>{note_text}</p>
                        <br>
                        <p>This community note was added by an X user to correct misinformation on <a href="https://twitter.com/username/status/{tweet_id}">this tweet</a>.</p>
                    </article>
                </li> 
            """
    return notes_html
