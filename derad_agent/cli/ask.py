#!/usr/bin/env python
"""CLI for replying to a statement using Community Notes evidence.

Loads the prebuilt tweet-level notes index, plans queries from the
statement, retrieves similar tweets, filters their notes to
``CURRENTLY_RATED_HELPFUL`` (latest 10 per tweet), and asks an LLM to compose
a reply grounded in those notes.

Usage::

    python -m derad_agent.cli.ask --statement "Mail-in voting increases fraud."
"""
import argparse
import json
import pathlib
import time
import sys
from datetime import datetime, timezone
from rich.panel import Panel

# Ensure project root is in path
sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent.parent))

try:
    from derad_agent.runtime.landscape_api import (
        get_notes_index_dir,
        retrieve_statement_landscape,
    )
    from derad_agent.llm.config import INDEX_ROOT
    from derad_agent.llm.prompts import RESPONSE_STYLES
    from derad_agent.cli.ui import (
        console, RichLogger, create_status,
    )
except ImportError:
    print("\n❌ ERROR: Could not import 'derad_agent'.")
    print("   Please run this script as a module from the project root:")
    print("   python -m derad_agent.cli.ask ...")
    sys.exit(1)


def check_notes_index_exists(index_root: pathlib.Path) -> bool:
    """Check that the notes index artifacts are on disk."""
    base_dir = get_notes_index_dir(index_root)
    return (
        base_dir.is_dir()
        and (base_dir / "tweet_ids.npy").exists()
        and (base_dir / "embeddings.npy").exists()
        and (base_dir / "notes_cache.json").exists()
    )


def _save_full_output(
    statement: str,
    args,
    duration_seconds: float,
    result_payload: dict,
) -> pathlib.Path:
    """Persist the complete pipeline result as a timestamped JSON file."""
    out_dir = pathlib.Path("results") / "ask_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"{ts}.json"
    payload = {
        "saved_at_utc": ts,
        "statement": statement,
        "run_config": {
            "index_root": str(args.index_root),
            "k_per_query": args.k_per_query,
            "notes_per_tweet": args.notes_per_tweet,
            "similarity_min": args.similarity_min,
            "exclude_tweet_id": args.exclude_tweet_id,
            "style": args.style,
            "max_sources": args.max_sources,
            "verbose": args.verbose,
        },
        "duration_seconds": round(duration_seconds, 3),
        "result": result_payload,
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


def main():
    ap = argparse.ArgumentParser(
        description="Compose a Community-Notes-grounded reply to a statement",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    ap.add_argument("--statement", dest="statement", required=True, help="Statement to reply to")
    ap.add_argument(
        "--index-root", dest="index_root", default=INDEX_ROOT, type=pathlib.Path,
        help=f"Root directory containing the notes index (default: {INDEX_ROOT})",
    )
    ap.add_argument("--exclude-tweet-id", type=str,
                    help="Exclude this tweet ID from retrieval (e.g. self-exclusion).")
    ap.add_argument("--similarity-min", type=float, default=0.0,
                    help="Minimum cosine similarity for retrieved tweets")
    ap.add_argument("--k-per-query", type=int, default=25,
                    help="Number of tweets to fetch per planner query")
    ap.add_argument("--notes-per-tweet", type=int, default=10,
                    help="Cap on CURRENTLY_RATED_HELPFUL notes kept per tweet (latest first)")
    ap.add_argument("--style", type=str, default="neutral",
                    choices=RESPONSE_STYLES,
                    help="Response style: agreeable, neutral, or satirical (default: neutral)")
    ap.add_argument("--max-sources", type=int, default=5, metavar="N",
                    help="Max deduplicated source URLs to show below the reply (default: 5).")
    ap.add_argument("--verbose", action="store_true", help="Show detailed logging")

    args = ap.parse_args()

    index_dir = get_notes_index_dir(args.index_root)
    if not check_notes_index_exists(args.index_root):
        console.print("[bold red]ERROR: Notes index not found[/bold red]")
        console.print(f"   Expected location: {index_dir}")
        console.print("   Build it with: python -m derad_agent.cli.embed_notes <notes.tsv> --out indexes/notes_index")
        sys.exit(1)

    agent_kwargs = {
        "statement": args.statement,
        "index_root": args.index_root,
        "k_per_query": args.k_per_query,
        "notes_per_tweet": args.notes_per_tweet,
        "similarity_min": args.similarity_min,
        "style": args.style,
        "verbose": args.verbose,
    }
    if args.exclude_tweet_id is not None:
        agent_kwargs["exclude_tweet_id"] = args.exclude_tweet_id

    console.print("\n[bold]Composing a Community-Notes-grounded reply[/bold]")
    console.print(f"[dim]Statement: {args.statement}[/dim]\n")

    try:
        with create_status("[bold blue]Initializing agent...[/bold blue]") as status:
            agent_kwargs["logger"] = RichLogger(status, verbose=args.verbose)

            start_time = time.time()
            res = retrieve_statement_landscape(**agent_kwargs)
            duration = time.time() - start_time

    except Exception as e:
        console.print(f"\n[bold red]Execution failed:[/bold red] {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    output_file = _save_full_output(args.statement, args, duration, res)

    reply = res.get("reply") or {}
    response_text = (reply.get("response") or "").strip()

    sources: list = []
    seen_urls: set = set()
    max_src = max(0, args.max_sources)
    for reason in (reply.get("reasons") or []):
        if max_src == 0 or len(sources) >= max_src:
            break
        for link in (reason.get("evidence_links") or []):
            if len(sources) >= max_src:
                break
            if isinstance(link, str) and link.strip() and link.strip() not in seen_urls:
                sources.append(link.strip())
                seen_urls.add(link.strip())
    if sources:
        response_text = response_text + "\n\nSources:\n" + "\n".join(sources)

    if response_text:
        console.print(Panel(response_text, title="Response", border_style="yellow"))
    else:
        console.print(Panel("No response generated.", title="Response", border_style="yellow"))

    console.print(f"[dim]Full run output saved to: {output_file}[/dim]")


if __name__ == "__main__":
    main()
