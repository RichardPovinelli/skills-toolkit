"""One-shot orchestrator: run a case, capture all evidence, then grade.

Implements design decision 6 (evidence-first): the run's artifacts are
written in a ``finally`` block so a timeout or a failed behavioral check
still leaves a complete evidence set; pre-launch verification and
metadata-only failures never launch the model at all (decision 4).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, replace
from pathlib import Path

from skillkit.evals.runner import (
    batch,
    checks,
    driver,
    metadata,
    model_selection,
    spec,
    transcript,
    workspace,
)
from skillkit.evals.runner.layout import CollectionLayout
from skillkit.evals.runner.types import DEFAULT_MODEL, CaseSpec


def _deviations_for(case: CaseSpec, requested_model: str, model_reason: str) -> list[str]:
    deviations = list(DEVIATIONS)
    if case.model != requested_model:
        deviations.append(f"model-fallback={case.model} (requested {requested_model}; {model_reason})")
    if case.shims:
        deviations.append(
            f"simulated-tool-env={case.shims} (shim PATH + $SHIM_STATE; install/tool "
            "outcomes are simulated, marked [SIMULATED], recorded under the run dir)"
        )
    return deviations


DEVIATIONS = [
    "containment=detective (guard + transcript checks, not OS-enforced)",
    "run-workspace-outside-repo + git-init anchor (approved 2026-10-11 after B02 run)",
    "agent-anchor=PWD env pin; TMPDIR pinned to sandbox tmp (2026-10-11 runs 2-4)",
    "skill-identity-source=state.input.id (live opencode event shape)",
    "global-skill-dirs=neutralized-via-synthetic-HOME",
    "scripted-turns=trailing-question await heuristic; runner_turn marker lines in events.jsonl",
]


@dataclass(frozen=True)
class RunResult:
    """Outcome of one evaluated case."""

    case_id: str
    outcome: str  # "PASS" or "FAIL"
    run_dir: Path
    failures: tuple[str, ...]


def run_case(
    case_id: str,
    repo_root: Path,
    runs_dir: Path | None = None,
    run_id: str | None = None,
    execute=driver.run_opencode,
    agent_root: Path | None = None,
    default_model: str | None = None,
    model_reason: str = "",
    layout: CollectionLayout | None = None,
    shims_root: Path | None = None,
) -> RunResult:
    """Load, isolate, execute, and check one case; never mutates the repo."""
    collection = layout if layout is not None else CollectionLayout.from_root(Path(repo_root))
    case = spec.load_case(case_id, collection.cases_dir)
    requested_model = case.model
    selection_reason = "case-default"
    if default_model and case.model == DEFAULT_MODEL:
        case = replace(case, model=default_model)
        selection_reason = model_reason or "default-override"
    elif case.model != DEFAULT_MODEL:
        selection_reason = "explicit"
    host = model_selection.host_for_model(case.model)
    started = time.monotonic()
    runs_dir = Path(runs_dir) if runs_dir else collection.runs_dir
    if run_id is None:
        offset_seconds = 0
        while True:
            run_id = batch.central_stamp(offset_seconds) + f"-{case_id}"
            if not (runs_dir / run_id).exists():
                break
            offset_seconds += 1
    paths = workspace.build_run_layout(
        Path(repo_root),
        case,
        runs_dir,
        run_id,
        agent_root=Path(agent_root) if agent_root else None,
        layout=collection,
        shims_root=Path(shims_root) if shims_root else None,
    )

    try:
        allowed = workspace.provision(paths)
        workspace.write_home_config(paths, case.model)
        workspace.leak_guard(paths, allowed)
    except workspace.WorkspaceError as err:
        _write_guard_marker(paths, err)
        raise

    shim_env = workspace.prepare_shim_env(paths)

    pre_snapshot = workspace.snapshot(paths.workspace_dir)
    events_path = paths.run_dir / "events.jsonl"
    results: list[driver.DriverResult] = []
    turns: list[dict] = []
    crash: Exception | None = None
    pending = list(case.canned_replies)
    session_id: str | None = None
    turn_snap = pre_snapshot
    try:
        turn_no = 1
        user_text = case.prompt
        while True:
            delivered = user_text
            if turn_no == 1 and case.session_context:
                delivered = "\n\n".join([*case.session_context, user_text])
            _append_turn_marker(events_path, turn_no, user_text)
            result = execute(
                prompt=delivered,
                model=case.model,
                cwd=paths.workspace_dir,
                env={**os.environ, **workspace.home_env(paths), **(shim_env or {})},
                events_path=events_path,
                stderr_path=paths.run_dir / "stderr.log",
                timeout_s=case.timeout_s,
                session_id=session_id,
                append_events=True,
            )
            results.append(result)
            facts = _parse_events(events_path)
            session_id = facts.session_id or session_id
            post_snapshot = workspace.snapshot(paths.workspace_dir)
            diff_after = workspace.diff_snapshots(turn_snap, post_snapshot)
            turn_snap = post_snapshot
            turns.append(
                {
                    "turn": turn_no,
                    "user": user_text,
                    "diff_after": list(diff_after),
                    "exit_code": result.exit_code,
                    "timed_out": result.timed_out,
                }
            )
            if result.timed_out or result.exit_code not in (0, None):
                break
            if not pending or not transcript.agent_awaits_user(facts):
                break
            user_text = pending.pop(0)
            turn_no += 1
    except Exception as err:  # noqa: BLE001 -- persist evidence for any run failure before re-raising
        crash = err
    finally:
        facts, diff = _collect_evidence(paths, pre_snapshot)
        turn_info = {
            "turn_count": len(turns),
            "replies_used": len(turns) - 1 if turns else 0,
            "replies_unused": len(pending),
        }
        _write_artifacts(
            paths,
            case,
            facts,
            diff,
            host=host,
            requested_model=requested_model,
            model_reason=selection_reason,
            turns=turns,
            turn_info=turn_info,
        )
    if crash is not None:
        raise crash
    assert results

    ctx = checks.CheckContext(
        facts=facts,
        catalog=tuple(workspace._skill_names(paths)),
        workspace_diff=diff,
        run_dir=str(paths.run_dir),
        workspace_dir=str(paths.workspace_dir),
        skills_dir=str(paths.collection.skills_dir.resolve()),
        sandbox_dir=str(paths.sandbox_dir),
        protected_dir_names=paths.collection.protected_dir_names,
    )
    failures = _failure_list(case, results, ctx)
    outcome = "PASS" if not failures else "FAIL"
    _write_report(
        paths,
        case,
        outcome,
        tuple(failures),
        time.monotonic() - started,
        host=host,
        requested_model=requested_model,
        model_reason=selection_reason,
    )
    return RunResult(case.case_id, outcome, paths.run_dir, tuple(failures))


def _write_report(
    paths: workspace.RunPaths,
    case: CaseSpec,
    outcome: str,
    failures: tuple[str, ...],
    duration_s: float,
    *,
    host: str,
    requested_model: str,
    model_reason: str,
) -> None:
    """Persist the per-run verdict report (R-10 evidence-first)."""
    report = {
        "case_id": case.case_id,
        "outcome": outcome,
        "failures": list(failures),
        "model": case.model,
        "model_selection": {
            "requested": requested_model,
            "resolved": case.model,
            "reason": model_reason,
        },
        "host": host,
        "duration_s": round(duration_s, 1),
    }
    (paths.run_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def _append_turn_marker(events_path: Path, turn_no: int, user_text: str) -> None:
    """Mark the scripted user turn inside the cumulative evidence stream."""
    events_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"type": "runner_turn", "turn": turn_no, "user": user_text})
    with events_path.open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")


def _parse_events(events_path: Path) -> transcript.RunFacts:
    lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return transcript.parse_events(lines)


def _collect_evidence(paths: workspace.RunPaths, pre_snapshot) -> tuple[transcript.RunFacts, tuple[str, ...]]:
    events_path = paths.run_dir / "events.jsonl"
    lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines() if events_path.exists() else []
    facts = transcript.parse_events(lines)
    diff = tuple(workspace.diff_snapshots(pre_snapshot, workspace.snapshot(paths.workspace_dir)))
    return facts, diff


def _write_artifacts(
    paths: workspace.RunPaths,
    case: CaseSpec,
    facts: transcript.RunFacts,
    diff,
    *,
    host: str,
    requested_model: str,
    model_reason: str,
    turns: list[dict] | None = None,
    turn_info: dict | None = None,
) -> None:
    ctx = checks.CheckContext(
        facts=facts,
        catalog=tuple(workspace._skill_names(paths)),
        workspace_diff=diff,
        run_dir=str(paths.run_dir),
        workspace_dir=str(paths.workspace_dir),
        skills_dir=str(paths.collection.skills_dir.resolve()),
        sandbox_dir=str(paths.sandbox_dir),
        protected_dir_names=paths.collection.protected_dir_names,
    )
    prechecks = checks.evaluate(case.checks, ctx)
    _touch_if_missing(paths.run_dir / "events.jsonl")
    _touch_if_missing(paths.run_dir / "stderr.log")
    (paths.run_dir / "conversation.log").write_text(transcript.render_conversation_log(facts), encoding="utf-8")
    _write_json(paths.run_dir / "prechecks.json", prechecks)
    if turns is not None:
        _write_json(paths.run_dir / "turns.json", turns)
    meta = metadata.build_metadata(
        case,
        host=host,
        created_central=batch.central_iso(),
        version_info=metadata.repo_version_info(paths.repo_root),
        deviations=_deviations_for(case, requested_model, model_reason),
        model_selection={
            "requested": requested_model,
            "resolved": case.model,
            "reason": model_reason,
        },
        turns=turn_info,
    )
    _write_json(paths.run_dir / "metadata.json", meta)


def _failure_list(case: CaseSpec, results: list[driver.DriverResult], ctx: checks.CheckContext) -> list[str]:
    failures: list[str] = []
    for index, result in enumerate(results, start=1):
        if result.timed_out:
            failures.append(f"turn {index} timed out after {case.timeout_s}s")
        elif result.exit_code not in (0, None):
            failures.append(f"turn {index}: agent process exited with code {result.exit_code}")
    for name, verdict in checks.evaluate(case.checks, ctx).items():
        failures.extend(f"{name}: {f}" for f in verdict["failures"])
    return failures


def _write_guard_marker(paths: workspace.RunPaths, err: Exception) -> None:
    marker = paths.run_dir / "FAILED_GUARD.md"
    try:
        marker.write_text(
            f"# Guard aborted this run before launch\n\n{type(err).__name__}: {err}\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _touch_if_missing(path: Path) -> None:
    if not path.exists():
        path.touch()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
