// Data layer. Fetches a single post thread from the server.
const MockXAPI = {
  // Participant path: an opaque code that reveals neither post nor condition.
  async getThreadByCode(code) {
    const r = await fetch(`/api/thread?v=${encodeURIComponent(code)}`);
    if (!r.ok) return null;
    return r.json();
  },
  // Legacy/internal path: explicit post id + condition.
  async getThread(postId, condition) {
    const r = await fetch(
      `/api/thread?post_id=${encodeURIComponent(postId)}&condition=${encodeURIComponent(condition)}`
    );
    if (!r.ok) return null;
    return r.json();
  },
};
window.MockXAPI = MockXAPI;
