"""Flask app for the mock-X study interface (local-first)."""
from __future__ import annotations

import html
import os

from flask import Flask, jsonify, request, send_from_directory

from . import db as dbmod


# ── Browse / demo gallery ───────────────────────────────────────────────────
# A researcher-facing index of every post with one-click links into each
# condition's thread. Server-rendered from the DB so it never goes stale.
_COND_LABEL = {"neutral": "Neutral", "agreeable": "Agreeable",
               "satirical": "Satirical", "control": "Note"}

_BROWSE_SHELL = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mock-X study — browse</title>
<style>
 :root{color-scheme:dark}
 body{background:#000;color:#e7e9ea;font:15px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif;margin:0}
 .top{position:sticky;top:0;background:rgba(0,0,0,.85);backdrop-filter:blur(8px);border-bottom:1px solid #2f3336;padding:14px 20px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;z-index:2}
 h1{font-size:19px;margin:0;font-weight:800}
 .sub{color:#71767b;font-size:13px}
 #q{margin-left:auto;background:#16181c;border:1px solid #2f3336;border-radius:999px;color:#e7e9ea;font-size:14px;padding:9px 16px;width:min(320px,42vw);outline:none}
 #q:focus{border-color:#1d9bf0}
 .wrap{max-width:1080px;margin:0 auto;padding:8px 20px 60px}
 table{border-collapse:collapse;width:100%}
 td{border-bottom:1px solid #2f3336;padding:11px 8px;vertical-align:top}
 .n{color:#71767b;width:34px;font-variant-numeric:tabular-nums}
 .dim{color:#71767b}.txt{color:#c9cdd1;margin-top:4px}
 .tag{background:#16181c;border:1px solid #2f3336;border-radius:999px;padding:1px 8px;font-size:11px;margin-left:6px;white-space:nowrap}
 .pol{color:#f491b2}
 .media{margin-left:6px;color:#71767b;font-size:11px;border:1px solid #2f3336;border-radius:4px;padding:1px 6px}
 .links{white-space:nowrap;text-align:right}
 .c{display:inline-block;padding:6px 10px;border-radius:7px;margin:2px;text-decoration:none;font-weight:700;font-size:12px;color:#fff}
 .c-neutral{background:#1d9bf0}.c-agreeable{background:#00ba7c}.c-satirical{background:#7856ff}.c-control{background:#536471}
 .c:hover{filter:brightness(1.12)}
 .empty{padding:40px 8px;color:#71767b;text-align:center}
</style></head><body>
 <div class="top">
   <h1>Mock-X study</h1><span class="sub">{{COUNT}} posts · click a condition to open the thread</span>
   <input id="q" placeholder="Filter by author, text, topic…" autocomplete="off">
 </div>
 <div class="wrap"><table><tbody>
{{ROWS}}
 </tbody></table><div class="empty" id="none" style="display:none">No posts match that filter.</div></div>
 <script>
  var q=document.getElementById('q'),none=document.getElementById('none');
  q.addEventListener('input',function(){var v=q.value.trim().toLowerCase(),shown=0;
   document.querySelectorAll('tbody tr').forEach(function(tr){var m=!v||tr.dataset.search.indexOf(v)>-1;tr.style.display=m?'':'none';if(m)shown++;});
   none.style.display=shown?'none':'';});
 </script>
</body></html>"""


def _render_browse(posts):
    rows = []
    for i, p in enumerate(posts, 1):
        snippet = html.escape(" ".join((p["content"] or "").split())[:120])
        topic = html.escape(p["topic"] or "")
        pol = html.escape(p["polarity"] or "")
        media = f'<span class="media">{html.escape(p["media"])}</span>' if p["media"] else ""
        pid = html.escape(p["post_id"])
        links = " ".join(
            f'<a class="c c-{c}" href="/?post_id={pid}&condition={c}" target="_blank" rel="noopener">{_COND_LABEL[c]}</a>'
            for c in dbmod.CONDITIONS
        )
        search = html.escape(
            f'{p["author_name"]} {p["author_handle"]} {p["content"]} {p["topic"]}'.lower()
        )
        rows.append(
            f'<tr data-search="{search}">'
            f'<td class="n">{i}</td>'
            f'<td><b>{html.escape(p["author_name"])}</b> '
            f'<span class="dim">@{html.escape(p["author_handle"])}</span>'
            f'<span class="tag">{topic}</span><span class="tag pol">{pol}</span> {media}'
            f'<div class="txt">{snippet}</div></td>'
            f'<td class="links">{links}</td></tr>'
        )
    return (_BROWSE_SHELL
            .replace("{{COUNT}}", str(len(posts)))
            .replace("{{ROWS}}", "\n".join(rows)))

_HERE = os.path.dirname(os.path.abspath(__file__))
_STUDY = os.path.dirname(_HERE)
_MEDIA_DIR = os.path.join(_STUDY, "data", "media")
_DEFAULT_DB = os.path.join(_STUDY, "data", "study.db")


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

    @app.get("/browse")
    def browse():
        conn = dbmod.connect(app.config["MOCKX_DB"])
        try:
            posts = dbmod.list_posts(conn)
        finally:
            conn.close()
        return _render_browse(posts)

    @app.get("/media/<path:filename>")
    def media(filename):
        return send_from_directory(_MEDIA_DIR, filename)

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
