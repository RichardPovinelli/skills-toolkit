"""Tests for skillkit.evals.runner.metadata run/version bookkeeping."""

from types import SimpleNamespace

from skillkit.evals.runner import metadata
from skillkit.evals.runner.toolkit import TOOLKIT_NAME, TOOLKIT_VERSION
from skillkit.evals.runner.types import CaseSpec


def fake_git(responses):
    def run_git(cmd, **kwargs):
        key = " ".join(cmd[1:])
        if key not in responses:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout=responses[key], stderr="")

    return run_git


def test_repo_version_info_clean_checkout():
    info = metadata.repo_version_info(
        "/repo",
        run_git=fake_git({"rev-parse HEAD": "abc123\n", "status --porcelain": ""}),
    )
    assert info == {"git_sha": "abc123", "dirty": False, "dirty_tree_hash": ""}


def test_repo_version_info_dirty_tree_hashes_changes():
    responses = {
        "rev-parse HEAD": "abc123\n",
        "status --porcelain": " M evals/runner/checks.py\n",
        "diff": "diff --git a/x b/x\n+hello\n",
    }
    info = metadata.repo_version_info("/repo", run_git=fake_git(responses))
    assert info["dirty"] is True
    assert len(info["dirty_tree_hash"]) == 64


def test_repo_version_info_survives_missing_git():
    def exploding_git(cmd, **kwargs):
        raise OSError("no git")

    info = metadata.repo_version_info("/repo", run_git=exploding_git)
    assert info["git_sha"] == "unknown"
    assert info["dirty"] is None


def test_build_metadata_includes_provenance():
    case = CaseSpec(case_id="B02", prompt="p", model="llama-router/test-model")
    meta = metadata.build_metadata(
        case,
        host="opencode/local-router",
        created_central="2026-10-11T01:00:00-05:00",
        version_info={"git_sha": "abc", "dirty": False, "dirty_tree_hash": ""},
        deviations=["containment=detective"],
    )
    assert meta["case_id"] == "B02"
    assert meta["model"] == "llama-router/test-model"
    assert meta["host"] == "opencode/local-router"
    assert meta["created_central"] == "2026-10-11T01:00:00-05:00"
    assert "created_utc" not in meta
    assert meta["repo"]["git_sha"] == "abc"
    assert meta["deviations"] == ["containment=detective"]
    assert meta["toolkit"] == {"name": TOOLKIT_NAME, "version": TOOLKIT_VERSION}
