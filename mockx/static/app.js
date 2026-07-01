// X thread renderer. Renders one post + one reply card or context card
// based on URL params (?post_id=&condition=).

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function fmtNum(n) {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
  if (n >= 1000) return (n / 1000).toFixed(1) + "K";
  return String(n);
}

function formatTimeFullDate(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }) +
    " · " + d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

// Canonical X (Twitter) action-row glyphs. Each is a single fill path that
// traces its own outline (even-odd), so they render as outlined icons under
// `fill: currentColor` — never stroke these (it loses the inner contour).
function actionIcons() {
  return {
    reply: `<svg viewBox="0 0 24 24"><path d="M1.751 10c0-4.42 3.584-8 8.005-8h4.366c4.49 0 8.129 3.64 8.129 8.13 0 2.96-1.607 5.68-4.196 7.11l-8.054 4.46v-3.69h-.067c-4.49.1-8.183-3.51-8.183-8.01zm8.005-6c-3.317 0-6.005 2.69-6.005 6 0 3.37 2.77 6.08 6.138 6.01l.351-.01h1.761v2.3l5.087-2.81c1.951-1.08 3.163-3.13 3.163-5.36 0-3.39-2.744-6.13-6.129-6.13H9.756z"/></svg>`,
    repost: `<svg viewBox="0 0 24 24"><path d="M4.5 3.88l4.432 4.14-1.364 1.46L6 7.13v8.37c0 1.105.895 2 2 2h6.13v-2H8c-.276 0-.5-.224-.5-.5V7.13l1.068 1.35 1.364-1.46L4.5 3.88zM19.5 16.87V8.5c0-1.105-.895-2-2-2h-6.13v2H17.5c.276 0 .5.224.5.5v8.37l-1.068-1.35-1.364 1.46 4.432 4.14 4.432-4.14-1.364-1.46L19.5 16.87z"/></svg>`,
    like: `<svg viewBox="0 0 24 24"><path d="M16.697 5.5c-1.222-.06-2.679.51-3.89 2.16l-.805 1.09-.806-1.09C9.984 6.01 8.526 5.44 7.304 5.5c-1.243.07-2.349.78-2.91 1.91-.552 1.12-.633 2.78.479 4.82 1.074 1.97 3.257 4.27 7.129 6.61 3.87-2.34 6.052-4.64 7.126-6.61 1.111-2.04 1.03-3.7.477-4.82-.561-1.13-1.666-1.84-2.908-1.91zm4.187 7.69c-1.351 2.48-4.001 4.928-8.087 7.44l-.797.49-.796-.49C7.108 18.118 4.458 15.67 3.107 13.19c-1.376-2.525-1.46-4.97-.578-6.71.882-1.74 2.736-2.916 4.846-3.066 1.643-.12 3.301.4 4.625 1.66 1.324-1.26 2.982-1.78 4.625-1.66 2.11.15 3.964 1.326 4.846 3.066.882 1.74.798 4.185-.578 6.71z"/></svg>`,
    views: `<svg viewBox="0 0 24 24"><path d="M8.75 21V3h2v18h-2zM18 21V8.5h2V21h-2zM4 21l.004-10h2L6 21H4zm9.248 0v-7h2v7h-2z"/></svg>`,
    share: `<svg viewBox="0 0 24 24"><path d="M12 2.59l5.7 5.7-1.41 1.42L13 6.41V16h-2V6.41l-3.3 3.3-1.41-1.42L12 2.59zM21 15l-.02 3.51c0 1.38-1.12 2.49-2.5 2.49H5.5C4.11 21 3 19.88 3 18.5V15h2v3.5c0 .28.22.5.5.5h12.98c.28 0 .5-.22.5-.5L19 15h2z"/></svg>`,
  };
}

function initials(name) {
  return String(name || "").trim().split(/\s+/).map((w) => w[0] || "")
    .slice(0, 2).join("").toUpperCase();
}

function renderAvatar(text, extraClass = "") {
  return `<div class="avatar ${extraClass}">${escapeHtml(text)}</div>`;
}

// X-style media grid for attached photos / video stills. `media` is a list of
// {type, src}; videos render their preview frame with a play badge.
function renderMedia(media) {
  if (!Array.isArray(media) || media.length === 0) return "";
  const shown = media.slice(0, 4);
  const items = shown.map((m) => `
    <div class="media-item">
      <img src="${escapeHtml(m.src)}" alt="${m.type === "video" ? "Video preview" : "Image attached to the post"}" loading="lazy">
      ${m.type === "video" ? '<span class="play-badge" aria-hidden="true">▶</span>' : ""}
    </div>`).join("");
  return `<div class="media-grid n${shown.length}">${items}</div>`;
}

// noteHtml is "" when there is no context card; populated for community notes.
function renderPost(post, noteHtml = "") {
  const icons = actionIcons();
  return `
    <div class="post-expanded">
      <div class="post-meta-row">
        ${renderAvatar(initials(post.author_name))}
        <div>
          <div class="font-bold">${escapeHtml(post.author_name)} ${post.author_verified ? '<span class="verified-badge" aria-label="Verified account"><svg viewBox="0 0 22 22" aria-hidden="true"><path d="M20.396 11c-.018-.646-.215-1.275-.57-1.816-.354-.54-.852-.972-1.438-1.246.223-.607.27-1.264.14-1.897-.131-.634-.437-1.218-.882-1.687-.47-.445-1.053-.75-1.687-.882-.633-.13-1.29-.083-1.897.14-.273-.587-.704-1.086-1.245-1.44S11.647 1.62 11 1.604c-.646.017-1.273.213-1.813.568s-.969.854-1.24 1.44c-.608-.223-1.267-.272-1.902-.14-.635.13-1.22.436-1.69.882-.445.47-.749 1.055-.878 1.688-.13.633-.08 1.29.144 1.896-.587.274-1.087.705-1.443 1.245-.356.54-.555 1.17-.574 1.817.02.647.218 1.276.574 1.817.356.54.856.972 1.443 1.245-.224.606-.274 1.263-.144 1.896.13.634.433 1.218.877 1.688.47.443 1.054.747 1.687.878.633.132 1.29.084 1.897-.136.274.586.705 1.084 1.246 1.439.54.354 1.17.551 1.816.569.647-.016 1.276-.213 1.817-.567s.972-.854 1.245-1.44c.604.239 1.266.296 1.903.164.636-.132 1.22-.447 1.68-.907.46-.46.477-1.054.597-1.686.122-.632.086-1.283-.106-1.882.604-.274 1.118-.724 1.472-1.282.354-.559.54-1.195.54-1.84z" fill="#1d9bf0"/><path d="M9.662 14.97L6.25 11.56l1.414-1.414 2 2 5-5 1.414 1.414z" fill="#fff"/></svg></span>' : ""}</div>
          <div class="text-dim">@${escapeHtml(post.author_handle)}</div>
        </div>
      </div>
      <div class="post-content">${escapeHtml(post.content)}</div>
      ${renderMedia(post.media)}
      ${noteHtml}
      <div class="post-time-full text-dim">${formatTimeFullDate(post.created_at)}</div>
      <div class="stats-row">
        <span><strong>${fmtNum(post.reposts)}</strong> Reposts</span>
        <span><strong>${fmtNum(post.likes)}</strong> Likes</span>
        <span><strong>${fmtNum(post.views)}</strong> Views</span>
      </div>
      <div class="post-actions" style="max-width:none; justify-content:space-around">
        <button class="action-btn" aria-label="Reply">${icons.reply}</button>
        <button class="action-btn repost" aria-label="Repost">${icons.repost}</button>
        <button class="action-btn like" aria-label="Like">${icons.like}</button>
        <button class="action-btn share" aria-label="Share">${icons.share}</button>
      </div>
    </div>`;
}

// Parse the bot body into main text and an optional sources block.
// Bot bodies may end with "\nSources & reasoning:\n<url>\n<url>..."
function parseBotBody(body) {
  const marker = "Sources & reasoning:";
  const idx = body.indexOf(marker);
  if (idx === -1) return { mainText: body, sources: [] };
  const mainText = body.slice(0, idx).trimEnd();
  const sourceBlock = body.slice(idx + marker.length).trim();
  const sources = sourceBlock.split(/\n+/).map((s) => s.trim()).filter(Boolean);
  return { mainText, sources };
}

function renderBotReply(iv) {
  const icons = actionIcons();
  const { mainText, sources } = parseBotBody(iv.body || "");
  const sourcesHtml = sources.length > 0 ? `
    <div class="bot-sources">
      <span class="bot-sources-label">Sources</span>
      <ul class="bot-sources-list">${sources.map((url) =>
        `<li><a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="bot-source-link">${escapeHtml(url)}</a></li>`
      ).join("")}</ul>
    </div>` : "";
  return `
    <div class="reply-card">
      <div class="post-inner">
        <div class="avatar-col">${renderAvatar(iv.bot_avatar || "ED")}</div>
        <div class="post-body">
          <div class="post-meta">
            <span class="author-name">${escapeHtml(iv.bot_name)}</span>
            <span class="author-handle text-dim">@${escapeHtml(iv.bot_handle)}</span>
          </div>
          <div class="post-content">${escapeHtml(mainText)}</div>
          ${sourcesHtml}
          <div class="post-actions">
            <button class="action-btn" aria-label="Reply">${icons.reply}</button>
            <button class="action-btn repost" aria-label="Repost">${icons.repost}<span>${fmtNum(iv.reply_reposts || 0)}</span></button>
            <button class="action-btn like" aria-label="Like">${icons.like}<span>${fmtNum(iv.reply_likes || 0)}</span></button>
            <button class="action-btn views" aria-label="Views">${icons.views}<span>${fmtNum(iv.reply_views || 0)}</span></button>
            <button class="action-btn share" aria-label="Share">${icons.share}</button>
          </div>
        </div>
      </div>
    </div>`;
}

function renderNoteCard(iv) {
  return `
    <div class="note-card">
      <div class="note-card-header">
        <svg class="note-icon" viewBox="0 0 24 24"><path d="M12 1.75a10.25 10.25 0 100 20.5 10.25 10.25 0 000-20.5zM2.75 12a9.25 9.25 0 1118.5 0 9.25 9.25 0 01-18.5 0zM12 7a1.1 1.1 0 100 2.2A1.1 1.1 0 0012 7zm-1 4h2v6h-2v-6z" fill="currentColor"/></svg>
        <span>Readers added context</span>
      </div>
      <div class="note-card-body">${escapeHtml(iv.body)}</div>
      <div class="note-card-footer">Context is written by people who use X, and appears when rated helpful by others. <span class="note-card-link">Learn more</span></div>
    </div>`;
}

function renderError(msg) {
  document.getElementById("main-content").innerHTML =
    `<div class="thread-header"><span class="font-bold" style="font-size:20px">Post</span></div>
     <div style="padding:24px" class="text-dim">${escapeHtml(msg)}</div>`;
}

function renderThread(data) {
  const { post, intervention } = data;
  const noteHtml = intervention.kind === "community_note" ? renderNoteCard(intervention) : "";
  const replyHtml = intervention.kind === "bot_reply" ? renderBotReply(intervention) : "";
  document.getElementById("main-content").innerHTML = `
    <div class="thread-header"><span class="font-bold" style="font-size:20px">Post</span></div>
    ${renderPost(post, noteHtml)}
    ${replyHtml}`;
  document.title = `${post.author_name} on X`;
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
