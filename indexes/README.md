# Indexes

This directory stores FAISS indexes built from Community Notes TSV data.

The prebuilt index currently shipped with the repository covers approximately **1% of the full Community Notes dataset** for faster iteration during development and testing.

For production-grade coverage, rebuild the index from the full dataset:

```bash
python -m derad_agent.cli.build_indexes \
  --tsv-root /path/to/full/notes \
  --global-index \
  --index-root indexes
```

Alternatively, download a prebuilt full index via the onboarding CLI:

```bash
python -m derad_agent.cli.onboard_data
```
