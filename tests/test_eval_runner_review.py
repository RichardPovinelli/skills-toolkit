"""Tests for skillkit.evals.runner.review: reviewer brief generation and score overlay merge."""

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from skillkit.evals.runner import __main__ as runner_main
from skillkit.evals.runner import review
from skillkit.evals.runner.orchestrator import RunResult

pytestmark = pytest.mark.usefixtures("local_model_choice")

CASE_TOML = """\
id = "B98"
prompt = "diagnose this failure"
model = "test/model"
timeout = 120
canned_replies = ["yes proceed"]
state = "state/b98"
shims = "mock-tools"

[exposure]
full_catalog = false
skill_names = ["debugging-failure-triage"]

[[fixture]]
src = "fixtures/broken.sh"
dest = "broken.sh"

[[inline_file]]
path = "notes.md"
content = "hello"

[[check]]
name = "owner_read_any_of"
skills = ["debugging-failure-triage"]
"""

EVENTS = (
    "\n".join(
        [
            json.dumps(
                {
                    "sessionID": "ses_1",
                    "type": "runner_turn",
                    "user": "diagnose this failure",
                }
            ),
            json.dumps(
                {
                    "sessionID": "ses_1",
                    "type": "tool_use",
                    "part": {
                        "tool": "skill",
                        "state": {
                            "status": "completed",
                            "input": {"id": "debugging-failure-triage"},
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "sessionID": "ses_1",
                    "type": "tool_use",
                    "part": {
                        "tool": "read",
                        "state": {
                            "status": "completed",
                            "input": {"filePath": "/sandbox/broken.sh"},
                        },
                    },
                }
            ),
            "not-json-at-all",
        ]
    )
    + "\n"
)

TURNS = [
    {
        "turn": 1,
        "user": "diagnose this failure",
        "exit_code": 0,
        "timed_out": False,
        "diff_after": ["broken.sh", "notes.md"],
    },
]


def make_case(runs_dir: Path, cases_dir: Path, case_id: str = "B98") -> None:
    cases_dir.mkdir(parents=True, exist_ok=True)
    (cases_dir / f"{case_id}.toml").write_text(
        CASE_TOML.replace("B98", case_id), encoding="utf-8"
    )


def set_case_model(cases_dir: Path, model: str) -> None:
    case_path = cases_dir / "B98.toml"
    content = case_path.read_text(encoding="utf-8").replace(
        'model = "test/model"', f'model = "{model}"'
    )
    case_path.write_text(content, encoding="utf-8")


def make_completed_run(
    runs_dir: Path, name: str, outcome: str = "PASS", **report
) -> Path:
    run = runs_dir / name
    run.mkdir(parents=True, exist_ok=True)
    payload = {
        "case_id": name.rsplit("-", 1)[1],
        "outcome": outcome,
        "duration_s": 42.5,
        "model": "test/model",
        "host": "opencode/local-router",
        "failures": [],
    }
    payload.update(report)
    (run / "report.json").write_text(json.dumps(payload), encoding="utf-8")
    (run / "events.jsonl").write_text(EVENTS, encoding="utf-8")
    (run / "turns.json").write_text(json.dumps(TURNS), encoding="utf-8")
    (run / "prechecks.json").write_text(
        json.dumps({"ok": True, "checks": [{"name": "catalog_isolated", "ok": True}]}),
        encoding="utf-8",
    )
    (run / "conversation.log").write_text(
        "user:diagnose this failure\n", encoding="utf-8"
    )
    return run


def make_repo(tmp_path: Path, case_id: str = "B98", with_rubric: bool = True):
    root = tmp_path / "repo"
    runs_dir = root / "validation-runs"
    cases_dir = root / "validation" / "cases"
    make_case(runs_dir, cases_dir, case_id)
    rubric = root / "validation" / "rubric.md"
    if with_rubric:
        rubric.write_text(
            "# Evaluator-only rubric\n\n| Case | Primary owner and required outcome | Failure examples |\n"
            "| --- | --- | --- |\n"
            f"| {case_id} | `debugging-failure-triage`: reproduces the failure. | Fixes unrequested. |\n",
            encoding="utf-8",
        )
    out_path = root / "reports" / "2026-10-11-T10-20-30-CDT" / "review-brief.md"
    scores_path = root / "projects" / "eval-runner" / "reports" / "review-scores.md"
    return root, runs_dir, cases_dir, out_path, scores_path


def generate(root, runs_dir, cases_dir, out_path, scores_path):
    return review.generate(
        runs_dir=runs_dir,
        cases_dir=cases_dir,
        root=root,
        out_path=out_path,
        scores_path=scores_path,
    )


@pytest.fixture(autouse=True)
def opencode_unavailable_by_default(monkeypatch):
    monkeypatch.setattr(review.shutil, "which", lambda executable: None)


# --- section content ---------------------------------------------------------


def test_brief_has_one_section_per_wired_case(tmp_path):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)
    make_case(runs_dir, cases_dir, "B99")

    md = generate(root, runs_dir, cases_dir, out_path, scores_path)

    assert md.count("\n## B") == 2
    assert "## B98" in md and "## B99" in md
    assert "generated automatically" in md.lower()
    assert "--review" not in md
    assert out_path.read_text(encoding="utf-8") == md


def test_spec_summary_fields_rendered(tmp_path):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)

    md = generate(root, runs_dir, cases_dir, out_path, scores_path)

    assert "diagnose this failure" in md
    assert "test/model" in md
    assert "120" in md
    assert "debugging-failure-triage" in md
    assert "owner_read_any_of" in md
    assert "broken.sh" in md
    assert "yes proceed" in md


def test_newest_completed_run_wins_and_batch_dirs_ignored(tmp_path):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)
    make_completed_run(
        runs_dir,
        "20261011T010000Z-B98",
        "FAIL",
        failures=["owner_read_any_of: no owner skill read"],
    )
    newest = make_completed_run(runs_dir, "20261011T020000Z-B98", "PASS")
    make_completed_run(
        runs_dir, "20261011T023000Z-B98-crashed-x", "PASS"
    )  # foreign dir
    (runs_dir / "20261011T030000Z-batch").mkdir()  # batch dir, not a case run

    md = generate(root, runs_dir, cases_dir, out_path, scores_path)

    assert newest.name in md
    assert "20261011T010000Z-B98" not in md
    assert "PASS" in md
    assert (
        "../../validation-runs/" in md
    )  # links are relative to the brief, not absolute


def test_verdict_failures_and_prechecks_passthrough(tmp_path):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)
    make_completed_run(
        runs_dir,
        "20261011T020000Z-B98",
        "FAIL",
        failures=["owner_read_any_of: no owner skill read"],
    )

    md = generate(root, runs_dir, cases_dir, out_path, scores_path)

    assert "FAIL" in md
    assert "owner_read_any_of: no owner skill read" in md
    assert "catalog_isolated" in md


def test_non_dict_report_degrades_verdict_section(tmp_path, monkeypatch):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)
    run = make_completed_run(runs_dir, "20261011T020000Z-B98", "PASS")
    (run / "report.json").write_text("null", encoding="utf-8")
    done = review.batch.CompletedRun(run=run, outcome="PASS")
    monkeypatch.setattr(
        review.batch, "discover_completed", lambda runs_dir, case_ids: {"B98": done}
    )

    md = generate(root, runs_dir, cases_dir, out_path, scores_path)

    assert "PASS" in md
    assert "No completed run yet" not in md


def test_skill_activation_and_files_touched(tmp_path):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)
    make_completed_run(runs_dir, "20261011T020000Z-B98", "PASS")

    md = generate(root, runs_dir, cases_dir, out_path, scores_path)

    assert "Skill activation" in md
    assert "debugging-failure-triage" in md.split("Skill activation")[1]
    assert "broken.sh" in md.split("Skill activation")[1]


def test_workspace_changes_rendered(tmp_path):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)
    make_completed_run(runs_dir, "20261011T020000Z-B98", "PASS")

    md = generate(root, runs_dir, cases_dir, out_path, scores_path)

    assert "Workspace changes" in md
    changes = md.split("Workspace changes")[1]
    assert "broken.sh" in changes
    assert "notes.md" in changes


def test_empty_change_list_renders_no_changes(tmp_path):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)
    run = make_completed_run(runs_dir, "20261011T020000Z-B98", "PASS")
    (run / "turns.json").write_text(
        json.dumps([{"turn": 1, "diff_after": [], "exit_code": 0, "timed_out": False}]),
        encoding="utf-8",
    )

    md = generate(root, runs_dir, cases_dir, out_path, scores_path)

    assert "no workspace changes" in md.split("Workspace changes")[1]


def test_turn_without_diff_after_renders_no_changes(tmp_path):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)
    run = make_completed_run(runs_dir, "20261011T020000Z-B98", "PASS")
    (run / "turns.json").write_text(
        json.dumps([{"turn": 1, "exit_code": 0, "timed_out": False}]), encoding="utf-8"
    )

    md = generate(root, runs_dir, cases_dir, out_path, scores_path)

    assert "no workspace changes" in md.split("Workspace changes")[1]


def test_llm_analysis_section_renders_model_summary_when_available(
    tmp_path, monkeypatch
):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)
    make_completed_run(runs_dir, "20261011T020000Z-B98", "PASS")
    monkeypatch.setattr(
        review,
        "_model_analysis_text",
        lambda case_id, case, done, report: (
            "Routing is coherent and the run stayed within the exposed skill set."
        ),
    )

    md = generate(root, runs_dir, cases_dir, out_path, scores_path)

    assert "### LLM analysis" in md
    assert "- provenance: model-generated analysis" in md
    assert "Routing is coherent and the run stayed within the exposed skill set." in md


def test_llm_analysis_placeholder_when_model_unavailable(tmp_path, monkeypatch):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)
    make_completed_run(
        runs_dir,
        "20261011T020000Z-B98",
        "FAIL",
        failures=["owner_read_any_of: no owner skill read"],
    )

    def boom(case_id, case, done, report):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(review, "_model_analysis_text", boom)

    md = generate(root, runs_dir, cases_dir, out_path, scores_path)

    assert "### LLM analysis" in md
    assert "analysis unavailable" in md.lower()
    assert "model unavailable" in md.lower()


def test_model_analysis_parses_opencode_jsonl_and_includes_events(
    tmp_path, monkeypatch
):
    _, runs_dir, cases_dir, _, _ = make_repo(tmp_path)
    set_case_model(cases_dir, "llama-router/test-model")
    make_completed_run(
        runs_dir, "20261011T020000Z-B98", "PASS", model="llama-router/test-model"
    )
    completed = review.batch.discover_completed(runs_dir, ["B98"])["B98"]
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["prompt"] = argv[-1]
        captured["cwd"] = kwargs["cwd"]
        captured["env"] = kwargs["env"]
        return review.subprocess.CompletedProcess(
            argv,
            0,
            stdout='{"type":"text","part":{"text":"Evidence supports the outcome."}}\n',
            stderr="",
        )

    monkeypatch.setattr(review.shutil, "which", lambda executable: "/usr/bin/opencode")
    monkeypatch.setattr(review.subprocess, "run", fake_run)

    text = review._model_analysis_text(
        "B98", review.spec.load_case("B98", cases_dir), completed, {}
    )

    assert text == "Evidence supports the outcome."
    assert "--standalone" in captured["argv"]
    assert captured["argv"][captured["argv"].index("--agent") + 1] == "plan"
    assert "--auto" not in captured["argv"]
    assert Path(captured["cwd"]) != runs_dir
    assert captured["env"]["PWD"] == captured["cwd"]
    assert json.loads(captured["env"]["OPENCODE_CONFIG_CONTENT"])["tools"] == {
        "*": False
    }
    assert "debugging-failure-triage" in captured["prompt"]
    assert "3 brief bullet points and 80 words total" in captured["prompt"]
    assert '"events":' in captured["prompt"]
    assert "ses_1" in captured["prompt"]


def test_failed_opencode_process_renders_unavailable_analysis(tmp_path, monkeypatch):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)
    set_case_model(cases_dir, "llama-router/test-model")
    make_completed_run(
        runs_dir, "20261011T020000Z-B98", "PASS", model="llama-router/test-model"
    )

    def failed_run(argv, **kwargs):
        return review.subprocess.CompletedProcess(
            argv, 1, stdout="", stderr="connection refused"
        )

    monkeypatch.setattr(review.shutil, "which", lambda executable: "/usr/bin/opencode")
    monkeypatch.setattr(review.subprocess, "run", failed_run)

    md = generate(root, runs_dir, cases_dir, out_path, scores_path)

    analysis = md.split("### LLM analysis", 1)[1]
    assert "analysis unavailable" in analysis.lower()
    assert "connection refused" not in analysis


def test_non_local_model_is_not_used_for_review_analysis(tmp_path, monkeypatch):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)
    set_case_model(cases_dir, "openai/review-model")
    make_completed_run(
        runs_dir, "20261011T020000Z-B98", "PASS", model="openai/review-model"
    )
    called = False

    def forbidden_run(argv, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("review evidence must not be sent to a non-local model")

    monkeypatch.setattr(review.shutil, "which", lambda executable: "/usr/bin/opencode")
    monkeypatch.setattr(review.subprocess, "run", forbidden_run)

    md = generate(root, runs_dir, cases_dir, out_path, scores_path)

    assert "analysis unavailable" in md.lower()
    assert not called


def test_review_analysis_uses_report_model_and_skips_hosted_runs(tmp_path, monkeypatch):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)
    set_case_model(cases_dir, "llama-router/test-model")
    make_completed_run(
        runs_dir,
        "20261011T020000Z-B98",
        "PASS",
        model="opencode/deepseek-v4.1-flash-free",
    )
    called = False

    def forbidden_run(argv, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("review evidence must not be sent to a hosted model")

    monkeypatch.setattr(review.shutil, "which", lambda executable: "/usr/bin/opencode")
    monkeypatch.setattr(review.subprocess, "run", forbidden_run)

    md = generate(root, runs_dir, cases_dir, out_path, scores_path)

    analysis = md.split("### LLM analysis", 1)[1]
    assert "local-only" in analysis.lower()
    assert not called


def test_unreadable_required_analysis_evidence_is_not_summarized(tmp_path, monkeypatch):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)
    set_case_model(cases_dir, "llama-router/test-model")
    run = make_completed_run(
        runs_dir, "20261011T020000Z-B98", "PASS", model="llama-router/test-model"
    )
    (run / "prechecks.json").write_text("{oops", encoding="utf-8")
    called = False

    def forbidden_run(argv, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("analysis must not treat malformed evidence as complete")

    monkeypatch.setattr(review.shutil, "which", lambda executable: "/usr/bin/opencode")
    monkeypatch.setattr(review.subprocess, "run", forbidden_run)

    md = generate(root, runs_dir, cases_dir, out_path, scores_path)

    analysis = md.split("### LLM analysis", 1)[1]
    assert "analysis unavailable" in analysis.lower()
    assert "prechecks.json" in analysis
    assert not called


def test_missing_run_section_renders_placeholder(tmp_path):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)

    md = generate(root, runs_dir, cases_dir, out_path, scores_path)

    assert "No completed run yet" in md
    assert "## B98" in md


def test_rubric_row_included_and_absent_rubric_degrades(tmp_path):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)

    md = generate(root, runs_dir, cases_dir, out_path, scores_path)
    assert "`debugging-failure-triage`: reproduces the failure." in md

    (root / "validation" / "rubric.md").unlink()
    md2 = generate(root, runs_dir, cases_dir, out_path, scores_path)
    assert "## B98" in md2


def test_unreadable_run_artifacts_degrade_per_section(tmp_path):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)
    run = make_completed_run(runs_dir, "20261011T020000Z-B98", "PASS")
    (run / "turns.json").write_text("{oops", encoding="utf-8")

    md = generate(root, runs_dir, cases_dir, out_path, scores_path)

    assert "PASS" in md
    assert "unreadable" in md


# --- automatic rubric grading ------------------------------------------------


def test_automatic_rubric_scores_are_generated_in_the_brief(tmp_path):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)
    make_completed_run(runs_dir, "20261011T020000Z-B98", "PASS")

    md = generate(root, runs_dir, cases_dir, out_path, scores_path)

    section = md.split("## B98")[1]
    for field in ("owner-routing", "execution", "technical-correctness", "notes"):
        assert field in section
    assert "execution: PASS" in section
    assert "technical-correctness: PASS" in section
    assert "owner-routing: PASS" in section
    assert not scores_path.exists()


def test_review_brief_does_not_require_or_create_a_score_overlay(tmp_path):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)
    make_completed_run(
        runs_dir,
        "20261011T020000Z-B98",
        "FAIL",
        failures=["owner_read_any_of: no owner skill read"],
    )

    md = generate(root, runs_dir, cases_dir, out_path, scores_path)

    assert "review-scores.md" not in md
    assert "automatic rubric" in md.lower() or "rubric" in md.lower()
    assert "execution: FAIL" in md
    assert not scores_path.exists()


def test_repeated_generation_recomputes_scores_without_overlay_state(tmp_path):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)
    make_completed_run(runs_dir, "20261011T020000Z-B98", "FAIL")
    first = generate(root, runs_dir, cases_dir, out_path, scores_path)
    assert "execution: FAIL" in first

    make_completed_run(runs_dir, "20261011T030000Z-B98", "PASS")
    second = generate(root, runs_dir, cases_dir, out_path, scores_path)
    assert "20261011T030000Z-B98" in second
    assert "execution: PASS" in second
    assert not scores_path.exists()


# --- CLI ---------------------------------------------------------------------


def test_batch_with_case_errors_keeps_completed_evidence_and_rubric_data(
    tmp_path, monkeypatch
):
    root, runs_dir, cases_dir, out_path, scores_path = make_repo(tmp_path)
    make_case(runs_dir, cases_dir, "B99")
    monkeypatch.setattr(
        runner_main.batch,
        "central_stamp",
        lambda offset_seconds=0: "2026-10-11-T10-20-30-CDT",
    )
    completed_run = "20261011T040000Z-B98"

    def run_case(
        case_id: str, repo_root: Path, runs_dir: Path | None = None, **kwargs: object
    ) -> RunResult:
        assert runs_dir is not None
        if case_id == "B99":
            raise ValueError("pre-flight failure")
        run = make_completed_run(runs_dir, completed_run, "PASS")
        return RunResult(case_id, "PASS", run, ())

    monkeypatch.setattr(runner_main.orchestrator, "run_case", run_case)
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        code = runner_main.main(["B98", "B99"], repo_root=root)

    assert code == 1
    brief = out_path.read_text(encoding="utf-8")
    assert completed_run in brief
    assert "PASS" in brief
    assert "execution: PASS" in brief
    assert not scores_path.exists()


def test_review_flag_is_not_supported(tmp_path):
    err = io.StringIO()
    try:
        with redirect_stderr(err):
            runner_main.main(["--review"], repo_root=tmp_path)
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("--review should no longer be a CLI option")
    assert "unrecognized arguments: --review" in err.getvalue()
