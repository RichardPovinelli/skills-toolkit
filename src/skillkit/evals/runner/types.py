"""Shared immutable data types for the eval runner (imports nothing internal)."""

from dataclasses import dataclass


LOCAL_MODEL = "llama-router/qwen3.8-27b-q6"
FALLBACK_MODEL = "opencode/deepseek-v4.1-flash-free"
DEFAULT_MODEL = LOCAL_MODEL
DEFAULT_TIMEOUT_S = 600
ROUTER_BASE_URL = "http://host.docker.internal:1235/v1"


@dataclass(frozen=True)
class Fixture:
    src: str
    dest: str


@dataclass(frozen=True)
class Check:
    name: str
    params: dict[str, object]


@dataclass(frozen=True)
class InlineFile:
    path: str
    content: str


@dataclass(frozen=True)
class Exposure:
    full_catalog: bool = False
    skill_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    prompt: str
    model: str = DEFAULT_MODEL
    timeout_s: int = DEFAULT_TIMEOUT_S
    env_notes: str = ""
    exposure: Exposure = Exposure()
    fixtures: tuple[Fixture, ...] = ()
    inline_files: tuple[InlineFile, ...] = ()
    session_context: tuple[str, ...] = ()
    canned_replies: tuple[str, ...] = ()
    state: str = ""
    skills_tree: str = ""
    shims: str = ""
    skill_routing: bool = False
    checks: tuple[Check, ...] = ()
