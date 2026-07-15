import random

def assign(post_counts, n_posts):
    """
    Randomly assign n_posts posts to next participant, favoring posts
    with less assigned participants

    :param post_counts: a dict of post ID to assignment count
    :param n_posts: the number of posts to assign
    :return: the list of post IDs assigned to the next participant
    """
    if len(post_counts) < n_posts:
        raise ValueError("n_posts must be >= len(post_counts)!")

    max_count = max(post_counts.values())

    count_posts = {}
    for i in range(max_count):
        count_posts[i] = []
    for post_id, count in post_counts.items():
        count_posts[count].append(post_id)

    assigned_posts = []
    count_posts_iter = iter(count_posts.values())
    while len(assigned_posts) < n_posts and (next_posts := next(count_posts_iter, None) is not None):
        if len(next_posts) < n_posts - len(assigned_posts):
            assigned_posts.extend(next_posts)
        else:
            assigned_posts.extend(random.sample(next_posts, n_posts - len(assigned_posts)))

    return assigned_posts
