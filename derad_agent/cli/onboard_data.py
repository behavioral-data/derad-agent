#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import zipfile

INDEX_FOLDER_URL_DEFAULT = "https://drive.google.com/drive/folders/1L3xD8DRFDDaVraH7ikCa3a16QqExPtkG?usp=drive_link"
NOTES_ZIP_URL_DEFAULT = "https://drive.google.com/file/d/1o864Ed-zXP7OJK42qISZAaEFK3K8qOJG/view?usp=drive_link"


def _repo_root() -> pathlib.Path:
    # File path: derad_agent/cli/onboard_data.py -> repo root is 3 parents up
    return pathlib.Path(__file__).resolve().parent.parent.parent


def _manifest_path(root: pathlib.Path) -> pathlib.Path:
    return root / "data" / "manifest.json"


def _checksums_path(root: pathlib.Path) -> pathlib.Path:
    return root / "data" / "checksums.sha256"


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(cmd)}")


def _require_gdown() -> None:
    try:
        import gdown  # noqa: F401
    except Exception as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("Missing dependency: gdown. Install with `pip install gdown`.") from exc


def _download_index(index_folder_url: str, index_root: pathlib.Path) -> None:
    target_dir = index_root / "community_notes_global" / "faiss_idx"
    target_dir.mkdir(parents=True, exist_ok=True)
    print("")
    print("[onboard] Downloading prebuilt FAISS index from Google Drive...")
    print(f"[onboard] Target root: {index_root}")
    _run([sys.executable, "-m", "gdown", "--folder", index_folder_url, "-O", str(target_dir)])


def _download_and_extract_notes(notes_zip_url: str, notes_root: pathlib.Path) -> pathlib.Path:
    notes_root.mkdir(parents=True, exist_ok=True)
    zip_path = notes_root / "notes.zip"
    print("")
    print("[onboard] Downloading full notes zip from Google Drive...")
    print(f"[onboard] Target zip: {zip_path}")
    _run([sys.executable, "-m", "gdown", "--fuzzy", notes_zip_url, "-O", str(zip_path)])

    print("")
    print(f"[onboard] Extracting notes zip to {notes_root}...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(notes_root)
    return zip_path


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_data_check(root: pathlib.Path, *, write: bool = False) -> None:
    manifest_path = _manifest_path(root)
    checksums_path = _checksums_path(root)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    file_specs = manifest.get("files", [])
    if not isinstance(file_specs, list):
        raise RuntimeError("Invalid manifest: 'files' must be a list.")

    checksum_entries: list[tuple[str, str]] = []
    failures: list[str] = []
    for spec in file_specs:
        rel_path = str(spec.get("path", "")).strip()
        required = bool(spec.get("required", True))
        if not rel_path:
            failures.append("Manifest entry missing 'path'.")
            continue

        file_path = root / rel_path
        if not file_path.exists():
            if required:
                failures.append(f"Missing required file: {rel_path}")
            continue

        digest = _sha256(file_path)
        checksum_entries.append((rel_path, digest))
        expected = str(spec.get("sha256", "")).strip()
        if write:
            spec["sha256"] = digest
        elif expected and expected != digest:
            failures.append(f"Checksum mismatch: {rel_path}")
        elif not expected:
            failures.append(f"Missing checksum in manifest for: {rel_path}")

    if write:
        lines = [f"{digest}  {rel_path}" for rel_path, digest in checksum_entries]
        checksums_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"[onboard] Wrote {len(checksum_entries)} checksum(s).")
        return

    if failures:
        raise RuntimeError("[onboard] Data check failed:\n- " + "\n- ".join(failures))
    print(f"[onboard] Data check passed ({len(checksum_entries)} file(s) verified).")


def _print_finish(index_root: pathlib.Path, notes_root: pathlib.Path) -> None:
    print("")
    print("[onboard] Done.")
    print("")
    print("Set these env vars in your shell (or in derad_agent/llm/.env):")
    print(f'  export DERAD_AGENT_INDEX_ROOT="{index_root}"')
    print(f'  export DERAD_AGENT_NOTES_TSV_ROOT="{notes_root}"')
    print("")
    print("Expected FAISS layout:")
    print(f"  {index_root}/community_notes_global/faiss_idx/index.faiss")
    print(f"  {index_root}/community_notes_global/faiss_idx/index.pkl")
    print("")
    print("Smoke test:")
    print(
        '  python -m derad_agent.cli.ask --statement "Mail-in voting increases fraud." '
        f'--index-root "{index_root}"'
    )


def main() -> int:
    root = _repo_root()
    parser = argparse.ArgumentParser(
        description="Download onboarding artifacts and validate tracked sample data."
    )
    parser.add_argument("--index-folder-url", default=INDEX_FOLDER_URL_DEFAULT)
    parser.add_argument("--notes-zip-url", default=NOTES_ZIP_URL_DEFAULT)
    parser.add_argument("--index-root", type=pathlib.Path, default=root / "indexes")
    parser.add_argument("--notes-root", type=pathlib.Path, default=root / "data" / "full")
    parser.add_argument("--skip-index", action="store_true")
    parser.add_argument("--skip-notes", action="store_true")
    parser.add_argument("--skip-data-check", action="store_true", help="Skip sample fixture checksum validation.")
    parser.add_argument(
        "--write-checksums",
        action="store_true",
        help="Recompute and write checksums to data/checksums.sha256 and data/manifest.json.",
    )
    parser.add_argument(
        "--data-check-only",
        action="store_true",
        help="Run only the sample fixture data check (no downloads).",
    )
    args = parser.parse_args()

    try:
        if args.data_check_only:
            _run_data_check(root, write=args.write_checksums)
            return 0

        do_download_index = not args.skip_index
        do_download_notes = not args.skip_notes
        if do_download_index or do_download_notes:
            _require_gdown()

        index_root = args.index_root.resolve()
        notes_root = args.notes_root.resolve()
        index_root.mkdir(parents=True, exist_ok=True)
        notes_root.mkdir(parents=True, exist_ok=True)

        if do_download_index:
            _download_index(args.index_folder_url, index_root)
        if do_download_notes:
            _download_and_extract_notes(args.notes_zip_url, notes_root)

        if not args.skip_data_check:
            _run_data_check(root, write=args.write_checksums)

        _print_finish(index_root, notes_root)
        return 0
    except Exception as exc:
        print(f"[onboard] ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
