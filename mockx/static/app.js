// Mock X — renderer

function formatTime(iso) {
  const d = new Date(iso);
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function formatTimeFullDate(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }) +
    " · " + d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function fmtNum(n) {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
  if (n >= 1000) return (n / 1000).toFixed(1) + "K";
  return n.toString();
}

function avatarClass(author) {
  if (!author.bot) return "";
  return `bot-${author.tone}`;
}

function botBadge(author) {
  if (!author.bot) return "";
  return `<span class="bot-badge ${author.tone}">${author.tone}</span>`;
}

function actionIcons() {
  return {
    reply: `<svg viewBox="0 0 24 24"><path d="M1.751 10c0-4.42 3.584-8 8.005-8h4.366c4.49 0 8.129 3.64 8.129 8.13 0 2.96-1.607 5.68-4.196 7.11l-8.054 4.46v-3.69h-.067c-4.49.1-8.183-3.51-8.183-8.01z" stroke="currentColor" fill="none" stroke-width="1.75"/></svg>`,
    repost: `<svg viewBox="0 0 24 24"><path d="M4.5 3.88l4.432 4.14-1.364 1.46L5.5 7.55V16c0 1.1.896 2 2 2H13v2H7.5c-2.209 0-4-1.79-4-4V7.55L1.932 9.48.568 8.02 5 3.88zM16.5 3v8.45l2.068-1.93 1.364 1.46L15.5 14.99l-4.432-4.14 1.364-1.46L14.5 11.32V5h-2.5V3h4.5z" fill="currentColor"/></svg>`,
    like: `<svg viewBox="0 0 24 24"><path d="M16.697 5.5c-1.222-.06-2.679.51-3.89 2.16l-.805 1.09-.806-1.09C9.984 6.01 8.526 5.44 7.304 5.5c-1.243.07-2.349.78-2.91 1.91-.552 1.12-.633 2.78.479 4.82 1.074 1.97 3.257 4.27 7.129 6.61 3.87-2.34 6.052-4.64 7.126-6.61 1.111-2.04 1.03-3.7.477-4.82-.561-1.13-1.666-1.84-2.908-1.91z" stroke="currentColor" fill="none"/></svg>`,
    views: `<svg viewBox="0 0 24 24"><path d="M8.75 21V3h2v18h-2zM18 21V8.5h2V21h-2zM4 21l.004-10h2L6 21H4zm9.248 0v-7h2v7h-2z" fill="currentColor"/></svg>`,
    share: `<svg viewBox="0 0 24 24"><path d="M12 2.59l5.7 5.7-1.41 1.42L13 6.41V16h-2V6.41l-3.3 3.3-1.41-1.42L12 2.59zM21 15l-.02 3.51c0 1.38-1.12 2.49-2.5 2.49H5.5C4.11 21 3 19.88 3 18.5V15h2v3.5c0 .28.22.5.5.5h12.98c.28 0 .5-.22.5-.5L19 15h2z" fill="currentColor"/></svg>`
  };
}

// ── State ──────────────────────────────────────────────────────────────────
let state = { view: "feed", threadId: null, posts: [], likedIds: new Set() };

// ── Render helpers ─────────────────────────────────────────────────────────

function renderAvatar(author, size = "") {
  const cls = avatarClass(author);
  const initials = author.avatar || author.name.slice(0, 2).toUpperCase();
  return `<div class="avatar ${cls} ${size}">${initials}</div>`;
}

function renderActions(item, compact = true) {
  const icons = actionIcons();
  const liked = state.likedIds.has(item.id);
  return `
    <div class="post-actions">
      <button class="action-btn reply-trigger" data-id="${item.id}">
        ${icons.reply}<span>${fmtNum(item.replies.length)}</span>
      </button>
      <button class="action-btn repost" data-id="${item.id}">
        ${icons.repost}<span>${fmtNum(item.reposts)}</span>
      </button>
      <button class="action-btn like ${liked ? "liked" : ""}" data-id="${item.id}">
        ${icons.like}<span>${fmtNum(item.likes + (liked ? 1 : 0))}</span>
      </button>
      <button class="action-btn views">
        ${icons.views}<span>${fmtNum(item.views)}</span>
      </button>
      <button class="action-btn share">
        ${icons.share}
      </button>
    </div>`;
}

function renderPostCard(post) {
  return `
    <div class="post-card" data-id="${post.id}">
      <div class="post-inner">
        <div class="avatar-col">
          ${renderAvatar(post.author)}
        </div>
        <div class="post-body">
          <div class="post-meta">
            <span class="author-name">${post.author.name}</span>
            ${post.author.verified ? "✓" : ""}
            ${botBadge(post.author)}
            <span class="author-handle text-dim">@${post.author.handle}</span>
            <span class="post-dot text-dim">·</span>
            <span class="post-time text-dim">${formatTime(post.timestamp)}</span>
          </div>
          <div class="post-content">${post.content}</div>
          ${renderActions(post)}
        </div>
      </div>
    </div>`;
}

function renderReplyCard(reply, depth = 0) {
  const wrapperClass = depth === 0 ? "reply-card" : "nested-reply-card";
  const hasNested = reply.replies && reply.replies.length > 0;

  return `
    <div class="${wrapperClass}" data-id="${reply.id}">
      <div class="post-inner">
        <div class="avatar-col">
          ${renderAvatar(reply.author)}
          ${hasNested && depth === 0 ? '<div class="thread-line"></div>' : ""}
        </div>
        <div class="post-body">
          <div class="post-meta">
            <span class="author-name">${reply.author.name}</span>
            ${botBadge(reply.author)}
            <span class="author-handle text-dim">@${reply.author.handle}</span>
            <span class="post-dot text-dim">·</span>
            <span class="post-time text-dim">${formatTime(reply.timestamp)}</span>
          </div>
          <div class="post-content">${reply.content}</div>
          ${renderActions(reply)}
        </div>
      </div>
    </div>
    ${hasNested && depth === 0
      ? reply.replies.map(r => renderReplyCard(r, 1)).join("")
      : ""}`;
}

function renderReplyInput(parentId) {
  return `
    <div class="reply-box">
      ${renderAvatar({ name: "You", avatar: "YO" })}
      <textarea class="reply-input" placeholder="Post your reply" rows="2" id="reply-input-${parentId}"></textarea>
      <button class="reply-submit" disabled onclick="submitReply('${parentId}')">Reply</button>
    </div>`;
}

// ── Views ──────────────────────────────────────────────────────────────────

function renderFeed() {
  const html = state.posts.map(renderPostCard).join("");
  document.getElementById("main-content").innerHTML = `
    <div class="feed-header">Home</div>
    ${renderReplyInput("new")}
    <div class="view-tabs">
      <div class="view-tab active">For you</div>
      <div class="view-tab">Following</div>
    </div>
    ${html}`;
}

function renderThread(post) {
  const icons = actionIcons();
  const liked = state.likedIds.has(post.id);

  const repliesHtml = post.replies.map(r => `
    ${renderReplyCard(r, 0)}
  `).join("");

  document.getElementById("main-content").innerHTML = `
    <div class="thread-header">
      <button class="back-btn" id="back-btn">
        <svg viewBox="0 0 24 24"><path d="M7.414 13l5.043 5.04-1.414 1.42L3.586 12l7.457-7.46 1.414 1.42L7.414 11H21v2H7.414z"/></svg>
      </button>
      <span class="font-bold" style="font-size:20px">Post</span>
    </div>

    <div class="post-expanded">
      <div class="post-meta-row">
        ${renderAvatar(post.author)}
        <div>
          <div class="font-bold">${post.author.name} ${botBadge(post.author)}</div>
          <div class="text-dim">@${post.author.handle}</div>
        </div>
      </div>
      <div class="post-content">${post.content}</div>
      <div class="post-time-full text-dim">${formatTimeFullDate(post.timestamp)}</div>
      <div class="stats-row">
        <span><strong>${fmtNum(post.reposts)}</strong> Reposts</span>
        <span><strong>${fmtNum(post.likes)}</strong> Likes</span>
        <span><strong>${fmtNum(post.views)}</strong> Views</span>
      </div>
      <div class="post-actions" style="max-width:none; justify-content:space-around">
        <button class="action-btn reply-trigger" data-id="${post.id}">${icons.reply}</button>
        <button class="action-btn repost" data-id="${post.id}">${icons.repost}</button>
        <button class="action-btn like ${liked ? "liked" : ""}" data-id="${post.id}">${icons.like}</button>
        <button class="action-btn share">${icons.share}</button>
      </div>
    </div>

    ${renderReplyInput(post.id)}
    ${repliesHtml}`;

  document.getElementById("back-btn").addEventListener("click", () => {
    state.view = "feed";
    state.threadId = null;
    renderFeed();
    bindFeedEvents();
  });

  bindThreadEvents();
}

// ── Event binding ──────────────────────────────────────────────────────────

function bindFeedEvents() {
  document.querySelectorAll(".post-card").forEach(card => {
    card.addEventListener("click", async (e) => {
      if (e.target.closest("button")) return;
      const id = card.dataset.id;
      const post = state.posts.find(p => p.id === id);
      if (!post) return;
      state.view = "thread";
      state.threadId = id;
      renderThread(post);
    });
  });
  bindActionButtons();
  bindReplyInputs();
}

function bindThreadEvents() {
  bindActionButtons();
  bindReplyInputs();

  // clicking a reply card opens nested thread
  document.querySelectorAll(".reply-card, .nested-reply-card").forEach(card => {
    card.addEventListener("click", async (e) => {
      if (e.target.closest("button, textarea")) return;
    });
  });
}

function bindActionButtons() {
  document.querySelectorAll(".action-btn.like").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const id = btn.dataset.id;
      if (state.likedIds.has(id)) {
        state.likedIds.delete(id);
      } else {
        state.likedIds.add(id);
        await MockXAPI.likePost(id);
      }
      // re-render current view
      if (state.view === "feed") {
        renderFeed(); bindFeedEvents();
      } else {
        const post = state.posts.find(p => p.id === state.threadId);
        renderThread(post);
      }
    });
  });

  document.querySelectorAll(".reply-trigger").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const id = btn.dataset.id;
      // focus reply input
      const input = document.getElementById(`reply-input-${id}`);
      if (input) input.focus();
    });
  });
}

function bindReplyInputs() {
  document.querySelectorAll(".reply-input").forEach(input => {
    input.addEventListener("input", () => {
      const btn = input.closest(".reply-box").querySelector(".reply-submit");
      btn.disabled = input.value.trim() === "";
    });
  });
}

window.submitReply = async function(parentId) {
  const input = document.getElementById(`reply-input-${parentId}`);
  const content = input.value.trim();
  if (!content) return;

  await MockXAPI.addReply(parentId, content);
  state.posts = await MockXAPI.getPosts();

  if (parentId === "new") {
    // new top-level post — not wired in demo
    input.value = "";
    renderFeed(); bindFeedEvents();
    return;
  }

  const post = state.posts.find(p => p.id === state.threadId || p.id === parentId);
  if (post) {
    state.threadId = post.id;
    renderThread(post);
  }
};

// ── Boot ──────────────────────────────────────────────────────────────────

(async () => {
  state.posts = await MockXAPI.getPosts();
  renderFeed();
  bindFeedEvents();
})();
