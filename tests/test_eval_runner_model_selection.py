"""Local-first model selection for the eval runner."""

import io
import json
import urllib.request
from typing import cast

import pytest

from skillkit.evals.runner import model_selection


def test_resolve_model_uses_local_when_probe_lists_it():
    choice = model_selection.resolve_model(probe=lambda: ("qwen3.8-27b-q6",))

    assert choice.model == "llama-router/qwen3.8-27b-q6"
    assert choice.reason == "local-available"


def test_resolve_model_falls_back_when_probe_is_unreachable():
    choice = model_selection.resolve_model(probe=lambda: None)

    assert choice.model == "opencode/deepseek-v4.1-flash-free"
    assert choice.reason == "local-unreachable"


def test_resolve_model_falls_back_when_local_model_is_missing():
    choice = model_selection.resolve_model(probe=lambda: ("another-model",))

    assert choice.model == "opencode/deepseek-v4.1-flash-free"
    assert choice.reason == "local-model-missing"


def test_resolve_model_uses_explicit_request_without_probing():
    def forbidden_probe():
        raise AssertionError("explicit model must not probe the local router")

    choice = model_selection.resolve_model(
        requested="opencode/deepseek-v4.1-flash-free", probe=forbidden_probe
    )

    assert choice.model == "opencode/deepseek-v4.1-flash-free"
    assert choice.reason == "explicit"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class FakeOpener:
    def __init__(self, payload=None, error=None, raw=None):
        self.payload = payload
        self.error = error
        self.raw = raw
        self.request: urllib.request.Request | None = None

    def open(self, request, timeout=None):
        self.request = request
        self.timeout = timeout
        if self.error is not None:
            raise self.error
        body = (
            self.raw
            if self.raw is not None
            else json.dumps(self.payload).encode("utf-8")
        )
        return FakeResponse(body)


def test_probe_local_models_returns_ids_from_models_endpoint():
    opener = FakeOpener({"data": [{"id": "qwen3.8-27b-q6"}, {"id": "other"}]})

    models = model_selection.probe_local_models(
        opener=cast(urllib.request.OpenerDirector, opener)
    )

    assert models == ("qwen3.8-27b-q6", "other")
    assert opener.request is not None
    assert opener.request.full_url == "http://host.docker.internal:1235/v1/models"
    assert opener.timeout == 1.5


@pytest.mark.parametrize(
    "opener",
    [
        FakeOpener(error=OSError("connection refused")),
        FakeOpener(raw=b"not json"),
        FakeOpener(payload=None),
        FakeOpener(payload={"data": "not-a-list"}),
    ],
)
def test_probe_local_models_returns_none_for_unusable_responses(opener):
    assert (
        model_selection.probe_local_models(
            opener=cast(urllib.request.OpenerDirector, opener)
        )
        is None
    )


def test_resolve_model_uses_the_default_probe(monkeypatch):
    monkeypatch.setattr(
        model_selection, "probe_local_models", lambda: ("qwen3.8-27b-q6",)
    )

    choice = model_selection.resolve_model()

    assert choice.model == "llama-router/qwen3.8-27b-q6"
    assert choice.reason == "local-available"


def test_host_for_model_labels_local_and_hosted_runs():
    assert model_selection.host_for_model("llama-router/qwen3.8-27b-q6") == (
        "opencode/local-router"
    )
    assert model_selection.host_for_model("opencode/deepseek-v4.1-flash-free") == (
        "opencode/console"
    )
