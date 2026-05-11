# Indexes

Built by `derad-embed-notes` (or `python -m derad_agent.cli.embed_notes`).

## notes_index format

```
indexes/notes_index/
├── tweet_ids.npy       object array of tweet IDs (row order matches embeddings)
├── embeddings.npy      float32 [N, D] matrix of L2-normalised tweet embeddings
└── notes_cache.json    {tweet_id: [{note_id, summary, classification,
                                     created_at_millis, current_status}]}
```

## Building from Community Notes TSVs

Two raw files are required — both from the [public Community Notes dataset](https://communitynotes.x.com/guide/en/under-the-hood/download-data):

```bash
python -m derad_agent.cli.embed_notes \
  data/full/notes.tsv \
  data/full/noteStatusHistory.tsv \
  --out indexes/notes_index
```

For faster iteration during development, point at the sample fixtures:

```bash
python -m derad_agent.cli.embed_notes \
  data/samples/notes-mini.tsv \
  data/samples/noteStatusHistory-mini.tsv \
  --out indexes/notes_index_mini
```

Then set `DERAD_AGENT_INDEX_ROOT` to the parent directory of whichever index you want to use.
