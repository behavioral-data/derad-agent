import random

def assign(post_counts, n_posts):
    """
    Randomly assign n_posts posts to next participant, favoring posts
    with less assigned participants

    :param post_counts: a dict of post ID to assignment count
    :param n_posts: the number of posts to assign
    :return: the list of posts IDs assigned to the next participant
    """
    if len(post_counts) < n_posts:
        raise ValueError(
            f"n_posts ({n_posts}) must be <= number of posts ({len(post_counts)})"
        )

    max_count = max(post_counts.values())

    # Bucket posts by current assignment count (0 .. max_count inclusive).
    count_posts = {i: [] for i in range(max_count + 1)}
    for post_id, count in post_counts.items():
        count_posts[count].append(post_id)

    # Fill from the lowest-count buckets first; break ties randomly within a bucket.
    assigned_posts = []
    for bucket in count_posts.values():
        if len(assigned_posts) >= n_posts:
            break
        remaining = n_posts - len(assigned_posts)
        if len(bucket) <= remaining:
            assigned_posts.extend(bucket)
        else:
            assigned_posts.extend(random.sample(bucket, remaining))

    return assigned_posts
