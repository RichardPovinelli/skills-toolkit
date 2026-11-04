"""CLI entry point: ``python -m evals.runner <CASE_ID> [...]`` or ``--all``.

Single-case exit codes: 0 = PASS, 1 = FAIL
(with all evidence written), 2 = pre-flight error (spec or guard) before
launch. Batch mode (multiple ids or ``--all``) skips cases that already have a
completed run unless ``--force``; exit code is 1 iff any case FAILed or
ERRORed, and an aggregate report lands in a Central Time ``*-batch`` directory
under the runs directory. Review generation runs after evaluation unless
``--skip-review`` is supplied.

The collection is supplied by ``--collection`` (or the programmatic
``repo_root`` argument) and defaults to the current directory; nothing is
derived from this tool's own source tree.
"""

from __future__ import annotations

import argparse
import sys
from functools import partial
from pathlib import Path

from skillkit.evals.runner import batch, model_selection, orchestrator, review, spec, workspace
from skillkit.evals.runner.layout import CollectionLayout


def _add_layout_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--collection",
        type=Path,
        default=None,
        help="collection root containing skills/ and its data directory (default: cwd)",
    )
    parser.add_argument("--cases-dir", type=Path, default=None)
    parser.add_argument("--state-dir", type=Path, default=None)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--rubric", type=Path, default=None)
    parser.add_argument("--runs-dir", type=Path, default=None)
    parser.add_argument("--reports-dir", type=Path, default=None)
    parser.add_argument(
        "--shims-root",
        type=Path,
        default=None,
        help="simulated tool environments (default: the packaged set)",
    )


def _layout_from_args(args: argparse.Namespace, repo_root: Path | None) -> CollectionLayout:
    collection_root = Path(args.collection or repo_root or Path.cwd())
    return CollectionLayout.from_root(
        collection_root,
        data_root=args.data_dir,
        cases_dir=args.cases_dir,
        state_dir=args.state_dir,
        rubric_path=args.rubric,
        runs_dir=args.runs_dir,
        reports_dir=args.reports_dir,
    )


def main(argv: list[str] | None = None, repo_root: Path | None = None) -> int:
    """Run one case (legacy contract) or a batch, printing per-case lines."""
    parser = argparse.ArgumentParser(prog="python -m evals.runner")
    parser.add_argument("case_ids", nargs="*", help="case ids, e.g. B02 B05")
    parser.add_argument(
        "--all",
        action="store_true",
        dest="run_all",
        help="run every case in the collection's cases directory",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_cases",
        help="print the discovered case ids and exit without running a model",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="in batch mode, re-run already-completed cases",
    )
    _add_layout_arguments(parser)
    parser.add_argument(
        "--model",
        default=None,
        help="force this provider/model for cases using the default; skips the local probe",
    )
    parser.add_argument(
        "--no-local-fallback",
        action="store_true",
        help="fail if the local model is unavailable instead of using the hosted fallback",
    )
    parser.add_argument(
        "--skip-review",
        action="store_true",
        help="return after evaluation without generating the model review brief",
    )
    args = parser.parse_args(argv)

    layout = _layout_from_args(args, repo_root)
    runs_dir = layout.runs_dir

    if args.list_cases:
        discovered = sorted(p.stem for p in layout.cases_dir.glob("*.toml"))
        if not discovered:
            print(f"ERROR: no case files found under {layout.cases_dir}", file=sys.stderr)
            return 2
        for case_id in discovered:
            print(case_id)
        return 0

    try:
        choice = model_selection.resolve_model(requested=args.model)
        if args.no_local_fallback and args.model is None and choice.reason != "local-available":
            raise model_selection.ModelSelectionError(
                f"local model is unavailable ({choice.reason}); "
                "refusing to use the hosted fallback because --no-local-fallback was set"
            )
    except model_selection.ModelSelectionError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2
    print(f"Model: {choice.model} ({choice.reason})", file=sys.stderr, flush=True)

    if args.run_all and args.case_ids:
        print("ERROR: --all cannot be combined with explicit case ids", file=sys.stderr)
        return 2
    if args.run_all:
        case_ids = sorted(p.stem for p in layout.cases_dir.glob("*.toml"))
        if not case_ids:
            print(
                f"ERROR: no case files found under {layout.cases_dir}",
                file=sys.stderr,
            )
            return 2
    elif args.case_ids:
        case_ids = list(args.case_ids)
    else:
        parser.error("provide case ids or --all")
        return 2  # unreachable; parser.error raises

    if len(case_ids) == 1 and not args.run_all:
        return _run_single(
            case_ids[0],
            layout,
            runs_dir,
            default_model=choice.model,
            model_reason=choice.reason,
            skip_review=args.skip_review,
            shims_root=args.shims_root,
        )
    return _run_batch(
        case_ids,
        layout,
        runs_dir,
        force=args.force,
        default_model=choice.model,
        model_reason=choice.reason,
        skip_review=args.skip_review,
        shims_root=args.shims_root,
    )


def _refresh_review(layout: CollectionLayout, runs_dir: Path) -> None:
    print(
        "Generating review brief (model analysis for every completed case; up to 120s per case). "
        "Use --skip-review to skip this step.",
        flush=True,
    )
    reports_dir = layout.reports_dir
    offset_seconds = 0
    while True:
        brief_dir = reports_dir / batch.central_stamp(offset_seconds)
        try:
            brief_dir.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            offset_seconds += 1
    out_path = brief_dir / "review-brief.md"
    try:
        review.generate(
            runs_dir=runs_dir,
            cases_dir=layout.cases_dir,
            root=layout.root,
            out_path=out_path,
            scores_path=reports_dir / "review-scores.md",
            rubric_path=layout.rubric_path,
        )
    except spec.SpecError as err:
        brief_dir.rmdir()
        print(f"WARNING: could not refresh review brief: {err}", file=sys.stderr)
        return
    print(f"Review brief: {out_path}")


def _run_single(
    case_id: str,
    layout: CollectionLayout,
    runs_dir: Path,
    *,
    default_model: str,
    model_reason: str,
    skip_review: bool = False,
    shims_root: Path | None = None,
) -> int:
    try:
        result = orchestrator.run_case(
            case_id,
            layout.root,
            runs_dir=runs_dir,
            default_model=default_model,
            model_reason=model_reason,
            layout=layout,
            shims_root=shims_root,
        )
    except spec.SpecError as err:
        print(f"ERROR: unknown or invalid case: {err}", file=sys.stderr)
        return 2
    except workspace.WorkspaceError as err:
        print(f"{case_id}: GUARD-FAIL {err}", file=sys.stderr)
        return 2

    print(f"{result.case_id}: {result.outcome} {result.run_dir}")
    for failure in result.failures:
        print(f"  - {failure}", file=sys.stderr)
    if not skip_review:
        _refresh_review(layout, runs_dir)
    return 0 if result.outcome == "PASS" else 1


def _run_batch(
    case_ids: list[str],
    layout: CollectionLayout,
    runs_dir: Path,
    *,
    force: bool,
    default_model: str,
    model_reason: str,
    skip_review: bool = False,
    shims_root: Path | None = None,
) -> int:
    started_central = batch.central_iso()
    run_one = partial(
        orchestrator.run_case,
        default_model=default_model,
        model_reason=model_reason,
        layout=layout,
        shims_root=shims_root,
    )
    entries = batch.run_batch(case_ids, layout.root, runs_dir, force=force, run_one=run_one)
    for entry in entries:
        run_dir = entry.run_dir if entry.run_dir else "-"
        print(f"{entry.case_id}: {entry.outcome} {run_dir}")
        for failure in entry.failures:
            print(f"  - {failure}", file=sys.stderr)
    offset_seconds = 0
    while True:
        batch_dir = runs_dir / f"{batch.central_stamp(offset_seconds)}-batch"
        if not batch_dir.exists():
            break
        offset_seconds += 1
    _, md_path = batch.write_aggregate(batch_dir, entries, started_central=started_central)
    print(f"Batch report: {md_path}")
    if not skip_review:
        _refresh_review(layout, runs_dir)
    bad = sum(1 for e in entries if e.outcome in ("FAIL", "ERROR"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
