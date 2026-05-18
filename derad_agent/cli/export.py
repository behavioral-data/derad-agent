#!/usr/bin/env python
"""Export MentionEvents and MentionDrops from Azure Table Storage.

Uses DefaultAzureCredential (App Service UAMI in prod, az-cli locally).
DERAD_TABLES_ENDPOINT must be set, or pass --endpoint explicitly.

Output formats:
  jsonl    — one JSON object per line, streamed (default)
  parquet  — columnar, requires pandas + pyarrow (installed separately)

Examples:

    # All events from May 2026 onward, to stdout
    derad-export events --since 2026-05-01

    # Events for a date range, saved to a file
    derad-export events --since 2026-01-01 --until 2026-05-31 --output events.jsonl

    # Drops as Parquet
    derad-export drops --since 2026-01-01 --format parquet --output drops.parquet

    # Pipe into jq
    derad-export events --output - | jq 'select(.outcome == "pipeline_error")'
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from typing import Iterator

# ── Schema constants ─────────────────────────────────────────────────────────

_EVENTS_TABLE = "MentionEvents"
_DROPS_TABLE = "MentionDrops"

# Fields whose stored name ends in _json and contain a JSON-encoded value.
# On export we strip the suffix and decode the value.
_EVENTS_JSON_FIELDS = {"queries_json", "cited_note_ids_json", "cited_tweet_ids_json"}
_DROPS_JSON_FIELDS = {"extra_json"}

# Azure Table internal fields we strip from output.
_SKIP_FIELDS = {"PartitionKey", "RowKey", "etag", "Timestamp", "metadata"}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _month_range(since: date, until: date) -> list[str]:
    """Return all YYYY-MM strings between since and until (inclusive)."""
    months: list[str] = []
    cur = since.replace(day=1)
    end = until.replace(day=1)
    while cur <= end:
        months.append(cur.strftime("%Y-%m"))
        cur = cur.replace(month=cur.month % 12 + 1, year=cur.year + (1 if cur.month == 12 else 0))
    return months


def _parse_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date '{s}'; expected YYYY-MM-DD")


def _get_table_client(endpoint: str, table_name: str):
    from azure.data.tables import TableServiceClient
    from azure.identity import DefaultAzureCredential

    svc = TableServiceClient(endpoint=endpoint, credential=DefaultAzureCredential())
    return svc.get_table_client(table_name)


def _decode_entity(entity: dict, json_fields: set[str]) -> dict:
    """Flatten an Azure Table entity into a plain dict for export.

    - Strips PartitionKey, RowKey, and SDK metadata fields.
    - Decodes _json suffix fields into their decoded types.
    - Serializes datetime objects to ISO strings.
    """
    result: dict = {}
    for k, v in entity.items():
        if k in _SKIP_FIELDS or k.startswith("@"):
            continue
        if k in json_fields:
            out_key = k[: -len("_json")]
            try:
                result[out_key] = json.loads(v) if isinstance(v, str) and v else (v or [])
            except (ValueError, TypeError):
                result[out_key] = v
        elif isinstance(v, datetime):
            result[k] = v.isoformat()
        else:
            result[k] = v
    return result


def _iter_rows(
    endpoint: str,
    table_name: str,
    json_fields: set[str],
    months: list[str],
) -> Iterator[dict]:
    """Stream decoded rows from the table, month-partition by month-partition."""
    client = _get_table_client(endpoint, table_name)
    for month in months:
        filter_q = f"PartitionKey eq '{month}'"
        for entity in client.query_entities(query_filter=filter_q):
            yield _decode_entity(dict(entity), json_fields)


def _write_jsonl(rows: Iterator[dict], out) -> int:
    count = 0
    for row in rows:
        out.write(json.dumps(row, ensure_ascii=False, default=str))
        out.write("\n")
        count += 1
    return count


def _write_parquet(rows: Iterator[dict], path: str) -> int:
    try:
        import pandas as pd
    except ImportError:
        print(
            "ERROR: Parquet output requires pandas and pyarrow.\n"
            "  pip install pandas pyarrow",
            file=sys.stderr,
        )
        sys.exit(1)

    records = list(rows)
    if not records:
        print("Warning: 0 rows — writing empty Parquet file", file=sys.stderr)
    df = pd.DataFrame(records)
    df.to_parquet(path, index=False)
    return len(df)


# ── Subcommands ──────────────────────────────────────────────────────────────

def _run_export(args, table_name: str, json_fields: set[str]) -> int:
    endpoint = args.endpoint or os.environ.get("DERAD_TABLES_ENDPOINT")
    if not endpoint:
        print(
            "ERROR: Table Storage endpoint not set. "
            "Set DERAD_TABLES_ENDPOINT or pass --endpoint.",
            file=sys.stderr,
        )
        return 1

    since: date = args.since or date(2026, 1, 1)
    until: date = args.until or date.today()
    if since > until:
        print(f"ERROR: --since {since} is after --until {until}", file=sys.stderr)
        return 1

    months = _month_range(since, until)
    rows = _iter_rows(endpoint, table_name, json_fields, months)

    fmt: str = args.format
    out_path: str | None = args.output

    if fmt == "parquet":
        if not out_path or out_path == "-":
            print("ERROR: --format parquet requires --output <path> (not stdout)", file=sys.stderr)
            return 1
        count = _write_parquet(rows, out_path)
        print(f"Wrote {count} rows to {out_path}", file=sys.stderr)
        return 0

    # JSONL
    if not out_path or out_path == "-":
        count = _write_jsonl(rows, sys.stdout)
    else:
        with open(out_path, "w", encoding="utf-8") as f:
            count = _write_jsonl(rows, f)
        print(f"Wrote {count} rows to {out_path}", file=sys.stderr)
    return 0


def cmd_events(args) -> int:
    return _run_export(args, _EVENTS_TABLE, _EVENTS_JSON_FIELDS)


def cmd_drops(args) -> int:
    return _run_export(args, _DROPS_TABLE, _DROPS_JSON_FIELDS)


# ── argparse plumbing ─────────────────────────────────────────────────────────

def _add_common_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "--since", type=_parse_date, metavar="YYYY-MM-DD",
        help="Start date (inclusive, UTC). Default: 2026-01-01.",
    )
    sp.add_argument(
        "--until", type=_parse_date, metavar="YYYY-MM-DD",
        help="End date (inclusive, UTC). Default: today.",
    )
    sp.add_argument(
        "--output", "-o", metavar="PATH",
        help="Output file path. Use '-' or omit for stdout (JSONL only).",
    )
    sp.add_argument(
        "--format", choices=["jsonl", "parquet"], default="jsonl",
        help="Output format. parquet requires pandas+pyarrow. Default: jsonl.",
    )
    sp.add_argument(
        "--endpoint", metavar="URL",
        help="Table Storage endpoint. Overrides DERAD_TABLES_ENDPOINT.",
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="derad-export",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("events", help="Export MentionEvents (accepted + processed mentions).")
    _add_common_args(sp)
    sp.set_defaults(func=cmd_events)

    sp = sub.add_parser("drops", help="Export MentionDrops (filtered/rejected mentions).")
    _add_common_args(sp)
    sp.set_defaults(func=cmd_drops)

    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
