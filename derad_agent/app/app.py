from flask import Flask, request, jsonify, url_for, render_template
import hmac
import hashlib
import base64

from derad_agent.app.utils import post_reply, fetch_tweet_text, generate_reply, generate_notes_html
from derad_agent.llm.config import _require_env

app = Flask(__name__)

CONSUMER_SECRET = _require_env("X_API_SECRET")


@app.route("/mention-agreeable", methods=["GET", "POST"])
def mention_agreeable():
    return mention("agreeable")

@app.route("/mention-neutral", methods=["GET", "POST"])
def mention_neutral():
    return mention("neutral")

@app.route("/mention-satirical", methods=["GET", "POST"])
def mention_satirical():
    return mention("satirical")

def mention(tone):
    # Code taken from https://docs.x.com/x-api/webhooks/quickstart
    if request.method == "GET":
        # Handle CRC check
        crc_token = request.args.get("crc_token")
        if crc_token:
            sha256_hash = hmac.new(
                CONSUMER_SECRET.encode("utf-8"),
                crc_token.encode("utf-8"),
                hashlib.sha256,
            ).digest()
            response_token = "sha256=" + base64.b64encode(sha256_hash).decode("utf-8")
            return jsonify({"response_token": response_token}), 200
        return "Missing crc_token", 400

    elif request.method == "POST":
        # Handle incoming webhook events
        event = request.get_json()
        print("Received event:", event)

        # Parse webhook payload
        # Webhook Payload: https://docs.x.com/x-api/account-activity/introduction#tweet_create_events-@mentions
        tweet_events = event.get("tweet_create_events", None)
        if not tweet_events:
            return "", 200

        tweet = tweet_events[0]
        mention_id = tweet.get("id_str")
        parent_id = tweet.get("in_reply_to_status_id_str")

        if not parent_id:
            # Bot was @mentioned directly with no OP post to fact-check
            return "", 200

        # Fetch text of the parent post (the claim being fact-checked)
        # Tweet Object Structure: https://docs.x.com/x-api/fundamentals/data-dictionary#post-tweet
        # Get Tweet text from ID: https://docs.x.com/x-api/posts/get-post-by-id
        parent_text = fetch_tweet_text(parent_id)
        if not parent_text:
            return "", 200

        # TODO (Aahan): return early if mention_id is in database already (duplicate events)
        # TODO (Aahan): rate limit if author_id has posted 3<= unique mention_ids in one second
        # TODO (Aahan): record mention_id, parent_id, author_id, mention_post_time

        # TODO (Trisha): record engagement with parent post (likes, reposts, replies)

        # Run Community Notes pipeline to generate a grounded reply
        reply = generate_reply(statement=parent_text, exclude_tweet_id=parent_id, tone=tone)

        # Post the reply to the mention
        reply_id = post_reply(parent_id=mention_id, reply_text=reply["text"], tone=tone)

        # Post sources as a follow-up thread reply
        if reply["sources"] is not None and reply_id > 0:
            sources_text = "Sources:\n" + "\n".join(reply["sources"])
            post_reply(parent_id=reply_id, reply_text=sources_text, tone=tone)
            info_url = url_for(
                "info",
                reply_id=reply_id,
                tweet_id=reply["tweets"],
                note_id=reply["notes"]
            )
            sources_text += f"\nMore Info: {info_url}"

        # TODO (Trisha): send survey via DMs
        # TODO (Trisha): queue job to measure engagement with bot reply in 3 days

        return "", 200

@app.route("/info", methods=["GET"])
def info():
    reply_id = request.args.get("reply_id")
    tweet_ids = request.args.getlist("tweet_id")
    note_ids = request.args.getlist("note_id")

    reply_html = f"""
        <blockquote class="twitter-tweet">
            <a href="https://twitter.com/username/status/{reply_id}"></a> 
        </blockquote>
    """
    notes_html = generate_notes_html(tweet_ids, note_ids)

    return render_template("info.html", reply=reply_html, notes=notes_html), 200
