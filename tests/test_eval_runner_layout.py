"""Tests for skillkit.evals.runner.layout collection path resolution."""

from pathlib import Path

from skillkit.evals.runner.layout import CollectionLayout


def test_defaults_place_data_under_validation(tmp_path: Path) -> None:
    layout = CollectionLayout.from_root(tmp_path)

    assert layout.root == tmp_path
    assert layout.skills_dir == tmp_path / "skills"
    assert layout.data_root == tmp_path / "validation"
    assert layout.cases_dir == tmp_path / "validation" / "cases"
    assert layout.state_dir == tmp_path / "validation" / "state"
    assert layout.rubric_path == tmp_path / "validation" / "rubric.md"
    assert layout.runs_dir == tmp_path / "validation-runs"
    assert layout.reports_dir == tmp_path / "reports"
    assert layout.protected_dir_names == frozenset({"validation"})


def test_defaults_do_not_require_the_directories_to_exist(tmp_path: Path) -> None:
    missing = tmp_path / "not-a-collection"

    layout = CollectionLayout.from_root(missing)

    assert not missing.exists()
    assert layout.cases_dir == missing / "validation" / "cases"


def test_overrides_replace_every_default(tmp_path: Path) -> None:
    layout = CollectionLayout.from_root(
        tmp_path,
        skills_dir=tmp_path / "catalog",
        data_root=tmp_path / "eval-data",
        cases_dir=tmp_path / "specs",
        state_dir=tmp_path / "states",
        rubric_path=tmp_path / "grading.md",
        runs_dir=tmp_path / "out" / "runs",
        reports_dir=tmp_path / "out" / "briefs",
    )

    assert layout.skills_dir == tmp_path / "catalog"
    assert layout.data_root == tmp_path / "eval-data"
    assert layout.cases_dir == tmp_path / "specs"
    assert layout.state_dir == tmp_path / "states"
    assert layout.rubric_path == tmp_path / "grading.md"
    assert layout.runs_dir == tmp_path / "out" / "runs"
    assert layout.reports_dir == tmp_path / "out" / "briefs"


def test_overriding_only_the_data_root_moves_derived_paths(tmp_path: Path) -> None:
    layout = CollectionLayout.from_root(tmp_path, data_root=tmp_path / "data")

    assert layout.cases_dir == tmp_path / "data" / "cases"
    assert layout.state_dir == tmp_path / "data" / "state"
    assert layout.rubric_path == tmp_path / "data" / "rubric.md"


def test_protected_directory_name_follows_the_data_root(tmp_path: Path) -> None:
    layout = CollectionLayout.from_root(tmp_path, data_root=tmp_path / "evals")

    assert layout.protected_dir_names == frozenset({"evals"})
