"""Generate the committed Study-2 profile pool from study.db.

Usage: python3 -m study.scripts.generate_profiles [--db PATH] [--out-dir PATH]
Writes profiles.json (canonical), profiles.csv (flat), profiles_report.md.
"""
from __future__ import annotations

import argparse
import csv
import json
import os

from study.interface import db as dbmod
from study.interface.profiles import CONDITIONS, SEED, generate_profiles, verify_balance

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DB = os.path.join(_HERE, "..", "data", "study.db")
_DEFAULT_OUT = os.path.join(_HERE, "..", "data", "profiles")


def run(db_path, out_dir, n_templates=None, seed=None):
    os.makedirs(out_dir, exist_ok=True)
    conn = dbmod.connect(db_path)
    try:
        cells = dbmod.cells(conn)
        post_pol = {pid: pol for (t, pol), ids in cells.items() for pid in ids}
        post_topic = {pid: t for (t, pol), ids in cells.items() for pid in ids}
        # code_lookup must hit the real access table so served links match.
        code_cache = {}

        def code_lookup(post_id, condition):
            key = (post_id, condition)
            if key not in code_cache:
                code = dbmod.code_for(conn, post_id, condition)
                if code is None:
                    raise LookupError(
                        f"no access code in study.db for post {post_id} / condition {condition}")
                code_cache[key] = code
            return code_cache[key]

        kw = {}
        if n_templates: kw["n_templates"] = n_templates
        eff_seed = seed or SEED
        profiles, claim_orders = generate_profiles(cells, code_lookup, seed=eff_seed, **kw)
        report = verify_balance(profiles, cells, **kw)
    finally:
        conn.close()

    # profiles.json (canonical)
    with open(os.path.join(out_dir, "profiles.json"), "w") as f:
        json.dump({
            "seed": eff_seed,
            "claim_orders": claim_orders,
            "profiles": [vars(p) for p in profiles],
        }, f)

    # profiles.csv (flat, one row per profile-post)
    with open(os.path.join(out_dir, "profiles.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["profile_id", "template_id", "condition", "target_party",
                    "day", "position", "post_id", "topic", "polarity", "access_code"])
        for p in profiles:
            i = 0
            for d, block in enumerate(p.blocks, 1):
                for pos, post_id in enumerate(block, 1):
                    w.writerow([p.profile_id, p.template_id, p.condition, p.target_party,
                                d, pos, post_id, post_topic[post_id], post_pol[post_id],
                                p.access_codes[i]])
                    i += 1

    # profiles_report.md (balance verification)
    lines = [f"# Profiles balance report (seed {eff_seed})", "",
             f"Profiles: {len(profiles)}  |  conditions: {', '.join(CONDITIONS)}", ""]
    for k, v in report.items():
        lines.append(f"- {k}: {'OK' if v else 'FAIL'}")
    with open(os.path.join(out_dir, "profiles_report.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=_DEFAULT_DB)
    ap.add_argument("--out-dir", default=_DEFAULT_OUT)
    ap.add_argument("--n-templates", type=int, default=None,
                    help="Override template count (must satisfy the divisibility invariants; default from spec)")
    ap.add_argument("--seed", type=int, default=None, help="Override the generation seed")
    args = ap.parse_args()
    report = run(args.db, args.out_dir, n_templates=args.n_templates, seed=args.seed)
    print("balance:", "OK" if report["ok"] else "FAIL", "->", args.out_dir)


if __name__ == "__main__":
    main()
