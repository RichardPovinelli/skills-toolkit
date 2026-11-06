"""Shared test fixtures for the eval runner suite."""

import pytest
from skillkit.evals.runner import __main__ as runner_main
from skillkit.evals.runner.model_selection import ModelChoice

LOCAL_CHOICE = ModelChoice(
    model="llama-router/qwen3.8-27b-q6",
    requested="llama-router/qwen3.8-27b-q6",
    reason="local-available",
)


@pytest.fixture
def local_model_choice(monkeypatch):
    """Keep CLI tests hermetic: resolve local without probing or credentials."""
    monkeypatch.setattr(
        runner_main.model_selection,
        "resolve_model",
        lambda **kwargs: LOCAL_CHOICE,
    )
    return LOCAL_CHOICE
