from flask import Flask, request, jsonify
import hmac
import hashlib
import base64

from derad_agent.api.utils import post_reply
from derad_agent.utils import get_secret


app = Flask(__name__)

CONSUMER_SECRET = get_secret("consumer_secret")


@app.route("/mention", methods=["GET", "POST"])
def mention():
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

        # TODO: parse webhook payload to extract text of mention's parent post
        # Webhook Payload: https://docs.x.com/x-api/account-activity/introduction#tweet_create_events-@mentions
        # Tweet Object Structure: https://docs.x.com/x-api/fundamentals/data-dictionary#post-tweet (see referenced_tweets field)
        # Get Tweet text from ID: https://docs.x.com/x-api/posts/get-post-by-id
        mention_id = -1
        parent_id = -1
        parent_text = ""

        # TODO: generate reply to given statement (create and call function from ask.py)
        reply = None  # function should return a dict with keys reply_text and sources, where sources is a list of links to community notes

        # TODO: post reply
        reply_id = post_reply(parent_id=mention, reply_text=reply['text'])

        # TODO: post sources
        if reply['sources'] is not None:
            sources_text = f"Sources:\n{reply['sources'].join("\n")}"
            post_reply(parent_id=reply_id, reply_text=f"Sources:\n{sources_text}")

        return "", 200
