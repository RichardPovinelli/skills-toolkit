"""Reviewer brief: one self-contained, regenerable markdown page per wired case (R-13).

The brief merges, per case: the TOML spec summary, the rubric row, the newest
completed run's automated verdict, skill-activation facts from the raw event
stream, workspace diffs, and pointers to the conversation log. Rubric values are
computed directly in the brief from the latest evidence, so the brief remains the
single source of truth without a separate hand-owned overlay file. Everything here
is derived from artifacts already on disk.
"""

from __future__ import annotations

import json
import os
import os.path
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from skillkit.evals.runner import batch, spec, transcript
from skillkit.evals.runner.layout import CollectionLayout

SCORE_FIELDS = ("owner-routing", "execution", "technical-correctness", "notes")


def generate(
    *,
    runs_dir: Path,
    cases_dir: Path,
    root: Path,
    out_path: Path,
    scores_path: Path,
    rubric_path: Path | None = None,
) -> str:
    """Render the brief for every wired case, write it to ``out_path``, return it.

    Individual unreadable per-run artifacts degrade their section only; a
    malformed *case spec* is a hard ``SpecError``. The brief itself is the
    single source for rubric values; no separate overlay file is created or read.
    """
    case_ids = sorted(p.stem for p in Path(cases_dir).glob("*.toml"))
    rubric_rows = _load_rubric_rows(_rubric_location(root, rubric_path))
    completed = batch.discover_completed(Path(runs_dir), case_ids) if Path(runs_dir).is_dir() else {}

    lines: list[str] = [
        "# Review brief",
        "",
        "<!-- Generated automatically after case and batch runs. Do not edit by hand:",
        "     this file is overwritten wholesale. Rubric values are computed from run evidence. -->",
        "",
        "| Case | Outcome | Run | " + " | ".join(SCORE_FIELDS) + " |",
        "| --- | --- | --- | " + " | ".join(["---"] * len(SCORE_FIELDS)) + " |",
    ]
    sections: list[str] = []
    for case_id in case_ids:
        case = spec.load_case(case_id, Path(cases_dir))
        done = completed.get(case_id)
        report = _read_json(done.run / "report.json") if done else {}
        if not isinstance(report, dict):
            report = {}
        outcome = done.outcome if done else "_"
        run_name = f"[{done.run.name}]({_rel(done.run, out_path.parent)})" if done else "_"
        score_values = _auto_scores(done, report)
        lines.append(
            f"| {case_id} | {outcome} | {run_name} | "
            + " | ".join(score_values.get(field, "_") for field in SCORE_FIELDS)
            + " |"
        )
        sections.append(
            _case_section(
                case_id,
                case,
                done,
                report,
                rubric_rows,
                score_values,
                out_path,
                scores_path,
            )
        )

    md = "\n".join(lines) + "\n\n" + "\n\n".join(sections) + "\n"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    return md


def _case_section(case_id, case, done, report, rubric_rows, score_values, out_path, scores_path) -> str:
    lines = [f"## {case_id}", ""]
    lines += ["### Spec", ""]
    lines += [
        f"- prompt: {case.prompt}",
        f"- model: {case.model}",
        f"- timeout: {case.timeout_s}s",
    ]
    exposure = "full catalog" if case.exposure.full_catalog else ", ".join(case.exposure.skill_names)
    lines.append(f"- exposure: {exposure}")
    if case.canned_replies:
        lines.append(f"- canned replies: {list(case.canned_replies)}")
    for source, label in (
        (case.state, "state tree"),
        (case.skills_tree, "skills subtree"),
        (case.shims, "shims"),
    ):
        if source:
            lines.append(f"- {label}: {source}")
    for fixture in case.fixtures:
        lines.append(f"- fixture: {fixture.src} -> {fixture.dest}")
    for inline in case.inline_files:
        lines.append(f"- inline file: {inline.path}")
    if case.checks:
        lines.append("- checks:")
        lines += [f"  - `{check.name}` {_params(check.params)}" for check in case.checks]
    lines.append("")

    if case_id in rubric_rows:
        lines += ["### Rubric row", "", rubric_rows[case_id], ""]

    lines += _verdict_lines(done, report)
    if done:
        lines += _activation_lines(done)
        lines += _diff_lines(done)
        lines += [
            "### Conversation log",
            "",
            f"- human-readable: [`conversation.log`]({_rel(done.run / 'conversation.log', out_path.parent)})",
            f"- raw events: [`events.jsonl`]({_rel(done.run / 'events.jsonl', out_path.parent)})",
            "",
        ]
    lines += _score_lines(score_values)
    lines += _model_analysis_lines(case_id, case, done, report)
    return "\n".join(lines).rstrip("\n")


def _model_analysis_lines(case_id: str, case, done, report) -> list[str]:
    lines = ["### LLM analysis", ""]
    try:
        text = _model_analysis_text(case_id, case, done, report)
    except RuntimeError as exc:
        lines.append(f"_analysis unavailable: {exc}_")
        lines.append("")
        return lines

    if not text or not str(text).strip():
        lines.append("_analysis unavailable: the local model did not return any output._")
        lines.append("")
        return lines

    lines.append(
        "- provenance: model-generated analysis based on the case spec and the newest run artifacts "
        "(`report.json`, `prechecks.json`, `events.jsonl`, `turns.json`, and `conversation.log`)."
    )
    lines.append("")
    lines.append(str(text).strip())
    lines.append("")
    return lines


def _verdict_lines(done, report) -> list[str]:
    lines = ["### Automated verdict", ""]
    if not done:
        return lines + ["_No completed run yet._", ""]
    lines.append(f"- run: `{done.run.name}` — **{done.outcome}**")
    model = report.get("model")
    duration = report.get("duration_s")
    host = report.get("host")
    if model:
        lines.append(f"- model: {model}")
    if host:
        lines.append(f"- host: {host}")
    if duration is not None:
        lines.append(f"- duration: {duration}s")
    for failure in report.get("failures") or ():
        lines.append(f"- failed check: {failure}")
    prechecks = _read_json(done.run / "prechecks.json", sentinel=_MISSING)
    if prechecks is _MISSING:
        lines.append("- prechecks: _(unreadable)_")
    elif isinstance(prechecks, dict):
        for item in prechecks.get("checks") or ():
            if isinstance(item, dict) and "name" in item:
                lines.append(f"- precheck `{item['name']}`: {'ok' if item.get('ok') else 'FAILED'}")
    lines.append("")
    return lines


def _activation_lines(done) -> list[str]:
    lines = ["### Skill activation", ""]
    try:
        events = (done.run / "events.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return lines + ["_(unreadable: events.jsonl)_", ""]
    facts = transcript.parse_events(events)
    lines.append(f"- skills read (completed): {', '.join(facts.skills_read) or 'none'}")
    incomplete = [name for name in facts.skills_dirs_seen if name not in facts.skills_read]
    lines.append(f"- skills requested but not completed: {', '.join(incomplete) or 'none'}")
    lines.append(f"- files touched: {', '.join(facts.paths_touched) or 'none'}")
    if facts.malformed_lines:
        lines.append(f"- malformed event lines: {facts.malformed_lines}")
    lines.append("")
    return lines


def _diff_lines(done) -> list[str]:
    lines = ["### Workspace changes", ""]
    turns = _read_json(done.run / "turns.json", sentinel=_MISSING)
    if turns is _MISSING:
        return lines + ["_(unreadable: turns.json)_", ""]
    if not isinstance(turns, list) or not turns:
        return lines + ["_No turns recorded._", ""]
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        number = turn.get("turn", "?")
        suffix = " (timed out)" if turn.get("timed_out") else ""
        changed = turn.get("diff_after")
        if isinstance(changed, str):
            changed = [changed] if changed else []
        elif not isinstance(changed, list):
            changed = []
        lines.append(f"Turn {number}{suffix}:")
        lines += [f"- {path}" for path in changed] or ["- _(no workspace changes)_"]
    lines.append("")
    return lines


def _score_lines(score_values: dict[str, str]) -> list[str]:
    lines = ["### Rubric scores", ""]
    for field in SCORE_FIELDS:
        lines.append(f"- {field}: {score_values.get(field, '___')}")
    lines.append("")
    lines.append(
        "_Rubric values are generated directly from the latest run evidence and remain reviewable in the brief._"
    )
    return lines


def _params(params: dict) -> str:
    return json.dumps(params, sort_keys=True) if params else "{}"


def _rel(path: Path, base: Path) -> str:
    try:
        return PurePosixPath(os.path.relpath(Path(path).resolve(), Path(base).resolve())).as_posix()
    except ValueError:
        return str(path)


class _Missing:
    pass


_MISSING = _Missing()


def _read_json(path: Path, sentinel=None):
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError):
        return sentinel


def _model_analysis_text(case_id: str, case, done, report: dict) -> str:
    if not done:
        raise RuntimeError("no completed run available")

    report_model = report.get("model") if isinstance(report, dict) else None
    model_name = report_model or getattr(case, "model", "") or "llama-router/qwen3.8-27b-q6"
    if not model_name.startswith("llama-router/"):
        raise RuntimeError("analysis unavailable: review analysis is local-only")
    if not shutil.which("opencode"):
        raise RuntimeError("opencode CLI is not available")
    if not isinstance(report, dict):
        raise RuntimeError("evidence unavailable: report.json")

    run_dir = Path(done.run)
    prechecks = _read_json(run_dir / "prechecks.json", sentinel=_MISSING)
    turns = _read_json(run_dir / "turns.json", sentinel=_MISSING)
    if not isinstance(prechecks, dict):
        raise RuntimeError("evidence unavailable: prechecks.json")
    if not isinstance(turns, list):
        raise RuntimeError("evidence unavailable: turns.json")
    try:
        events = (run_dir / "events.jsonl").read_text(encoding="utf-8", errors="replace")
        conversation = (run_dir / "conversation.log").read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        artifact = Path(exc.filename).name if exc.filename else "run artifact"
        raise RuntimeError(f"evidence unavailable: {artifact}") from exc

    evidence = {
        "case_id": case_id,
        "outcome": getattr(done, "outcome", "_"),
        "report": json.dumps(report, sort_keys=True)[:8000],
        "prechecks": json.dumps(prechecks, sort_keys=True)[:8000],
        "events": events[:12000],
        "turns": json.dumps(turns, sort_keys=True)[:8000],
        "conversation": conversation[:6000],
        "case_spec": json.dumps(
            {
                "prompt": getattr(case, "prompt", ""),
                "model": getattr(case, "model", ""),
                "timeout_s": getattr(case, "timeout_s", ""),
                "exposure": getattr(case, "exposure", None),
                "checks": [check.name for check in getattr(case, "checks", ())],
            },
            sort_keys=True,
            default=str,
        )[:4000],
    }
    prompt = (
        "You are a careful reviewer for a software-evaluation brief. "
        "Use only the evidence below. Do not invent missing files or grading output. "
        "Identify the strongest evidence for PASS/FAIL, note any missing or contradictory facts, "
        "and answer in at most 3 brief bullet points and 80 words total. Cite artifact names like report.json, "
        "events.jsonl, turns.json, conversation.log, and prechecks.json.\n\n"
        f"Case ID: {case_id}\n"
        f"Prompt: {getattr(case, 'prompt', '')}\n"
        f"Model: {model_name}\n"
        f"Evidence: {json.dumps(evidence, sort_keys=True, default=str)}"
    )
    try:
        with tempfile.TemporaryDirectory(prefix="eval-review-") as analysis_dir:
            env = os.environ.copy()
            env["PWD"] = analysis_dir
            env["TMPDIR"] = analysis_dir
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps({"tools": {"*": False}})
            completed = subprocess.run(
                [
                    "opencode",
                    "run",
                    "--format",
                    "json",
                    "--standalone",
                    "--agent",
                    "plan",
                    "--model",
                    model_name,
                    prompt,
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                cwd=analysis_dir,
                env=env,
                stdin=subprocess.DEVNULL,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"model unavailable: {exc}") from exc
    if completed.returncode != 0:
        raise RuntimeError(f"model unavailable: opencode exited with status {completed.returncode}")

    text = (completed.stdout or "").strip()
    if not text:
        raise RuntimeError("local model returned no analysis output")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payloads = []
        for line in text.splitlines():
            try:
                payloads.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        extracted = _extract_model_analysis_text(payloads)
        return extracted or text

    extracted = _extract_model_analysis_text(payload)
    if extracted:
        return extracted
    return text


def _extract_model_analysis_text(payload):
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        pieces = [_extract_model_analysis_text(item) for item in payload]
        return "\n".join(part for part in pieces if part)
    if isinstance(payload, dict):
        if payload.get("type") == "text":
            return _extract_model_analysis_text((payload.get("part") or {}).get("text", ""))
        if payload.get("type"):
            return ""
        for key in ("text", "content", "answer", "response", "output", "message"):
            value = payload.get(key)
            if value is not None:
                extracted = _extract_model_analysis_text(value)
                if extracted:
                    return extracted
        for key, value in payload.items():
            if key in {"tool", "meta", "usage"}:
                continue
            extracted = _extract_model_analysis_text(value)
            if extracted:
                return extracted
    return ""


def _auto_scores(done, report: dict) -> dict[str, str]:
    outcome = (
        "PASS"
        if getattr(done, "outcome", "_") == "PASS"
        else "FAIL"
        if getattr(done, "outcome", "_") in {"FAIL", "ERROR"}
        else "_"
    )
    values = {field: outcome if outcome else "_" for field in SCORE_FIELDS}
    if done and isinstance(report, dict):
        failures = report.get("failures") or ()
        owner_failed = any("owner_read_" in str(f) for f in failures)
        values["owner-routing"] = (
            "FAIL"
            if owner_failed
            else "PASS"
            if outcome == "PASS" or not any("turn 1: agent process exited with code 1" in str(f) for f in failures)
            else "FAIL"
        )

        note_parts = []
        if report.get("failures"):
            note_parts.extend(str(item) for item in report["failures"])
        if report.get("model"):
            note_parts.append(f"model={report['model']}")
        values["notes"] = "; ".join(note_parts) if note_parts else outcome
    elif not done:
        values["notes"] = "no completed run"
    return values


def _rubric_location(root: Path, rubric_path: Path | None) -> Path:
    """Resolve the rubric file; an explicit path wins over the collection default."""
    if rubric_path is not None:
        return Path(rubric_path)
    return CollectionLayout.from_root(Path(root)).rubric_path


def _load_rubric_rows(rubric: Path) -> dict[str, str]:
    rows: dict[str, str] = {}
    rubric = Path(rubric)
    if not rubric.is_file():
        return rows
    for line in rubric.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("| B") and stripped.count("|") >= 4:
            case_id = stripped.split("|")[1].strip()
            if case_id and case_id not in rows:
                rows[case_id] = stripped
    return rows
