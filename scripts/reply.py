"""Reply to a specific tweet with a fact-check.

Usage:
    python scripts/reply.py --tweet-id <ID> [--tone satirical] [--dry-run]

The script fetches the tweet text, runs the LLM pipeline, and posts a reply.
With --dry-run (or DERAD_DRY_RUN=true) it prints the reply instead of posting.
"""

import argparse
import os
import sys
from pathlib import Path

# Resolve imports from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / "derad_agent" / "llm" / ".env")

from derad_agent.app.utils import fetch_tweet, generate_reply, post_reply
from derad_agent.llm.prompts import RESPONSE_STYLES


def main():
    parser = argparse.ArgumentParser(description="Fact-check a tweet and post a reply.")
    parser.add_argument("--mention-id", required=True,
                        help="ID of the mention tweet (the @bot tweet). "
                             "The bot replies to this and fact-checks its parent.")
    parser.add_argument("--tone", default="neutral", choices=RESPONSE_STYLES)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print reply instead of posting (also set by DERAD_DRY_RUN=true)")
    args = parser.parse_args()

    dry_run = args.dry_run or os.getenv("DERAD_DRY_RUN", "false").lower() == "true"

    # Step 1: fetch the mention tweet to find the parent claim tweet
    print(f"Fetching mention tweet {args.mention_id} ...")
    mention_snap = fetch_tweet(args.mention_id, tone=args.tone)
    if not mention_snap:
        print("ERROR: Could not fetch mention tweet.", file=sys.stderr)
        sys.exit(1)

    # The mention tweet should be a reply — get the parent ID from it
    from derad_agent.app.utils import get_x_client
    from xdk import Client
    # Re-fetch with expansions to get in_reply_to
    try:
        client = get_x_client(tone=args.tone)
        raw = client.posts.get_by_id(
            id=str(args.mention_id),
            tweet_fields=["referenced_tweets"],
        )
        data = getattr(raw, "data", None) or {}
        refs = data.get("referenced_tweets") or [] if isinstance(data, dict) else []
        parent_id = next(
            (r["id"] for r in refs if isinstance(r, dict) and r.get("type") == "replied_to"),
            None,
        )
    except Exception as e:
        print(f"ERROR fetching mention references: {e}", file=sys.stderr)
        sys.exit(1)

    if not parent_id:
        print("ERROR: Mention tweet is not a reply to anything. "
              "Pass a tweet that @-mentions the bot as a reply to a claim tweet.", file=sys.stderr)
        sys.exit(1)

    # Step 2: fetch the parent claim tweet
    print(f"Fetching parent claim tweet {parent_id} ...")
    parent_snap = fetch_tweet(parent_id, tone=args.tone)
    if not parent_snap:
        print("ERROR: Could not fetch parent tweet.", file=sys.stderr)
        sys.exit(1)

    print(f"Claim: {parent_snap.text!r}")
    print(f"Running pipeline (tone={args.tone}) ...")

    # Step 3: run the pipeline on the claim
    reply = generate_reply(
        statement=parent_snap.text,
        tone=args.tone,
        exclude_tweet_id=parent_id,
    )

    print(f"\n--- Reply ---\n{reply['text']}\n")
    if reply.get("sources"):
        print("Sources:", reply["sources"])

    if dry_run:
        print("[dry-run] Not posting.")
        return

    # Step 4: post reply to the MENTION tweet (not the claim)
    print(f"Posting reply to mention {args.mention_id} ...")
    reply_id = post_reply(
        parent_id=args.mention_id,
        reply_text=reply["text"],
        tone=args.tone,
    )
    if reply_id:
        print(f"Posted: https://x.com/i/web/status/{reply_id}")
    else:
        print("ERROR: Post failed. Check logs.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
