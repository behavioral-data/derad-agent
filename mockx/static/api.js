// Mock X — data layer. Fetches a single (post, intervention) thread.
const MockXAPI = {
  async getThread(postId, condition) {
    const r = await fetch(
      `/api/thread?post_id=${encodeURIComponent(postId)}&condition=${encodeURIComponent(condition)}`
    );
    if (!r.ok) return null;
    return r.json();
  },
};
window.MockXAPI = MockXAPI;
