"""Tests for skillkit.evals.runner.orchestrator end-of-run sequencing."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from skillkit.evals.runner import orchestrator, workspace
from skillkit.evals.runner.driver import DriverResult


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    skill = repo / "skills" / "alpha"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# alpha\n", encoding="utf-8")
    fixtures = repo / "validation" / "fixtures"
    fixtures.mkdir(parents=True)
    (fixtures / "sample.txt").write_text("x\n", encoding="utf-8")
    return repo


def write_case(repo: Path, case_id="B98", check_names=("owner_read_any_of",)):
    cases_dir = repo / "validation" / "cases"
    cases_dir.mkdir(parents=True)
    text = (
        f'id = "{case_id}"\nprompt = "do it"\n[exposure]\nfull_catalog = true\n'
        '[[fixture]]\nsrc = "fixtures/sample.txt"\ndest = "sample.txt"\n'
    )
    for name in check_names:
        text += f'[[check]]\nname = "{name}"\n'
        if name == "owner_read_any_of":
            text += 'any_of = ["alpha"]\n'
    (cases_dir / f"{case_id}.toml").write_text(text, encoding="utf-8")


def skill_line(name="alpha"):
    return json.dumps(
        {
            "sessionID": "ses_t",
            "type": "tool_use",
            "part": {
                "type": "tool",
                "tool": "skill",
                "state": {"status": "completed", "input": {"id": name}, "output": "ok"},
            },
        }
    )


def make_executor(lines=(), result=DriverResult(0, False)):
    def execute(**kwargs):
        Path(kwargs["events_path"]).write_text("".join(line + "\n" for line in lines))
        return result

    return execute


def test_run_case_passes_and_writes_all_artifacts(tmp_path):
    repo = make_repo(tmp_path)
    write_case(repo)

    result = orchestrator.run_case(
        "B98",
        repo,
        tmp_path / "runs",
        run_id="R1",
        execute=make_executor([skill_line()]),
        agent_root=tmp_path / "agent",
    )

    run = tmp_path / "runs" / "R1"
    assert result.outcome == "PASS"
    for name in (
        "events.jsonl",
        "conversation.log",
        "prechecks.json",
        "metadata.json",
        "stderr.log",
    ):
        assert (run / name).exists(), name
    prechecks = json.loads((run / "prechecks.json").read_text(encoding="utf-8"))
    assert prechecks["owner_read_any_of"]["passed"] is True
    meta = json.loads((run / "metadata.json").read_text(encoding="utf-8"))
    assert meta["case_id"] == "B98"
    assert meta["host"] == "opencode/local-router"
    assert meta["created_central"].endswith(("-05:00", "-06:00"))
    assert "created_utc" not in meta
    assert "skill:alpha" in (run / "conversation.log").read_text(encoding="utf-8")


def test_run_case_default_path_uses_central_timestamp_and_validation_runs(
    tmp_path, monkeypatch
):
    repo = make_repo(tmp_path)
    write_case(repo)
    summer = datetime(2026, 7, 1, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr(orchestrator.batch, "central_now", lambda: summer)

    result = orchestrator.run_case(
        "B98",
        repo,
        execute=make_executor([skill_line()]),
        agent_root=tmp_path / "agent",
    )

    assert result.run_dir == repo / "validation-runs" / "2026-07-01-T12-00-00-CDT-B98"


def test_run_case_fails_when_a_check_fails(tmp_path):
    repo = make_repo(tmp_path)
    write_case(repo)

    result = orchestrator.run_case(
        "B98",
        repo,
        tmp_path / "runs",
        run_id="R2",
        execute=make_executor([]),
        agent_root=tmp_path / "agent",
    )

    assert result.outcome == "FAIL"
    assert result.failures


def test_run_case_fails_on_timeout(tmp_path):
    repo = make_repo(tmp_path)
    write_case(repo, check_names=())

    result = orchestrator.run_case(
        "B98",
        repo,
        tmp_path / "runs",
        run_id="R3",
        execute=make_executor([], DriverResult(None, True)),
        agent_root=tmp_path / "agent",
    )

    assert result.outcome == "FAIL"
    assert any("timed out" in f for f in result.failures)


def test_guard_failure_marks_run_and_propagates(tmp_path):
    repo = make_repo(tmp_path)
    cases_dir = repo / "validation" / "cases"
    cases_dir.mkdir(parents=True)
    (cases_dir / "B97.toml").write_text(
        'id = "B97"\nprompt = "p"\n[[fixture]]\nsrc = "fixtures/sample.txt"\ndest = "plans/leak.md"\n',
        encoding="utf-8",
    )

    with pytest.raises(workspace.LeakError):
        orchestrator.run_case(
            "B97",
            repo,
            tmp_path / "runs",
            run_id="R4",
            execute=make_executor([]),
            agent_root=tmp_path / "agent",
        )

    marker = tmp_path / "runs" / "R4" / "FAILED_GUARD.md"
    assert marker.exists()
    assert "plans" in marker.read_text(encoding="utf-8") or "leak" in marker.read_text(
        encoding="utf-8"
    )


def test_run_case_writes_evidence_even_if_driver_crashes(tmp_path):
    repo = make_repo(tmp_path)
    write_case(repo, check_names=())

    def boom(**kwargs):
        raise RuntimeError("driver exploded")

    with pytest.raises(RuntimeError):
        orchestrator.run_case(
            "B98",
            repo,
            tmp_path / "runs",
            run_id="R5",
            execute=boom,
            agent_root=tmp_path / "agent",
        )

    run = tmp_path / "runs" / "R5"
    assert (run / "metadata.json").exists()
    assert (run / "conversation.log").exists()


def text_line(text: str, session: str = "ses_turns") -> str:
    return json.dumps(
        {"sessionID": session, "type": "text", "part": {"type": "text", "text": text}}
    )


def write_raw_case(repo: Path, body: str, case_id: str = "B98") -> None:
    cases_dir = repo / "validation" / "cases"
    cases_dir.mkdir(parents=True)
    (cases_dir / f"{case_id}.toml").write_text(body, encoding="utf-8")


def make_scripted_executor(
    turn_events: list[list[str]], turn_files: dict[int, list[str]] = None
):
    calls: list[dict] = []
    turn_files = turn_files or {}

    def execute(**kwargs):
        calls.append(kwargs)
        with open(kwargs["events_path"], "a", encoding="utf-8") as handle:
            for line in turn_events[len(calls) - 1]:
                handle.write(line + "\n")
        for name in turn_files.get(len(calls), []):
            Path(kwargs["cwd"], name).write_text(
                "def test_x(): pass\n", encoding="utf-8"
            )
        return DriverResult(0, False)

    execute.calls = calls
    return execute


def test_multiturn_continues_while_agent_asks_and_writes_turns_json(tmp_path) -> None:
    repo = make_repo(tmp_path)
    write_raw_case(
        repo,
        'id = "B98"\nmodel = "llama-router/fake-model"\n'
        'prompt = "Add a test for normalize()."\n'
        'canned_replies = ["Approved, add it.", "continue"]\n'
        '[[check]]\nname = "no_skill_outside_catalog"\n'
        '[[check]]\nname = "artifacts_exist"\npaths = ["test_normalize.py"]\n',
    )
    file_line = json.dumps(
        {
            "sessionID": "ses_turns",
            "type": "tool_use",
            "part": {
                "type": "tool",
                "tool": "write",
                "state": {
                    "status": "completed",
                    "input": {"filePath": "test_normalize.py"},
                },
            },
        }
    )
    executor = make_scripted_executor(
        [
            [text_line("Should I add tests only?")],
            [text_line("Please confirm I may create the test file?")],
            [file_line],
        ],
        turn_files={3: ["test_normalize.py"]},
    )

    result = orchestrator.run_case(
        "B98",
        repo,
        tmp_path / "runs",
        run_id="R6",
        execute=executor,
        agent_root=tmp_path / "agent",
    )

    assert result.outcome == "PASS"
    assert len(executor.calls) == 3
    assert executor.calls[0]["session_id"] is None
    assert executor.calls[1]["session_id"] == "ses_turns"
    assert executor.calls[1]["prompt"] == "Approved, add it."
    assert executor.calls[2]["prompt"] == "continue"
    assert executor.calls[1]["append_events"] is True

    turns = json.loads((result.run_dir / "turns.json").read_text(encoding="utf-8"))
    assert [entry["turn"] for entry in turns] == [1, 2, 3]
    assert [entry["user"] for entry in turns] == [
        "Add a test for normalize().",
        "Approved, add it.",
        "continue",
    ]
    assert turns[0]["diff_after"] == []
    assert "test_normalize.py" in turns[2]["diff_after"]

    conversation = (result.run_dir / "conversation.log").read_text(encoding="utf-8")
    assert "user:Approved, add it." in conversation

    metadata = json.loads(
        (result.run_dir / "metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["turns"] == {
        "turn_count": 3,
        "replies_used": 2,
        "replies_unused": 0,
    }


def test_multiturn_stops_when_agent_does_not_ask(tmp_path) -> None:
    repo = make_repo(tmp_path)
    write_raw_case(
        repo,
        'id = "B98"\nmodel = "llama-router/fake-model"\n'
        'prompt = "Fix this."\ncanned_replies = ["continue", "continue"]\n'
        '[[check]]\nname = "no_skill_outside_catalog"\n',
    )
    executor = make_scripted_executor([[text_line("Here is the finished test.")]])

    result = orchestrator.run_case(
        "B98",
        repo,
        tmp_path / "runs",
        run_id="R7",
        execute=executor,
        agent_root=tmp_path / "agent",
    )

    assert len(executor.calls) == 1
    metadata = json.loads(
        (result.run_dir / "metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["turns"] == {
        "turn_count": 1,
        "replies_used": 0,
        "replies_unused": 2,
    }


def test_multiturn_stops_on_timeout_and_records_failure(tmp_path) -> None:
    repo = make_repo(tmp_path)
    write_raw_case(
        repo,
        'id = "B98"\nmodel = "llama-router/fake-model"\n'
        'prompt = "Keep going."\ncanned_replies = ["continue"]\n'
        '[[check]]\nname = "no_skill_outside_catalog"\n',
    )
    calls: list[dict] = []

    def execute(**kwargs):
        calls.append(kwargs)
        with open(kwargs["events_path"], "a", encoding="utf-8") as handle:
            handle.write(text_line("Working... shall I continue?") + "\n")
        return DriverResult(None, timed_out=len(calls) == 1)

    result = orchestrator.run_case(
        "B98",
        repo,
        tmp_path / "runs",
        run_id="R8",
        execute=execute,
        agent_root=tmp_path / "agent",
    )

    assert len(calls) == 1
    assert result.outcome == "FAIL"
    assert any("timed out" in failure for failure in result.failures)


def test_run_case_writes_report_json_on_pass(tmp_path):
    repo = make_repo(tmp_path)
    write_case(repo)

    orchestrator.run_case(
        "B98",
        repo,
        tmp_path / "runs",
        run_id="R9",
        execute=make_executor([skill_line()]),
        agent_root=tmp_path / "agent",
    )

    report = json.loads(
        (tmp_path / "runs" / "R9" / "report.json").read_text(encoding="utf-8")
    )
    assert report["case_id"] == "B98"
    assert report["outcome"] == "PASS"
    assert report["failures"] == []
    assert report["model"]
    assert report["host"] == "opencode/local-router"
    assert isinstance(report["duration_s"], (int, float))


def test_run_case_uses_default_model_override_and_records_selection(tmp_path):
    repo = make_repo(tmp_path)
    write_case(repo)

    orchestrator.run_case(
        "B98",
        repo,
        tmp_path / "runs",
        run_id="R11",
        execute=make_executor([skill_line()]),
        agent_root=tmp_path / "agent",
        default_model="opencode/deepseek-v4.1-flash-free",
        model_reason="local-unreachable",
    )

    run = tmp_path / "runs" / "R11"
    report = json.loads((run / "report.json").read_text(encoding="utf-8"))
    metadata = json.loads((run / "metadata.json").read_text(encoding="utf-8"))
    assert report["model"] == "opencode/deepseek-v4.1-flash-free"
    assert report["host"] == "opencode/console"
    assert report["model_selection"] == {
        "requested": "llama-router/qwen3.8-27b-q6",
        "resolved": "opencode/deepseek-v4.1-flash-free",
        "reason": "local-unreachable",
    }
    assert metadata["model"] == "opencode/deepseek-v4.1-flash-free"
    assert metadata["model_selection"] == report["model_selection"]
    assert any("model-fallback" in item for item in metadata["deviations"])


def test_run_case_keeps_explicit_case_model_when_default_is_provided(tmp_path):
    repo = make_repo(tmp_path)
    write_raw_case(
        repo,
        'id = "B98"\nmodel = "llama-router/pinned-model"\nprompt = "do it"\n'
        '[[check]]\nname = "no_skill_outside_catalog"\n',
    )

    orchestrator.run_case(
        "B98",
        repo,
        tmp_path / "runs",
        run_id="R12",
        execute=make_executor([]),
        agent_root=tmp_path / "agent",
        default_model="opencode/deepseek-v4.1-flash-free",
        model_reason="local-unreachable",
    )

    report = json.loads(
        (tmp_path / "runs" / "R12" / "report.json").read_text(encoding="utf-8")
    )
    assert report["model"] == "llama-router/pinned-model"
    assert report["host"] == "opencode/local-router"
    assert report["model_selection"]["reason"] == "explicit"


def test_run_case_writes_report_json_on_fail(tmp_path):
    repo = make_repo(tmp_path)
    write_case(repo)

    orchestrator.run_case(
        "B98",
        repo,
        tmp_path / "runs",
        run_id="R10",
        execute=make_executor([]),
        agent_root=tmp_path / "agent",
    )

    report = json.loads(
        (tmp_path / "runs" / "R10" / "report.json").read_text(encoding="utf-8")
    )
    assert report["outcome"] == "FAIL"
    assert report["failures"]


def test_run_case_with_shims_exports_path_and_records_metadata(tmp_path):
    repo = make_repo(tmp_path)
    shim_dir = tmp_path / "shims" / "fake-set"
    shim_dir.mkdir(parents=True)
    (shim_dir / "exclude.txt").write_text("fake-tool\n", encoding="utf-8")
    (shim_dir / "fake-tool").write_text("#!/bin/sh\necho stub\n", encoding="utf-8")
    write_case(repo)
    case_path = repo / "validation" / "cases" / "B98.toml"
    case_path.write_text(
        case_path.read_text(encoding="utf-8").replace(
            'prompt = "do it"\n', 'prompt = "do it"\nshims = "fake-set"\n'
        ),
        encoding="utf-8",
    )

    seen = {}

    def execute(**kwargs):
        seen["env"] = kwargs["env"]
        Path(kwargs["events_path"]).write_text(skill_line() + "\n")
        return DriverResult(0, False)

    result = orchestrator.run_case(
        "B98",
        repo,
        tmp_path / "runs",
        run_id="RSH",
        execute=execute,
        agent_root=tmp_path / "agent",
        shims_root=tmp_path / "shims",
    )
    assert result.outcome == "PASS"
    env = seen["env"]
    path_parts = env["PATH"].split(":")
    staged = tmp_path / "runs" / "RSH" / "shim-env" / "fake-set"
    assert path_parts[0] == str(staged)
    assert (staged / "fake-tool").exists()
    assert path_parts[1] == str(
        (tmp_path / "runs" / "RSH" / "shim-state" / "bin").resolve()
    )
    assert Path(env["SHIM_STATE"]).is_dir()
    assert Path(env["SHIM_BIN"]).is_dir()
    meta = json.loads(
        (tmp_path / "runs" / "RSH" / "metadata.json").read_text(encoding="utf-8")
    )
    assert meta["shims"] == "fake-set"
    assert any(d.startswith("simulated-tool-env=fake-set") for d in meta["deviations"])


def test_run_case_without_shims_leaves_path_unchanged(tmp_path):
    repo = make_repo(tmp_path)
    write_case(repo)
    seen = {}

    def execute(**kwargs):
        seen["env"] = kwargs["env"]
        Path(kwargs["events_path"]).write_text(skill_line() + "\n")
        return DriverResult(0, False)

    orchestrator.run_case(
        "B98",
        repo,
        tmp_path / "runs",
        run_id="RNS",
        execute=execute,
        agent_root=tmp_path / "agent",
    )
    import os

    assert "SHIM_STATE" not in seen["env"]
    assert seen["env"]["PATH"] == os.environ["PATH"]
    meta = json.loads(
        (tmp_path / "runs" / "RNS" / "metadata.json").read_text(encoding="utf-8")
    )
    assert "shims" not in meta
