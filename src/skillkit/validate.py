#!/usr/bin/env python3
"""Validate collection-wide skill structure and ownership conventions.

The checks are collection-agnostic: the caller supplies the collection root and
an optional :class:`ValidationPolicy`, so the same validator can serve more than
one skills repository. The defaults reproduce the development collection's
rules exactly.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias, cast
from urllib.parse import urlsplit

import yaml

SKILL_LINK: re.Pattern[str] = re.compile(r"\[[^]]+\]\((\.\./[^)]+/SKILL\.md)\)")
MARKDOWN_LINK: re.Pattern[str] = re.compile(r"\[[^]]+\]\(([^)#]+\.md)(?:#[^)]*)?\)")
MATRIX_SKILL: re.Pattern[str] = re.compile(r"`([A-Za-z0-9_-]+)`")
MATRIX_COMPANION: re.Pattern[str] = re.compile(r"`([A-Za-z0-9_-]+)`(?: \([^`]*\))?")
JsonValue: TypeAlias = dict[str, Any] | list[Any] | str | int | float | bool | None

# Policy files are read from this path under the collection root when present.
DEFAULT_CONFIG_PATH = Path("validation") / "validate.toml"

DEFAULT_MANIFESTS = (
    ".github/plugin/plugin.json",
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
)


@dataclass(frozen=True)
class PolicyCheck:
    phrase: str
    owner: str
    intrinsic_users: frozenset[str] = frozenset()


POLICY_CHECKS = (
    PolicyCheck("Run coverage only when it is explicitly requested", "test-coverage"),
    PolicyCheck(
        "Preserve the separation between human-edited direct dependency intent and generated lock or resolved state",
        "tool-and-dependency-installation",
    ),
    PolicyCheck(
        "Order authorized multi-tool cleanup as lint-fix first, then format, then static or type check",
        "static-analysis-and-linting",
    ),
    PolicyCheck(
        "Never write a test whose only assertion is that a value equals its current value",
        "test-driven-development",
    ),
)


def _optional_path(value: object) -> str | None:
    """Policy path fields disable a check when absent or empty."""
    if value is None or value == "":
        return None
    return str(value)


@dataclass(frozen=True)
class ValidationPolicy:
    """Collection-specific limits and companion documents.

    Defaults match the development collection. A collection that lacks a
    document sets it to ``None`` (or an empty string in TOML) to skip that
    check instead of weakening it for everyone.
    """

    max_skill_lines: int = 499
    max_reference_lines: int = 100
    allowed_frontmatter: frozenset[str] = frozenset({"name", "description"})
    required_headings: tuple[str, ...] = ("## Scope and routing", "## Output contract")
    rejected_headings: tuple[tuple[str, str], ...] = (("## Scope", "## Scope and routing"),)
    readme: str | None = "README.md"
    matrix: str | None = "skill-trigger-ownership-matrix.md"
    copilot_instructions: str | None = ".github/copilot-instructions.md"
    manifests: tuple[str, ...] = DEFAULT_MANIFESTS
    policy_checks: tuple[PolicyCheck, ...] = POLICY_CHECKS

    @classmethod
    def from_file(cls, path: Path) -> ValidationPolicy:
        """Load a policy from TOML; unspecified keys keep their defaults."""
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        defaults = cls()
        checks = tuple(
            PolicyCheck(
                phrase=str(item["phrase"]),
                owner=str(item["owner"]),
                intrinsic_users=frozenset(str(user) for user in item.get("intrinsic_users", ())),
            )
            for item in raw.get("policy_check", ())
        )
        return cls(
            max_skill_lines=int(raw.get("max_skill_lines", defaults.max_skill_lines)),
            max_reference_lines=int(raw.get("max_reference_lines", defaults.max_reference_lines)),
            allowed_frontmatter=frozenset(
                str(key) for key in raw.get("allowed_frontmatter", defaults.allowed_frontmatter)
            ),
            required_headings=tuple(
                str(heading) for heading in raw.get("required_headings", defaults.required_headings)
            ),
            rejected_headings=tuple(
                (str(alias), str(canonical))
                for alias, canonical in raw.get("rejected_headings", defaults.rejected_headings)
            ),
            readme=_optional_path(raw.get("readme", defaults.readme)),
            matrix=_optional_path(raw.get("matrix", defaults.matrix)),
            copilot_instructions=_optional_path(raw.get("copilot_instructions", defaults.copilot_instructions)),
            manifests=tuple(str(entry) for entry in raw.get("manifests", defaults.manifests)),
            policy_checks=checks or defaults.policy_checks,
        )


def load_policy(root: Path, config: Path | None = None) -> ValidationPolicy:
    """Load the collection policy, falling back to the built-in defaults."""
    path = config if config is not None else root / DEFAULT_CONFIG_PATH
    if not path.is_file():
        return ValidationPolicy()
    return ValidationPolicy.from_file(path)


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate keys instead of silently replacing an earlier value."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        mapping = super().construct_mapping(node, deep=deep)
        if len(mapping) != len(node.value):
            raise yaml.constructor.ConstructorError(None, None, "duplicate frontmatter key", node.start_mark)
        return mapping


def parse_frontmatter(path: Path) -> tuple[dict[str, str], list[str]]:
    lines = path.read_text().splitlines()
    if not lines or lines[0] != "---":
        return {}, [f"{path}: missing opening frontmatter delimiter"]
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, [f"{path}: missing closing frontmatter delimiter"]

    try:
        values = yaml.load("\n".join(lines[1:end]), Loader=UniqueKeyLoader)
    except yaml.YAMLError as error:
        return {}, [f"{path}: invalid YAML frontmatter: {error}"]
    if not isinstance(values, dict):
        return {}, [f"{path}: frontmatter must be a mapping"]
    # yaml.load returns Any and isinstance narrows only to a bare,
    # unparameterised dict, so element types stay unknown without a cast.
    mapping = cast("dict[Any, Any]", values)
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in mapping.items()):
        return {}, [f"{path}: frontmatter keys and values must be strings"]
    # All keys and values verified as str above; isinstance cannot narrow
    # the untyped mapping to dict[str, str] directly.
    return cast("dict[str, str]", values), []


def headings_outside_fences(text: str) -> list[str]:
    """Return level-two heading lines that are not inside a fenced code block."""
    headings: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        marker: str | None
        if stripped.startswith("```"):
            marker = "```"
        elif stripped.startswith("~~~"):
            marker = "~~~"
        else:
            marker = None
        if marker is not None:
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is None and stripped.startswith("## "):
            headings.append(stripped)
    return headings


def validate_skill_headings(text: str, path: Path, policy: ValidationPolicy | None = None) -> list[str]:
    """Require each configured heading exactly once."""
    policy = policy if policy is not None else ValidationPolicy()
    headings = headings_outside_fences(text)
    errors: list[str] = []
    for required in policy.required_headings:
        count = sum(heading == required for heading in headings)
        if count != 1:
            errors.append(f"{path}: expected exactly one {required!r} heading, found {count}")
    for alias, canonical in policy.rejected_headings:
        if any(heading == alias for heading in headings):
            errors.append(f"{path}: use {canonical!r} instead of a bare {alias!r} heading")
    return errors


def _skill_names_in(skills_root: Path) -> frozenset[str]:
    if not skills_root.is_dir():
        return frozenset()
    return frozenset(path.name for path in skills_root.iterdir() if path.is_dir())


def validate_skill(
    path: Path,
    *,
    skills_root: Path | None = None,
    policy: ValidationPolicy | None = None,
) -> tuple[list[str], list[str]]:
    policy = policy if policy is not None else ValidationPolicy()
    errors: list[str] = []
    warnings: list[str] = []
    text = path.read_text()
    skill_name = path.parent.name
    errors.extend(validate_skill_headings(text, path, policy))
    metadata, metadata_errors = parse_frontmatter(path)
    errors.extend(metadata_errors)

    extra = set(metadata) - policy.allowed_frontmatter
    missing = policy.allowed_frontmatter - set(metadata)
    if extra:
        errors.append(f"{path}: unsupported frontmatter keys: {', '.join(sorted(extra))}")
    if missing:
        errors.append(f"{path}: missing frontmatter keys: {', '.join(sorted(missing))}")
    if metadata.get("name") != skill_name:
        errors.append(f"{path}: name must match directory {skill_name!r}")
    if not metadata.get("description", "").strip():
        errors.append(f"{path}: description must not be empty")

    line_count = len(text.splitlines())
    if line_count > policy.max_skill_lines:
        errors.append(f"{path}: {line_count} lines exceeds the {policy.max_skill_lines}-line limit")

    markdown_files = [path]
    references = path.parent / "references"
    if references.exists():
        markdown_files.extend(sorted(references.glob("*.md")))

    for markdown_path in markdown_files:
        markdown_text = markdown_path.read_text()
        link_targets = cast("list[str]", MARKDOWN_LINK.findall(markdown_text))
        for target in link_targets:
            if urlsplit(target).scheme:
                continue
            resolved = (markdown_path.parent / target).resolve()
            if not resolved.exists():
                errors.append(f"{markdown_path}: broken Markdown link {target!r}")

    known_skills = _skill_names_in(skills_root) if skills_root is not None else frozenset()
    for target in SKILL_LINK.findall(text):
        target_name = Path(target).parent.name
        if target_name not in known_skills:
            errors.append(f"{path}: referenced skill {target_name!r} does not exist")

    if references.exists():
        for reference in references.glob("*.md"):
            relative = reference.relative_to(path.parent).as_posix()
            if f"]({relative})" not in text:
                errors.append(f"{path}: reference {relative!r} is not linked directly from SKILL.md")
            ref_lines = reference.read_text().splitlines()
            if len(ref_lines) > policy.max_reference_lines and "## Contents" not in ref_lines:
                errors.append(
                    f"{reference}: references over {policy.max_reference_lines} lines require a Contents section"
                )

    for check in policy.policy_checks:
        if check.phrase in text and skill_name != check.owner and skill_name not in check.intrinsic_users:
            warnings.append(f"{path}: duplicates policy owned by {check.owner!r}: {check.phrase!r}")

    return errors, warnings


def validate_matrix(
    skill_names: frozenset[str],
    matrix_path: Path,
    *,
    policy: ValidationPolicy | None = None,
) -> list[str]:
    """Check the ownership matrix, if the collection has one."""
    if policy is not None and policy.matrix is None:
        return []
    path_label = str(matrix_path)
    errors: list[str] = []
    owners: set[str] = set()
    if not matrix_path.exists():
        return [f"{path_label}: ownership matrix not found"]
    lines = matrix_path.read_text().splitlines()
    table_line_indexes = [index for index, line in enumerate(lines) if line.strip().startswith("|")]
    for index in range(table_line_indexes[0] + 1, table_line_indexes[-1]):
        if not lines[index].strip().startswith("|"):
            errors.append(
                f"{path_label}:{index + 1}: routing table rows must be contiguous; "
                "blank or non-table lines before the final row break Markdown table rendering"
            )
    for line_number, line in enumerate(lines, start=1):
        line = line.strip()
        if not line.startswith("|"):
            continue
        columns = [column.strip() for column in line.strip("|").split("|")]
        is_separator = all(re.fullmatch(r":?-+:?", column) for column in columns)
        if columns[0] == "Representative prompt" or is_separator:
            continue
        location = f"{path_label}:{line_number}"
        if len(columns) != 4 or not all(columns):
            errors.append(f"{location}: routing rows require four nonempty columns")
            continue
        _, owner, companions, _ = columns
        owner_match = MATRIX_SKILL.fullmatch(owner)
        mentioned_skills: list[str] = []
        if owner_match is None:
            errors.append(f"{location}: primary owner must be exactly one backticked skill name")
        else:
            mentioned_skills.append(owner_match.group(1))
            owners.add(owner_match.group(1))
        if companions != "None":
            for companion in companions.split("<br>"):
                companion_match = MATRIX_COMPANION.fullmatch(companion.strip())
                if companion_match is None:
                    errors.append(f"{location}: invalid companion {companion!r}; use `skill-name` (condition) or None")
                else:
                    mentioned_skills.append(companion_match.group(1))
        for mentioned in mentioned_skills:
            if mentioned not in skill_names:
                errors.append(f"{location}: names skill {mentioned!r}, which does not exist under skills/")
    for missing_owner in sorted(skill_names - owners):
        errors.append(f"{path_label}: no primary-owner row for {missing_owner!r}")
    return errors


def validate_manifests(root: Path, *, policy: ValidationPolicy | None = None) -> list[str]:
    """Check shared bundle metadata without pinning a particular release value."""
    policy = policy if policy is not None else ValidationPolicy()
    if len(policy.manifests) != 3:
        # The comparison below is positional: copilot, claude, marketplace. A
        # collection with a different manifest set opts out instead of failing.
        return []
    documents: list[dict[str, JsonValue]] = []
    paths = policy.manifests
    for relative_path in paths:
        path = root / relative_path
        try:
            document = json.loads(path.read_text())
        except (OSError, ValueError) as error:
            return [f"{path}: cannot read JSON: {error}"]
        if not isinstance(document, dict):
            return [f"{path}: expected a JSON object"]
        documents.append(cast("dict[str, JsonValue]", document))

    copilot, claude, marketplace = documents
    errors: list[str] = []
    for key in ("name", "version", "description", "license"):
        if not copilot.get(key) or copilot.get(key) != claude.get(key):
            errors.append(f"Plugin manifests: missing or inconsistent {key}")
    copilot_author = copilot.get("author")
    if copilot_author is None or copilot_author != claude.get("author"):
        errors.append("Plugin manifests: missing or inconsistent author")
    plugins: JsonValue = marketplace.get("plugins")
    if not isinstance(plugins, list) or len(plugins) != 1 or not isinstance(plugins[0], dict):
        errors.append("Marketplace: expected one bundled plugin entry")
    else:
        plugin = cast("dict[str, JsonValue]", plugins[0])
        for key in ("name", "description", "license"):
            if plugin.get(key) != copilot.get(key):
                errors.append(f"Marketplace: inconsistent plugin {key}")
    return errors


def validate_readme(
    skill_names: frozenset[str],
    root: Path,
    *,
    policy: ValidationPolicy | None = None,
) -> list[str]:
    """Compare the linked README skill table with the discovered collection."""
    policy = policy if policy is not None else ValidationPolicy()
    if policy.readme is None:
        return []
    path = root / policy.readme
    try:
        text = path.read_text()
    except OSError as error:
        return [f"{path}: cannot read README: {error}"]
    errors: list[str] = []
    section = re.search(r"^## Skills and their scope\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    names: list[str] = []
    if section is not None:
        links = re.findall(
            r"^\|\s+\[`([A-Za-z0-9_-]+)`\]\(([^)]+)\)\s+\|",
            section.group(1),
            re.MULTILINE,
        )
        for name, target in links:
            names.append(name)
            expected_target = f"skills/{name}/SKILL.md"
            if target != expected_target:
                errors.append(f"{path}: skill {name!r} must link to {expected_target!r}")
            if name not in skill_names:
                errors.append(f"{path}: README names unknown skill {name!r}")
            if not (root / target).is_file():
                errors.append(f"{path}: broken skill link target {target!r}")
    if frozenset(names) != skill_names or len(names) != len(set(names)):
        errors.append(f"{path}: Skills and their scope must link each discovered skill exactly once")
    for count in re.findall(r"\ball (\d+) skills\b", text):
        if int(count) != len(skill_names):
            errors.append(f"{path}: documented skill count {count} differs from {len(skill_names)} discovered skills")
    return errors


def validate_copilot_instructions(
    skill_names: frozenset[str],
    root: Path,
    *,
    policy: ValidationPolicy | None = None,
) -> list[str]:
    """Ensure the Workflow section in the harness instructions references every skill."""
    policy = policy if policy is not None else ValidationPolicy()
    if policy.copilot_instructions is None:
        return []
    path = root / policy.copilot_instructions
    try:
        text = path.read_text()
    except OSError as error:
        return [f"{path}: cannot read copilot instructions: {error}"]
    section = re.search(r"^## Workflow\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if section is None:
        return [f"{path}: missing '## Workflow' section"]
    referenced = frozenset(MATRIX_SKILL.findall(section.group(1)))
    errors: list[str] = []
    for missing in sorted(skill_names - referenced):
        errors.append(f"{path}: Workflow section does not reference skill {missing!r}")
    return errors


def main(
    argv: list[str] | None = None,
    root: Path | None = None,
    policy: ValidationPolicy | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="skillkit-validate")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="collection root containing skills/ (default: current directory)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"policy file (default: <root>/{DEFAULT_CONFIG_PATH.as_posix()})",
    )
    args = parser.parse_args(argv)
    collection_root = Path(root) if root is not None else (args.root or Path.cwd())
    collection_policy = policy if policy is not None else load_policy(collection_root, args.config)
    skills_root = collection_root / "skills"

    errors: list[str] = []
    warnings: list[str] = []
    skill_files = sorted(skills_root.glob("*/SKILL.md"))
    if not skill_files:
        print(f"ERROR: no skills found under {skills_root}", file=sys.stderr)
        return 1

    skill_names = frozenset(path.parent.name for path in skill_files)
    for path in skill_files:
        skill_errors, skill_warnings = validate_skill(path, skills_root=skills_root, policy=collection_policy)
        errors.extend(skill_errors)
        warnings.extend(skill_warnings)

    if collection_policy.matrix is not None:
        errors.extend(
            validate_matrix(
                skill_names,
                collection_root / collection_policy.matrix,
                policy=collection_policy,
            )
        )
    errors.extend(validate_manifests(collection_root, policy=collection_policy))
    errors.extend(validate_readme(skill_names, collection_root, policy=collection_policy))
    errors.extend(validate_copilot_instructions(skill_names, collection_root, policy=collection_policy))

    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)

    print(f"Validated {len(skill_files)} skills: {len(errors)} error(s), {len(warnings)} warning(s).")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
