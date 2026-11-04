"""Run provenance recorded alongside every eval's evidence."""

from __future__ import annotations

import hashlib
import subprocess

from skillkit.evals.runner.toolkit import TOOLKIT_NAME, TOOLKIT_VERSION
from skillkit.evals.runner.types import CaseSpec


def repo_version_info(repo_root, run_git=subprocess.run) -> dict:
    """Git identity of the catalog, tolerant of a missing repository or git."""
    sha = _git_text(run_git, ["rev-parse", "HEAD"], repo_root)
    porcelain = _git_text(run_git, ["status", "--porcelain"], repo_root)
    diff = _git_text(run_git, ["diff"], repo_root)
    if sha is None and porcelain is None:
        return {"git_sha": "unknown", "dirty": None, "dirty_tree_hash": ""}
    git_sha = (sha or "").strip() or "unknown"
    dirty = bool((porcelain or "").strip() or (diff or "").strip())
    dirty_tree_hash = ""
    if dirty:
        material = f"{porcelain}\n{diff}".encode("utf-8", errors="replace")
        dirty_tree_hash = hashlib.sha256(material).hexdigest()
    return {"git_sha": git_sha, "dirty": dirty, "dirty_tree_hash": dirty_tree_hash}


def _git_text(run_git, args, repo_root) -> str | None:
    try:
        proc = run_git(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def build_metadata(
    case: CaseSpec,
    *,
    host: str,
    created_central: str,
    version_info: dict,
    deviations: list[str],
    model_selection: dict | None = None,
    turns: dict | None = None,
) -> dict:
    """Assemble the ``metadata.json`` payload for a run."""
    payload = {
        "case_id": case.case_id,
        "prompt": case.prompt,
        "model": case.model,
        "host": host,
        "created_central": created_central,
        "timeout_s": case.timeout_s,
        "repo": version_info,
        "toolkit": {"name": TOOLKIT_NAME, "version": TOOLKIT_VERSION},
        "deviations": list(deviations),
    }
    if case.shims:
        payload["shims"] = case.shims
    if model_selection is not None:
        payload["model_selection"] = model_selection
    if turns is not None:
        payload["turns"] = turns
    return payload
