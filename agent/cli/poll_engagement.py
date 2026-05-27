"""Poll X public metrics for bot reply tweets and write EngagementSnapshots.

Designed for a twice-daily cron (00:00 + 12:00 UTC). Each run polls every bot
reply still inside its 10-day measurement window, as long as ~12 h have passed
since that reply's last poll. This yields ≈20 snapshots per reply over 10 days,
all retained — a cumulative time series of engagement. Missed or delayed runs
are safe: a reply is simply polled on the next run once the gap reopens.

Usage:
    derad-poll-engagement
"""

import logging
from datetime import datetime, timedelta, timezone

from agent.app.events import EngagementSnapshot, get_store, log_engagement_snapshot
from agent.llm.config import get_x_client

logger = logging.getLogger(__name__)

# Measurement window per bot reply: poll from creation up to this age.
POLL_MAX_AGE = timedelta(days=10)
# Minimum spacing between polls of the same reply. Slightly under 12 h so a
# cron firing a little early still counts; guards against duplicate snapshots
# from off-schedule or manual reruns.
MIN_POLL_GAP = timedelta(hours=11)


def _poll_one(
    reply_id: str, tone: str, mention_id: str | None = None, parent_id: str | None = None
) -> None:
    try:
        response = get_x_client().posts.get_by_id(
            id=reply_id,
            tweet_fields=["public_metrics"],
        )
    except Exception:
        logger.warning(
            "Failed to fetch metrics for reply %s (tone=%s)", reply_id, tone, exc_info=True
        )
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
    last_polled = store.latest_snapshot_times()

    candidates = []
    for reply_id, tone, posted_at, mention_id, parent_id in store.iter_reply_ids():
        if posted_at is None:
            continue
        posted = posted_at if posted_at.tzinfo else posted_at.replace(tzinfo=timezone.utc)
        if now - posted >= POLL_MAX_AGE:
            continue  # past the 10-day measurement window
        last = last_polled.get(reply_id)
        if last is not None:
            last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
            if now - last < MIN_POLL_GAP:
                continue  # polled within the last ~12 h — wait for the next slot
        candidates.append((reply_id, tone, mention_id, parent_id))

    if not candidates:
        logger.info("No bot replies due for an engagement poll (window=10d, gap=12h)")
        return
    logger.info("Polling engagement for %d replies", len(candidates))
    for reply_id, tone, mention_id, parent_id in candidates:
        _poll_one(reply_id, tone, mention_id=mention_id, parent_id=parent_id)
    logger.info("Done")
