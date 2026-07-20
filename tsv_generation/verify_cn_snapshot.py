#!/usr/bin/env python3
"""Verify a downloaded Community Notes snapshot is COMPLETE and consistent.

Checks (fails loudly on any problem):
  1. Schema — every file's header matches the old known-working files in cn_data/.
  2. Ratings coverage — shards are chronological & contiguous, and the LAST shard
     reaches the snapshot date (the bug we're fixing: ratings stopped 2024-09-10).
  3. Notes coverage — notes exist right up to the snapshot date.
  4. Shard presence — the counts recorded at download time are all on disk.

Usage:  python verify_cn_snapshot.py cn_data_20260630 [--snapshot 2026-06-30]
"""
import argparse
import datetime as dt
import glob
import os
import sys

OLD = "/projects/bdata/advaitmb/derad-agent/tsv_generation/cn_data"


def ms_to_iso(ms):
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).isoformat()


def header(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.readline().rstrip("\n").split("\t")


def first_data_row(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        f.readline()                      # skip header
        return f.readline().rstrip("\n").split("\t")


def last_data_row(path, block=65536):
    """Read the final non-empty line without loading the whole file."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        buf = b""
        while end > 0:
            step = min(block, end)
            end -= step
            f.seek(end)
            buf = f.read(step) + buf
            lines = buf.split(b"\n")
            # keep last two non-empty candidates; need a complete final line
            nonempty = [ln for ln in lines if ln.strip()]
            if len(nonempty) >= 2 and end == 0 or (len(lines) > 2):
                break
    last = [ln for ln in buf.split(b"\n") if ln.strip()][-1]
    return last.decode("utf-8", "replace").split("\t")


def col_index(hdr, name):
    return hdr.index(name)


def check_schema(root, problems):
    print("\n== 1. SCHEMA (new header must match old known-working header) ==")
    pairs = {
        "notes": (glob.glob(f"{root}/notes/notes-*.tsv"), f"{OLD}/notes-00000.tsv"),
        "ratings": (glob.glob(f"{root}/ratings/ratings-*.tsv"), f"{OLD}/ratings/ratings-00000.tsv"),
        "noteStatusHistory": ([f"{root}/noteStatusHistory-00000.tsv"], f"{OLD}/noteStatusHistory-00000.tsv"),
        "userEnrollment": ([f"{root}/userEnrollment-00000.tsv"], f"{OLD}/userEnrollment-00000.tsv"),
    }
    for kind, (news, old) in pairs.items():
        if not news:
            problems.append(f"{kind}: no files found"); continue
        oldh = header(old) if os.path.exists(old) else None
        for nf in sorted(news):
            nh = header(nf)
            if oldh is not None and nh != oldh:
                problems.append(f"{kind}: header of {os.path.basename(nf)} differs from old {os.path.basename(old)}")
                print(f"  MISMATCH {os.path.basename(nf)}: {len(nh)} cols vs old {len(oldh)} cols")
            else:
                print(f"  ok  {os.path.basename(nf):32s} {len(nh)} cols" + ("" if oldh is not None else " (no old baseline)"))


def check_ratings(root, snap_ms, problems):
    print("\n== 2. RATINGS coverage & chronological continuity ==")
    files = sorted(glob.glob(f"{root}/ratings/ratings-*.tsv"))
    if not files:
        problems.append("ratings: no shards found"); return
    prev_last = None
    global_last = 0
    for f in files:
        h = header(f); ci = col_index(h, "createdAtMillis")
        fr = int(first_data_row(f)[ci]); lr = int(last_data_row(f)[ci])
        global_last = max(global_last, lr)
        print(f"  {os.path.basename(f):22s} {ms_to_iso(fr)}  ->  {ms_to_iso(lr)}")
        if lr < fr:
            print(f"    (note: shard not strictly time-sorted internally — checking envelope only)")
        if prev_last is not None:
            gap_days = (fr - prev_last) / 86400000.0
            if fr < prev_last - 86400000:          # overlap > 1 day: possible duplicate/mixed snapshot
                problems.append(f"ratings: {os.path.basename(f)} starts {ms_to_iso(fr)} well before prev end {ms_to_iso(prev_last)} (overlap/mix?)")
            elif fr > prev_last + 3 * 86400000:     # gap > 3 days: a missing shard between
                problems.append(f"ratings: {gap_days:.1f}-day gap before {os.path.basename(f)} — a shard may be missing")
        prev_last = lr
    days_short = (snap_ms - global_last) / 86400000.0
    print(f"  --> last rating: {ms_to_iso(global_last)}  ({days_short:.1f} days before snapshot)")
    if days_short > 3:
        problems.append(f"ratings END is {days_short:.1f} days before the snapshot date — likely TRUNCATED (this was the original bug)")


def check_notes(root, snap_ms, problems):
    print("\n== 3. NOTES coverage (recent notes present) ==")
    files = sorted(glob.glob(f"{root}/notes/notes-*.tsv"))
    if not files:
        problems.append("notes: no shards found"); return
    mx = 0
    for f in files:
        h = header(f); ci = col_index(h, "createdAtMillis")
        # notes files are not guaranteed time-sorted; scan col for max (bounded, streams)
        fmax = 0
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            fh.readline()
            for line in fh:
                p = line.split("\t", ci + 1)
                if len(p) > ci and p[ci].isdigit():
                    v = int(p[ci])
                    if v > fmax:
                        fmax = v
        mx = max(mx, fmax)
        print(f"  {os.path.basename(f):22s} max note date {ms_to_iso(fmax)}")
    days_short = (snap_ms - mx) / 86400000.0
    print(f"  --> newest note: {ms_to_iso(mx)}  ({days_short:.1f} days before snapshot)")
    if days_short > 3:
        problems.append(f"notes newest is {days_short:.1f} days before snapshot — notes may be truncated")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--snapshot", default="2026-06-30", help="YYYY-MM-DD")
    args = ap.parse_args()
    root = args.root.rstrip("/")
    snap_ms = int(dt.datetime.strptime(args.snapshot, "%Y-%m-%d")
                  .replace(tzinfo=dt.timezone.utc).timestamp() * 1000)

    print(f"Verifying {root} against snapshot {args.snapshot}")
    problems = []
    # shard presence vs recorded counts
    sc = f"{root}/.shardcounts"
    if os.path.exists(sc):
        print("\n== 0. SHARD PRESENCE (recorded at download time) ==")
        for line in open(sc):
            kind, n = line.split(); n = int(n)
            patt = {"notes": f"{root}/notes/notes-*.tsv", "ratings": f"{root}/ratings/ratings-*.tsv",
                    "noteStatusHistory": f"{root}/noteStatusHistory-*.tsv",
                    "userEnrollment": f"{root}/userEnrollment-*.tsv"}[kind]
            got = len(glob.glob(patt))
            flag = "ok" if got == n else "MISSING"
            if got != n:
                problems.append(f"{kind}: recorded {n} shards but {got} TSVs on disk")
            print(f"  {flag:8s} {kind:20s} recorded={n} on_disk={got}")

    check_schema(root, problems)
    check_ratings(root, snap_ms, problems)
    check_notes(root, snap_ms, problems)

    print("\n" + "=" * 60)
    if problems:
        print("VERIFICATION FAILED:")
        for p in problems:
            print("  - " + p)
        sys.exit(1)
    print("VERIFICATION PASSED — snapshot is complete and consistent.")


if __name__ == "__main__":
    main()
