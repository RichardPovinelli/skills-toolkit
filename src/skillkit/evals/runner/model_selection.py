"""Local-first model selection with a hosted fallback.

The runner prefers the configured local llama-router model. When the local
endpoint is unreachable or the model is absent, it uses OpenCode's free
DeepSeek V4.1 Flash model instead. The selection is made once per CLI
invocation so a batch does not pay a connection timeout for every case.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from skillkit.evals.runner.types import (
    DEFAULT_MODEL,
    FALLBACK_MODEL,
    LOCAL_MODEL,
    ROUTER_BASE_URL,
)


@dataclass(frozen=True)
class ModelChoice:
    """The resolved model and the reason it was selected."""

    model: str
    requested: str
    reason: str


class ModelSelectionError(Exception):
    """Raised when the requested model cannot be used."""


def probe_local_models(
    *,
    timeout_s: float = 1.5,
    opener: urllib.request.OpenerDirector | None = None,
) -> tuple[str, ...] | None:
    """Return the model IDs advertised by the local router, or ``None``.

    Connection failures, timeouts, non-2xx responses, and malformed payloads
    all mean "local is unavailable"; they are not fatal runner errors.
    """
    request = urllib.request.Request(
        f"{ROUTER_BASE_URL}/models",
        headers={"Accept": "application/json"},
    )
    if opener is None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout_s) as response:
            payload = json.load(response)
    except (OSError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    return tuple(item["id"] for item in data if isinstance(item, dict) and isinstance(item.get("id"), str))


def resolve_model(
    *,
    requested: str | None = None,
    probe: Callable[[], tuple[str, ...] | None] | None = None,
) -> ModelChoice:
    """Resolve the model to use, preferring the local router."""
    if requested:
        return ModelChoice(model=requested, requested=requested, reason="explicit")
    models = probe() if probe is not None else probe_local_models()
    local_name = LOCAL_MODEL.partition("/")[2]
    if models is not None and (local_name in models or LOCAL_MODEL in models):
        return ModelChoice(model=LOCAL_MODEL, requested=DEFAULT_MODEL, reason="local-available")
    reason = "local-unreachable" if models is None else "local-model-missing"
    return ModelChoice(model=FALLBACK_MODEL, requested=DEFAULT_MODEL, reason=reason)


def host_for_model(model: str) -> str:
    """Return the evidence host label for a resolved model."""
    if model.startswith("llama-router/"):
        return "opencode/local-router"
    if model.startswith("opencode/"):
        return "opencode/console"
    provider = model.partition("/")[0] or "unknown"
    return f"opencode/{provider}"
