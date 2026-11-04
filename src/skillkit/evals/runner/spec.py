"""Load and validate per-case TOML specs (evals/runner/cases/<id>.toml)."""

import re
import tomllib
from pathlib import Path, PurePosixPath

from skillkit.evals.runner.types import (
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_S,
    CaseSpec,
    Check,
    Exposure,
    Fixture,
    InlineFile,
)


class SpecError(Exception):
    """Raised when a case spec is missing, malformed, or invalid."""


_SHIMS_NAME_RE = re.compile(r"[a-z][a-z0-9_-]*")


def _require_string(raw: dict, field: str) -> str:
    if field not in raw:
        raise SpecError(f"missing required field '{field}'")
    value = raw[field]
    if not isinstance(value, str):
        raise SpecError(f"field '{field}' must be a string, got {type(value).__name__}")
    return value


def _require_workspace_relative(value: str, field: str = "inline_file path") -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not value:
        raise SpecError(f"{field} must be workspace-relative without '..': got {value!r}")
    return value


def _require_data_relative(value: str, field: str = "fixture src") -> str:
    """Fixtures are resolved under the collection data directory, never outside it."""
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise SpecError(
            f"{field} must be relative to the collection data directory without '..': got {value!r}"
        )
    return value


def load_case(case_id: str, cases_dir: Path) -> CaseSpec:
    path = cases_dir / f"{case_id}.toml"
    if not path.is_file():
        raise SpecError(f"unknown case '{case_id}': no spec at {path}")
    try:
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
    except tomllib.TOMLDecodeError as err:
        raise SpecError(f"case '{case_id}': invalid TOML: {err}") from err
    timeout = raw.get("timeout", DEFAULT_TIMEOUT_S)
    if not isinstance(timeout, int) or isinstance(timeout, bool):
        raise SpecError(
            f"case '{case_id}': field 'timeout' must be an integer number of seconds, got {type(timeout).__name__}"
        )
    fixtures = tuple(
        Fixture(src=_require_data_relative(_require_string(item, "src")), dest=item["dest"])
        for item in raw.get("fixture", ())
    )
    checks = tuple(
        Check(
            name=_require_string(item, "name"),
            params={key: value for key, value in item.items() if key != "name"},
        )
        for item in raw.get("check", ())
    )
    inline_files = []
    for item in raw.get("inline_file", ()):
        inline_path = _require_workspace_relative(_require_string(item, "path"))
        inline_files.append(InlineFile(path=inline_path, content=_require_string(item, "content")))
    session_context = tuple(raw.get("session_context", ()))
    if any(not isinstance(line, str) for line in session_context):
        raise SpecError("case spec field 'session_context' must be a list of strings")
    state = raw.get("state", "")
    if not isinstance(state, str):
        raise SpecError("case spec field 'state' must be a string")
    if state:
        _require_workspace_relative(state, "state")
    skills_tree = raw.get("skills_tree", "")
    if not isinstance(skills_tree, str):
        raise SpecError("case spec field 'skills_tree' must be a string")
    if skills_tree:
        _require_workspace_relative(skills_tree, "skills_tree")
    shims = raw.get("shims", "")
    if not isinstance(shims, str):
        raise SpecError("case spec field 'shims' must be a string")
    if shims and not _SHIMS_NAME_RE.fullmatch(shims):
        raise SpecError(
            f"case spec field 'shims' must be a lowercase shim-set name [a-z][a-z0-9_-]*, got {shims!r}"
        )
    skill_routing = raw.get("skill_routing", False)
    if not isinstance(skill_routing, bool):
        raise SpecError(
            f"case spec field 'skill_routing' must be a boolean, got {type(skill_routing).__name__}"
        )
    exposure_raw = raw.get("exposure", {})
    exposure = Exposure(
        full_catalog=exposure_raw.get("full_catalog", False),
        skill_names=tuple(exposure_raw.get("skill_names", ())),
    )
    return CaseSpec(
        case_id=_require_string(raw, "id"),
        prompt=_require_string(raw, "prompt"),
        model=raw.get("model", DEFAULT_MODEL),
        timeout_s=timeout,
        env_notes=raw.get("env_notes", ""),
        canned_replies=tuple(raw.get("canned_replies", ())),
        exposure=exposure,
        fixtures=fixtures,
        inline_files=tuple(inline_files),
        session_context=session_context,
        state=state,
        skills_tree=skills_tree,
        shims=shims,
        skill_routing=skill_routing,
        checks=checks,
    )
