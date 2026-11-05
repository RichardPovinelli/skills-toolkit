"""Behavioral tests for the simulated tool environments (Inc 4, R-9).

Each shim set is exercised through a subprocess with the contract
environment (SHIM_STATE / SHIM_BIN / EVAL_PYTHON) the runner exports, so
the graded transcripts can rely on the stubs' recorded outcomes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from skillkit.evals.runner import workspace

# Shim sets ship with the runner as package data, not with any collection.
SHIMS = workspace.SHIMS_DATA_DIR


def run_shim(set_name: str, tool: str, args: list[str], tmp_path: Path, **extra_env):
    shim_dir = SHIMS / set_name
    state = tmp_path / "state"
    bin_dir = tmp_path / "shim-bin"
    env = {
        **os.environ,
        "SHIM_STATE": str(state),
        "SHIM_BIN": str(bin_dir),
        "EVAL_PYTHON": sys.executable,
        "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}",
    }
    env.update(extra_env)
    proc = subprocess.run(
        [str(shim_dir / tool), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    return proc, state, bin_dir


def test_no_shellcheck_excludes_shellcheck_and_blocks_installs(tmp_path):
    excluded = (SHIMS / "no-shellcheck" / "exclude.txt").read_text().split()
    assert "shellcheck" in excluded
    proc, state, _ = run_shim("no-shellcheck", "apt-get", ["install", "shellcheck"], tmp_path)
    assert proc.returncode != 0
    assert "disabled" in (proc.stderr + proc.stdout).lower()
    assert "SIMULATED" in proc.stderr
    assert "apt-get install shellcheck" in (state / "guard.log").read_text()


def test_apt_shfmt_ok_installs_shfmt_into_shim_bin(tmp_path):
    proc, state, bin_dir = run_shim("apt-shfmt-ok", "apt-get", ["install", "-y", "shfmt"], tmp_path)
    assert proc.returncode == 0
    assert "Setting up shfmt" in proc.stdout
    installed = bin_dir / "shfmt"
    assert installed.exists()
    ver = subprocess.run(
        [str(installed), "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert ver.returncode == 0
    assert "SIMULATED" in ver.stdout
    assert "apt-get install -y shfmt" in (state / "apt-get.log").read_text()


def test_apt_shfmt_denied_fails_with_permission_errors(tmp_path):
    proc, state, _ = run_shim("apt-shfmt-denied", "apt-get", ["install", "-y", "shfmt"], tmp_path)
    assert proc.returncode == 100
    assert "Permission denied" in proc.stderr
    assert "apt-get install -y shfmt" in (state / "apt-get.log").read_text()
    sudo = run_shim("apt-shfmt-denied", "sudo", ["apt-get", "install", "shfmt"], tmp_path)
    assert sudo[0].returncode != 0
    assert "denied" in sudo[0].stderr.lower()


def test_ps_split_psscriptanalyzer_present_only_on_windows_host(tmp_path):
    win, _, _ = run_shim(
        "ps-split",
        "powershell.exe",
        ["-Command", "Get-Module -ListAvailable PSScriptAnalyzer"],
        tmp_path,
    )
    assert win.returncode == 0
    assert "1.21.0" in win.stdout  # module table => analyzer present
    wsl, _, _ = run_shim(
        "ps-split",
        "pwsh",
        ["-Command", "Get-Module -ListAvailable PSScriptAnalyzer"],
        tmp_path,
    )
    assert wsl.returncode == 0
    assert "No matches" in wsl.stdout
    assert "1.21.0" not in wsl.stdout  # no module table => analyzer absent
    assert (SHIMS / "ps-split" / "exclude.txt").read_text().split() == ["pwsh"]


def test_uv_stub_adds_mutmut_to_dev_group(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0"\nrequires-python = ">=3.11"\ndependencies = []\n'
        '\n[dependency-groups]\ndev = ["pytest"]\n',
        encoding="utf-8",
    )
    shim_dir = SHIMS / "mutmut-uv"
    env = {
        **os.environ,
        "SHIM_STATE": str(tmp_path / "state"),
        "EVAL_PYTHON": sys.executable,
    }
    proc = subprocess.run(
        [str(shim_dir / "uv"), "add", "--dev", "mutmut"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=project,
        timeout=60,
    )
    assert proc.returncode == 0
    assert "SIMULATED" in proc.stdout
    text = (project / "pyproject.toml").read_text(encoding="utf-8")
    assert '"mutmut"' in text
    assert 'dev = ["pytest", "mutmut"]' in text


def test_uv_stub_runs_real_pytest_and_simulated_mutmut(tmp_path):
    shim_dir = SHIMS / "mutmut-uv"
    test_file = tmp_path / "test_ok.py"
    test_file.write_text("def test_yes():\n    assert True\n", encoding="utf-8")
    env = {
        **os.environ,
        "SHIM_STATE": str(tmp_path / "state"),
        "EVAL_PYTHON": sys.executable,
    }
    pytest_proc = subprocess.run(
        [str(shim_dir / "uv"), "run", "pytest", str(test_file), "-q"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=120,
    )
    assert pytest_proc.returncode == 0, pytest_proc.stdout + pytest_proc.stderr
    mutmut_proc = subprocess.run(
        [str(shim_dir / "uv"), "run", "mutmut", "run"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
    )
    assert mutmut_proc.returncode == 0
    assert "survived" in mutmut_proc.stdout
    assert (tmp_path / "state" / "uv.log").read_text().count("uv run") >= 2
