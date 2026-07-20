"""Flask app for the mock-X study interface (local-first)."""
from __future__ import annotations

import hmac
import html
import os
import re

from flask import Flask, abort, jsonify, request, send_from_directory

from . import db as dbmod
from .profiles import DAYS
from .study_store import Exposure, get_store

# Participant ids come from Prolific (?PROLIFIC_PID=…) through a Qualtrics Web
# Service call. Bound the shape before any store call: it becomes an Azure Table
# RowKey and feeds an OData filter, so anything outside this set is rejected.
_PID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Which parents may embed the interface in an <iframe> (Qualtrics survey).
# Override with DERAD_FRAME_ANCESTORS if the survey is hosted elsewhere.
_FRAME_ANCESTORS = os.environ.get(
    "DERAD_FRAME_ANCESTORS", "'self' https://*.qualtrics.com")


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
        codes = p.get("codes", {})
        links = " ".join(
            f'<a class="c c-{c}" href="/?v={html.escape(codes[c])}" target="_blank" rel="noopener">{_COND_LABEL[c]}</a>'
            for c in dbmod.CONDITIONS if c in codes
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
_DEFAULT_PROFILES = os.path.join(_STUDY, "data", "profiles", "profiles.json")


def create_app(db_path=None):
    db_path = db_path or os.environ.get("MOCKX_DB", _DEFAULT_DB)
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config["MOCKX_DB"] = db_path
    # Read the env-driven gates once at app creation (mirrors MOCKX_DB/profiles).
    app.config["DERAD_SESSION_TOKEN"] = os.environ.get("DERAD_SESSION_TOKEN", "")
    app.config["DERAD_ENABLE_BROWSE"] = (
        os.environ.get("DERAD_ENABLE_BROWSE", "").lower() in ("1", "true", "yes"))

    profiles_path = os.environ.get("DERAD_PROFILES", _DEFAULT_PROFILES)
    if os.path.exists(profiles_path):
        from .pool_loader import load_pool_file
        store = get_store()
        n = load_pool_file(profiles_path, store)   # load_profiles is a no-op if already loaded
        app.logger.info("Loaded %d profiles from %s into %s",
                        n, profiles_path, type(store).__name__)
    else:
        app.logger.warning(
            "Profiles pool file not found at %s — /api/session will find an "
            "empty pool (every claim returns 409)", profiles_path)

    @app.after_request
    def _allow_qualtrics_iframe(resp):
        # Permit embedding inside the Qualtrics survey; frame-ancestors is the
        # modern replacement for X-Frame-Options (which we deliberately omit).
        resp.headers["Content-Security-Policy"] = f"frame-ancestors {_FRAME_ANCESTORS}"
        return resp

    @app.get("/healthz")
    def healthz():
        return "ok", 200

    @app.get("/api/session")
    def api_session():
        """Claim this participant's profile (first call) and return that day's
        opaque codes. Condition is never returned — it stays server-side."""
        # Auth gate: when DERAD_SESSION_TOKEN is configured, every /api/session
        # call must carry a matching ?token=. The Qualtrics Web Service adds it
        # server-side, so the token never reaches participants. Unset → dev mode,
        # no check. Constant-time compare avoids a timing oracle on the token.
        required_token = app.config.get("DERAD_SESSION_TOKEN") or ""
        if required_token and not hmac.compare_digest(
                request.args.get("token", "").encode("utf-8"), required_token.encode("utf-8")):
            return jsonify({"error": "invalid or missing token"}), 401
        pid = request.args.get("pid", "").strip()
        day = request.args.get("day", "").strip()
        raw = request.args.get("party", "").strip().lower()
        party = {"d": "D", "democrat": "D", "r": "R", "republican": "R"}.get(raw)
        if not pid or not day.isdigit():
            return jsonify({"error": "pid and numeric day are required"}), 400
        # Validate pid shape BEFORE any store call: claim_profile() burns a finite
        # slot for a first-time pid, and the pid becomes a Table RowKey / OData
        # filter value. A malformed pid must 400 without cost.
        if not _PID_RE.match(pid):
            return jsonify({"error": "pid must match ^[A-Za-z0-9_-]{1,64}$"}), 400
        if party is None:
            return jsonify({"error": "party must be Democrat/Republican (or D/R)"}), 400
        day_i = int(day)
        # Validate the day range BEFORE claiming: the profile pool is finite, and
        # claim_profile() permanently burns a slot on a first-time pid. A bad day
        # (typo, stale client, URL probing) must 400 without cost.
        if not (1 <= day_i <= DAYS):
            return jsonify({"error": f"day out of range 1..{DAYS}"}), 400
        a = get_store().claim_profile(pid, party)
        if a is None:
            return jsonify({"error": "no profiles available for this party"}), 409
        if not (1 <= day_i <= len(a.blocks)):    # belt-and-braces: pool data should match DAYS
            return jsonify({"error": f"day out of range 1..{len(a.blocks)}"}), 400
        conn = dbmod.connect(app.config["MOCKX_DB"])
        try:
            codes = [dbmod.code_for(conn, post_id, a.condition)
                     for post_id in a.blocks[day_i - 1]]
        finally:
            conn.close()
        return jsonify({"pid": pid, "day": day_i, "codes": codes})

    @app.post("/api/exposure")
    def api_exposure():
        """Record that a participant viewed a thread (idempotent per post)."""
        data = request.get_json(silent=True, force=True) or {}   # force: sendBeacon mimetype varies
        code = (data.get("code") or "").strip()
        pid = (data.get("pid") or "").strip()
        if not code or not pid:
            return jsonify({"error": "code and pid are required"}), 400
        conn = dbmod.connect(app.config["MOCKX_DB"])
        try:
            resolved = dbmod.resolve_code(conn, code)
        finally:
            conn.close()
        if resolved is None:
            return jsonify({"error": "unknown code"}), 404
        post_id, condition = resolved
        try:
            day = int(data.get("day") or 0)
        except (TypeError, ValueError):
            day = 0
        try:
            dwell = int(data.get("dwell_ms") or 0)
        except (TypeError, ValueError):
            dwell = 0
        get_store().log_exposure(Exposure(
            pid=pid, condition=condition, post_id=post_id, code=code,
            day=day, dwell_ms=dwell))
        return jsonify({"ok": True})

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/browse")
    def browse():
        # Operator-only gallery: it enumerates every post across all four
        # conditions, so a participant who found it would see the whole design.
        # Gated behind DERAD_ENABLE_BROWSE; unset (prod default) → 404, which is
        # indistinguishable from an unknown route.
        if not app.config.get("DERAD_ENABLE_BROWSE"):
            abort(404)
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
        # Participant links carry only an opaque code (?v=…) — that path must
        # keep working unchanged, always. The legacy post_id+condition form is
        # a dev/QA convenience that would otherwise serve any condition for a
        # known post_id on a live host, so it's gated behind the same
        # DERAD_ENABLE_BROWSE flag as /browse (404 when disabled).
        conn = dbmod.connect(app.config["MOCKX_DB"])
        try:
            code = request.args.get("v", "")
            if code:
                resolved = dbmod.resolve_code(conn, code)
                if resolved is None:
                    return jsonify({"error": "not found"}), 404
                post_id, condition = resolved
            else:
                if not app.config.get("DERAD_ENABLE_BROWSE"):
                    abort(404)
                post_id = request.args.get("post_id", "")
                condition = request.args.get("condition", "")
                if condition not in dbmod.CONDITIONS:
                    return jsonify({"error": f"invalid condition: {condition!r}"}), 400
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
