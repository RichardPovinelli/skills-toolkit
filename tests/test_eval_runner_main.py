"""Tests for the python -m evals.runner CLI contract."""

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from skillkit.evals.runner import __main__ as runner_main
from skillkit.evals.runner import spec, workspace
from skillkit.evals.runner.model_selection import ModelChoice
from skillkit.evals.runner.orchestrator import RunResult

pytestmark = pytest.mark.usefixtures("local_model_choice")


def test_single_case_defaults_collection_to_working_directory(tmp_path, monkeypatch):
    cases_dir = tmp_path / "validation" / "cases"
    cases_dir.mkdir(parents=True)
    (cases_dir / "B05.toml").write_text('id = "B05"\nprompt = "test"\n')
    monkeypatch.chdir(tmp_path)
    seen = {}

    def fake_run_case(
        case_id: str, root: Path, runs_dir: Path | None = None, **kwargs: object
    ) -> RunResult:
        assert runs_dir is not None
        case = spec.load_case(case_id, root / "validation" / "cases")
        seen["root"] = root
        seen["runs_dir"] = runs_dir
        return RunResult(case.case_id, "PASS", runs_dir / "R1", ())

    monkeypatch.setattr(runner_main.orchestrator, "run_case", fake_run_case)
    monkeypatch.setattr(runner_main.review, "generate", lambda **kwargs: "")

    assert runner_main.main(["B05"]) == 0
    assert seen == {"root": tmp_path, "runs_dir": tmp_path / "validation-runs"}
    assert (tmp_path / "reports").is_dir()


def test_collection_flag_selects_another_checkout(tmp_path, monkeypatch):
    collection = tmp_path / "collection"
    cases_dir = collection / "validation" / "cases"
    cases_dir.mkdir(parents=True)
    (cases_dir / "B05.toml").write_text('id = "B05"\nprompt = "test"\n')
    monkeypatch.chdir(tmp_path)
    seen = {}

    def fake_run_case(
        case_id: str, root: Path, runs_dir: Path | None = None, **kwargs: object
    ) -> RunResult:
        assert runs_dir is not None
        seen["root"] = root
        seen["runs_dir"] = runs_dir
        return RunResult(case_id, "PASS", runs_dir / "R1", ())

    monkeypatch.setattr(runner_main.orchestrator, "run_case", fake_run_case)
    monkeypatch.setattr(runner_main.review, "generate", lambda **kwargs: "")

    assert (
        runner_main.main(["B05", "--collection", str(collection), "--skip-review"])
        == 0
    )
    assert seen["root"] == collection
    assert seen["runs_dir"] == collection / "validation-runs"
    assert not (tmp_path / "validation-runs").exists()


def test_list_prints_case_ids_without_probing_the_model(tmp_path, monkeypatch):
    cases_dir = tmp_path / "validation" / "cases"
    cases_dir.mkdir(parents=True)
    for case_id in ("B05", "B02"):
        (cases_dir / f"{case_id}.toml").write_text(
            f'id = "{case_id}"\nprompt = "test"\n'
        )

    def forbidden_resolve(**kwargs):
        raise AssertionError("--list must not resolve or probe a model")

    monkeypatch.setattr(runner_main.model_selection, "resolve_model", forbidden_resolve)
    out = io.StringIO()
    with redirect_stdout(out):
        code = runner_main.main(["--list"], repo_root=tmp_path)

    assert code == 0
    assert out.getvalue().split() == ["B02", "B05"]


def test_list_reports_an_empty_collection(tmp_path):
    err = io.StringIO()
    with redirect_stderr(err):
        code = runner_main.main(["--list"], repo_root=tmp_path)
    assert code == 2
    assert "no case files found" in err.getvalue()


@pytest.mark.parametrize("launch_dir", [".", "validation", "validation/cases"])
def test_all_discovers_cases_independent_of_launch_directory(
    tmp_path, monkeypatch, launch_dir
):
    cases_dir = tmp_path / "validation" / "cases"
    cases_dir.mkdir(parents=True)
    for case_id in ("B05", "B02"):
        (cases_dir / f"{case_id}.toml").write_text(
            f'id = "{case_id}"\nprompt = "test"\n'
        )
    monkeypatch.chdir(tmp_path / launch_dir)
    monkeypatch.setenv("PWD", str(tmp_path))
    seen = []

    def fake_run_case(
        case_id: str, root: Path, runs_dir: Path | None = None, **kwargs: object
    ) -> RunResult:
        assert runs_dir is not None
        case = spec.load_case(case_id, root / "validation" / "cases")
        seen.append((case.case_id, root, runs_dir))
        return RunResult(case.case_id, "PASS", runs_dir / case.case_id, ())

    monkeypatch.setattr(runner_main.orchestrator, "run_case", fake_run_case)
    monkeypatch.setattr(runner_main.review, "generate", lambda **kwargs: "")

    assert runner_main.main(["--all", "--collection", str(tmp_path)]) == 0
    assert seen == [
        (case_id, tmp_path, tmp_path / "validation-runs") for case_id in ("B02", "B05")
    ]
    assert len(list((tmp_path / "validation-runs").glob("*-batch/report.json"))) == 1
    assert (tmp_path / "reports").is_dir()


def test_prints_result_line_and_exits_zero(tmp_path, monkeypatch):
    seen = {}

    def fake_run_case(case_id, root, runs_dir=None, **kwargs):
        seen["args"] = (case_id, str(root), runs_dir)
        return RunResult(case_id, "PASS", tmp_path / "runs" / "R1", ())

    monkeypatch.setattr(runner_main.orchestrator, "run_case", fake_run_case)
    out = io.StringIO()
    with redirect_stdout(out):
        code = runner_main.main(["B98"], repo_root=tmp_path)
    assert code == 0
    assert out.getvalue().startswith("B98: PASS ")
    assert seen["args"][0] == "B98"


def test_fail_exits_one_and_prints_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runner_main.orchestrator,
        "run_case",
        lambda *a, **k: RunResult(
            "B98", "FAIL", tmp_path / "r", ("owner_read_any_of: x",)
        ),
    )
    err = io.StringIO()
    with redirect_stderr(err):
        code = runner_main.main(["B98"], repo_root=tmp_path)
    assert code == 1
    assert "owner_read_any_of" in err.getvalue()


def test_single_runs_use_validation_runs_and_unique_timestamped_briefs(
    tmp_path, monkeypatch
):
    seen = {}

    def central_stamp(offset_seconds=0):
        return f"2026-10-11-T10-20-{30 + offset_seconds:02d}-CDT"

    def fake_run_case(case_id, root, runs_dir=None, **kwargs):
        seen.setdefault("runs_dirs", []).append(runs_dir)
        return RunResult(case_id, "FAIL", tmp_path / "runs" / "R1", ("check failed",))

    def fake_generate(**kwargs):
        seen.setdefault("out_paths", []).append(kwargs["out_path"])
        return "# Review brief\n"

    monkeypatch.setattr(runner_main.orchestrator, "run_case", fake_run_case)
    monkeypatch.setattr(runner_main.review, "generate", fake_generate)
    monkeypatch.setattr(runner_main.batch, "central_stamp", central_stamp)

    assert runner_main.main(["B98"], repo_root=tmp_path) == 1
    assert runner_main.main(["B98"], repo_root=tmp_path) == 1

    assert seen["runs_dirs"] == [tmp_path / "validation-runs"] * 2
    assert seen["out_paths"] == [
        tmp_path / "reports/2026-10-11-T10-20-30-CDT/review-brief.md",
        tmp_path / "reports/2026-10-11-T10-20-31-CDT/review-brief.md",
    ]


def test_unknown_case_exits_two_before_any_run_dir(tmp_path):
    err = io.StringIO()
    with redirect_stderr(err):
        code = runner_main.main(["NOPE"], repo_root=tmp_path)
    assert code == 2
    assert "NOPE" in err.getvalue()
    assert not (tmp_path / "validation-runs").exists()


def test_guard_error_exits_two(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise workspace.LeakError("would expose skills")

    monkeypatch.setattr(runner_main.orchestrator, "run_case", boom)
    err = io.StringIO()
    with redirect_stderr(err):
        code = runner_main.main(["B98"], repo_root=tmp_path)
    assert code == 2
    assert "GUARD-FAIL" in err.getvalue()


@pytest.mark.parametrize("case_ids", [["B05"], ["B02", "B05"], ["--all"]])
def test_skip_review_returns_result_without_starting_review(
    tmp_path, monkeypatch, case_ids
):
    cases_dir = tmp_path / "validation" / "cases"
    cases_dir.mkdir(parents=True)
    for case_id in ("B02", "B05"):
        (cases_dir / f"{case_id}.toml").write_text(
            f'id = "{case_id}"\nprompt = "test"\n'
        )
    monkeypatch.setattr(
        runner_main.orchestrator,
        "run_case",
        lambda cid, root, runs_dir=None, **kwargs: RunResult(
            cid, "PASS", runs_dir / cid, ()
        ),
    )

    def forbidden_review(**kwargs):
        raise AssertionError("--skip-review must not start review generation")

    monkeypatch.setattr(runner_main.review, "generate", forbidden_review)
    assert runner_main.main([*case_ids, "--skip-review"], repo_root=tmp_path) == 0
    assert not (tmp_path / "reports").exists()


def _choice(model="opencode/deepseek-v4.1-flash-free", reason="local-unreachable"):
    return ModelChoice(
        model=model,
        requested="llama-router/qwen3.8-27b-q6",
        reason=reason,
    )


def test_main_passes_resolved_model_to_single_run_and_prints_selection(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        runner_main.model_selection, "resolve_model", lambda **kwargs: _choice()
    )
    seen = {}

    def fake_run_case(case_id, root, runs_dir=None, **kwargs):
        seen.update(kwargs)
        return RunResult(case_id, "PASS", tmp_path / "runs" / "R1", ())

    monkeypatch.setattr(runner_main.orchestrator, "run_case", fake_run_case)
    err = io.StringIO()
    with redirect_stderr(err):
        code = runner_main.main(["B98", "--skip-review"], repo_root=tmp_path)

    assert code == 0
    assert seen["default_model"] == "opencode/deepseek-v4.1-flash-free"
    assert seen["model_reason"] == "local-unreachable"
    assert "opencode/deepseek-v4.1-flash-free" in err.getvalue()


def test_main_model_flag_requests_an_explicit_model(tmp_path, monkeypatch):
    seen = {}

    def fake_resolve(*, requested: str | None = None, probe=None):
        seen["requested"] = requested
        assert requested is not None
        return _choice(model=requested, reason="explicit")

    monkeypatch.setattr(runner_main.model_selection, "resolve_model", fake_resolve)
    monkeypatch.setattr(
        runner_main.orchestrator,
        "run_case",
        lambda cid, root, runs_dir=None, **kwargs: RunResult(
            cid, "PASS", tmp_path / "runs" / "R1", ()
        ),
    )

    assert (
        runner_main.main(
            ["B98", "--model", "opencode/deepseek-v4.1-flash-free", "--skip-review"],
            repo_root=tmp_path,
        )
        == 0
    )
    assert seen["requested"] == "opencode/deepseek-v4.1-flash-free"


def test_main_no_local_fallback_exits_two_before_running(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runner_main.model_selection, "resolve_model", lambda **kwargs: _choice()
    )

    def forbidden_run(*args, **kwargs):
        raise AssertionError("run_case must not start when local fallback is refused")

    monkeypatch.setattr(runner_main.orchestrator, "run_case", forbidden_run)
    err = io.StringIO()
    with redirect_stderr(err):
        code = runner_main.main(
            ["B98", "--no-local-fallback", "--skip-review"], repo_root=tmp_path
        )

    assert code == 2
    assert "local model" in err.getvalue().lower()


def test_main_free_fallback_runs_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runner_main.model_selection, "resolve_model", lambda **kwargs: _choice()
    )
    seen = {}

    def fake_run_case(case_id, root, runs_dir=None, **kwargs):
        seen.update(kwargs)
        return RunResult(case_id, "PASS", tmp_path / "runs" / "R1", ())

    monkeypatch.setattr(runner_main.orchestrator, "run_case", fake_run_case)
    err = io.StringIO()
    with redirect_stderr(err):
        code = runner_main.main(["B98", "--skip-review"], repo_root=tmp_path)

    assert code == 0
    assert seen["default_model"] == "opencode/deepseek-v4.1-flash-free"
    assert "opencode/deepseek-v4.1-flash-free" in err.getvalue()


def test_main_batch_passes_resolved_model_to_every_case(tmp_path, monkeypatch):
    cases_dir = tmp_path / "validation" / "cases"
    cases_dir.mkdir(parents=True)
    for case_id in ("B02", "B05"):
        (cases_dir / f"{case_id}.toml").write_text(
            f'id = "{case_id}"\nprompt = "test"\n'
        )
    monkeypatch.setattr(
        runner_main.model_selection, "resolve_model", lambda **kwargs: _choice()
    )
    seen = []

    def fake_run_case(
        case_id: str, root: Path, runs_dir: Path | None = None, **kwargs: object
    ) -> RunResult:
        assert runs_dir is not None
        seen.append((case_id, kwargs.get("default_model"), kwargs.get("model_reason")))
        return RunResult(case_id, "PASS", runs_dir / case_id, ())

    monkeypatch.setattr(runner_main.orchestrator, "run_case", fake_run_case)

    assert runner_main.main(["--all", "--skip-review"], repo_root=tmp_path) == 0
    assert seen == [
        ("B02", "opencode/deepseek-v4.1-flash-free", "local-unreachable"),
        ("B05", "opencode/deepseek-v4.1-flash-free", "local-unreachable"),
    ]
