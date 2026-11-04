"""Run-layout, provisioning, leak guard, and snapshots for eval runs.

Every run gets a disposable workspace and a synthetic HOME so the agent can
only see what the case spec explicitly exposes (ADR 0003). Detection of any
protected path inside the agent's reach is fail-closed: we abort, never clean.
"""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from skillkit.evals.runner.layout import CollectionLayout
from skillkit.evals.runner.types import ROUTER_BASE_URL, CaseSpec

# Exact names that must never appear anywhere inside the agent's workspace.
FORBIDDEN_NAMES = frozenset(
    {
        "rubric.md",
        "cases.md",
        "skill-trigger-ownership-matrix.md",
        "requirements",
        "design",
        "plans",
        "reports",
        "projects",
        "runs",
        "validation-runs",
    }
)

# Stored workspace states and simulated tool environments ship with this tool
# and are staged into each run from their packaged locations.
SHIMS_DATA_DIR = Path(__file__).resolve().parent / "data" / "shims"

# Never copy caches from stored state or skills trees into a run workspace.
SKIP_COPY_NAMES = frozenset({"__pycache__", ".pytest_cache"})

# Generic, case-agnostic routing guidance written into the synthetic HOME for
# full-catalog runs. It names no skill or case and contains no graded material.
SKILL_ROUTING_INSTRUCTION = """# Skill routing

Before starting a task, inspect the available skills and load the skill whose
description owns the requested outcome. Use the host's skill tool (the `skill`
tool in OpenCode) so the skill's instructions are in context; do not treat
reading a `SKILL.md` file as a substitute for loading it. If more than one skill
applies, load the primary owner first and then only the companions the task
requires. Do not load unrelated skills. If no listed skill clearly matches,
proceed without inventing one. Loading a skill does not expand the request or
authorize edits, installs, or external effects.
"""


class WorkspaceError(Exception):
    """Raised when a run layout cannot be built or provisioned."""


class LeakError(WorkspaceError):
    """Raised when the agent could reach a protected path; the run aborts."""


@dataclass(frozen=True)
class RunPaths:
    """All locations a run needs. ``repo_root`` is real and read-only to the agent."""

    repo_root: Path
    case: CaseSpec
    run_dir: Path
    sandbox_dir: Path
    workspace_dir: Path
    home_dir: Path
    tmp_dir: Path
    layout: CollectionLayout | None = None
    shims_root: Path | None = None

    @property
    def collection(self) -> CollectionLayout:
        """The graded collection's locations (resolved lazily for callers)."""
        if self.layout is not None:
            return self.layout
        return CollectionLayout.from_root(self.repo_root)

    @property
    def shim_sets_root(self) -> Path:
        return self.shims_root if self.shims_root is not None else SHIMS_DATA_DIR

    @property
    def exposure_dir(self) -> Path:
        return self.workspace_dir / ".opencode" / "skills"


def build_run_layout(
    repo_root: Path,
    case: CaseSpec,
    runs_dir: Path,
    run_id: str,
    agent_root: Path | None = None,
    layout: CollectionLayout | None = None,
    shims_root: Path | None = None,
) -> RunPaths:
    """Create the run layout: artifacts under ``runs_dir``, agent-visible dirs outside any repo.

    ``agent_root`` (default: a temp directory) hosts the workspace and synthetic
    HOME. It must not sit inside the graded repo: opencode anchors the agent's
    project at the enclosing git root, so an in-repo workspace would let every
    tool default to the repo itself (observed 2026-10-11, approved fix).
    """
    run_dir = runs_dir / run_id
    if run_dir.exists():
        raise WorkspaceError(f"run directory already exists (collision): {run_dir}")
    if agent_root is None:
        import tempfile

        agent_root = Path(tempfile.gettempdir()) / "evals-runner"
    agent_run_dir = agent_root / run_id
    workspace_dir = agent_run_dir / "workspace"
    home_dir = agent_run_dir / "home"
    tmp_dir = agent_run_dir / "tmp"
    run_dir.mkdir(parents=True)
    workspace_dir.mkdir(parents=True)
    home_dir.mkdir(parents=True)
    tmp_dir.mkdir(parents=True)
    return RunPaths(
        repo_root=repo_root,
        case=case,
        run_dir=run_dir,
        sandbox_dir=agent_run_dir,
        workspace_dir=workspace_dir,
        home_dir=home_dir,
        tmp_dir=tmp_dir,
        layout=layout,
        shims_root=shims_root,
    )


def _skill_names(paths: RunPaths) -> list[str]:
    exposure = paths.case.exposure
    if exposure.full_catalog:
        skills_root = paths.collection.skills_dir
        return sorted(p.name for p in skills_root.iterdir() if p.is_dir())
    missing = [
        name
        for name in exposure.skill_names
        if not (paths.collection.skills_dir / name).is_dir()
    ]
    if missing:
        raise WorkspaceError(
            f"case '{paths.case.case_id}' exposes unknown skills: {', '.join(missing)}"
        )
    return list(exposure.skill_names)


def _copy_state(paths: RunPaths, allowed: set[str]) -> None:
    """Copy the case's stored state tree into the workspace root (Inc 3, A-1)."""
    if not paths.case.state:
        return
    src_root = paths.collection.state_dir / paths.case.state
    if not src_root.is_dir():
        raise WorkspaceError(f"state dir missing: {src_root}")
    for root, dir_names, file_names in os.walk(src_root):
        dir_names[:] = [d for d in dir_names if d not in SKIP_COPY_NAMES]
        rel_base = Path(root).relative_to(src_root)
        for name in file_names:
            dest = paths.workspace_dir / rel_base / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(root) / name, dest)
    for child in src_root.iterdir():
        if child.name not in SKIP_COPY_NAMES:
            allowed.add(child.name)


def _copy_skills_tree(paths: RunPaths, allowed: set[str]) -> None:
    """Copy repo skills (a subtree, or all of skills/ for ".") into the workspace (A-2)."""
    subtree = paths.case.skills_tree
    if not subtree:
        return
    src = paths.collection.skills_dir
    if subtree != ".":
        src = src / subtree
    if not src.is_dir():
        raise WorkspaceError(f"skills_tree source missing: {src}")
    dest = paths.workspace_dir / "skills"
    if subtree != ".":
        dest = dest / subtree

    def _ignore(dir_path: str, names: list[str]) -> set[str]:
        skip = {n for n in names if n in SKIP_COPY_NAMES}
        # This repo contains a self-referential symlink (skills/skills); copying
        # any symlink would either recurse infinitely or let the sandbox agent
        # follow an absolute path back into the host repo. Regular files only.
        skip |= {n for n in names if (Path(dir_path) / n).is_symlink()}
        return skip

    shutil.copytree(src, dest, ignore=_ignore)
    allowed.add("skills")


def provision(paths: RunPaths) -> frozenset[str]:
    """Provision the workspace; return workspace-relative top-level names the
    leak guard should treat as provisioned (state, fixtures, inline, skills)."""
    allowed: set[str] = set()
    _copy_state(paths, allowed)

    for fixture in paths.case.fixtures:
        src = paths.collection.data_root / fixture.src
        if not src.is_file():
            raise WorkspaceError(f"fixture source missing: {src}")
        dest = paths.workspace_dir / fixture.dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        # Fixture destinations are deliberately NOT added to ``allowed``:
        # pointing a fixture at a protected top-level name is a
        # misconfiguration tripwire, not a provisioning right.

    for inline in paths.case.inline_files:
        dest = paths.workspace_dir / inline.path
        if not dest.resolve().is_relative_to(paths.workspace_dir.resolve()):
            raise WorkspaceError(f"inline_file escapes workspace: {inline.path!r}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(inline.content, encoding="utf-8")

    _copy_skills_tree(paths, allowed)

    paths.exposure_dir.mkdir(parents=True, exist_ok=True)
    for name in _skill_names(paths):
        link = paths.exposure_dir / name
        if link.is_symlink() or link.exists():
            raise WorkspaceError(f"exposure collision for skill '{name}': {link}")
        link.symlink_to(paths.collection.skills_dir / name, target_is_directory=True)

    _init_git(paths)
    return frozenset(allowed)


def _init_git(paths: RunPaths) -> None:
    """Pin opencode's project anchor to the disposable workspace.

    Without an own ``.git``, tool working directories resolve to the enclosing
    git root, which could be the graded repo (observed 2026-10-11).
    """
    subprocess.run(
        ["git", "init", "-q", "."],
        cwd=paths.workspace_dir,
        check=True,
    )


def _empty_home_dirs(home_dir: Path) -> None:
    for rel in (
        ".config/opencode/skills",
        ".claude/skills",
        ".agents/skills",
    ):
        (home_dir / rel).mkdir(parents=True, exist_ok=True)


def write_home_config(paths: RunPaths, model: str) -> None:
    home_config_dir = paths.home_dir / ".config" / "opencode"
    home_config_dir.mkdir(parents=True, exist_ok=True)
    provider, _, model_name = model.partition("/")
    if not model_name:
        provider, model_name = "llama-router", model
    if provider == "llama-router":
        base_url = ROUTER_BASE_URL
        config = "\n".join(
            [
                "{",
                '  "$schema": "https://opencode.ai/config.json",',
                '  "provider": {',
                '    "llama-router": {',
                '      "npm": "@ai-sdk/openai-compatible",',
                '      "options": {',
                f'        "baseURL": "{base_url}"',
                "      },",
                f'      "models": {{ "{model_name}": {{}} }}',
                "    }",
                "  }",
                "}",
            ]
        )
    elif provider == "opencode":
        # Use the built-in OpenCode provider and free-model catalog. The
        # synthetic HOME has no saved account; the free tier works without one.
        config = "\n".join(
            [
                "{",
                '  "$schema": "https://opencode.ai/config.json"',
                "}",
            ]
        )
    else:
        raise WorkspaceError(
            f"unsupported eval model provider {provider!r}: expected "
            "'llama-router' or 'opencode'"
        )
    (home_config_dir / "opencode.jsonc").write_text(config, encoding="utf-8")
    if paths.case.exposure.full_catalog or paths.case.skill_routing:
        (home_config_dir / "AGENTS.md").write_text(
            SKILL_ROUTING_INSTRUCTION, encoding="utf-8"
        )
    _empty_home_dirs(paths.home_dir)


def home_env(paths: RunPaths) -> dict[str, str]:
    """Environment overrides pinning HOME, XDG dirs, PWD, and TMPDIR to the run.

    PWD matters because opencode anchors the agent project at the env
    PWD rather than the process cwd (2026-10-11 controlled experiment);
    without pinning it the agent inherits the driver shell's PWD and
    works inside the graded repo. TMPDIR points at the run sandbox
    ``tmp``: run 3 showed the model writes scratch under TMPDIR and run
    4 showed the bun runtime unpacks temp files there, so it must live
    outside the watched workspace while staying inside the sandbox.
    """
    home = str(paths.home_dir)
    return {
        "HOME": home,
        "XDG_CONFIG_HOME": home + "/.config",
        "XDG_DATA_HOME": home + "/.local/share",
        "XDG_CACHE_HOME": home + "/.cache",
        "PWD": str(paths.workspace_dir),
        "TMPDIR": str(paths.tmp_dir),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def create_path_mirror(dest: Path, excludes: set[str], source_dirs: list[Path]) -> None:
    """Populate ``dest`` with symlinks to executables in ``source_dirs``.

    Earlier source dirs win on name collisions (first-wins), mirroring normal
    PATH precedence; names in ``excludes`` are omitted from every source dir.
    The exact-replacement mirror is what lets shim cases express tool
    *absence*: shadowing alone cannot hide a tool that exists on the host.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for source in source_dirs:
        if not source.is_dir():
            continue
        for entry in sorted(source.iterdir()):
            if entry.name in excludes or entry.name in {"__pycache__"}:
                continue
            link = dest / entry.name
            if link.exists() or link.is_symlink():
                continue
            link.symlink_to(entry)


def _default_mirror_sources() -> list[Path]:
    return [Path("/usr/local/bin"), Path("/usr/bin"), Path.home() / ".local" / "bin"]


def prepare_shim_env(paths: RunPaths) -> dict[str, str] | None:
    """Build the exact-replacement PATH environment for a case's shim set.

    PATH becomes ``<shim set> : <run>/shim-state/bin : <path mirror>``: shim
    executables first, a writable ``bin`` the shims drop simulated installs
    into, then a symlink mirror of the host bins minus ``exclude.txt`` names.
    ``SHIM_STATE``/``SHIM_BIN`` point the shims at run-local evidence dirs and
    ``EVAL_PYTHON`` exposes the runner interpreter for real pytest passthrough.
    Returns ``None`` when the case declares no shims (host PATH is inherited).
    """
    if not paths.case.shims:
        return None
    source_set = paths.shim_sets_root / paths.case.shims
    if not source_set.is_dir():
        raise WorkspaceError(
            f"shim set missing: '{paths.case.shims}' (expected {source_set})"
        )
    # Stage the set inside the run dir: the repo lives under the protected
    # 'src/evals' tree, so PATH must reference the staged copy, never the source
    # (a curious agent listing PATH directories would otherwise touch graded
    # material).
    shim_set = paths.run_dir / "shim-env" / paths.case.shims
    if not shim_set.is_dir():
        shutil.copytree(source_set, shim_set, symlinks=True)
    excludes: set[str] = set()
    exclude_file = source_set / "exclude.txt"
    if exclude_file.is_file():
        for line in exclude_file.read_text(encoding="utf-8").splitlines():
            name = line.strip()
            if name and not name.startswith("#"):
                excludes.add(name)
    shim_state = paths.run_dir / "shim-state"
    shim_bin = shim_state / "bin"
    shim_bin.mkdir(parents=True, exist_ok=True)
    mirror = paths.run_dir / "path-mirror"
    create_path_mirror(mirror, excludes, _default_mirror_sources())
    return {
        "PATH": ":".join([str(shim_set), str(shim_bin), str(mirror)]),
        "SHIM_STATE": str(shim_state),
        "SHIM_BIN": str(shim_bin),
        "EVAL_PYTHON": sys.executable,
    }


def leak_guard(paths: RunPaths, allowed: frozenset[str] = frozenset()) -> None:
    """Abort the run if any protected name is visible unless it sits under a
    top-level path that provisioning itself created (``allowed``)."""
    skills_root = paths.collection.skills_dir.resolve()
    for root, dir_names, file_names in os.walk(paths.workspace_dir):
        rel_dir = Path(root).relative_to(paths.workspace_dir)
        for name in dir_names + file_names:
            entry = Path(root) / name
            top = rel_dir.parts[0] if rel_dir.parts else name
            if name in FORBIDDEN_NAMES and top not in allowed:
                raise LeakError(
                    f"forbidden name '{name}' inside agent workspace: {entry}"
                )
            if entry.is_symlink():
                target = entry.resolve()
                if target != skills_root and skills_root not in target.parents:
                    raise LeakError(
                        f"symlink '{name}' escapes the exposed skills tree: {target}"
                    )


def snapshot(root: Path) -> dict[str, tuple[bool, int, int]]:
    """Map each path under ``root`` (not followed links) to (is_dir, mtime_ns, size)."""
    entries: dict[str, tuple[bool, int, int]] = {}
    for dir_path, dir_names, file_names in os.walk(root):
        # opencode maintains its own git snapshot repo in the agent root and
        # commits to it each step; that churn is infrastructure, not an agent
        # edit, so it must not trip the workspace_unchanged check.
        dir_names[:] = [
            d for d in dir_names if d not in {".git", "__pycache__", ".pytest_cache"}
        ]
        base = Path(dir_path)
        for name in dir_names + file_names:
            if name.endswith(".pyc") or name in {"__pycache__", ".pytest_cache"}:
                continue
            entry = base / name
            rel = str(entry.relative_to(root))
            info = entry.lstat()
            entries[rel] = (entry.is_dir(), info.st_mtime_ns, info.st_size)
    return entries


def diff_snapshots(
    pre: dict[str, tuple[bool, int, int]], post: dict[str, tuple[bool, int, int]]
) -> list[str]:
    changed = set(pre) ^ set(post)
    changed |= {key for key in set(pre) & set(post) if pre[key] != post[key]}
    return sorted(changed)
