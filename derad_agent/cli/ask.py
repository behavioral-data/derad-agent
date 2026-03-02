#!/usr/bin/env python
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
    from derad_agent.runtime.landscape_api import retrieve_statement_landscape
    from derad_agent.llm.config import INDEX_ROOT, INDEX_NAME
    from derad_agent.indexing.index_builder import get_global_index_dir
    from derad_agent.cli.ui import (
        console, RichLogger, create_status,
    )
except ImportError:
    print("\n❌ ERROR: Could not import 'derad_agent'.")
    print("   Please run this script as a module from the project root:")
    print("   python -m derad_agent.cli.ask ...")
    sys.exit(1)

def check_index_exists(index_root: pathlib.Path) -> bool:
    """Checks if global FAISS index exists."""
    base_dir = get_global_index_dir(index_root)
    index_dir_path = base_dir / INDEX_NAME
    faiss_file = index_dir_path / "index.faiss"
    pkl_file = index_dir_path / "index.pkl"
    return index_dir_path.is_dir() and faiss_file.exists() and pkl_file.exists()


def _to_jsonable(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(v) for v in value]
    if hasattr(value, "model_dump") and callable(getattr(value, "model_dump")):
        try:
            return _to_jsonable(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "dict") and callable(getattr(value, "dict")):
        try:
            return _to_jsonable(value.dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            return _to_jsonable(vars(value))
        except Exception:
            pass
    return repr(value)


def _save_full_output(
    statement: str,
    args,
    duration_seconds: float,
    result_payload: dict,
) -> pathlib.Path:
    out_dir = pathlib.Path("results") / "ask_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"{ts}.json"
    payload = {
        "saved_at_utc": ts,
        "statement": statement,
        "run_config": {
            "index_root": str(args.index_root),
            "similarity_min": args.similarity_min,
            "max_points": args.max_points,
            "filter_before_utc": args.filter_before_utc,
            "exclude_tweet_id": args.exclude_tweet_id,
            "verbose": args.verbose,
        },
        "duration_seconds": round(duration_seconds, 3),
        "result": _to_jsonable(result_payload),
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path

def main():
    ap = argparse.ArgumentParser(
        description="Build a statement-conditioned Community Notes misleadingness landscape",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    # Required arguments
    ap.add_argument("--statement", dest="statement", required=True, help="Statement to analyze")
    ap.add_argument("--index-root", dest="index_root", default=INDEX_ROOT, type=pathlib.Path, 
                    help=f"Root directory for user indexes (default: {INDEX_ROOT})")
    
    # Filtering options
    ap.add_argument("--filter-before-utc", type=float)
    ap.add_argument("--exclude-tweet-id", type=str)
    ap.add_argument("--similarity-min", type=float, default=0.0,
                    help="Minimum retrieval similarity required for seed notes before thread expansion")
    ap.add_argument("--max-points", type=int, default=300,
                    help="Maximum number of 1D points to return")
    
    # Output options
    ap.add_argument("--verbose", action="store_true", help="Show detailed logging")
    
    args = ap.parse_args()

    index_dir = get_global_index_dir(args.index_root)
    
    # Check if index exists
    if not check_index_exists(args.index_root):
        console.print("[bold red]ERROR: Global Community Notes index not found[/bold red]")
        console.print(f"   Expected location: {index_dir}")
        sys.exit(1)

    # Build kwargs for landscape API
    agent_kwargs = {
        "statement": args.statement,
        "index_root": args.index_root,
        "verbose": args.verbose,
    }

    if args.filter_before_utc is not None:
        agent_kwargs["filter_docs_before_utc"] = args.filter_before_utc
    if args.exclude_tweet_id is not None:
        agent_kwargs["exclude_tweet_id"] = args.exclude_tweet_id
    agent_kwargs["similarity_min"] = args.similarity_min
    agent_kwargs["max_points"] = args.max_points

    # Run the landscape API with rich UI
    console.print("\n[bold]Building statement-conditioned misleadingness landscape[/bold]")
    console.print(f"[dim]Statement: {args.statement}[/dim]\n")

    try:
        with create_status("[bold blue]Initializing agent...[/bold blue]") as status:
            # Inject rich logger
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
    landscape = res.get("statement_landscape") or {}
    summary_text = (landscape.get("landscape_summary") or "").strip()
    reasons = landscape.get("key_reasons") or []
    if reasons:
        reason_lines = []
        for idx, reason in enumerate(reasons, start=1):
            line = str(reason.get("reason") or "").strip()
            if line:
                reason_lines.append(f"{idx}. {line}")
                links = reason.get("evidence_links") or []
                if isinstance(links, list):
                    for link in links:
                        if isinstance(link, str) and link.strip():
                            reason_lines.append(f"   - source: {link.strip()}")
        if reason_lines:
            summary_text = f"{summary_text}\n\nKey reasons:\n" + "\n".join(reason_lines)

    if summary_text:
        console.print(Panel(summary_text, title="Statement Landscape", border_style="yellow"))
    else:
        console.print(Panel("No landscape answer generated.", title="Statement Landscape", border_style="yellow"))

    console.print(f"[dim]Full run output saved to: {output_file}[/dim]")

if __name__ == "__main__":
    main()
