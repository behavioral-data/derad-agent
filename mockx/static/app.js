// Mock X — study renderer. Renders one post + one intervention by URL params
// (?post_id=&condition=). Bot reply -> reply card (no tone label);
// community note -> native "Readers added context" card (Task 7).

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

function actionIcons() {
  return {
    reply: `<svg viewBox="0 0 24 24"><path d="M1.751 10c0-4.42 3.584-8 8.005-8h4.366c4.49 0 8.129 3.64 8.129 8.13 0 2.96-1.607 5.68-4.196 7.11l-8.054 4.46v-3.69h-.067c-4.49.1-8.183-3.51-8.183-8.01z" stroke="currentColor" fill="none" stroke-width="1.75"/></svg>`,
    repost: `<svg viewBox="0 0 24 24"><path d="M4.5 3.88l4.432 4.14-1.364 1.46L5.5 7.55V16c0 1.1.896 2 2 2H13v2H7.5c-2.209 0-4-1.79-4-4V7.55L1.932 9.48.568 8.02 5 3.88zM16.5 3v8.45l2.068-1.93 1.364 1.46L15.5 14.99l-4.432-4.14 1.364-1.46L14.5 11.32V5h-2.5V3h4.5z" fill="currentColor"/></svg>`,
    like: `<svg viewBox="0 0 24 24"><path d="M16.697 5.5c-1.222-.06-2.679.51-3.89 2.16l-.805 1.09-.806-1.09C9.984 6.01 8.526 5.44 7.304 5.5c-1.243.07-2.349.78-2.91 1.91-.552 1.12-.633 2.78.479 4.82 1.074 1.97 3.257 4.27 7.129 6.61 3.87-2.34 6.052-4.64 7.126-6.61 1.111-2.04 1.03-3.7.477-4.82-.561-1.13-1.666-1.84-2.908-1.91z" stroke="currentColor" fill="none"/></svg>`,
    views: `<svg viewBox="0 0 24 24"><path d="M8.75 21V3h2v18h-2zM18 21V8.5h2V21h-2zM4 21l.004-10h2L6 21H4zm9.248 0v-7h2v7h-2z" fill="currentColor"/></svg>`,
    share: `<svg viewBox="0 0 24 24"><path d="M12 2.59l5.7 5.7-1.41 1.42L13 6.41V16h-2V6.41l-3.3 3.3-1.41-1.42L12 2.59zM21 15l-.02 3.51c0 1.38-1.12 2.49-2.5 2.49H5.5C4.11 21 3 19.88 3 18.5V15h2v3.5c0 .28.22.5.5.5h12.98c.28 0 .5-.22.5-.5L19 15h2z" fill="currentColor"/></svg>`,
  };
}

function initials(name) {
  return String(name || "").trim().split(/\s+/).map((w) => w[0] || "")
    .slice(0, 2).join("").toUpperCase();
}

function renderAvatar(text, extraClass = "") {
  return `<div class="avatar ${extraClass}">${escapeHtml(text)}</div>`;
}

// noteHtml is "" for bot conditions; the control card (Task 7) for control.
function renderPost(post, noteHtml = "") {
  const icons = actionIcons();
  return `
    <div class="post-expanded">
      <div class="post-meta-row">
        ${renderAvatar(initials(post.author_name))}
        <div>
          <div class="font-bold">${escapeHtml(post.author_name)} ${post.author_verified ? "✓" : ""}</div>
          <div class="text-dim">@${escapeHtml(post.author_handle)}</div>
        </div>
      </div>
      <div class="post-content">${escapeHtml(post.content)}</div>
      ${noteHtml}
      <div class="post-time-full text-dim">${formatTimeFullDate(post.created_at)}</div>
      <div class="stats-row">
        <span><strong>${fmtNum(post.reposts)}</strong> Reposts</span>
        <span><strong>${fmtNum(post.likes)}</strong> Likes</span>
        <span><strong>${fmtNum(post.views)}</strong> Views</span>
      </div>
      <div class="post-actions" style="max-width:none; justify-content:space-around">
        <button class="action-btn">${icons.reply}</button>
        <button class="action-btn repost">${icons.repost}</button>
        <button class="action-btn like">${icons.like}</button>
        <button class="action-btn share">${icons.share}</button>
      </div>
    </div>`;
}

function renderBotReply(iv) {
  const icons = actionIcons();
  return `
    <div class="reply-card">
      <div class="post-inner">
        <div class="avatar-col">${renderAvatar(iv.bot_avatar || "ED")}</div>
        <div class="post-body">
          <div class="post-meta">
            <span class="author-name">${escapeHtml(iv.bot_name)}</span>
            <span class="author-handle text-dim">@${escapeHtml(iv.bot_handle)}</span>
          </div>
          <div class="post-content">${escapeHtml(iv.body)}</div>
          <div class="post-actions">
            <button class="action-btn">${icons.reply}</button>
            <button class="action-btn repost">${icons.repost}<span>${fmtNum(iv.reply_reposts || 0)}</span></button>
            <button class="action-btn like">${icons.like}<span>${fmtNum(iv.reply_likes || 0)}</span></button>
            <button class="action-btn views">${icons.views}<span>${fmtNum(iv.reply_views || 0)}</span></button>
            <button class="action-btn share">${icons.share}</button>
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
