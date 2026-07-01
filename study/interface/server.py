"""Flask app for the mock-X study interface (local-first)."""
from __future__ import annotations

import os

from flask import Flask, jsonify, request

from . import db as dbmod

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DB = os.path.join(_HERE, "study.db")


def create_app(db_path=None):
    db_path = db_path or os.environ.get("MOCKX_DB", _DEFAULT_DB)
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config["MOCKX_DB"] = db_path

    @app.get("/healthz")
    def healthz():
        return "ok", 200

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/thread")
    def api_thread():
        post_id = request.args.get("post_id", "")
        condition = request.args.get("condition", "")
        if condition not in dbmod.CONDITIONS:
            return jsonify({"error": f"invalid condition: {condition!r}"}), 400
        conn = dbmod.connect(app.config["MOCKX_DB"])
        try:
            thread = dbmod.get_thread(conn, post_id, condition)
        finally:
            conn.close()
        if thread is None:
            return jsonify({"error": "post not found"}), 404
        # Strip fields that would reveal study-design details in the browser Network tab.
        for field in ("condition", "is_stub", "note_classification", "source_note_id"):
            thread["intervention"].pop(field, None)
        for field in ("polarity_condition", "topic_condition"):
            thread["post"].pop(field, None)
        return jsonify(thread)

    return app


def main():
    create_app().run(host="127.0.0.1", port=8000, debug=True)


if __name__ == "__main__":
    main()
