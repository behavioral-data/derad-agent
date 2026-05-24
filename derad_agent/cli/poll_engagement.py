"""Poll X public metrics for bot reply tweets and write EngagementSnapshots.

Run on a schedule (e.g. daily cron) to track likes/retweets over time.
Each run appends a new snapshot row per reply so you can see engagement grow.

Usage:
    derad-poll-engagement
"""

import logging
from datetime import datetime, timezone

from derad_agent.app.events import EngagementSnapshot, get_store, in_three_day_window, log_engagement_snapshot
from derad_agent.llm.config import get_x_client

logger = logging.getLogger(__name__)


def _poll_one(
    reply_id: str, tone: str, mention_id: str | None = None, parent_id: str | None = None
) -> None:
    try:
        response = get_x_client(tone=tone).posts.get_by_id(
            id=reply_id,
            tweet_fields=["public_metrics"],
        )
    except Exception:
        logger.warning("Failed to fetch metrics for reply %s (tone=%s)", reply_id, tone)
        return

    data = getattr(response, "data", None) or {}
    if not isinstance(data, dict):
        logger.warning("Unexpected data shape for reply %s", reply_id)
        return

    m = data.get("public_metrics") or {}
    snap = EngagementSnapshot(
        reply_id=reply_id,
        tone=tone,
        polled_at_utc=datetime.now(timezone.utc),
        like_count=m.get("like_count", 0),
        retweet_count=m.get("retweet_count", 0),
        reply_count=m.get("reply_count", 0),
        quote_count=m.get("quote_count", 0),
        mention_id=mention_id,
        parent_id=parent_id,
    )
    log_engagement_snapshot(snap)
    logger.info(
        "reply=%s tone=%s likes=%d retweets=%d replies=%d quotes=%d",
        reply_id, tone, snap.like_count, snap.retweet_count, snap.reply_count, snap.quote_count,
    )


def main() -> None:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
    store = get_store()
    now = datetime.now(timezone.utc)
    candidates = [
        (reply_id, tone, mention_id, parent_id)
        for reply_id, tone, posted_at, mention_id, parent_id in store.iter_reply_ids()
        if in_three_day_window(posted_at, now)
    ]
    if not candidates:
        logger.info("No reply IDs found in the 3-day measurement window — nothing to poll")
        return
    logger.info("Polling 3-day engagement for %d replies", len(candidates))
    for reply_id, tone, mention_id, parent_id in candidates:
        _poll_one(reply_id, tone, mention_id=mention_id, parent_id=parent_id)
    logger.info("Done")
