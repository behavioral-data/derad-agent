"""Custom OpenTelemetry metrics for derad-agent.

These counters and the latency histogram surface in Application Insights once
``configure_azure_monitor()`` has been called from ``app.py``. When OTel is not
configured (tests, local dev), the no-op meter provider is in effect — every
``.add()`` / ``.record()`` here is a free function call.
"""

from __future__ import annotations

import logging

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
