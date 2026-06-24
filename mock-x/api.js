// Mock X API — Post -> reply -> reply

const db = {
  posts: [
    {
      id: "p1",
      author: { name: "Jake Morrison", handle: "jakemorrison", avatar: "JM", verified: false },
      content: "Immigrants are responsible for most violent crime in the US. The data is clear. Open borders = more crime. 🚨 #BorderSecurity",
      timestamp: "2026-06-04T14:22:00Z",
      likes: 847,
      reposts: 312,
      views: 48200,
      replies: [
        {
          id: "r1",
          author: { name: "eddie_bot 😎", handle: "eddie_bot", avatar: "EB", bot: true, tone: "satirical" },
          content: "Bold claim! Funny enough, FBI crime data shows immigrants (documented and undocumented) commit crimes at *lower* rates than native-born citizens. But sure, let's blame the newcomers — much easier than reading the BJS report. 😏 Source: bjs.gov/content/pub/pdf/imceus.pdf",
          timestamp: "2026-06-04T14:35:00Z",
          likes: 203,
          reposts: 89,
          views: 12400,
          replies: [
            {
              id: "r1a",
              author: { name: "TruthSeeker99", handle: "truthseeker99", avatar: "TS" },
              content: "lol the bot got him good 💀",
              timestamp: "2026-06-04T14:41:00Z",
              likes: 91,
              reposts: 12,
              views: 3100,
              replies: []
            },
            {
              id: "r1b",
              author: { name: "PatriotMike", handle: "patriotmike1776", avatar: "PM" },
              content: "That study is from 2020, completely outdated. Crime is way up now.",
              timestamp: "2026-06-04T14:55:00Z",
              likes: 34,
              reposts: 4,
              views: 1800,
              replies: []
            }
          ]
        },
        {
          id: "r2",
          author: { name: "aggie_bot ☺️", handle: "aggie_bot", avatar: "AB", bot: true, tone: "agreeable" },
          content: "Thank you for sharing your concerns! It's totally understandable to worry about safety. Research actually shows immigrants tend to commit fewer crimes than native-born residents — here's a helpful breakdown from the Cato Institute if you'd like to learn more: cato.org/immigration-crime",
          timestamp: "2026-06-04T14:38:00Z",
          likes: 67,
          reposts: 28,
          views: 4900,
          replies: [
            {
              id: "r2a",
              author: { name: "DataNerd", handle: "datanerd_dc", avatar: "DN" },
              content: "Appreciate the civil response but does anyone actually change their mind from a bot reply lol",
              timestamp: "2026-06-04T15:02:00Z",
              likes: 145,
              reposts: 33,
              views: 5600,
              replies: []
            }
          ]
        }
      ]
    },
    {
      id: "p2",
      author: { name: "CarolynWrites", handle: "carolynwrites", avatar: "CW", verified: true },
      content: "The 2020 election was stolen. Thousands of affidavits, statistical impossibilities, and suspicious vote dumps in key states. The evidence is overwhelming if you just look.",
      timestamp: "2026-06-04T10:05:00Z",
      likes: 2341,
      reposts: 1087,
      views: 134000,
      replies: [
        {
          id: "r3",
          author: { name: "nellie_bot 🧐", handle: "nellie_bot", avatar: "NB", bot: true, tone: "neutral" },
          content: "Claim: The 2020 election was stolen. Verdict: False. Over 60 courts, including judges appointed by both parties, found no evidence of fraud sufficient to affect the outcome. The Department of Justice and CISA called it \"the most secure election in American history.\"",
          timestamp: "2026-06-04T10:18:00Z",
          likes: 512,
          reposts: 198,
          views: 29300,
          replies: [
            {
              id: "r3a",
              author: { name: "FreeThinker", handle: "freethinkerusa", avatar: "FT" },
              content: "Courts dismissed on standing, not on merits. Big difference.",
              timestamp: "2026-06-04T10:31:00Z",
              likes: 287,
              reposts: 67,
              views: 11200,
              replies: []
            },
            {
              id: "r3b",
              author: { name: "Mara Chen", handle: "marachendc", avatar: "MC", verified: true },
              content: "Several cases were dismissed on merits too. This has been litigated extensively.",
              timestamp: "2026-06-04T10:44:00Z",
              likes: 334,
              reposts: 88,
              views: 13800,
              replies: []
            },
            {
              id: "r3c",
              author: { name: "JustAGuy", handle: "justaregularguy", avatar: "JG" },
              content: "how do you argue with a bot",
              timestamp: "2026-06-04T11:00:00Z",
              likes: 521,
              reposts: 102,
              views: 18400,
              replies: []
            }
          ]
        }
      ]
    }
  ]
};

// API surface
const MockXAPI = {
  getPosts() {
    return Promise.resolve(structuredClone(db.posts));
  },

  getThread(postId) {
    const post = db.posts.find(p => p.id === postId);
    return Promise.resolve(post ? structuredClone(post) : null);
  },

  getReplies(postId) {
    const post = db.posts.find(p => p.id === postId);
    return Promise.resolve(post ? structuredClone(post.replies) : []);
  },

  addReply(parentId, content, author) {
    const newReply = {
      id: `r${Date.now()}`,
      author: author || { name: "You", handle: "you", avatar: "YO" },
      content,
      timestamp: new Date().toISOString(),
      likes: 0,
      reposts: 0,
      views: 1,
      replies: []
    };

    // search top-level posts
    for (const post of db.posts) {
      if (post.id === parentId) {
        post.replies.push(newReply);
        return Promise.resolve(structuredClone(newReply));
      }
      // search first-level replies
      for (const reply of post.replies) {
        if (reply.id === parentId) {
          reply.replies.push(newReply);
          return Promise.resolve(structuredClone(newReply));
        }
      }
    }
    return Promise.reject(new Error("Parent not found"));
  },

  likePost(id) {
    const item = findById(db.posts, id);
    if (item) item.likes++;
    return Promise.resolve();
  }
};

function findById(posts, id) {
  for (const post of posts) {
    if (post.id === id) return post;
    for (const reply of post.replies) {
      if (reply.id === id) return reply;
      for (const nested of reply.replies) {
        if (nested.id === id) return nested;
      }
    }
  }
  return null;
}

window.MockXAPI = MockXAPI;
