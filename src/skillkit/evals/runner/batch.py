"""Batch execution: run many cases, skip completed work, aggregate reports.

A case counts as *completed* when ``<runs-dir>/<central-time>-<ID>/report.json`` with a
parseable ``outcome`` exists; batches skip those unless ``force=True``. The
aggregate report is a grader handoff only: automated outcomes are not rubric
grades (see evals/README.md).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

CENTRAL_TZ = ZoneInfo("America/Chicago")


@dataclass(frozen=True)
class CompletedRun:
    """A previous finished run found on disk."""

    run: Path
    outcome: str


@dataclass(frozen=True)
class BatchEntry:
    """One row of the aggregate batch report."""

    case_id: str
    outcome: str  # PASS | FAIL | SKIP | ERROR
    run_dir: Path | None
    failures: tuple[str, ...] = ()
    note: str = ""
    duration_s: float | None = None


def central_now() -> datetime:
    return datetime.now(CENTRAL_TZ)


def central_stamp(offset_seconds: int = 0) -> str:
    central_time = central_now() + timedelta(seconds=offset_seconds)
    return central_time.strftime("%Y-%m-%d-T%H-%M-%S-%Z")


def central_iso() -> str:
    return central_now().isoformat(timespec="seconds")


def _read_report(run: Path) -> dict | None:
    report = run / "report.json"
    if not report.is_file():
        return None
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "outcome" in data else None


def discover_completed(runs_dir: Path, case_ids: list[str]) -> dict[str, CompletedRun]:
    """Map each case id to its most recent completed run under ``runs_dir``.

    The report file's modification time determines which completed run is
    latest; directory names use Central Time and switch between CST and CDT.
    Directories without a readable report (crashed runs, batch dirs) are ignored.
    """
    found: dict[str, CompletedRun] = {}
    if not runs_dir.is_dir():
        return found
    for case_id in case_ids:
        latest: CompletedRun | None = None
        latest_key: tuple[int, str] | None = None
        for run in runs_dir.glob(f"*-{case_id}"):
            if not run.is_dir():
                continue
            data = _read_report(run)
            if data is None:
                continue
            key = ((run / "report.json").stat().st_mtime_ns, run.name)
            if latest_key is None or key > latest_key:
                latest = CompletedRun(run=run, outcome=str(data["outcome"]))
                latest_key = key
        if latest is not None:
            found[case_id] = latest
    return found


def run_batch(
    case_ids: list[str],
    repo_root: Path,
    runs_dir: Path,
    *,
    force: bool = False,
    run_one: Callable[..., object],
) -> list[BatchEntry]:
    """Run every pending case; never abort the batch on a single case error.

    ``run_one`` mirrors ``orchestrator.run_case(case_id, root, runs_dir=...)``
    and returns a RunResult; tests inject fakes so nothing launches opencode.
    """
    completed = {} if force else discover_completed(Path(runs_dir), case_ids)
    entries: list[BatchEntry] = []
    for case_id in case_ids:
        prior = completed.get(case_id)
        if prior is not None:
            entries.append(
                BatchEntry(
                    case_id=case_id,
                    outcome="SKIP",
                    run_dir=prior.run,
                    note=f"earlier run: {prior.outcome}",
                    duration_s=_duration(prior.run),
                )
            )
            continue
        try:
            result = run_one(case_id, repo_root, runs_dir=runs_dir)
        except Exception as err:  # noqa: BLE001 - batch must keep going
            entries.append(
                BatchEntry(
                    case_id=case_id,
                    outcome="ERROR",
                    run_dir=None,
                    failures=(f"{type(err).__name__}: {err}",),
                )
            )
            continue
        run_dir = Path(result.run_dir)  # type: ignore[attr-defined]
        entries.append(
            BatchEntry(
                case_id=case_id,
                outcome=str(result.outcome),  # type: ignore[attr-defined]
                run_dir=run_dir,
                failures=tuple(result.failures),  # type: ignore[attr-defined]
                duration_s=_duration(run_dir),
            )
        )
    return entries


def _duration(run_dir: Path) -> float | None:
    data = _read_report(run_dir)
    value = data.get("duration_s") if data else None
    return float(value) if isinstance(value, (int, float)) else None


def write_aggregate(
    batch_dir: Path, entries: list[BatchEntry], *, started_central: str
) -> tuple[Path, Path]:
    """Write aggregate ``report.json`` + ``report.md`` into ``batch_dir``."""
    batch_dir = Path(batch_dir)
    batch_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, int] = {}
    for entry in entries:
        summary[entry.outcome] = summary.get(entry.outcome, 0) + 1

    payload = {
        "started_central": started_central,
        "summary": {k: summary.get(k, 0) for k in ("PASS", "FAIL", "SKIP", "ERROR")},
        "cases": [
            {
                "case_id": e.case_id,
                "outcome": e.outcome,
                "run_dir": str(e.run_dir) if e.run_dir else None,
                "failures": list(e.failures),
                "note": e.note,
                "duration_s": e.duration_s,
            }
            for e in entries
        ],
    }
    json_path = batch_dir / "report.json"
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Eval batch report",
        "",
        f"Started (Central): {started_central}",
        "",
        "Automated outcomes only — rubric grading is separate (see evals/README.md).",
        "",
        f"Summary: {payload['summary']}",
        "",
        "| Case | Outcome | Duration (s) | Run dir | Notes / failures |",
        "| --- | --- | --- | --- | --- |",
    ]
    for e in entries:
        notes = "; ".join(part for part in (*e.failures, e.note) if part)
        duration = f"{e.duration_s:.1f}" if e.duration_s is not None else "-"
        run_dir = str(e.run_dir) if e.run_dir else "-"
        lines.append(
            f"| {e.case_id} | {e.outcome} | {duration} | {run_dir} | {notes or '-'} |"
        )
    md_path = batch_dir / "report.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
