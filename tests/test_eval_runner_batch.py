"""Tests for skillkit.evals.runner.batch: discovery, aggregation, skip/force semantics."""

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from skillkit.evals.runner import __main__ as runner_main
from skillkit.evals.runner import batch, spec
from skillkit.evals.runner.orchestrator import RunResult

pytestmark = pytest.mark.usefixtures("local_model_choice")


# --- timestamps --------------------------------------------------------------


def test_central_stamp_uses_cdt_in_summer(monkeypatch):
    summer = datetime(2026, 7, 1, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr(batch, "central_now", lambda: summer, raising=False)

    assert batch.central_stamp() == "2026-07-01-T12-00-00-CDT"


def test_central_stamp_uses_cst_in_winter(monkeypatch):
    winter = datetime(2026, 1, 15, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr(batch, "central_now", lambda: winter, raising=False)

    assert batch.central_stamp() == "2026-01-15-T12-00-00-CST"


def test_central_iso_includes_central_offset(monkeypatch):
    summer = datetime(2026, 7, 1, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr(batch, "central_now", lambda: summer, raising=False)

    assert batch.central_iso() == "2026-07-01T12:00:00-05:00"


def test_central_iso_uses_cst_offset_in_winter(monkeypatch):
    winter = datetime(2026, 1, 15, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr(batch, "central_now", lambda: winter, raising=False)

    assert batch.central_iso() == "2026-01-15T12:00:00-06:00"


def make_run(runs_dir: Path, name: str, outcome: str | None = "PASS") -> Path:
    run = runs_dir / name
    run.mkdir(parents=True)
    if outcome is not None:
        (run / "report.json").write_text(
            json.dumps(
                {
                    "case_id": name.rsplit("-", 1)[1],
                    "outcome": outcome,
                    "duration_s": 12.5,
                }
            ),
            encoding="utf-8",
        )
    return run


# --- discover_completed -----------------------------------------------------


def test_discover_finds_latest_completed_run_per_case(tmp_path):
    make_run(tmp_path, "20261011T010000Z-B02", "FAIL")
    make_run(tmp_path, "20261011T020000Z-B02", "PASS")
    make_run(tmp_path, "20261011T010000Z-B05", "FAIL")
    make_run(tmp_path, "20261011T030000Z-B05", outcome=None)  # crashed run

    found = batch.discover_completed(tmp_path, ["B02", "B05", "B99"])

    assert found["B02"].outcome == "PASS"
    assert found["B02"].run.name.endswith("020000Z-B02")
    assert found["B05"].outcome == "FAIL"
    assert "B99" not in found


def test_discover_uses_report_completion_order_across_local_time_changes(tmp_path):
    make_run(tmp_path, "2026-10-11-T10-20-31-CDT-B02", "FAIL")
    newest = make_run(tmp_path, "2026-10-11-T10-20-05-CST-B02", "PASS")

    found = batch.discover_completed(tmp_path, ["B02"])

    assert found["B02"].run == newest
    assert found["B02"].outcome == "PASS"


def test_discover_ignores_malformed_reports_and_foreign_dirs(tmp_path):
    broken = make_run(tmp_path, "20261011T010000Z-B02")
    (broken / "report.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "notes").mkdir()

    found = batch.discover_completed(tmp_path, ["B02"])

    assert found == {}


# --- write_aggregate --------------------------------------------------------


def make_entries():
    return [
        batch.BatchEntry("B02", "PASS", Path("/r/R1"), (), duration_s=10.0),
        batch.BatchEntry(
            "B05",
            "FAIL",
            Path("/r/R2"),
            ("owner_read_any_of: missing",),
            duration_s=20.0,
        ),
        batch.BatchEntry("B06", "SKIP", Path("/r/R0"), (), note="earlier run: PASS"),
        batch.BatchEntry("B07", "ERROR", None, ("case file missing",)),
    ]


def test_write_aggregate_json_and_markdown(tmp_path):
    entries = make_entries()

    json_path, md_path = batch.write_aggregate(
        tmp_path, entries, started_central="2026-10-11T09:00:00-05:00"
    )

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["started_central"] == "2026-10-11T09:00:00-05:00"
    assert "started_utc" not in data
    assert data["summary"] == {"PASS": 1, "FAIL": 1, "SKIP": 1, "ERROR": 1}
    assert data["cases"][1]["case_id"] == "B05"
    assert data["cases"][1]["failures"] == ["owner_read_any_of: missing"]

    md = md_path.read_text(encoding="utf-8")
    assert md.startswith("# Eval batch report")
    assert "Started (Central): 2026-10-11T09:00:00-05:00" in md
    for row_id in ("B02", "B05", "B06", "B07"):
        assert f"| {row_id} |" in md
    assert "earlier run: PASS" in md


# --- run_batch ---------------------------------------------------------------


def fake_executors(**per_case):
    def run_one(case_id, root, runs_dir=None):
        behavior = per_case[case_id]
        if isinstance(behavior, Exception):
            raise behavior
        return behavior

    return run_one


def test_run_batch_skips_completed_unless_forced(tmp_path):
    make_run(tmp_path / "runs", "20261011T010000Z-B02", "PASS")
    executed = []

    def run_one(case_id, root, runs_dir=None):
        executed.append(case_id)
        return RunResult(case_id, "PASS", tmp_path / "runs" / f"R-{case_id}", ())

    entries = batch.run_batch(
        ["B02", "B05"], tmp_path, tmp_path / "runs", run_one=run_one
    )

    assert executed == ["B05"]
    assert entries[0].outcome == "SKIP"
    assert entries[0].note == "earlier run: PASS"
    assert entries[1].outcome == "PASS"

    forced = batch.run_batch(
        ["B02", "B05"], tmp_path, tmp_path / "runs", force=True, run_one=run_one
    )
    assert executed == ["B05", "B02", "B05"]
    assert all(e.outcome == "PASS" for e in forced)


def test_run_batch_records_errors_without_aborting(tmp_path):
    run_one = fake_executors(
        B02=spec.SpecError("bad toml"),
        B05=RunResult("B05", "FAIL", Path("/r/R2"), ("check: nope",)),
        B06=RunResult("B06", "PASS", Path("/r/R3"), ()),
    )

    entries = batch.run_batch(
        ["B02", "B05", "B06"], tmp_path, tmp_path / "runs", run_one=run_one
    )

    assert [e.outcome for e in entries] == ["ERROR", "FAIL", "PASS"]
    assert "bad toml" in entries[0].failures[0]


def test_run_batch_writes_no_batch_dir_internals_on_error_cases(tmp_path):
    # run_batch itself never creates the batch dir; write_aggregate does.
    entries = batch.run_batch(
        ["B06"],
        tmp_path,
        tmp_path / "runs",
        run_one=fake_executors(B06=RunResult("B06", "PASS", Path("/r"), ())),
    )
    assert entries[0].outcome == "PASS"
    assert not (tmp_path / "runs" / "20261011T090000Z-batch").exists()


# --- CLI ---------------------------------------------------------------------


def write_case_toml(root: Path, case_id: str) -> None:
    cases = root / "validation" / "cases"
    cases.mkdir(parents=True, exist_ok=True)
    (cases / f"{case_id}.toml").write_text(
        f'id = "{case_id}"\nprompt = "p"\n', encoding="utf-8"
    )


def test_cli_all_expands_case_dir_and_writes_aggregate(tmp_path, monkeypatch):
    write_case_toml(tmp_path, "B02")
    write_case_toml(tmp_path, "B05")
    monkeypatch.setattr(
        runner_main.orchestrator,
        "run_case",
        lambda cid, root, runs_dir=None, **kwargs: RunResult(
            cid, "PASS", (tmp_path / "runs" / f"R-{cid}"), ()
        ),
    )
    monkeypatch.setattr(
        runner_main.batch, "central_iso", lambda: "2026-10-11T09:00:00-05:00"
    )
    monkeypatch.setattr(
        runner_main.batch,
        "central_stamp",
        lambda offset_seconds=0: "2026-10-11-T10-20-30-CDT",
    )
    out = io.StringIO()
    with redirect_stdout(out):
        code = runner_main.main(["--all"], repo_root=tmp_path)
    assert code == 0
    assert "B02: PASS" in out.getvalue() and "B05: PASS" in out.getvalue()
    batch_dir = tmp_path / "validation-runs" / "2026-10-11-T10-20-30-CDT-batch"
    assert (batch_dir / "report.json").is_file()
    assert (batch_dir / "report.md").is_file()
    data = json.loads((batch_dir / "report.json").read_text(encoding="utf-8"))
    assert data["started_central"] == "2026-10-11T09:00:00-05:00"


def test_cli_all_with_explicit_ids_exits_two(tmp_path):
    err = io.StringIO()
    with redirect_stderr(err):
        code = runner_main.main(["--all", "B02"], repo_root=tmp_path)
    assert code == 2
    assert "--all" in err.getvalue()


def test_cli_single_id_has_no_batch_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runner_main.orchestrator,
        "run_case",
        lambda cid, root, runs_dir=None, **kwargs: RunResult(
            cid, "PASS", tmp_path / "r", ()
        ),
    )
    out = io.StringIO()
    with redirect_stdout(out):
        code = runner_main.main(["B98"], repo_root=tmp_path)
    assert code == 0
    assert not (tmp_path / "validation-runs").exists()


def test_cli_multi_id_batch_force_flag_plumbed(tmp_path, monkeypatch):
    seen = {}

    def fake(cid, root, runs_dir=None, **kwargs):
        seen.setdefault("ids", []).append(cid)
        return RunResult(cid, "FAIL", tmp_path / "r" / cid, ("x: y",))

    monkeypatch.setattr(runner_main.orchestrator, "run_case", fake)
    make_run(tmp_path / "runs", "20261011T010000Z-B02", "PASS")
    err = io.StringIO()
    with redirect_stderr(err):
        code = runner_main.main(
            ["B02", "B05", "--force", "--runs-dir", str(tmp_path / "runs")],
            repo_root=tmp_path,
        )
    assert code == 1  # B05 FAIL
    assert seen["ids"] == ["B02", "B05"]  # --force reran the completed B02

    err2 = io.StringIO()
    with redirect_stderr(err2):
        code = runner_main.main(
            ["B02", "--runs-dir", str(tmp_path / "runs")], repo_root=tmp_path
        )
    # Single-id mode keeps always-run semantics even when completed.
    assert code == 1
