"""Repository hygiene acceptance tests for the public research artifact.

These tests gate a public release of the repository. They shell out to git
(never import project code) so they stay valid even if the package layout
changes, and they inspect *tracked* content at HEAD — what a stranger would
actually receive on clone — rather than the working tree.

Run as part of the normal suite: ``python3 -m pytest tests/ -q``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Commits that must remain reachable after consolidation (guards against a
# bad merge or an over-eager gc). See the release notes in the cleanup report.
LOCAL_TIP_BEFORE_MERGE = "4ebe10ded6b76758563af649220c114196b13c8e"  # video-path-t9 pre-merge tip
COLLABORATOR_COMMIT = "d74d352ae33520efce4179983f4580376dc84a52"  # "Move post selection and data analysis to main branch"


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=check,
    )


def _tracked_files() -> list[str]:
    return _git("ls-files").stdout.splitlines()


# ---------------------------------------------------------------------------
# 1. Secrets
# ---------------------------------------------------------------------------

# Tracked filenames that *look* secret-bearing but are known benign:
#   scripts/seed_keyvault.sh      -- writes env values *into* Azure Key Vault; reads
#                                    them from the caller's environment, contains none.
#   study/post_selection/keywords.py -- topic keyword lists ("keywords" matches "key").
#   tests/test_info_token_size.py -- checks LLM *token counts* of the info page.
SECRET_NAME_ALLOWLIST = {
    "scripts/seed_keyvault.sh",
    "study/post_selection/keywords.py",
    "tests/test_info_token_size.py",
}

SECRET_NAME_RE = re.compile(
    r"(^|/)[^/]*(key|token|secret|credential|passwd|password|\.env|\.pem)[^/]*$",
    re.IGNORECASE,
)

# Coarse (POSIX ERE, for `git grep -E` pre-filter) and precise (Python re,
# post-filter) patterns for secret-shaped *content*.
SECRET_CONTENT_PATTERNS: list[tuple[str, re.Pattern]] = [
    # OpenAI/Anthropic-style keys. The lookbehind rejects substrings of words
    # ("...-risk-large-studies-..." in a URL contains "sk-").
    (
        r"sk-(ant-|proj-)?[A-Za-z0-9]{20,}",
        re.compile(r"(?<![A-Za-z0-9])sk-(ant-|proj-)?[A-Za-z0-9]{20,}"),
    ),
    # Azure storage connection strings.
    (
        r"AccountKey=[A-Za-z0-9+/=]{16,}",
        re.compile(r"AccountKey=[A-Za-z0-9+/=]{16,}"),
    ),
    # Bearer tokens with a literal value (placeholders like `Bearer $TOKEN`
    # or `Bearer <token>` do not match).
    (
        r"[Bb]earer +[A-Za-z0-9._~+/=-]{25,}",
        re.compile(r"[Bb]earer +[A-Za-z0-9._~+/=-]{25,}"),
    ),
    # AWS, GitHub, Slack, private key blocks.
    (
        r"(AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,}|"
        r"xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)",
        re.compile(
            r"(AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,}|"
            r"xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
        ),
    ),
    # Env-style assignment of a *literal* credential (quoted, long, no $/<{
    # placeholder chars). Covers the Prolific and study session token shapes.
    (
        r"(API_KEY|APIKEY|API_SECRET|ACCESS_TOKEN|SECRET_KEY|PASSWORD|"
        r"SESSION_TOKEN|PROLIFIC[A-Z_]*TOKEN)[[:space:]]*[=:][[:space:]]*"
        r"[\"'][A-Za-z0-9+/_.-]{16,}[\"']",
        re.compile(
            r"(API_KEY|APIKEY|API_SECRET|ACCESS_TOKEN|SECRET_KEY|PASSWORD|"
            r"SESSION_TOKEN|PROLIFIC[A-Z_]*TOKEN)\s*[=:]\s*"
            r"[\"'][A-Za-z0-9+/_.-]{16,}[\"']"
        ),
    ),
]


def test_no_secrets_tracked():
    # (a) No tracked *path* is secret-shaped, apart from the audited allowlist.
    suspicious_names = [
        f
        for f in _tracked_files()
        if SECRET_NAME_RE.search(f) and f not in SECRET_NAME_ALLOWLIST
    ]
    assert not suspicious_names, (
        "Tracked filenames look secret-bearing (audit them, then either remove "
        f"or extend the allowlist with a justification): {suspicious_names}"
    )

    # (b) No tracked *content* matches a secret shape.
    hits: list[str] = []
    for coarse, precise in SECRET_CONTENT_PATTERNS:
        res = _git("grep", "-I", "-n", "-E", coarse, "HEAD", "--", ".", check=False)
        for line in res.stdout.splitlines():
            # git grep output: HEAD:path:lineno:content
            try:
                _, path, lineno, content = line.split(":", 3)
            except ValueError:
                continue
            if precise.search(content):
                hits.append(f"{path}:{lineno}")
    assert not hits, f"Secret-shaped content in tracked files: {hits}"


# ---------------------------------------------------------------------------
# 2. Participant PII in notebook outputs (the one that matters most)
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
HEX24_RE = re.compile(r"\b[0-9a-f]{24}\b")  # Prolific-style participant ids


def test_no_pii_in_notebook_outputs():
    notebooks = [f for f in _tracked_files() if f.endswith(".ipynb")]
    offenders: list[str] = []
    for nb_path in notebooks:
        raw = _git("show", f"HEAD:{nb_path}").stdout
        nb = json.loads(raw)
        bad_cells = []
        for idx, cell in enumerate(nb.get("cells", [])):
            blob = json.dumps(cell.get("outputs", []))
            if EMAIL_RE.search(blob) or HEX24_RE.search(blob):
                bad_cells.append(idx)
        if bad_cells:
            offenders.append(f"{nb_path} (cells {bad_cells})")
    assert not offenders, (
        "Participant identifiers (email addresses / 24-hex ids) found in "
        f"notebook cell OUTPUTS — strip outputs before committing: {offenders}"
    )


# ---------------------------------------------------------------------------
# 3. No large tracked files
# ---------------------------------------------------------------------------

MAX_TRACKED_BLOB_BYTES = 15 * 1024 * 1024  # 15 MB


def test_no_large_tracked_files():
    out = _git("ls-tree", "-r", "-l", "HEAD").stdout
    too_big = []
    for line in out.splitlines():
        meta, path = line.split("\t", 1)
        parts = meta.split()
        if parts[1] != "blob":
            continue
        size = int(parts[3])
        if size > MAX_TRACKED_BLOB_BYTES:
            too_big.append(f"{path} ({size} bytes)")
    assert not too_big, f"Tracked blobs exceed 15 MB: {too_big}"


# ---------------------------------------------------------------------------
# 4. .gitignore covers scratch / generated / oversized local data
# ---------------------------------------------------------------------------

MUST_BE_IGNORED = [
    ".claude/",
    ".playwright-mcp/",
    "paper/",
    "tsv_generation/cn_data_20260630/",
    "tsv_generation/cn_data_20260630_run.out",
    "example.out",  # representative of the *.out rule
    "final_final_output.csv",
]


def test_gitignore_covers_generated_and_scratch():
    not_ignored = [
        p
        for p in MUST_BE_IGNORED
        if _git("check-ignore", "-q", p, check=False).returncode != 0
    ]
    assert not not_ignored, f".gitignore does not cover: {not_ignored}"


# ---------------------------------------------------------------------------
# 5. Clean working tree
# ---------------------------------------------------------------------------

def test_working_tree_clean():
    status = _git("status", "--porcelain").stdout.strip()
    assert status == "", f"Working tree is not clean:\n{status}"


# ---------------------------------------------------------------------------
# 6. Single canonical analysis notebook
# ---------------------------------------------------------------------------

def test_single_canonical_analysis_notebook():
    copies = [f for f in _tracked_files() if f.endswith("daily_survey.ipynb")]
    assert copies == ["study/data_analysis/daily_survey.ipynb"], (
        "Expected exactly one canonical analysis notebook at "
        f"study/data_analysis/daily_survey.ipynb, found: {copies}"
    )


# ---------------------------------------------------------------------------
# 7. README covers reproduction
# ---------------------------------------------------------------------------

README_REQUIREMENTS = {
    "install/setup instructions": r"(?i)\b(install|set ?up)\b",
    "how to run the fact-check pipeline": r"(?i)fact-?check",
    "how to regenerate the 108-post stimuli": r"108",
    "how to run the tests": r"(?i)pytest",
    "where the study materials live": r"study/",
    "data-availability statement": r"(?i)data availability",
    "participant data exclusion": r"(?i)participant",
    "raw snapshot exclusion / re-download": r"(?i)(community notes|snapshot)",
}


def test_readme_covers_reproduction():
    readme = REPO_ROOT / "README.md"
    assert readme.exists(), "Top-level README.md is missing"
    text = readme.read_text(encoding="utf-8")
    missing = [
        label
        for label, pattern in README_REQUIREMENTS.items()
        if not re.search(pattern, text)
    ]
    assert not missing, f"README.md does not cover: {missing}"


# ---------------------------------------------------------------------------
# 8. History integrity (guard against a bad merge or gc)
# ---------------------------------------------------------------------------

def test_history_integrity():
    for ref in ("origin/main", "video-path-t9"):
        assert _git("rev-parse", "--verify", f"{ref}^{{commit}}", check=False).returncode == 0, (
            f"ref {ref} does not resolve"
        )
    merge_base = _git("merge-base", "origin/main", "video-path-t9").stdout.strip()
    for ref in ("origin/main", "video-path-t9"):
        assert (
            _git("merge-base", "--is-ancestor", merge_base, ref, check=False).returncode == 0
        ), f"merge base {merge_base} is not an ancestor of {ref}"
    # Both pre-consolidation tips must remain reachable from HEAD.
    for sha in (LOCAL_TIP_BEFORE_MERGE, COLLABORATOR_COMMIT):
        assert (
            _git("merge-base", "--is-ancestor", sha, "HEAD", check=False).returncode == 0
        ), f"commit {sha} is no longer reachable from HEAD"


# ---------------------------------------------------------------------------
# 9. Bloat reclamation: .git must stay small
# ---------------------------------------------------------------------------
# Historical context: the CN snapshot directories (tens of GB) were once
# staged with `git add -A` and abandoned, leaving unreachable objects that
# ballooned .git to 34 GB. After `git reflog expire --expire=now --all &&
# git gc --prune=now`, .git should be roughly the reachable content
# (~0.3 GB). This test catches a recurrence *before* the next release.

MAX_GIT_DIR_BYTES = 2 * 1024**3  # 2 GB


def test_git_dir_under_two_gb():
    total = 0
    for dirpath, _dirnames, filenames in os.walk(REPO_ROOT / ".git"):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass  # transient lock/tmp files
    assert total < MAX_GIT_DIR_BYTES, (
        f".git is {total / 1024**3:.1f} GB (limit 2 GB) — giant objects have "
        "likely been staged again; see the bloat-reclamation notes above."
    )
