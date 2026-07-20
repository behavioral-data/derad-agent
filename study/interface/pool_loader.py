"""Load the committed profiles.json into a StudyStore as a claimable pool."""
from __future__ import annotations

import json

from .study_store import StoredProfile


def load_pool_file(path, store):
    with open(path) as f:
        data = json.load(f)
    profiles = [StoredProfile(p["profile_id"], p["condition"], p["target_party"], p["blocks"])
                for p in data["profiles"]]
    store.load_profiles(profiles, data["claim_orders"])
    return len(profiles)
