// X thread renderer. Renders one post + one reply card or context card
// based on URL params (?post_id=&condition=). Aims for exact visual fidelity
// with real x.com (dark, desktop). Stored text is already entity-decoded by the
// build step, so escapeHtml here is purely XSS-safety before we inject markup.

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// X number formatting: full comma-grouped numbers below 10,000; abbreviated
// (truncated, not rounded) K/M above. e.g. 4271 -> "4,271", 15999 -> "15.9K",
// 137100 -> "137K", 1_250_000 -> "1.2M". No trailing ".0".
function fmtNum(n) {
  n = Number(n) || 0;
  if (n < 10000) return n.toLocaleString("en-US");
  if (n < 1e6) {
    const k = n / 1000;
    const s = k < 100 ? Math.floor(k * 10) / 10 : Math.floor(k);
    return String(s).replace(/\.0$/, "") + "K";
  }
  const m = Math.floor(n / 1e5) / 10;
  return String(m).replace(/\.0$/, "") + "M";
}

function hashStr(s) {
  let h = 0;
  s = String(s);
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h;
}

function formatTimeFullDate(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }) +
    " · " + d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

// X-style relative timestamp: "45s" / "12m" / "6h" / "3d", then "Mon D"
// (adding ", YYYY" only for a different calendar year).
function relTime(date) {
  const now = new Date();
  const s = Math.max(0, Math.floor((now - date) / 1000));
  if (s < 60) return s + "s";
  const m = Math.floor(s / 60);
  if (m < 60) return m + "m";
  const h = Math.floor(m / 60);
  if (h < 24) return h + "h";
  const d = Math.floor(h / 24);
  if (d < 7) return d + "d";
  const sameYear = date.getFullYear() === now.getFullYear();
  return date.toLocaleDateString("en-US",
    sameYear ? { month: "short", day: "numeric" }
             : { month: "short", day: "numeric", year: "numeric" });
}

// Shorten a URL for display the way X does: drop scheme + "www.", ellipsize a long tail.
function shortenUrl(u) {
  let d = u.replace(/^https?:\/\//i, "").replace(/^www\./i, "");
  if (d.length > 28) d = d.slice(0, 27) + "…";
  return d;
}

// Turn plain (already HTML-escaped) text into X-style markup: URLs become blue
// truncated links; @mentions and #hashtags become blue tokens. Input MUST be
// escaped first so this only ever introduces our own anchors.
function linkify(escaped, opts = {}) {
  let s = escaped.replace(/(https?:\/\/[^\s<]+)/g, (u) =>
    `<a class="tweet-link" href="${u}" target="_blank" rel="noopener noreferrer nofollow">${escapeHtml(shortenUrl(u))}</a>`);
  if (opts.entities) {
    s = s.replace(/(^|[^\w/=])@(\w{1,15})\b/g,
      (m, pre, h) => `${pre}<a class="tweet-link" href="#" onclick="return false">@${h}</a>`);
    s = s.replace(/(^|[^\w&/=])#(\w+)/g,
      (m, pre, t) => `${pre}<a class="tweet-link" href="#" onclick="return false">#${t}</a>`);
  }
  return s;
}

// When a post has attached media, X hides the trailing t.co link that points to it.
function stripTrailingMediaLink(content, hasMedia) {
  return hasMedia ? content.replace(/\s*https?:\/\/t\.co\/\S+\s*$/i, "") : content;
}

// Canonical X (Twitter) glyphs. Each is a single fill path tracing its own outline
// (even-odd), rendered as an outlined icon under `fill: currentColor`.
function actionIcons() {
  return {
    reply: `<svg viewBox="0 0 24 24"><path d="M1.751 10c0-4.42 3.584-8 8.005-8h4.366c4.49 0 8.129 3.64 8.129 8.13 0 2.96-1.607 5.68-4.196 7.11l-8.054 4.46v-3.69h-.067c-4.49.1-8.183-3.51-8.183-8.01zm8.005-6c-3.317 0-6.005 2.69-6.005 6 0 3.37 2.77 6.08 6.138 6.01l.351-.01h1.761v2.3l5.087-2.81c1.951-1.08 3.163-3.13 3.163-5.36 0-3.39-2.744-6.13-6.129-6.13H9.756z"/></svg>`,
    repost: `<svg viewBox="0 0 24 24"><path d="M4.75 3.79l4.603 4.3-1.706 1.82L6 8.38v7.37c0 .97.784 1.75 1.75 1.75H13V20H7.75c-2.347 0-4.25-1.9-4.25-4.25V8.38L1.853 9.91.147 8.09l4.603-4.3zm11.5 2.71H11V4h5.25c2.347 0 4.25 1.9 4.25 4.25v7.37l1.647-1.53 1.706 1.82-4.603 4.3-4.603-4.3 1.706-1.82L18 15.62V8.25c0-.97-.784-1.75-1.75-1.75z"/></svg>`,
    like: `<svg viewBox="0 0 24 24"><path d="M16.697 5.5c-1.222-.06-2.679.51-3.89 2.16l-.805 1.09-.806-1.09C9.984 6.01 8.526 5.44 7.304 5.5c-1.243.07-2.349.78-2.91 1.91-.552 1.12-.633 2.78.479 4.82 1.074 1.97 3.257 4.27 7.129 6.61 3.87-2.34 6.052-4.64 7.126-6.61 1.111-2.04 1.03-3.7.477-4.82-.561-1.13-1.666-1.84-2.908-1.91zm4.187 7.69c-1.351 2.48-4.001 4.928-8.087 7.44l-.797.49-.796-.49C7.108 18.118 4.458 15.67 3.107 13.19c-1.376-2.525-1.46-4.97-.578-6.71.882-1.74 2.736-2.916 4.846-3.066 1.643-.12 3.301.4 4.625 1.66 1.324-1.26 2.982-1.78 4.625-1.66 2.11.15 3.964 1.326 4.846 3.066.882 1.74.798 4.185-.578 6.71z"/></svg>`,
    views: `<svg viewBox="0 0 24 24"><path d="M8.75 21V3h2v18h-2zM18 21V8.5h2V21h-2zM4 21l.004-10h2L6 21H4zm9.248 0v-7h2v7h-2z"/></svg>`,
    bookmark: `<svg viewBox="0 0 24 24"><path d="M4 4.5C4 3.12 5.119 2 6.5 2h11C18.881 2 20 3.12 20 4.5v18.44l-8-5.71-8 5.71V4.5zM6.5 4c-.276 0-.5.22-.5.5v14.56l6-4.29 6 4.29V4.5c0-.28-.224-.5-.5-.5h-11z"/></svg>`,
    share: `<svg viewBox="0 0 24 24"><path d="M12 2.59l5.7 5.7-1.41 1.42L13 6.41V16h-2V6.41l-3.3 3.3-1.41-1.42L12 2.59zM21 15l-.02 3.51c0 1.38-1.12 2.49-2.5 2.49H5.5C4.11 21 3 19.88 3 18.5V15h2v3.5c0 .28.22.5.5.5h12.98c.28 0 .5-.22.5-.5L19 15h2z"/></svg>`,
    more: `<svg viewBox="0 0 24 24"><path d="M3 12c0-1.1.9-2 2-2s2 .9 2 2-.9 2-2 2-2-.9-2-2zm9 2c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm7 0c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2z"/></svg>`,
    back: `<svg viewBox="0 0 24 24"><path d="M7.414 13l5.043 5.04-1.414 1.42L3.586 12l7.457-7.46 1.414 1.42L7.414 11H21v2H7.414z"/></svg>`,
  };
}

// Media-player control glyphs (play/pause/mute/unmute/fullscreen).
const VIDEO_ICONS = {
  play: `<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>`,
  pause: `<svg viewBox="0 0 24 24"><path d="M6 5h4v14H6zm8 0h4v14h-4z"/></svg>`,
  muted: `<svg viewBox="0 0 24 24"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM19 12l2.5 2.5 1.5-1.5L20.5 11 23 8.5 21.5 7 19 9.5 16.5 7 15 8.5 17.5 11 15 13.5 16.5 15 19 12z"/></svg>`,
  unmuted: `<svg viewBox="0 0 24 24"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg>`,
  full: `<svg viewBox="0 0 24 24"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>`,
};

function fmtDur(s) {
  s = Math.max(0, Math.floor(s || 0));
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}

// Wire up X-style behaviour on each rendered <video>: hover controls (play/pause,
// scrubber, time, mute, fullscreen) and click-the-frame-to-toggle-mute.
function initVideoPlayers(root) {
  root.querySelectorAll(".video-player").forEach((wrap) => {
    const v = wrap.querySelector("video");
    const playBtn = wrap.querySelector(".vc-play");
    const muteBtn = wrap.querySelector(".vc-mute");
    const fullBtn = wrap.querySelector(".vc-full");
    const prog = wrap.querySelector(".vc-progress");
    const fill = wrap.querySelector(".vc-fill");
    const time = wrap.querySelector(".vc-time");
    if (!v) return;
    const syncPlay = () => {
      playBtn.innerHTML = v.paused ? VIDEO_ICONS.play : VIDEO_ICONS.pause;
      playBtn.setAttribute("aria-label", v.paused ? "Play" : "Pause");
    };
    const syncMute = () => {
      muteBtn.innerHTML = v.muted ? VIDEO_ICONS.muted : VIDEO_ICONS.unmuted;
      muteBtn.setAttribute("aria-label", v.muted ? "Unmute" : "Mute");
    };
    v.addEventListener("timeupdate", () => {
      if (v.duration) fill.style.width = (v.currentTime / v.duration * 100) + "%";
      time.textContent = fmtDur(v.currentTime);
    });
    v.addEventListener("play", syncPlay);
    v.addEventListener("pause", syncPlay);
    v.addEventListener("volumechange", syncMute);
    playBtn.addEventListener("click", (e) => { e.stopPropagation(); v.paused ? v.play() : v.pause(); });
    muteBtn.addEventListener("click", (e) => { e.stopPropagation(); v.muted = !v.muted; });
    fullBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const req = v.requestFullscreen || v.webkitRequestFullscreen || wrap.requestFullscreen;
      if (req) req.call(v.requestFullscreen ? v : wrap);
    });
    prog.addEventListener("click", (e) => {
      e.stopPropagation();
      const r = prog.getBoundingClientRect();
      if (v.duration) v.currentTime = ((e.clientX - r.left) / r.width) * v.duration;
    });
    v.addEventListener("click", () => { v.muted = !v.muted; });  // X: click frame to (un)mute
    syncPlay(); syncMute();
  });
}

function initials(name) {
  return String(name || "").trim().split(/\s+/).map((w) => w[0] || "")
    .slice(0, 2).join("").toUpperCase();
}

// Deterministic avatar colour per author so the feed isn't a wall of identical
// grey circles (X shows varied avatar images; synthetic authors get varied hues).
const AVATAR_COLORS = ["#1d9bf0", "#00ba7c", "#f91880", "#7856ff", "#ff7a00",
  "#ffd400", "#e0245e", "#17bf63", "#794bc4", "#e8590c", "#0f8a6b", "#c9376e"];

function renderAvatar(name, seed, extraClass = "") {
  const bg = AVATAR_COLORS[hashStr(seed || name) % AVATAR_COLORS.length];
  return `<div class="avatar ${extraClass}" style="background:${bg}">${escapeHtml(initials(name))}</div>`;
}

// X-style media grid for attached photos / video stills. `media` is a list of
// {type, src}; videos render their preview frame with a play badge.
function renderMedia(media) {
  if (!Array.isArray(media) || media.length === 0) return "";
  const shown = media.slice(0, 4);
  const items = shown.map((m) => {
    // Animated GIF → autoplay muted loop, no controls (X shows just a GIF badge).
    if (m.type === "animated_gif" && m.video) {
      return `
    <div class="media-item">
      <video src="${escapeHtml(m.video)}" poster="${escapeHtml(m.src)}" autoplay muted loop playsinline preload="metadata"></video>
      <span class="gif-badge" aria-hidden="true">GIF</span>
    </div>`;
    }
    // Video → autoplay muted loop with X-style hover controls (see initVideoPlayers).
    if (m.type === "video" && m.video) {
      return `
    <div class="media-item video-player">
      <video src="${escapeHtml(m.video)}" poster="${escapeHtml(m.src)}" autoplay muted loop playsinline preload="metadata"></video>
      <div class="video-controls">
        <button class="vc-btn vc-play" type="button" aria-label="Pause"></button>
        <div class="vc-progress"><div class="vc-fill"></div></div>
        <span class="vc-time">0:00</span>
        <button class="vc-btn vc-mute" type="button" aria-label="Unmute"></button>
        <button class="vc-btn vc-full" type="button" aria-label="Full screen"></button>
      </div>
    </div>`;
    }
    // Photo, or a video with no downloadable file → poster still (+ play badge for video).
    return `
    <div class="media-item">
      <img src="${escapeHtml(m.src)}" alt="${m.type === "video" ? "Video preview" : "Image attached to the post"}" loading="lazy">
      ${m.type === "video" ? `<span class="play-badge" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></span>` : ""}
    </div>`;
  }).join("");
  return `<div class="media-grid n${shown.length}">${items}</div>`;
}

// Synthetic secondary counts derived deterministically from the primary ones,
// so Quotes/Bookmarks look plausible and stay stable per post.
function derivedPostCounts(post) {
  const h = hashStr(post.post_id);
  return {
    quotes: Math.round((post.reposts || 0) * (0.05 + (h % 20) / 100)),
    bookmarks: Math.round((post.likes || 0) * (0.10 + ((h >>> 5) % 40) / 100)),
  };
}

function statSpan(n, label) {
  return n > 0 ? `<span><strong>${fmtNum(n)}</strong> ${label}</span>` : "";
}

// Blue X verified badge (shared by the focal author and the bot).
const VERIFIED_BADGE = ' <span class="verified-badge" aria-label="Verified account"><svg viewBox="0 0 22 22" aria-hidden="true"><path d="M20.396 11c-.018-.646-.215-1.275-.57-1.816-.354-.54-.852-.972-1.438-1.246.223-.607.27-1.264.14-1.897-.131-.634-.437-1.218-.882-1.687-.47-.445-1.053-.75-1.687-.882-.633-.13-1.29-.083-1.897.14-.273-.587-.704-1.086-1.245-1.44S11.647 1.62 11 1.604c-.646.017-1.273.213-1.813.568s-.969.854-1.24 1.44c-.608-.223-1.267-.272-1.902-.14-.635.13-1.22.436-1.69.882-.445.47-.749 1.055-.878 1.688-.13.633-.08 1.29.144 1.896-.587.274-1.087.705-1.443 1.245-.356.54-.555 1.17-.574 1.817.02.647.218 1.276.574 1.817.356.54.856.972 1.443 1.245-.224.606-.274 1.263-.144 1.896.13.634.433 1.218.877 1.688.47.443 1.054.747 1.687.878.633.132 1.29.084 1.897-.136.274.586.705 1.084 1.246 1.439.54.354 1.17.551 1.816.569.647-.016 1.276-.213 1.817-.567s.972-.854 1.245-1.44c.604.239 1.266.296 1.903.164.636-.132 1.22-.447 1.68-.907.46-.46.477-1.054.597-1.686.122-.632.086-1.283-.106-1.882.604-.274 1.118-.724 1.472-1.282.354-.559.54-1.195.54-1.84z" fill="#1d9bf0"/><path d="M9.662 14.97L6.25 11.56l1.414-1.414 2 2 5-5 1.414 1.414z" fill="#fff"/></svg></span>';
const BOT_AVATAR_URL = "/static/eddiexbot.jpg";

// noteHtml is "" when there is no context card; populated for community notes.
function renderPost(post, noteHtml = "") {
  const icons = actionIcons();
  const hasMedia = Array.isArray(post.media) && post.media.length > 0;
  const bodyText = stripTrailingMediaLink(post.content || "", hasMedia);
  const content = bodyText.trim()
    ? `<div class="post-content">${linkify(escapeHtml(bodyText), { entities: true })}</div>` : "";
  const { quotes, bookmarks } = derivedPostCounts(post);
  const verified = post.author_verified ? VERIFIED_BADGE : "";
  return `
    <div class="post-expanded">
      <div class="post-meta-row">
        ${renderAvatar(post.author_name, post.author_handle)}
        <div class="post-author">
          <div class="font-bold author-name-line">${escapeHtml(post.author_name)}${verified}</div>
          <div class="text-dim">@${escapeHtml(post.author_handle)}</div>
        </div>
        <button class="more-btn" aria-label="More">${icons.more}</button>
        <button class="follow-btn" type="button">Follow</button>
      </div>
      ${content}
      ${renderMedia(post.media)}
      ${noteHtml}
      <div class="post-time-full text-dim">${formatTimeFullDate(post.created_at)} · <strong>${fmtNum(post.views)}</strong> Views</div>
      <div class="stats-row">
        ${statSpan(post.reposts, "Reposts")}
        ${statSpan(quotes, "Quotes")}
        ${statSpan(post.likes, "Likes")}
        ${statSpan(bookmarks, "Bookmarks")}
      </div>
      <div class="post-actions post-actions-focal">
        <button class="action-btn" aria-label="Reply">${icons.reply}</button>
        <button class="action-btn repost" aria-label="Repost">${icons.repost}</button>
        <button class="action-btn like" aria-label="Like">${icons.like}</button>
        <button class="action-btn" aria-label="Bookmark">${icons.bookmark}</button>
        <button class="action-btn" aria-label="Share">${icons.share}</button>
      </div>
    </div>`;
}

function renderBotReply(iv, post) {
  const icons = actionIcons();
  // Reply timestamp: shortly after the post, deterministic per post.
  const offsetMin = 5 + (hashStr(post.post_id) % 715);
  const replyDate = new Date(new Date(post.created_at).getTime() + offsetMin * 60000);
  const replies = Math.round((iv.reply_reposts || 0) * (0.3 + (hashStr(iv.bot_handle + post.post_id) % 50) / 100));
  const body = linkify(escapeHtml(iv.body || ""), { entities: true });
  return `
    <div class="reply-card">
      <div class="post-inner">
        <div class="avatar-col"><img class="avatar" src="${BOT_AVATAR_URL}" alt=""></div>
        <div class="post-body">
          <div class="post-meta">
            <span class="author-name">${escapeHtml(iv.bot_name)}</span>${VERIFIED_BADGE}
            <span class="author-handle text-dim">@${escapeHtml(iv.bot_handle)}</span>
            <span class="text-dim">·</span>
            <span class="post-time text-dim">${relTime(replyDate)}</span>
          </div>
          <div class="post-content">${body}</div>
          <div class="post-actions">
            <button class="action-btn" aria-label="Reply">${icons.reply}<span>${fmtNum(replies)}</span></button>
            <button class="action-btn repost" aria-label="Repost">${icons.repost}<span>${fmtNum(iv.reply_reposts || 0)}</span></button>
            <button class="action-btn like" aria-label="Like">${icons.like}<span>${fmtNum(iv.reply_likes || 0)}</span></button>
            <button class="action-btn views" aria-label="Views">${icons.views}<span>${fmtNum(iv.reply_views || 0)}</span></button>
            <button class="action-btn" aria-label="Bookmark">${icons.bookmark}</button>
            <button class="action-btn" aria-label="Share">${icons.share}</button>
          </div>
        </div>
      </div>
    </div>`;
}

function renderNoteCard(iv) {
  const body = linkify(escapeHtml(iv.body || ""), { entities: true });
  return `
    <div class="note-card">
      <div class="note-card-header"><span>Readers added context</span></div>
      <div class="note-card-body">${body}</div>
      <div class="note-card-footer">Context is written by people who use X, and appears when rated helpful by others. <span class="note-card-link">Learn more</span></div>
      <div class="note-rate-row">
        <span class="text-dim">Do you find this helpful?</span>
        <button class="note-rate-btn" type="button">Rate it</button>
      </div>
    </div>`;
}

function renderError(msg) {
  document.getElementById("main-content").innerHTML =
    `<div class="thread-header"><button class="header-back" aria-label="Back">${actionIcons().back}</button><span class="font-bold" style="font-size:20px">Post</span></div>
     <div style="padding:24px" class="text-dim">${escapeHtml(msg)}</div>`;
}

function renderThread(data) {
  const { post, intervention } = data;
  const icons = actionIcons();
  const noteHtml = intervention.kind === "community_note" ? renderNoteCard(intervention) : "";
  const replyHtml = intervention.kind === "bot_reply" ? renderBotReply(intervention, post) : "";
  const el = document.getElementById("main-content");
  el.innerHTML = `
    <div class="thread-header"><button class="header-back" aria-label="Back">${icons.back}</button><span class="font-bold" style="font-size:20px">Post</span></div>
    ${renderPost(post, noteHtml)}
    ${replyHtml}`;
  // Twemoji: render every emoji as X does (identical across all participant OSes).
  if (window.twemoji) {
    window.twemoji.parse(el, { base: "/static/twemoji/", folder: "svg", ext: ".svg", className: "emoji" });
  }
  initVideoPlayers(el);
  const snippet = (post.content || "").replace(/\s+/g, " ").trim().slice(0, 60);
  document.title = `${post.author_name} on X: "${snippet}${snippet.length >= 60 ? "…" : ""}" / X`;
}

(async () => {
  const p = new URLSearchParams(location.search);
  const postId = p.get("post_id");
  const condition = p.get("condition");
  if (!postId || !condition) {
    renderError("Missing post_id or condition.");
    return;
  }
  const data = await MockXAPI.getThread(postId, condition);
  if (!data) {
    renderError("Post or condition not found.");
    return;
  }
  renderThread(data);
})();
