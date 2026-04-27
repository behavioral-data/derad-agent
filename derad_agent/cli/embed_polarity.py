"""Build and save per-tweet embeddings from a polarity-scored notes TSV."""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import numpy as np
from derad_agent.llm.config import get_embedder


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tsv", type=Path)
    parser.add_argument("--out", type=Path, default=Path("indexes/polarity_embeddings"))
    args = parser.parse_args()

    # Group note summaries by tweet
    groups = defaultdict(list)
    with args.tsv.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            tweet_id = row.get("tweetId", "").strip()
            summary = row.get("summary", "").strip()
            if tweet_id and summary:
                groups[tweet_id].append(summary)

    tweet_ids = sorted(groups)
    texts = ["\n\n".join(groups[tid]) for tid in tweet_ids]
    print(f"{len(tweet_ids):,} unique tweets, {sum(len(v) for v in groups.values()):,} notes total")

    # Embed in batches of 500
    embedder = get_embedder()
    all_embeddings = []
    for i in range(0, len(texts), 500):
        batch = texts[i : i + 500]
        all_embeddings.extend(embedder.embed_documents(batch))
        print(f"  embedded {min(i + 500, len(texts))}/{len(texts)}")

    # Save to disk
    args.out.mkdir(parents=True, exist_ok=True)
    np.save(args.out / "tweet_ids.npy", np.array(tweet_ids, dtype=object), allow_pickle=True)
    np.save(args.out / "embeddings.npy", np.array(all_embeddings, dtype="float32"))
    print(f"Saved to {args.out}/")


if __name__ == "__main__":
    main()
