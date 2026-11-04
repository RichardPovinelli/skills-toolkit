"""Case-spec loading for the eval runner (skillkit.evals.runner.spec)."""

from pathlib import Path

import pytest

from skillkit.evals.runner import spec
from skillkit.evals.runner.types import Check, Fixture, InlineFile


def write_case(cases_dir: Path, case_id: str, body: str) -> Path:
    cases_dir.mkdir(parents=True, exist_ok=True)
    path = cases_dir / f"{case_id}.toml"
    path.write_text(body, encoding="utf-8")
    return path


MINIMAL_CASE = """
id = "B98"
prompt = "Fix this script."
"""


def test_load_case_returns_spec_fields(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(cases_dir, "B98", MINIMAL_CASE)

    case = spec.load_case("B98", cases_dir)

    assert case.case_id == "B98"
    assert case.prompt == "Fix this script."


def test_load_case_applies_default_model_and_timeout(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(cases_dir, "B98", MINIMAL_CASE)

    case = spec.load_case("B98", cases_dir)

    assert case.model == "llama-router/qwen3.8-27b-q6"
    assert case.timeout_s == 600


def test_load_case_uses_explicit_model_and_timeout(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(
        cases_dir,
        "B98",
        MINIMAL_CASE + 'model = "llama-router/other"\ntimeout = 42\n',
    )

    case = spec.load_case("B98", cases_dir)

    assert case.model == "llama-router/other"
    assert case.timeout_s == 42


def test_load_case_rejects_missing_prompt(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(cases_dir, "B98", 'id = "B98"\n')

    with pytest.raises(spec.SpecError, match="prompt"):
        spec.load_case("B98", cases_dir)


def test_load_case_rejects_unknown_case_id(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(cases_dir, "B98", MINIMAL_CASE)

    with pytest.raises(spec.SpecError, match="B99"):
        spec.load_case("B99", cases_dir)


def test_load_case_parses_skill_names_exposure(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(
        cases_dir,
        "B98",
        MINIMAL_CASE + '\n[exposure]\nskill_names = ["alpha", "beta"]\n',
    )

    case = spec.load_case("B98", cases_dir)

    assert case.exposure.full_catalog is False
    assert case.exposure.skill_names == ("alpha", "beta")


def test_load_case_parses_full_catalog_exposure(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(cases_dir, "B98", MINIMAL_CASE + "\n[exposure]\nfull_catalog = true\n")

    case = spec.load_case("B98", cases_dir)

    assert case.exposure.full_catalog is True
    assert case.exposure.skill_names == ()


def test_load_case_parses_skill_routing_opt_in(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(cases_dir, "B98", MINIMAL_CASE + "skill_routing = true\n")

    case = spec.load_case("B98", cases_dir)

    assert case.skill_routing is True


def test_load_case_defaults_skill_routing_to_false(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(cases_dir, "B98", MINIMAL_CASE)

    case = spec.load_case("B98", cases_dir)

    assert case.skill_routing is False


def test_load_case_rejects_non_boolean_skill_routing(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(cases_dir, "B98", MINIMAL_CASE + 'skill_routing = "yes"\n')

    with pytest.raises(spec.SpecError, match="skill_routing"):
        spec.load_case("B98", cases_dir)


def test_load_case_parses_fixtures(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(
        cases_dir,
        "B98",
        MINIMAL_CASE + '\n[[fixture]]\nsrc = "fixtures/pipeline.sh"\ndest = "pipeline.sh"\n',
    )

    case = spec.load_case("B98", cases_dir)

    assert case.fixtures == (Fixture(src="fixtures/pipeline.sh", dest="pipeline.sh"),)


def test_load_case_parses_checks(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(
        cases_dir,
        "B98",
        MINIMAL_CASE
        + '\n[[check]]\nname = "workspace_unchanged"\n'
        + '\n[[check]]\nname = "owner_read_any_of"\n'
        + 'any_of = ["debugging-failure-triage"]\n',
    )

    case = spec.load_case("B98", cases_dir)

    assert case.checks == (
        Check(name="workspace_unchanged", params={}),
        Check(name="owner_read_any_of", params={"any_of": ["debugging-failure-triage"]}),
    )


def test_load_case_defaults_optional_text_fields(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(cases_dir, "B98", MINIMAL_CASE)

    case = spec.load_case("B98", cases_dir)

    assert case.env_notes == ""
    assert case.canned_replies == ()


def test_load_case_parses_env_notes_and_canned_replies(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(
        cases_dir,
        "B98",
        MINIMAL_CASE + 'env_notes = "Bash is available."\n' + 'canned_replies = ["yes", "stop"]\n',
    )

    case = spec.load_case("B98", cases_dir)

    assert case.env_notes == "Bash is available."


def test_load_case_parses_inline_files_and_session_context(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(
        cases_dir,
        "B98",
        MINIMAL_CASE
        + 'session_context = ["Earlier: safe fixes approved."]\n'
        + 'canned_replies = ["yes", "stop"]\n'
        + '\n[[inline_file]]\npath = "pkg/settings.py"\n'
        + 'content = "MAX_RETRIES = 3\\n"\n',
    )

    case = spec.load_case("B98", cases_dir)

    assert case.session_context == ("Earlier: safe fixes approved.",)
    assert case.inline_files == (InlineFile(path="pkg/settings.py", content="MAX_RETRIES = 3\n"),)
    assert case.canned_replies == ("yes", "stop")  # parsed from the appended line above


def test_load_case_rejects_inline_file_escaping_workspace(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(
        cases_dir,
        "B98",
        MINIMAL_CASE + '\n[[inline_file]]\npath = "../evil.txt"\ncontent = "x"\n',
    )

    with pytest.raises(spec.SpecError, match="inline_file"):
        spec.load_case("B98", cases_dir)


def test_load_case_rejects_absolute_inline_file_path(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(
        cases_dir,
        "B98",
        MINIMAL_CASE + '\n[[inline_file]]\npath = "/etc/evil.txt"\ncontent = "x"\n',
    )

    with pytest.raises(spec.SpecError, match="inline_file"):
        spec.load_case("B98", cases_dir)


@pytest.mark.parametrize("src", ["../outside.txt", "/etc/passwd", ""])
def test_load_case_rejects_fixture_sources_outside_the_data_directory(
    tmp_path: Path, src: str
) -> None:
    cases_dir = tmp_path / "cases"
    write_case(
        cases_dir,
        "B98",
        MINIMAL_CASE + f'\n[[fixture]]\nsrc = "{src}"\ndest = "input.txt"\n',
    )

    with pytest.raises(spec.SpecError, match="fixture"):
        spec.load_case("B98", cases_dir)


def test_load_case_rejects_malformed_toml(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(cases_dir, "B98", "id = ][ broken")

    with pytest.raises(spec.SpecError, match="B98"):
        spec.load_case("B98", cases_dir)


def test_load_case_rejects_non_integer_timeout(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(cases_dir, "B98", MINIMAL_CASE + 'timeout = "soon"\n')

    with pytest.raises(spec.SpecError, match="timeout"):
        spec.load_case("B98", cases_dir)


def test_load_case_rejects_missing_id(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(cases_dir, "B98", 'prompt = "Fix this script."\n')

    with pytest.raises(spec.SpecError, match="id"):
        spec.load_case("B98", cases_dir)


REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_CASES_DIR = REPO_ROOT / "validation" / "cases"
CASE_CATALOG = REPO_ROOT / "validation" / "cases.md"
def test_load_case_reads_state_and_skills_tree(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(
        cases_dir,
        "B98",
        MINIMAL_CASE + 'state = "b98_state"\nskills_tree = "repository-reconnaissance"\n',
    )

    case = spec.load_case("B98", cases_dir)

    assert case.state == "b98_state"
    assert case.skills_tree == "repository-reconnaissance"


def test_load_case_rejects_state_escaping_state_root(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(cases_dir, "B98", MINIMAL_CASE + 'state = "../outside"\n')

    with pytest.raises(spec.SpecError, match=r"\.\."):
        spec.load_case("B98", cases_dir)


def test_load_case_rejects_absolute_skills_tree(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(cases_dir, "B98", MINIMAL_CASE + 'skills_tree = "/etc"\n')

    with pytest.raises(spec.SpecError, match="skills_tree"):
        spec.load_case("B98", cases_dir)


def test_load_case_reads_shims(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(cases_dir, "B98", MINIMAL_CASE + 'shims = "apt-shfmt-ok"\n')

    case = spec.load_case("B98", cases_dir)

    assert case.shims == "apt-shfmt-ok"


def test_load_case_rejects_unsafe_shims_name(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(cases_dir, "B98", MINIMAL_CASE + 'shims = "../evil"\n')

    with pytest.raises(spec.SpecError, match="shims"):
        spec.load_case("B98", cases_dir)
