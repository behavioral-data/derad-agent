"""Guard against acting on bot-thread continuations (see _is_bot_thread_continuation).

X auto-prepends @-mentions of thread participants, so a reply inside a bot
thread silently mentions the bot and arrives as a "mention". Acting on those
makes the bot reply to the replier / to a reply instead of the original
invocation. The guard keys off the PARENT (the post we'd fact-check):
  - parent authored by the bot  → someone replied to the bot
  - parent text mentions the bot → someone replied to an invocation
A genuine invocation replies to a third-party post that is neither.
"""
from __future__ import annotations

import os

os.environ.setdefault("X_API_KEY", "test_consumer_key")
os.environ.setdefault("X_API_SECRET", "test_consumer_secret_abc")
os.environ.setdefault("SERVER_NAME", "test.local")
os.environ.setdefault("BOT_USER_ID", "999")
os.environ.setdefault("BOT_HANDLE", "eddiexbot")

from agent.app import app as app_module  # noqa: E402


def _cont(parent_author_id, parent_text):
    return app_module._is_bot_thread_continuation(parent_author_id, parent_text)


def test_genuine_invocation_is_not_continuation():
    # Parent is a third-party post: not the bot, doesn't mention the bot.
    assert _cont("12345", "Vaccines cause autism, the data is clear.") is False


def test_reply_to_bot_is_continuation():
    # Parent authored by the bot (someone replied to the bot's tweet).
    assert _cont(app_module.BOT_USER_ID, "AP News and Reuters both report otherwise.") is True


def test_reply_to_invocation_is_continuation():
    # Parent is an invocation: a third-party author, but its text tags the bot
    # (X auto-prepends it), so a reply to it arrives as a mention.
    assert _cont("67890", "@eddiexbot provide context on this") is True


def test_bot_handle_match_is_case_insensitive():
    assert _cont("67890", "@EddieXBot is this true?") is True


def test_unrelated_at_mention_is_not_continuation():
    # A parent that @-mentions someone else (not the bot) is still a valid target.
    assert _cont("67890", "@SomeoneElse this claim is false") is False
