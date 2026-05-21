"""Collect replies to bot posts and write them to BotReplyReplies for NLP analysis.

Searches for tweets that reply to each bot reply tweet aged 3–4 days old,
then writes a BotReplyReply row for each bystander reply found.

Run once per day after derad-poll-engagement to collect bystander content
at the same 3-day measurement point used for engagement metrics.

Usage:
    derad-collect-replies
"""

import logging
from datetime import datetime

from derad_agent.app.events import BotReplyReply, get_store, in_three_day_window, log_reply_reply, utcnow
from derad_agent.llm.config import get_x_client

logger = logging.getLogger(__name__)

MAX_REPLIES_PER_BOT_TWEET = 100


def _collect_one(
    bot_reply_id: str,
    tone: str,
    mention_id: str | None,
    parent_id: str | None,
) -> int:
    """Fetch replies to bot_reply_id from X and write BotReplyReply rows. Returns count written."""
    now = utcnow()
    written = 0

    # conversation_id equals the root tweet of the thread, which is parent_id
    # (the post the invoker was replying to). We search that conversation and
    # filter in Python for tweets that directly reply to the bot's reply.
    query = f"conversation_id:{parent_id}" if parent_id else f"in_reply_to_tweet_id:{bot_reply_id}"

    try:
        client = get_x_client(tone=tone)
        response = client.tweets.search_recent(
            query=query,
            tweet_fields=["author_id", "public_metrics", "in_reply_to_user_id", "text"],
            expansions=["author_id"],
            user_fields=["username"],
            max_results=MAX_REPLIES_PER_BOT_TWEET,
        )
    except Exception:
        logger.warning("Failed to search replies for bot_reply_id=%s", bot_reply_id)
        return 0

    data = getattr(response, "data", None) or []
    includes = getattr(response, "includes", {}) or {}
    users_by_id = {u["id"]: u for u in (includes.get("users") or [])}

    for tweet in data:
        tweet_dict = tweet if isinstance(tweet, dict) else (tweet.__dict__ if hasattr(tweet, "__dict__") else {})
        direct = tweet_dict.get("in_reply_to_tweet_id") == bot_reply_id
        ref_reply = any(
            r.get("id") == bot_reply_id and r.get("type") == "replied_to"
            for r in (tweet_dict.get("referenced_tweets") or [])
        )
        if not direct and not ref_reply:
            continue

        tweet_id = str(tweet_dict.get("id", ""))
        author_id = str(tweet_dict.get("author_id", ""))
        text = tweet_dict.get("text", "")
        metrics_data = tweet_dict.get("public_metrics") or {}
        user = users_by_id.get(author_id, {})
        username = user.get("username") if isinstance(user, dict) else None

        if not tweet_id or not author_id or not text:
            continue

        reply = BotReplyReply(
            bot_reply_id=bot_reply_id,
            reply_tweet_id=tweet_id,
            author_id=author_id,
            text=text,
            collected_at_utc=now,
            author_username=username,
            like_count=metrics_data.get("like_count", 0),
            mention_id=mention_id,
            tone=tone,
        )
        log_reply_reply(reply)
        written += 1

    return written


def main() -> None:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")

    store = get_store()
    now = utcnow()

    candidates = [
        (reply_id, tone, mention_id, parent_id)
        for reply_id, tone, posted_at, mention_id, parent_id in store.iter_reply_ids()
        if in_three_day_window(posted_at, now)
    ]

    if not candidates:
        logger.info("No bot replies in the 3-day window — nothing to collect")
        return

    logger.info("Collecting replies for %d bot tweets", len(candidates))
    total = 0
    for reply_id, tone, mention_id, parent_id in candidates:
        n = _collect_one(reply_id, tone, mention_id, parent_id)
        logger.info("bot_reply_id=%s: collected %d bystander replies", reply_id, n)
        total += n

    logger.info("Done — collected %d bystander replies total", total)
