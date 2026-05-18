"""Custom OpenTelemetry metrics for derad-agent.

These counters and the latency histogram surface in Application Insights once
``configure_azure_monitor()`` has been called from ``app.py``. When OTel is not
configured (tests, local dev), the no-op meter provider is in effect — every
``.add()`` / ``.record()`` here is a free function call.

Also implements the daily mention cap (kill switch): an in-memory per-tone
counter that resets on UTC date rollover. Lose-on-restart is acceptable for a
research bot; the goal is to catch a runaway loop, not perfect accounting.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import date, datetime, timezone
from typing import Optional

from opentelemetry import metrics

logger = logging.getLogger(__name__)

_meter = metrics.get_meter("derad-agent")

mentions_received = _meter.create_counter(
    "mentions_received",
    description="Webhook POST requests that passed signature verification.",
    unit="1",
)
mentions_accepted = _meter.create_counter(
    "mentions_accepted",
    description="Mentions that cleared every guard and were dispatched to the pipeline.",
    unit="1",
)
mentions_dropped = _meter.create_counter(
    "mentions_dropped",
    description="Mentions dropped at a guard; the `reason` attribute carries which one.",
    unit="1",
)
replies_posted = _meter.create_counter(
    "replies_posted",
    description="Pipeline terminal outcomes; the `outcome` attribute carries which one.",
    unit="1",
)
pipeline_latency_ms = _meter.create_histogram(
    "pipeline_latency_ms",
    description="End-to-end pipeline duration in ms; tagged by tone + outcome.",
    unit="ms",
)


# ── Mention-rate kill switch ────────────────────────────────────────────────

_MAX_PER_DAY = int(os.getenv("DERAD_MAX_MENTIONS_PER_DAY", "0"))  # 0 = no cap
_counts: dict[str, int] = {}
_count_day: Optional[date] = None
_count_lock = threading.Lock()


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def daily_cap_reached(tone: str) -> bool:
    """Return True if this tone has hit the daily mention cap.

    Increments the in-memory counter for the day as a side effect. Reset on
    UTC date rollover. Cap of 0 (default) disables the check entirely.
    """
    if _MAX_PER_DAY <= 0:
        return False
    today = _utc_today()
    with _count_lock:
        global _count_day
        if _count_day != today:
            _counts.clear()
            _count_day = today
        _counts[tone] = _counts.get(tone, 0) + 1
        if _counts[tone] > _MAX_PER_DAY:
            logger.warning(
                "Daily cap (%d) reached for tone=%s — dropping further mentions until UTC rollover",
                _MAX_PER_DAY, tone,
            )
            return True
        return False


def _reset_counts_for_test() -> None:
    """Test hook: reset the in-memory counter."""
    global _count_day
    with _count_lock:
        _counts.clear()
        _count_day = None
