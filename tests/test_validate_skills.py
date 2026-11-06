"""Exercise validation against small, independent skill fixtures."""

import json
from pathlib import Path

import pytest
from skillkit import validate


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.skill = root / "example" / "SKILL.md"
        self.skill.parent.mkdir()

    def write_skill(
        self,
        frontmatter: str,
        body: str = "# Example\n\n## Scope and routing\n\nText\n\n## Output contract\n\nReport.\n",
    ) -> None:
        self.skill.write_text(f"---\n{frontmatter}\n---\n{body}")


@pytest.fixture
def workspace(tmp_path: Path) -> _Workspace:
    return _Workspace(tmp_path)


def test_rejects_unquoted_colon_in_description(workspace: _Workspace) -> None:
    workspace.write_skill("name: example\ndescription: Explain this: with details")
    metadata, errors = validate.parse_frontmatter(workspace.skill)
    assert errors, "Malformed YAML must not be accepted as a skill"
    assert metadata == {}


@pytest.mark.parametrize(
    "document",
    [
        "- example",
        "null",
        "name: example\ndescription: null",
        "name: example\ndescription: [one, two]",
        "name: example\ndescription: true",
        "name: example\ndescription: first\ndescription: second",
    ],
)
def test_frontmatter_requires_unique_string_fields(workspace: _Workspace, document: str) -> None:
    workspace.write_skill(document)
    _, errors = validate.parse_frontmatter(workspace.skill)
    assert errors


@pytest.mark.parametrize(
    "description",
    [
        '"Explain this: with details"',
        ">-\n  Explain this:\n  with details",
    ],
)
def test_accepts_quoted_and_folded_descriptions(workspace: _Workspace, description: str) -> None:
    workspace.write_skill(f"name: example\ndescription: {description}")
    metadata, errors = validate.parse_frontmatter(workspace.skill)
    assert errors == []
    assert metadata["description"] == "Explain this: with details"


def test_rejects_missing_reference_and_accepts_resolved_reference(
    workspace: _Workspace,
) -> None:
    workspace.write_skill(
        "name: example\ndescription: Review an example",
        "# Example\n\n## Scope and routing\n\nText\n\n[Details](references/details.md)\n\n## Output contract\n\nReport.\n",
    )
    errors, _ = validate.validate_skill(workspace.skill)
    assert any("broken Markdown link" in error for error in errors)
    reference = workspace.skill.parent / "references" / "details.md"
    reference.parent.mkdir()
    reference.write_text("# Details\nA small reference.\n")
    errors, _ = validate.validate_skill(workspace.skill)
    assert errors == []


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("# Example\n\n## Scope\n\nText\n", "bare '## Scope'"),
        (
            "# Example\n\n## Scope and routing\n\nText\n\n## Scope and routing\n\nAgain\n",
            "exactly one '## Scope and routing'",
        ),
        ("# Example\n\n## Purpose\n\nText\n", "exactly one '## Scope and routing'"),
    ],
)
def test_skill_requires_exactly_one_scope_and_routing_heading(workspace: _Workspace, body: str, expected: str) -> None:
    workspace.write_skill("name: example\ndescription: Review an example", body)
    errors, _ = validate.validate_skill(workspace.skill)
    assert any(expected in error for error in errors)


@pytest.mark.parametrize(
    "body",
    [
        "# Example\n\n## Scope and routing\n\nText\n",
        "# Example\n\n## Scope and routing\n\nText\n\n## Output contract\n\nOne\n\n## Output contract\n\nTwo\n",
    ],
)
def test_skill_requires_exactly_one_output_contract_heading(workspace: _Workspace, body: str) -> None:
    workspace.write_skill("name: example\ndescription: Review an example", body)
    errors, _ = validate.validate_skill(workspace.skill)
    assert any("exactly one '## Output contract'" in error for error in errors)


def test_skill_heading_check_ignores_fenced_examples(workspace: _Workspace) -> None:
    workspace.write_skill(
        "name: example\ndescription: Review an example",
        "# Example\n\n## Scope and routing\n\nText\n\n"
        "```markdown\n## Scope\n\n## Scope and routing\n\n## Output contract\n```\n\n"
        "~~~markdown\n## Scope\n~~~\n\n"
        "## Output contract\n\nReport.\n",
    )
    errors, _ = validate.validate_skill(workspace.skill)
    assert errors == []


def test_matrix_rejects_unknown_skill(workspace: _Workspace) -> None:
    matrix = workspace.root / "matrix.md"
    matrix.write_text("| Example request | `missing-owner` | `example` | Read only |\n")
    errors = validate.validate_matrix(frozenset({"example"}), matrix)
    assert any("missing-owner" in error for error in errors)


@pytest.mark.parametrize(
    "row",
    [
        "| Request | | None | Read only |\n",
        "| Request | `example`, `example` | None | Read only |\n",
        "| Request | example | None | Read only |\n",
        "| Request | `example` | missing-companion | Read only |\n",
        "| Request | `example` | `missing-companion` (when needed) | Read only |\n",
        "| Request | `example` | None |\n",
        "| Request | `example` | None | |\n",
    ],
)
def test_matrix_rejects_malformed_owner_and_companion_columns(workspace: _Workspace, row: str) -> None:
    matrix = workspace.root / "matrix.md"
    valid_row = "| Request | `example` | None | Read only |\n"
    matrix.write_text(valid_row + row)
    errors = validate.validate_matrix(frozenset({"example"}), matrix)
    assert errors, "Malformed routing rows must be rejected"


def test_matrix_accepts_well_formed_owner_and_companion_columns(
    workspace: _Workspace,
) -> None:
    matrix = workspace.root / "matrix.md"
    matrix.write_text(
        "| Representative prompt | Primary owner | Conditional companions or handoff | Authorization boundary |\n"
        "|---|---|---|---|\n"
        "| Explain `some-code` | `example` | `second` (hand off after diagnosis) | Read only |\n"
        "| Other request | `second` | `example` (also apply when needed) | Read only |\n"
    )
    assert validate.validate_matrix(frozenset({"example", "second"}), matrix) == []


def _contiguous_matrix() -> str:
    return (
        "| Representative prompt | Primary owner | Conditional companions or handoff | Authorization boundary |\n"
        "|---|---|---|---|\n"
        "| Explain `some-code` | `example` | `second` (hand off after diagnosis) | Read only |\n"
        "| Other request | `second` | `example` (also apply when needed) | Read only |\n"
    )


@pytest.mark.parametrize(
    "broken",
    [
        # Blank line between two data rows.
        _contiguous_matrix().replace("| Other request", "\n| Other request", 1),
        # Blank line between the separator and the first data row.
        _contiguous_matrix().replace("| Explain `some-code`", "\n| Explain `some-code`", 1),
        # Non-table line splitting the data rows.
        _contiguous_matrix().replace("| Other request", "stray prose\n| Other request", 1),
    ],
)
def test_matrix_rejects_non_contiguous_tables(workspace: _Workspace, broken: str) -> None:
    matrix = workspace.root / "matrix.md"
    matrix.write_text(broken)
    errors = validate.validate_matrix(frozenset({"example", "second"}), matrix)
    assert errors, "A broken or split routing table must be rejected"


def test_matrix_allows_prose_after_the_table(workspace: _Workspace) -> None:
    matrix = workspace.root / "matrix.md"
    matrix.write_text(_contiguous_matrix() + "\n## Notes\n\n- Content after the final row is allowed.\n")
    assert validate.validate_matrix(frozenset({"example", "second"}), matrix) == []


def test_matrix_requires_each_skill_as_an_owner(workspace: _Workspace) -> None:
    matrix = workspace.root / "matrix.md"
    matrix.write_text("| Request | `example` | `second` (when needed) | Read only |\n")
    errors = validate.validate_matrix(frozenset({"example", "second"}), matrix)
    assert any("no primary-owner row" in error and "second" in error for error in errors)
    with matrix.open("a") as stream:
        stream.write("| Other request | `second` | None | Read only |\n")
    assert validate.validate_matrix(frozenset({"example", "second"}), matrix) == []


def test_manifest_metadata_must_agree(workspace: _Workspace) -> None:
    metadata: dict[str, object] = {
        "name": "fixture-plugin",
        "version": "1.2.3",
        "description": "Fixture skills",
        "author": {"name": "Example"},
        "license": "MIT",
    }
    manifest_paths = [
        workspace.root / ".github/plugin/plugin.json",
        workspace.root / ".claude-plugin/plugin.json",
    ]
    for path in manifest_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(metadata))
    marketplace_path = workspace.root / ".claude-plugin/marketplace.json"
    marketplace: dict[str, list[dict[str, object]]] = {
        "plugins": [
            {
                "name": metadata["name"],
                "description": metadata["description"],
                "license": metadata["license"],
                "source": "./",
            }
        ]
    }
    marketplace_path.write_text(json.dumps(marketplace))
    assert validate.validate_manifests(workspace.root) == []
    manifest_paths[1].write_text(json.dumps({**metadata, "version": "2.0.0"}))
    assert any("version" in error for error in validate.validate_manifests(workspace.root))
    manifest_paths[1].write_text(json.dumps({**metadata, "license": "Apache-2.0"}))
    assert any("license" in error for error in validate.validate_manifests(workspace.root))
    manifest_paths[1].write_text(json.dumps(metadata))
    marketplace["plugins"][0]["description"] = "Stale description"
    marketplace_path.write_text(json.dumps(marketplace))
    assert any("description" in error for error in validate.validate_manifests(workspace.root))
    marketplace["plugins"][0]["description"] = metadata["description"]
    marketplace["plugins"][0]["license"] = "Apache-2.0"
    marketplace_path.write_text(json.dumps(marketplace))
    assert any("license" in error for error in validate.validate_manifests(workspace.root))
    marketplace_path.write_text("{")
    assert any("JSON" in error for error in validate.validate_manifests(workspace.root))


def test_readme_accepts_grouped_linked_skill_inventory(workspace: _Workspace) -> None:
    readme = workspace.root / "README.md"
    for name in ("example", "second"):
        target = workspace.root / "skills" / name / "SKILL.md"
        target.parent.mkdir(parents=True)
        target.touch()
    readme.write_text(
        "## Skills and their scope\n\n"
        "### First group\n\n"
        "| Skill | Scope |\n| --- | --- |\n"
        "| [`example`](skills/example/SKILL.md) | First workflow |\n\n"
        "### Second group\n\n"
        "| Skill | Scope |\n| --- | --- |\n"
        "| [`second`](skills/second/SKILL.md) | Second workflow |\n\n"
        "The bundle includes all 2 skills.\n"
    )
    assert validate.validate_readme(frozenset({"example", "second"}), workspace.root) == []


def test_readme_rejects_duplicate_skill_links(workspace: _Workspace) -> None:
    readme = workspace.root / "README.md"
    for name in ("example", "second"):
        target = workspace.root / "skills" / name / "SKILL.md"
        target.parent.mkdir(parents=True)
        target.touch()
    readme.write_text(
        "## Skills and their scope\n\n"
        "### Group\n\n"
        "| Skill | Scope |\n| --- | --- |\n"
        "| [`example`](skills/example/SKILL.md) | First workflow |\n"
        "| [`example`](skills/example/SKILL.md) | Duplicate workflow |\n"
        "| [`second`](skills/second/SKILL.md) | Second workflow |\n"
    )
    assert validate.validate_readme(frozenset({"example", "second"}), workspace.root)


def test_readme_rejects_unknown_skill_links(workspace: _Workspace) -> None:
    readme = workspace.root / "README.md"
    for name in ("example", "second", "unknown"):
        target = workspace.root / "skills" / name / "SKILL.md"
        target.parent.mkdir(parents=True)
        target.touch()
    readme.write_text(
        "## Skills and their scope\n\n"
        "### Group\n\n"
        "| Skill | Scope |\n| --- | --- |\n"
        "| [`example`](skills/example/SKILL.md) | First workflow |\n"
        "| [`second`](skills/second/SKILL.md) | Second workflow |\n"
        "| [`unknown`](skills/unknown/SKILL.md) | Unknown workflow |\n"
    )
    errors = validate.validate_readme(frozenset({"example", "second"}), workspace.root)
    assert any("unknown skill 'unknown'" in error for error in errors)


def test_readme_rejects_mismatched_skill_link_target(workspace: _Workspace) -> None:
    readme = workspace.root / "README.md"
    for name in ("example", "second"):
        target = workspace.root / "skills" / name / "SKILL.md"
        target.parent.mkdir(parents=True)
        target.touch()
    readme.write_text(
        "## Skills and their scope\n\n"
        "### Group\n\n"
        "| Skill | Scope |\n| --- | --- |\n"
        "| [`example`](skills/second/SKILL.md) | First workflow |\n"
        "| [`second`](skills/second/SKILL.md) | Second workflow |\n"
    )
    errors = validate.validate_readme(frozenset({"example", "second"}), workspace.root)
    assert any("skill 'example' must link" in error for error in errors)


def test_readme_rejects_missing_skill_link(workspace: _Workspace) -> None:
    readme = workspace.root / "README.md"
    target = workspace.root / "skills" / "example" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.touch()
    readme.write_text(
        "## Skills and their scope\n\n"
        "### Group\n\n"
        "| Skill | Scope |\n| --- | --- |\n"
        "| [`example`](skills/example/SKILL.md) | First workflow |\n"
    )
    errors = validate.validate_readme(frozenset({"example", "second"}), workspace.root)
    assert any("link each discovered skill exactly once" in error for error in errors)


def test_readme_rejects_broken_skill_link_target(workspace: _Workspace) -> None:
    readme = workspace.root / "README.md"
    readme.write_text(
        "## Skills and their scope\n\n"
        "### Group\n\n"
        "| Skill | Scope |\n| --- | --- |\n"
        "| [`example`](skills/example/README.md) | First workflow |\n"
    )
    errors = validate.validate_readme(frozenset({"example"}), workspace.root)
    assert any("broken skill link target" in error for error in errors)


def test_readme_rejects_mismatched_skill_count(workspace: _Workspace) -> None:
    readme = workspace.root / "README.md"
    for name in ("example", "second"):
        target = workspace.root / "skills" / name / "SKILL.md"
        target.parent.mkdir(parents=True)
        target.touch()
    readme.write_text(
        "## Skills and their scope\n\n"
        "### Group\n\n"
        "| Skill | Scope |\n| --- | --- |\n"
        "| [`example`](skills/example/SKILL.md) | First workflow |\n"
        "| [`second`](skills/second/SKILL.md) | Second workflow |\n\n"
        "The bundle includes all 3 skills.\n"
    )
    errors = validate.validate_readme(frozenset({"example", "second"}), workspace.root)
    assert any("documented skill count 3 differs from 2" in error for error in errors)


def test_remote_documentation_is_not_treated_as_a_local_file(
    workspace: _Workspace,
) -> None:
    workspace.write_skill(
        "name: example\ndescription: Review an example",
        "# Example\n\n## Scope and routing\n\nText\n\n[Documentation](https://example.org/guide.md)\n\n## Output contract\n\nReport.\n",
    )
    errors, _ = validate.validate_skill(workspace.skill)
    assert errors == []


def test_fragment_does_not_hide_a_missing_reference(workspace: _Workspace) -> None:
    workspace.write_skill(
        "name: example\ndescription: Review an example",
        "# Example\n\n## Scope and routing\n\nText\n\n[Details](missing.md#details)\n\n## Output contract\n\nReport.\n",
    )
    errors, _ = validate.validate_skill(workspace.skill)
    assert any("broken Markdown link" in error for error in errors)


def test_copilot_instructions_requires_all_discovered_skills(
    workspace: _Workspace,
) -> None:
    instructions = workspace.root / ".github" / "copilot-instructions.md"
    instructions.parent.mkdir(parents=True)
    instructions.write_text(
        "## Git process\n"
        "Mention `second` outside the workflow section.\n\n"
        "## Workflow\n"
        "- **Example** → load `example`\n"
    )
    errors = validate.validate_copilot_instructions(frozenset({"example", "second"}), workspace.root)
    assert any("does not reference skill 'second'" in error for error in errors)
    instructions.write_text("## Workflow\n- **Example** → load `example`\n- **Second** → load `second`\n")
    assert validate.validate_copilot_instructions(frozenset({"example", "second"}), workspace.root) == []


def test_policy_can_relax_collection_specific_rules(workspace: _Workspace) -> None:
    workspace.write_skill("name: example\ndescription: Review an example", "# Example\n\nText\n")
    strict_errors, _ = validate.validate_skill(workspace.skill)
    assert strict_errors, "the default policy requires the routing headings"

    relaxed = validate.ValidationPolicy(required_headings=(), rejected_headings=(), policy_checks=())
    errors, warnings = validate.validate_skill(workspace.skill, policy=relaxed)

    assert errors == []
    assert warnings == []


def test_policy_file_overrides_the_builtin_defaults(tmp_path: Path) -> None:
    config = tmp_path / "validate.toml"
    config.write_text(
        'max_skill_lines = 10\nrequired_headings = []\nmatrix = ""\nmanifests = []\n',
        encoding="utf-8",
    )

    policy = validate.load_policy(tmp_path, config)

    assert policy.max_skill_lines == 10
    assert policy.required_headings == ()
    assert policy.matrix is None
    assert policy.manifests == ()
    assert policy.readme == "README.md"
