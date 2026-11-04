"""Launch opencode headless as a killable subprocess (evidence first, ADR 0005).

stdout streams directly to ``events.jsonl`` (kernel-buffered, so a crash or
kill still leaves every event already emitted on disk); stderr goes to
``stderr.log``. The child runs in its own process group so a timeout can kill
the whole tree, not just the leader.
"""

import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class DriverResult:
    """Outcome of one opencode invocation."""

    exit_code: int | None
    timed_out: bool


def _killpg_run(argv: list[str], *, timeout: float, **kwargs) -> subprocess.CompletedProcess:
    """Default run_ci: own process group, SIGKILL the group on timeout."""
    proc = subprocess.Popen(argv, start_new_session=True, **kwargs)
    try:
        return subprocess.CompletedProcess(argv, proc.wait(timeout=timeout))
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        raise


def run_opencode(
    *,
    prompt: str,
    model: str,
    cwd: Path,
    env: dict[str, str],
    events_path: Path,
    stderr_path: Path,
    timeout_s: int,
    run_ci: Callable[..., subprocess.CompletedProcess] = _killpg_run,
    session_id: str | None = None,
    append_events: bool = False,
) -> DriverResult:
    argv = [
        "opencode",
        "run",
        "--format",
        "json",
        "--standalone",
        "-m",
        model,
    ]
    if session_id:
        # Continue the same opencode session so scripted replies see context.
        argv += ["-s", session_id]
    argv.append(prompt)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "ab" if append_events else "wb"
    with events_path.open(mode) as events_file, stderr_path.open(mode) as stderr_file:
        try:
            completed = run_ci(
                argv,
                cwd=str(cwd),
                env=env,
                stdout=events_file,
                stderr=stderr_file,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return DriverResult(exit_code=None, timed_out=True)
    return DriverResult(exit_code=completed.returncode, timed_out=False)
