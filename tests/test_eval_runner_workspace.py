"""Tests for skillkit.evals.runner.workspace run isolation (build, provision, guard)."""

from pathlib import Path
from typing import Any

import pytest

from skillkit.evals.runner import workspace
from skillkit.evals.runner.types import CaseSpec, Exposure, Fixture, InlineFile

RUN_ID = "20260101T000000-B98"


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    for name in ("alpha", "beta"):
        skill_dir = repo / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    fixtures = repo / "validation" / "fixtures"
    fixtures.mkdir(parents=True)
    (fixtures / "sample.txt").write_text("sample content\n", encoding="utf-8")
    return repo


def make_case(**overrides: Any) -> CaseSpec:
    base: dict[str, Any] = {
        "case_id": "B98",
        "prompt": "p",
        "exposure": Exposure(full_catalog=True),
        "fixtures": (Fixture(src="fixtures/sample.txt", dest="input/sample.txt"),),
    }
    base.update(overrides)
    return CaseSpec(**base)


def build(tmp_path: Path, case: CaseSpec | None = None) -> workspace.RunPaths:
    repo = make_repo(tmp_path)
    return workspace.build_run_layout(
        repo,
        case or make_case(),
        tmp_path / "runs",
        RUN_ID,
        agent_root=tmp_path / "agent",
    )


def test_build_run_layout_creates_directories(tmp_path: Path) -> None:
    paths = build(tmp_path)

    assert paths.run_dir.is_dir()
    assert paths.workspace_dir.is_dir()
    assert paths.home_dir.is_dir()
    assert paths.run_dir.name == RUN_ID


def test_agent_root_defaults_outside_repo_and_runs(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)

    paths = workspace.build_run_layout(repo, make_case(), repo / "evals" / "runs", RUN_ID)
    try:
        assert not paths.workspace_dir.is_relative_to(repo)
        assert not paths.home_dir.is_relative_to(repo)
        assert paths.run_dir.is_relative_to(repo / "evals" / "runs")
    finally:
        import shutil

        shutil.rmtree(paths.workspace_dir.parent, ignore_errors=True)


def test_provision_inits_own_git_root_in_workspace(tmp_path: Path) -> None:
    paths = build(tmp_path)

    workspace.provision(paths)

    assert (paths.workspace_dir / ".git").is_dir()
    import subprocess

    toplevel = subprocess.run(
        ["git", "-C", str(paths.workspace_dir), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert Path(toplevel) == paths.workspace_dir.resolve()


def test_build_run_layout_refuses_collision(tmp_path: Path) -> None:
    paths = build(tmp_path)

    with pytest.raises(workspace.WorkspaceError, match="exists"):
        workspace.build_run_layout(paths.repo_root, make_case(), tmp_path / "runs", RUN_ID)


def test_provision_copies_fixtures_and_links_full_catalog(tmp_path: Path) -> None:
    paths = build(tmp_path)

    workspace.provision(paths)

    copied = paths.workspace_dir / "input" / "sample.txt"
    assert copied.read_text(encoding="utf-8") == "sample content\n"
    skills = paths.workspace_dir / ".opencode" / "skills"
    assert (skills / "alpha").is_symlink()
    assert (skills / "beta").is_symlink()
    assert (skills / "alpha").resolve() == (paths.repo_root / "skills" / "alpha").resolve()


def test_provision_links_only_named_skills(tmp_path: Path) -> None:
    case = make_case(exposure=Exposure(skill_names=("alpha",)))
    paths = build(tmp_path, case)

    workspace.provision(paths)

    skills = paths.workspace_dir / ".opencode" / "skills"
    assert (skills / "alpha").is_symlink()
    assert not (skills / "beta").exists()


def test_provision_rejects_unknown_skill_name(tmp_path: Path) -> None:
    case = make_case(exposure=Exposure(skill_names=("gamma",)))
    paths = build(tmp_path, case)

    with pytest.raises(workspace.WorkspaceError, match="gamma"):
        workspace.provision(paths)


def test_write_home_config_writes_provider_and_empty_skill_dirs(tmp_path: Path) -> None:
    paths = build(tmp_path)

    workspace.write_home_config(paths, "llama-router/test-model")

    config_text = (paths.home_dir / ".config" / "opencode" / "opencode.jsonc").read_text(encoding="utf-8")
    assert "test-model" in config_text
    assert "http://host.docker.internal:1235/v1" in config_text
    for empty in (".config/opencode/skills", ".claude/skills", ".agents/skills"):
        empty_dir = paths.home_dir / empty
        assert empty_dir.is_dir()
        assert list(empty_dir.iterdir()) == []


def test_write_home_config_writes_generic_skill_routing_instruction(
    tmp_path: Path,
) -> None:
    paths = build(tmp_path)

    workspace.write_home_config(paths, "llama-router/test-model")

    instruction = (paths.home_dir / ".config" / "opencode" / "AGENTS.md").read_text(encoding="utf-8")
    assert "Before starting a task" in instruction
    assert "skill tool" in instruction
    assert "primary owner" in instruction
    assert "debugging-failure-triage" not in instruction
    assert "B02" not in instruction


def test_write_home_config_omits_skill_routing_for_limited_catalog(
    tmp_path: Path,
) -> None:
    case = make_case(exposure=Exposure(skill_names=("alpha",)))
    paths = build(tmp_path, case)

    workspace.write_home_config(paths, "llama-router/test-model")

    assert not (paths.home_dir / ".config" / "opencode" / "AGENTS.md").exists()


def test_write_home_config_writes_skill_routing_for_case_opt_in(
    tmp_path: Path,
) -> None:
    case = make_case(
        exposure=Exposure(skill_names=("alpha",)),
        skill_routing=True,
    )
    paths = build(tmp_path, case)

    workspace.write_home_config(paths, "llama-router/test-model")

    instruction = (paths.home_dir / ".config" / "opencode" / "AGENTS.md").read_text(encoding="utf-8")
    assert "Before starting a task" in instruction


def test_write_home_config_uses_builtin_opencode_provider_for_free_model(
    tmp_path: Path,
) -> None:
    paths = build(tmp_path)

    workspace.write_home_config(paths, "opencode/deepseek-v4.1-flash-free")

    config_text = (paths.home_dir / ".config" / "opencode" / "opencode.jsonc").read_text(encoding="utf-8")
    assert "host.docker.internal" not in config_text
    assert "apiKey" not in config_text
    assert "opencode-go" not in config_text


def test_write_home_config_rejects_unsupported_provider(tmp_path: Path) -> None:
    paths = build(tmp_path)

    with pytest.raises(workspace.WorkspaceError, match="unsupported"):
        workspace.write_home_config(paths, "openai/gpt-5")


def test_home_env_pins_pwd_to_workspace(tmp_path: Path) -> None:
    # 2026-10-11 controlled experiment: opencode anchors the agent
    # project at the env PWD, not the process cwd. A stale inherited
    # PWD made the agent work inside /workspaces/skills despite
    # cwd=<isolated workspace>, so home_env must pin PWD explicitly.
    paths = build(tmp_path)
    workspace.provision(paths)

    env = workspace.home_env(paths)

    assert env["PWD"] == str(paths.workspace_dir)
    # Run 4 (2026-10-11): the bun runtime unpacks temp files at TMPDIR,
    # so TMPDIR must live in the run sandbox but outside the watched
    # workspace, keeping workspace_unchanged about agent behavior only.
    assert env["TMPDIR"] == str(paths.sandbox_dir / "tmp")
    assert paths.tmp_dir.is_dir()
    assert not str(paths.tmp_dir).startswith(str(paths.workspace_dir))


def test_leak_guard_passes_on_clean_layout(tmp_path: Path) -> None:
    paths = build(tmp_path)
    workspace.provision(paths)

    workspace.leak_guard(paths)


def test_leak_guard_blocks_forbidden_name_in_workspace(tmp_path: Path) -> None:
    paths = build(tmp_path)
    (paths.workspace_dir / "rubric.md").write_text("leak\n", encoding="utf-8")

    with pytest.raises(workspace.LeakError, match="rubric.md"):
        workspace.leak_guard(paths)


def test_leak_guard_blocks_forbidden_name_in_nested_dir(tmp_path: Path) -> None:
    paths = build(tmp_path)
    nested = paths.workspace_dir / "data" / "plans"
    nested.mkdir(parents=True)
    (nested / "secret.md").write_text("x\n", encoding="utf-8")

    with pytest.raises(workspace.LeakError, match="plans"):
        workspace.leak_guard(paths)


def test_leak_guard_blocks_projects_dir_in_workspace(tmp_path: Path) -> None:
    # Repo reorg (2026-10-11): project working docs moved under the
    # top-level "projects/" tree, so that name joins the protected set.
    paths = build(tmp_path)
    (paths.workspace_dir / "projects").mkdir()

    with pytest.raises(workspace.LeakError, match="projects"):
        workspace.leak_guard(paths)


def test_leak_guard_blocks_symlink_escaping_skills(tmp_path: Path) -> None:
    paths = build(tmp_path)
    outside = paths.repo_root / "requirements"
    outside.mkdir()
    link = paths.workspace_dir / "innocent"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(workspace.LeakError, match="innocent"):
        workspace.leak_guard(paths)


def test_snapshot_diff_detects_added_changed_removed(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "stable.txt").write_text("a\n", encoding="utf-8")
    (root / "mutates.txt").write_text("one\n", encoding="utf-8")
    (root / "goes_away.txt").write_text("bye\n", encoding="utf-8")

    pre = workspace.snapshot(root)
    (root / "added.txt").write_text("new\n", encoding="utf-8")
    (root / "mutates.txt").write_text("much longer content now\n", encoding="utf-8")
    (root / "goes_away.txt").unlink()
    post = workspace.snapshot(root)

    changes = workspace.diff_snapshots(pre, post)

    assert sorted(changes) == ["added.txt", "goes_away.txt", "mutates.txt"]


def test_snapshot_diff_empty_when_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "f.txt").write_text("a\n", encoding="utf-8")

    pre = workspace.snapshot(root)
    post = workspace.snapshot(root)

    assert workspace.diff_snapshots(pre, post) == []


def test_leak_guard_aborts_on_planted_design_dir(tmp_path: Path) -> None:
    paths = build(tmp_path)
    workspace.provision(paths)
    (paths.workspace_dir / "design").mkdir()

    with pytest.raises(workspace.LeakError, match="design"):
        workspace.leak_guard(paths)


def test_provision_writes_inline_files(tmp_path: Path) -> None:
    case = make_case(
        inline_files=(
            InlineFile(path="pkg/settings.py", content="MAX_RETRIES = 3\n"),
            InlineFile(path="empty.txt", content=""),
        )
    )
    paths = build(tmp_path, case)

    workspace.provision(paths)

    assert (paths.workspace_dir / "pkg" / "settings.py").read_text(encoding="utf-8") == ("MAX_RETRIES = 3\n")
    assert (paths.workspace_dir / "empty.txt").read_text(encoding="utf-8") == ""


def test_provision_rejects_inline_file_escaping_workspace(tmp_path: Path) -> None:
    case = make_case(inline_files=(InlineFile(path="../escape.txt", content="x"),))
    paths = build(tmp_path, case)

    with pytest.raises(workspace.WorkspaceError):
        workspace.provision(paths)


def state_dir(paths: workspace.RunPaths, name: str) -> Path:
    state = paths.collection.state_dir / name
    state.mkdir(parents=True)
    return state


def test_provision_copies_state_tree_into_workspace(tmp_path: Path) -> None:
    paths = build(tmp_path, make_case(state="demo_state"))
    state = state_dir(paths, "demo_state")
    (state / "requirements").mkdir()
    (state / "requirements" / "spec.md").write_text("# Approved spec\n", encoding="utf-8")
    src = state / "src"
    src.mkdir()
    (src / "app.py").write_text("print('hi')\n", encoding="utf-8")
    cache = src / "__pycache__"
    cache.mkdir()
    (cache / "app.cpython-311.pyc").write_bytes(b"\x00")

    allowed = workspace.provision(paths)

    assert (paths.workspace_dir / "requirements" / "spec.md").read_text(encoding="utf-8") == ("# Approved spec\n")
    assert (paths.workspace_dir / "src" / "app.py").exists()
    assert not (paths.workspace_dir / "src" / "__pycache__").exists()
    assert "requirements" in allowed and "src" in allowed


def test_provision_missing_state_dir_raises(tmp_path: Path) -> None:
    paths = build(tmp_path, make_case(state="nope"))

    with pytest.raises(workspace.WorkspaceError, match="state dir"):
        workspace.provision(paths)


def test_provision_inline_overwrites_state_file(tmp_path: Path) -> None:
    case = make_case(
        state="demo_state",
        inline_files=(InlineFile(path="notes.md", content="case-specific\n"),),
    )
    paths = build(tmp_path, case)
    state = state_dir(paths, "demo_state")
    (state / "notes.md").write_text("original\n", encoding="utf-8")

    workspace.provision(paths)

    assert (paths.workspace_dir / "notes.md").read_text(encoding="utf-8") == "case-specific\n"


def test_provision_copies_skills_subtree_into_workspace(tmp_path: Path) -> None:
    paths = build(tmp_path, make_case(skills_tree="alpha"))

    allowed = workspace.provision(paths)

    copied = paths.workspace_dir / "skills" / "alpha" / "SKILL.md"
    assert copied.read_text(encoding="utf-8") == "# alpha\n"
    assert not (paths.workspace_dir / "skills" / "beta").exists()
    assert "skills" in allowed


def test_provision_copies_whole_skills_tree_for_dot(tmp_path: Path) -> None:
    paths = build(tmp_path, make_case(skills_tree="."))

    workspace.provision(paths)

    skills_ws = paths.workspace_dir / "skills"
    assert (skills_ws / "alpha" / "SKILL.md").exists()
    assert (skills_ws / "beta" / "SKILL.md").exists()


def test_provision_skills_tree_skips_self_referential_symlink(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / "skills" / "skills").symlink_to(repo / "skills", target_is_directory=True)
    paths = workspace.build_run_layout(
        repo,
        make_case(skills_tree="."),
        tmp_path / "runs",
        RUN_ID,
        agent_root=tmp_path / "agent",
    )

    workspace.provision(paths)

    assert (paths.workspace_dir / "skills" / "alpha" / "SKILL.md").exists()
    assert not (paths.workspace_dir / "skills" / "skills").exists()


def test_snapshot_ignores_git_dirs_but_tracks_other_changes(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    (root / ".git" / "objects").mkdir(parents=True)
    (root / ".git" / "objects" / "blob").write_text("a\n", encoding="utf-8")
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")

    pre = workspace.snapshot(root)
    assert not any(key.startswith(".git") for key in pre)

    (root / ".git" / "objects" / "blob").write_text("b c d\n", encoding="utf-8")
    (root / "app.py").write_text("x = 2\n", encoding="utf-8")

    assert workspace.diff_snapshots(pre, workspace.snapshot(root)) == ["app.py"]


def test_leak_guard_allows_provisioned_state_names(tmp_path: Path) -> None:
    paths = build(tmp_path, make_case(state="demo_state"))
    state = state_dir(paths, "demo_state")
    (state / "requirements").mkdir()
    (state / "requirements" / "spec.md").write_text("# Spec\n", encoding="utf-8")

    allowed = workspace.provision(paths)
    workspace.leak_guard(paths, allowed=allowed)

    with pytest.raises(workspace.LeakError, match="requirements"):
        workspace.leak_guard(paths)


def test_leak_guard_allowed_does_not_mask_other_forbidden_names(tmp_path: Path) -> None:
    paths = build(tmp_path, make_case(state="demo_state"))
    state = state_dir(paths, "demo_state")
    (state / "requirements").mkdir()
    (state / "requirements" / "spec.md").write_text("# Spec\n", encoding="utf-8")

    allowed = workspace.provision(paths)
    (paths.workspace_dir / "plans").mkdir()

    with pytest.raises(workspace.LeakError, match="plans") as excinfo:
        workspace.leak_guard(paths, allowed=allowed)
    assert "requirements" not in str(excinfo.value)


def make_shims_root(tmp_path: Path, set_name: str, exclude: str = "") -> Path:
    shims_root = tmp_path / "shims"
    shim_set = shims_root / set_name
    shim_set.mkdir(parents=True)
    tool = shim_set / "fake-tool"
    tool.write_text("#!/bin/sh\necho fake\n", encoding="utf-8")
    tool.chmod(0o755)
    if exclude:
        (shim_set / "exclude.txt").write_text(exclude, encoding="utf-8")
    return shims_root


def test_prepare_shim_env_returns_none_without_shims(tmp_path: Path) -> None:
    paths = build(tmp_path)

    assert workspace.prepare_shim_env(paths) is None


def test_create_path_mirror_links_and_excludes(tmp_path: Path) -> None:
    src_a = tmp_path / "binA"
    src_b = tmp_path / "binB"
    src_a.mkdir()
    src_b.mkdir()
    (src_a / "alpha").write_text("a", encoding="utf-8")
    (src_b / "beta").write_text("b", encoding="utf-8")
    (src_b / "alpha").write_text("shadow", encoding="utf-8")
    dest = tmp_path / "mirror"

    workspace.create_path_mirror(dest, {"beta"}, [src_b, src_a])

    assert (dest / "alpha").readlink() == src_b / "alpha"
    assert not (dest / "beta").exists()


def test_prepare_shim_env_builds_exact_path_and_state(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    shims_root = make_shims_root(tmp_path, "fake-set", exclude="git\n")
    case = make_case(shims="fake-set")
    paths = workspace.build_run_layout(
        repo,
        case,
        tmp_path / "runs",
        RUN_ID,
        agent_root=tmp_path / "agent",
        shims_root=shims_root,
    )

    env = workspace.prepare_shim_env(paths)

    assert env is not None
    path_parts = env["PATH"].split(":")
    staged = paths.run_dir / "shim-env" / "fake-set"
    assert path_parts[0] == str(staged)
    # The staged copy (not the in-repo source set) is what lands on PATH,
    # so the agent never sees graded material through a PATH listing.
    assert (staged / "fake-tool").exists()
    assert path_parts[1] == str(paths.run_dir / "shim-state" / "bin")
    mirror = Path(path_parts[2])
    assert (mirror / "bash").is_symlink()
    assert not (mirror / "git").exists()
    assert env["SHIM_STATE"] == str(paths.run_dir / "shim-state")
    assert (paths.run_dir / "shim-state" / "bin").is_dir()
    assert env["EVAL_PYTHON"]


def test_prepare_shim_env_rejects_missing_shim_set(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    case = make_case(shims="nope")
    paths = workspace.build_run_layout(repo, case, tmp_path / "runs", RUN_ID, agent_root=tmp_path / "agent")

    with pytest.raises(workspace.WorkspaceError, match="nope"):
        workspace.prepare_shim_env(paths)
