"""Pre-launch verification checks over the evidence recorded for a run.

Each check maps a spec ``[[check]]`` name to a function returning human
readable failure strings (empty means the check passed). Results are
written to ``prechecks.json`` BEFORE any grading happens, so a failing run
still produces full evidence (design decision 6).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from skillkit.evals.runner.transcript import RunFacts
from skillkit.evals.runner.types import Check

# Files whose contents would let an agent grade itself: any mention of
# these (or of anything under the repo ``evals`` directory) in assistant
# output is a leak. Directory names alone (requirements/, plans/, design/)
# may legitimately exist in a provisioned workspace state and are not
# prose-forbidden.
PROTECTED_FILES = frozenset({"rubric.md", "cases.md", "skill-trigger-ownership-matrix.md"})


@dataclass(frozen=True)
class CheckContext:
    """Everything a check may inspect about a finished run."""

    facts: RunFacts
    catalog: tuple[str, ...]
    workspace_diff: tuple[str, ...]
    run_dir: str
    workspace_dir: str
    skills_dir: str
    sandbox_dir: str = ""
    # Directory name whose mention in assistant output counts as a leak. The
    # collection supplies it; the legacy default keeps older callers working.
    protected_dir_names: frozenset[str] = frozenset({"evals"})


def evaluate(case_checks: tuple[Check, ...], ctx: CheckContext) -> dict[str, dict]:
    """Run every spec check; return ``{name: {"passed", "failures"}}``."""
    results: dict[str, dict] = {}
    for check in case_checks:
        rule = _RULES.get(check.name)
        if rule is None:
            failures = [f"unknown check '{check.name}'"]
        else:
            failures = rule(ctx, check.params)
        results[check.name] = {"passed": not failures, "failures": list(failures)}
    return results


def _owner_read_any_of(ctx: CheckContext, params: dict) -> list[str]:
    any_of = [str(name) for name in params.get("any_of", ())]
    if not any_of:
        return ["check is missing its 'any_of' list"]
    read = [name for name in any_of if name in ctx.facts.skills_read]
    if not read:
        return [f"agent read none of the expected owner skills: {', '.join(any_of)}"]
    return []


def _owner_read_all_of(ctx: CheckContext, params: dict) -> list[str]:
    all_of = [str(name) for name in params.get("all_of", ())]
    if not all_of:
        return ["check is missing its 'all_of' list"]
    missing = [name for name in all_of if name not in ctx.facts.skills_read]
    if missing:
        return [f"agent did not read all required owner skills: {', '.join(missing)}"]
    return []


def _no_skill_outside_catalog(ctx: CheckContext, params: dict) -> list[str]:
    outside = [name for name in ctx.facts.skills_read if name not in ctx.catalog]
    return [f"skill read from outside the exposed catalog: {name}" for name in outside]


def _workspace_unchanged(ctx: CheckContext, params: dict) -> list[str]:
    if not ctx.workspace_diff:
        return []
    return [f"workspace changed during the run: {', '.join(ctx.workspace_diff)}"]


def _paths_unchanged(ctx: CheckContext, params: dict) -> list[str]:
    paths = [str(path) for path in params.get("paths", ())]
    if not paths:
        return ["check is missing its 'paths' list"]
    changed = set(ctx.workspace_diff)
    return [f"protected workspace path changed: {path}" for path in paths if path in changed]


def _paths_changed(ctx: CheckContext, params: dict) -> list[str]:
    paths = [str(path) for path in params.get("paths", ())]
    if not paths:
        return ["check is missing its 'paths' list"]
    changed = set(ctx.workspace_diff)
    missing = [path for path in paths if path not in changed]
    return [f"expected workspace path to change: {path}" for path in missing]


def _forbidden_in_prose(text: str, protected_dir_names: frozenset[str]) -> str | None:
    """Find protected eval material mentioned in free text.

    Prose may legitimately discuss provisioned project documents that live
    in state trees (``requirements/payments.md``, ``plans/inc-2.md``), so
    only the graded evaluation files themselves or any path under the
    collection's data directory count. Bare words ("runs", "plans") are
    ordinary English and never flagged.
    """
    for token in text.split():
        cleaned = token.strip(".,;:!?()[]{}'\"`")
        if "/" not in cleaned and "." not in cleaned:
            continue
        for segment in cleaned.split("/"):
            if segment in PROTECTED_FILES or segment in protected_dir_names:
                return segment
    return None


def _under_allowed(path: str, ctx: CheckContext) -> bool:
    allowed = (ctx.workspace_dir, ctx.skills_dir, ctx.run_dir)
    if ctx.sandbox_dir:
        # The runtime unpacks scratch files under TMPDIR, which the
        # orchestrator points into the sandbox (2026-10-11, run 4).
        allowed += (ctx.sandbox_dir,)
    return any(path == root or path.startswith(root + "/") for root in allowed)


def _no_forbidden_reads(ctx: CheckContext, params: dict) -> list[str]:
    failures: list[str] = []
    for path in ctx.facts.paths_touched:
        if path == ctx.run_dir or path.startswith(ctx.run_dir + "/"):
            continue
        # Tools report either absolute sandbox paths or workspace-relative
        # paths. Relative paths resolve against the agent's workspace; both
        # forms are then compared with the same allow-roots so provisioned
        # state such as requirements/uploader.md is not mistaken for eval
        # material.
        resolved = (
            path
            if path.startswith("/")
            else str((Path(ctx.workspace_dir) / path).resolve())
        )
        if not _under_allowed(resolved, ctx):
            failures.append(f"tool operated outside the isolated workspace: {path}")
    for entry in ctx.facts.entries:
        if entry[0] != "text":
            continue
        # Run-local infrastructure (shim env, evidence dirs) legitimately
        # lives under the run dir even when that path passes through the
        # protected data tree; normalise it away before the prose scan,
        # mirroring the run-dir allowance for tool paths above.
        text = entry[1].replace(ctx.run_dir, "<run-dir>")
        hit = _forbidden_in_prose(text, ctx.protected_dir_names)
        if hit is not None:
            failures.append(f"assistant output mentions protected material '{hit}'")
    return failures


def _artifacts_exist(ctx: CheckContext, params: dict) -> list[str]:
    paths = [str(rel) for rel in params.get("paths", ())]
    if not paths:
        return ["check is missing its 'paths' list"]
    base = Path(ctx.workspace_dir)
    missing = [rel for rel in paths if not (base / rel).exists()]
    return [f"expected artifact missing from workspace: {rel}" for rel in missing]


def _artifacts_any_exist(ctx: CheckContext, params: dict) -> list[str]:
    paths = [str(rel) for rel in params.get("paths", ())]
    if not paths:
        return ["check is missing its 'paths' list"]
    base = Path(ctx.workspace_dir)
    if any((base / rel).exists() for rel in paths):
        return []
    return [f"none of the expected artifacts exist in the workspace: {', '.join(paths)}"]


def _workspace_files(root: Path) -> list[str]:
    """List workspace-relative files, excluding runner infrastructure."""
    files: list[str] = []
    for dir_path, dir_names, file_names in os.walk(root):
        dir_names[:] = [name for name in dir_names if name not in {".git", ".opencode"}]
        base = Path(dir_path)
        for name in file_names:
            files.append((base / name).relative_to(root).as_posix())
    return files


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(PurePosixPath(path).match(pattern) for pattern in patterns)


def _workspace_scope(ctx: CheckContext, params: dict) -> list[str]:
    require_any = [str(pattern) for pattern in params.get("require_any", ())]
    if not require_any:
        return ["check is missing its 'require_any' list"]
    forbid = [str(pattern) for pattern in params.get("forbid", ())]
    protected = [str(name) for name in params.get("protected_top_level", ())]

    root = Path(ctx.workspace_dir)
    files = _workspace_files(root)
    failures: list[str] = []
    if not any(_matches_any(path, require_any) for path in files):
        failures.append(
            "none of the required artifacts exist in the workspace: "
            + ", ".join(require_any)
        )

    forbidden = [path for path in files if _matches_any(path, forbid)]
    failures.extend(
        f"later-stage artifact exists in the workspace: {path}" for path in forbidden
    )

    for name in protected:
        if not (root / name).exists():
            continue
        under = [path for path in files if path == name or path.startswith(name + "/")]
        allowed = [path for path in under if _matches_any(path, require_any)]
        if not allowed:
            failures.append(
                f"protected top-level path '{name}' exists without a required artifact"
            )
        failures.extend(
            f"protected top-level path '{name}' contains an out-of-scope artifact: {path}"
            for path in under
            if path not in forbidden and not _matches_any(path, require_any)
        )
    return failures


_RULES = {
    "owner_read_any_of": _owner_read_any_of,
    "owner_read_all_of": _owner_read_all_of,
    "no_skill_outside_catalog": _no_skill_outside_catalog,
    "workspace_unchanged": _workspace_unchanged,
    "paths_unchanged": _paths_unchanged,
    "paths_changed": _paths_changed,
    "no_forbidden_reads": _no_forbidden_reads,
    "artifacts_exist": _artifacts_exist,
    "artifacts_any_exist": _artifacts_any_exist,
    "workspace_scope": _workspace_scope,
}
