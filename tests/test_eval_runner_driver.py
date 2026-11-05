"""Tests for skillkit.evals.runner.driver opencode invocation."""

import subprocess
from pathlib import Path

import pytest

from skillkit.evals.runner import driver


class FakeCi:
    def __init__(
        self,
        result: subprocess.CompletedProcess | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(
        self, argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess:
        self.calls.append((argv, kwargs))
        if self.error:
            raise self.error
        assert self.result is not None
        return self.result


@pytest.fixture()
def run_paths(tmp_path: Path):
    workspace_dir = tmp_path / "run" / "workspace"
    workspace_dir.mkdir(parents=True)
    return workspace_dir, tmp_path / "run" / "events.jsonl", tmp_path / "run" / "stderr.log"


def test_run_opencode_builds_expected_command(run_paths) -> None:
    workspace_dir, events_path, stderr_path = run_paths
    fake = FakeCi(result=subprocess.CompletedProcess([], 0))

    result = driver.run_opencode(
        prompt="do the thing",
        model="llama-router/test-model",
        cwd=workspace_dir,
        env={"HOME": "/run/home"},
        events_path=events_path,
        stderr_path=stderr_path,
        timeout_s=7,
        run_ci=fake,
    )

    argv, kwargs = fake.calls[0]
    assert argv == [
        "opencode",
        "run",
        "--format",
        "json",
        "--standalone",
        "-m",
        "llama-router/test-model",
        "do the thing",
    ]
    assert kwargs["cwd"] == str(workspace_dir)
    assert kwargs["env"]["HOME"] == "/run/home"
    assert kwargs["timeout"] == 7
    assert not result.timed_out
    assert result.exit_code == 0


def test_run_opencode_streams_stdout_and_stderr_to_files(run_paths) -> None:
    workspace_dir, events_path, stderr_path = run_paths
    fake = FakeCi(result=subprocess.CompletedProcess([], 0))

    driver.run_opencode(
        prompt="p",
        model="m",
        cwd=workspace_dir,
        env={},
        events_path=events_path,
        stderr_path=stderr_path,
        timeout_s=1,
        run_ci=fake,
    )

    _, kwargs = fake.calls[0]
    assert kwargs["stdout"].name == str(events_path)
    assert kwargs["stderr"].name == str(stderr_path)


def test_run_opencode_reports_timeout(run_paths) -> None:
    workspace_dir, events_path, stderr_path = run_paths
    fake = FakeCi(error=subprocess.TimeoutExpired(["opencode"], 1))

    result = driver.run_opencode(
        prompt="p",
        model="m",
        cwd=workspace_dir,
        env={},
        events_path=events_path,
        stderr_path=stderr_path,
        timeout_s=1,
        run_ci=fake,
    )

    assert result.timed_out
    assert result.exit_code is None


def test_run_opencode_propagates_nonzero_exit(run_paths) -> None:
    workspace_dir, events_path, stderr_path = run_paths
    fake = FakeCi(result=subprocess.CompletedProcess([], 3))

    result = driver.run_opencode(
        prompt="p",
        model="m",
        cwd=workspace_dir,
        env={},
        events_path=events_path,
        stderr_path=stderr_path,
        timeout_s=1,
        run_ci=fake,
    )

    assert result.exit_code == 3
    assert not result.timed_out


def test_run_opencode_adds_session_flag_when_resuming(run_paths) -> None:
    workspace_dir, events_path, stderr_path = run_paths
    fake = FakeCi(result=subprocess.CompletedProcess([], 0))

    driver.run_opencode(
        prompt="reply",
        model="m",
        cwd=workspace_dir,
        env={},
        events_path=events_path,
        stderr_path=stderr_path,
        timeout_s=1,
        run_ci=fake,
        session_id="ses_42",
    )

    (argv, _), = fake.calls
    assert argv == [
        "opencode",
        "run",
        "--format",
        "json",
        "--standalone",
        "-m",
        "m",
        "-s",
        "ses_42",
        "reply",
    ]


def test_run_opencode_append_preserves_existing_events(run_paths) -> None:
    workspace_dir, events_path, stderr_path = run_paths
    events_path.write_text("prior line\n", encoding="utf-8")
    fake = FakeCi(result=subprocess.CompletedProcess([], 0))

    driver.run_opencode(
        prompt="p",
        model="m",
        cwd=workspace_dir,
        env={},
        events_path=events_path,
        stderr_path=stderr_path,
        timeout_s=1,
        run_ci=fake,
        append_events=True,
    )

    _, kwargs = fake.calls[0]
    assert "a" in kwargs["stdout"].mode
    assert events_path.read_text(encoding="utf-8") == "prior line\n"
