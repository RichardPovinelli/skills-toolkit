"""Tests for skillkit.evals.runner.checks pre-launch verification rules."""

from skillkit.evals.runner import checks
from skillkit.evals.runner.transcript import RunFacts
from skillkit.evals.runner.types import Check

RUN = "/run/root"
SANDBOX = "/agent/sandbox"


def make_ctx(skills_read=(), paths_touched=(), texts=(), catalog=("alpha", "beta"), diff=()):
    entries = [("text", t) for t in texts]
    facts = RunFacts(
        session_id="ses_x",
        skills_read=tuple(skills_read),
        paths_touched=tuple(paths_touched),
        entries=tuple(entries),
    )
    return checks.CheckContext(
        facts=facts,
        catalog=tuple(catalog),
        workspace_diff=tuple(diff),
        run_dir=RUN,
        workspace_dir=f"{SANDBOX}/workspace",
        skills_dir="/repo/skills",
        sandbox_dir=SANDBOX,
    )


def run_check(name, ctx, **params):
    result = checks.evaluate((Check(name=name, params=params),), ctx)
    return result[name]["failures"]


def test_owner_read_any_of_pass_and_fail():
    ctx = make_ctx(skills_read=["alpha"])

    assert run_check("owner_read_any_of", ctx, any_of=["alpha"]) == []
    failures = run_check("owner_read_any_of", ctx, any_of=["gamma", "delta"])
    assert len(failures) == 1
    assert "gamma" in failures[0] and "delta" in failures[0]


def test_owner_read_all_of_requires_each_handoff_skill():
    params = {"all_of": ["debugging-failure-triage", "test-driven-development"]}

    assert (
        run_check(
            "owner_read_all_of",
            make_ctx(skills_read=["debugging-failure-triage", "test-driven-development"]),
            **params,
        )
        == []
    )
    failures = run_check(
        "owner_read_all_of",
        make_ctx(skills_read=["debugging-failure-triage"]),
        **params,
    )

    assert len(failures) == 1
    assert "test-driven-development" in failures[0]


def test_no_skill_outside_catalog():
    ctx = make_ctx(skills_read=["alpha", "rogue"])
    failures = run_check("no_skill_outside_catalog", ctx)
    assert len(failures) == 1
    assert "rogue" in failures[0]
    assert run_check("no_skill_outside_catalog", make_ctx(skills_read=["alpha"])) == []


def test_workspace_unchanged():
    assert run_check("workspace_unchanged", make_ctx()) == []
    failures = run_check("workspace_unchanged", make_ctx(diff=("mutated.txt",)))
    assert len(failures) == 1
    assert "mutated.txt" in failures[0]


def test_paths_unchanged_allows_other_artifacts_but_protects_named_paths():
    params = {"paths": ["normalize_name.py"]}

    assert run_check("paths_unchanged", make_ctx(diff=("tests/test_normalize.py",)), **params) == []
    failures = run_check("paths_unchanged", make_ctx(diff=("normalize_name.py",)), **params)

    assert len(failures) == 1
    assert "normalize_name.py" in failures[0]


def test_paths_changed_requires_named_paths_to_change():
    params = {"paths": ["test_retry_operation.py"]}

    assert run_check("paths_changed", make_ctx(diff=("test_retry_operation.py",)), **params) == []
    failures = run_check("paths_changed", make_ctx(diff=("retry_operation.py",)), **params)

    assert len(failures) == 1
    assert "test_retry_operation.py" in failures[0]


def test_no_forbidden_reads_flags_protected_paths_in_reads():
    ctx = make_ctx(paths_touched=["/repo/evals/rubric.md", "/repo/plans/eval-runner.md"])
    failures = run_check("no_forbidden_reads", ctx)
    assert len(failures) == 2


def test_no_forbidden_reads_flags_protected_names_in_assistant_text():
    ctx = make_ctx(texts=["let me read evals/rubric.md first"])
    assert len(run_check("no_forbidden_reads", ctx)) == 1


def test_no_forbidden_reads_allows_run_dir_infrastructure_in_text():
    # Staged shim env paths live under the run dir even when that path runs
    # through the protected 'evals' tree; mentioning them is not leakage.
    ctx = make_ctx(
        texts=["shims are staged at /run/root/shim-env/apt-shfmt-ok, state at /run/root/shim-state/shfmt.log"]
    )
    assert run_check("no_forbidden_reads", ctx) == []


def test_no_forbidden_reads_ignores_run_dir_and_allowed_paths():
    ctx = make_ctx(
        paths_touched=[
            f"{RUN}/workspace/pipeline.sh",
            "/repo/skills/alpha/SKILL.md",
            "/repo/skills/alpha/skills/skills/nested.md",
        ]
    )
    assert run_check("no_forbidden_reads", ctx) == []


def test_no_forbidden_reads_flags_paths_escaping_allowed_roots():
    ctx = make_ctx(
        paths_touched=[
            "/home/vscode/.claude/skills/alpha/SKILL.md",
            "/repo/evals/fixtures/pipeline.sh",
        ]
    )
    assert len(run_check("no_forbidden_reads", ctx)) == 2


def test_no_forbidden_reads_outside_sandbox_wording_is_accurate():
    # The third B02 run (2026-10-11) had the model write scratch to
    # /tmp/opencode/repro.sh: a real sandbox escape, but it exposes no
    # eval material, so the failure text must say "outside the isolated
    # workspace" rather than claiming skills were exposed.
    ctx = make_ctx(paths_touched=["/tmp/opencode/repro.sh"])

    failures = run_check("no_forbidden_reads", ctx)

    assert len(failures) == 1
    assert "outside the isolated workspace" in failures[0]
    assert "exposed skills" not in failures[0]


def test_no_forbidden_reads_allows_sandbox_tmp():
    # Run 4 (2026-10-11): the bun runtime inside opencode unpacks temp
    # files at TMPDIR, which now points into the run sandbox (outside
    # the watched workspace). Sandbox paths are allowed, not escapes.
    ctx = make_ctx(paths_touched=[f"{SANDBOX}/tmp/repro.sh", f"{SANDBOX}/tmp/opencode/blob"])

    assert run_check("no_forbidden_reads", ctx) == []


def test_requirements_txt_matches_requirements_name():
    ctx = make_ctx(paths_touched=["/repo/requirements.txt"])
    assert len(run_check("no_forbidden_reads", ctx)) == 1


def test_prose_common_words_are_not_forbidden_mentions():
    ctx = make_ctx(texts=["The exporter runs but the pipeline exits 0 without set -o pipefail."])
    assert run_check("no_forbidden_reads", ctx) == []


def test_prose_path_like_mentions_are_forbidden():
    ctx = make_ctx(texts=["let me read evals/rubric.md first"])
    failures = run_check("no_forbidden_reads", ctx)
    assert len(failures) == 1
    ctx2 = make_ctx(texts=["check the rubric.md file"])
    assert len(run_check("no_forbidden_reads", ctx2)) == 1
    ctx3 = make_ctx(texts=["see evals/runs/2026 for prior results"])
    assert len(run_check("no_forbidden_reads", ctx3)) == 1


def test_artifacts_exist(tmp_path):
    workspace_dir = tmp_path / "ws"
    (workspace_dir / "tests").mkdir(parents=True)
    (workspace_dir / "tests" / "test_x.py").write_text("def test_x(): pass\n", encoding="utf-8")
    facts = RunFacts(session_id="s", entries=())
    ctx = checks.CheckContext(
        facts=facts,
        catalog=(),
        workspace_diff=(),
        run_dir=str(tmp_path / "run"),
        workspace_dir=str(workspace_dir),
        skills_dir="/repo/skills",
        sandbox_dir=str(tmp_path),
    )

    assert run_check("artifacts_exist", ctx, paths=["tests/test_x.py"]) == []
    failures = run_check("artifacts_exist", ctx, paths=["tests/test_x.py", "test_red.py"])
    assert len(failures) == 1
    assert "test_red.py" in failures[0]


def test_artifacts_any_exist_accepts_one_of_multiple_paths(tmp_path):
    workspace_dir = tmp_path / "ws"
    (workspace_dir / "plans").mkdir(parents=True)
    (workspace_dir / "plans" / "pipeline-plan.md").write_text("plan", encoding="utf-8")
    facts = RunFacts(session_id="s", entries=())
    ctx = checks.CheckContext(
        facts=facts,
        catalog=(),
        workspace_diff=(),
        run_dir=str(tmp_path / "run"),
        workspace_dir=str(workspace_dir),
        skills_dir="/repo/skills",
        sandbox_dir=str(tmp_path),
    )

    assert (
        run_check(
            "artifacts_any_exist",
            ctx,
            paths=["plans/fix-plan.md", "plans/pipeline-plan.md"],
        )
        == []
    )
    failures = run_check("artifacts_any_exist", ctx, paths=["plans/first.md", "plans/second.md"])

    assert len(failures) == 1
    assert "none of the expected artifacts" in failures[0]


def test_no_forbidden_reads_survives_mixed_entry_shapes(tmp_path):
    # Regression: skill entries are 3-tuples and tool entries carry dict
    # payloads; the prose scan must tolerate every entry shape (live B20).
    facts = RunFacts(
        session_id="s",
        entries=(
            ("user", "do the thing"),
            ("skill", "test-driven-development", "completed"),
            ("bash", {"command": "pytest"}),
            ("text", "all tests pass"),
        ),
    )
    ctx = checks.CheckContext(
        facts=facts,
        catalog=(),
        workspace_diff=(),
        run_dir=str(tmp_path / "run"),
        workspace_dir=str(tmp_path),
        skills_dir="/repo/skills",
        sandbox_dir=str(tmp_path),
    )

    assert run_check("no_forbidden_reads", ctx) == []


def test_prose_state_document_paths_are_not_forbidden():
    ctx = make_ctx(texts=["Approved scope is in requirements/payments.md; see plans/inc-2.md too"])

    assert run_check("no_forbidden_reads", ctx) == []


def test_prose_evals_path_is_forbidden():
    ctx = make_ctx(texts=["the checks live in evals/cases.md"])

    failures = run_check("no_forbidden_reads", ctx)
    assert any("protected material 'evals'" in f for f in failures)


def test_workspace_state_paths_touched_are_allowed():
    ctx = make_ctx(paths_touched=[f"{SANDBOX}/workspace/requirements/payments.md"])

    assert run_check("no_forbidden_reads", ctx) == []


def test_relative_workspace_state_paths_are_allowed():
    ctx = make_ctx(paths_touched=["requirements/uploader.md", "docs/retry-plan.md"])

    assert run_check("no_forbidden_reads", ctx) == []


def test_relative_paths_escaping_the_workspace_are_flagged():
    ctx = make_ctx(paths_touched=["../../../etc/passwd"])

    failures = run_check("no_forbidden_reads", ctx)

    assert len(failures) == 1
    assert "outside the isolated workspace" in failures[0]


def _scope_ctx(tmp_path):
    facts = RunFacts(session_id="s", entries=())
    return checks.CheckContext(
        facts=facts,
        catalog=(),
        workspace_diff=(),
        run_dir=str(tmp_path / "run"),
        workspace_dir=str(tmp_path / "ws"),
        skills_dir="/repo/skills",
        sandbox_dir=str(tmp_path),
    )


SCOPE_PARAMS = {
    "require_any": [
        "requirements/*.md",
        "requirements/**/*.md",
        "projects/*/requirements.md",
    ],
    "forbid": [
        "plans/**",
        "docs/adr/**",
        "docs/designs/**",
        "design/**",
        "src/**",
        "tests/**",
        "*.py",
        "*.sh",
        "*.ps1",
        "*.js",
        "*.ts",
    ],
    "protected_top_level": ["projects"],
}


def test_workspace_scope_accepts_requirements_only_workspace(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "requirements").mkdir(parents=True)
    (workspace / "requirements" / "idea.md").write_text("requirements\n", encoding="utf-8")

    assert run_check("workspace_scope", _scope_ctx(tmp_path), **SCOPE_PARAMS) == []


def test_workspace_scope_accepts_single_project_requirements_artifact(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "projects" / "alpha").mkdir(parents=True)
    (workspace / "projects" / "alpha" / "requirements.md").write_text("requirements\n", encoding="utf-8")

    assert run_check("workspace_scope", _scope_ctx(tmp_path), **SCOPE_PARAMS) == []


def test_workspace_scope_rejects_later_stage_artifacts(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "requirements").mkdir(parents=True)
    (workspace / "requirements" / "idea.md").write_text("requirements\n", encoding="utf-8")
    (workspace / "plans").mkdir()
    (workspace / "plans" / "increment.md").write_text("plan\n", encoding="utf-8")
    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("print('x')\n", encoding="utf-8")

    failures = run_check("workspace_scope", _scope_ctx(tmp_path), **SCOPE_PARAMS)

    assert any("plans/increment.md" in failure for failure in failures)
    assert any("src/app.py" in failure for failure in failures)


def test_workspace_scope_rejects_bare_projects_root(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "requirements").mkdir(parents=True)
    (workspace / "requirements" / "idea.md").write_text("requirements\n", encoding="utf-8")
    (workspace / "projects").mkdir()

    failures = run_check("workspace_scope", _scope_ctx(tmp_path), **SCOPE_PARAMS)

    assert any("protected top-level" in failure and "projects" in failure for failure in failures)


def test_workspace_scope_rejects_project_root_with_later_stage_tree(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "projects" / "alpha").mkdir(parents=True)
    (workspace / "projects" / "alpha" / "requirements.md").write_text("requirements\n", encoding="utf-8")
    (workspace / "projects" / "alpha" / "plans").mkdir()
    (workspace / "projects" / "alpha" / "plans" / "increment.md").write_text("plan\n", encoding="utf-8")

    failures = run_check("workspace_scope", _scope_ctx(tmp_path), **SCOPE_PARAMS)

    assert any("protected top-level" in failure or "later-stage" in failure for failure in failures)


def test_workspace_scope_requires_its_pattern_list(tmp_path):
    assert run_check("workspace_scope", _scope_ctx(tmp_path)) == ["check is missing its 'require_any' list"]


def test_lives_outside_workspace_paths_are_still_flagged():
    ctx = make_ctx(paths_touched=["/elsewhere/plans/secret.md"])

    assert run_check("no_forbidden_reads", ctx) != []
