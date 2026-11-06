# skillkit

Validation and behavioral-evaluation tooling for Agent Skill collections.

This repository holds the machinery, not the skills. A skills repository depends
on `skillkit` at a pinned revision and keeps its own skills, cases, fixtures, and
policy beside them.

## What it provides

| Entry point | Purpose |
| --- | --- |
| `skillkit-validate` | Structural and ownership checks for a skill collection: frontmatter, heading contract, link integrity, line limits, ownership matrix, plugin manifests, README inventory, and harness instructions |
| `skillkit-evals` | One-shot behavioral evaluation of a case through `opencode run --format json --standalone`, with isolated workspaces, scripted turns, simulated tool environments, mechanical checks, and a generated review brief |

Both are collection-agnostic. Paths are supplied by the caller; nothing is
derived from where this package is installed.

## Install

From a checkout, in another project's environment:

```bash
uv add git+https://github.com/RichardPovinelli/skills-toolkit@v0.1.0
```

Or run without adding a dependency:

```bash
uv run --with git+https://github.com/RichardPovinelli/skills-toolkit@v0.1.0 skillkit-validate --root .
```

## Collection layout

A collection supplies this shape. All of it is overridable per invocation.

```text
<collection>/
  skills/
    <skill-name>/
      SKILL.md
      references/           # optional
  validation/
    cases/                  # <CASE_ID>.toml
    fixtures/               # copied into each run workspace
    state/                  # stored workspace states, selected by a case
    rubric.md               # evaluator-only grading rows (optional)
    cases.md                # human-readable case catalog (optional)
    validate.toml           # collection policy (optional)
  validation-runs/          # run evidence (gitignore this)
  reports/                  # generated review briefs (gitignore this)
```

A case selects data with names relative to `validation/`:

```toml
[[fixture]]
src = "fixtures/pipeline.sh"
dest = "pipeline.sh"
```

`src` must stay inside `validation/`; absolute paths and `..` are rejected.

## Validation policy

`skillkit-validate` carries defaults that match a fully specified collection.
`validation/validate.toml` overrides them, so a collection that has no ownership
matrix or no plugin manifests skips those checks instead of weakening the rule
for everyone.

```toml
max_skill_lines = 499
required_headings = ["## Scope and routing", "## Output contract"]
rejected_headings = [["## Scope", "## Scope and routing"]]

readme = "README.md"
matrix = "skill-trigger-ownership-matrix.md"
copilot_instructions = ".github/copilot-instructions.md"
manifests = [
  ".github/plugin/plugin.json",
  ".claude-plugin/plugin.json",
  ".claude-plugin/marketplace.json",
]

[[policy_check]]
phrase = "Run coverage only when it is explicitly requested"
owner = "test-coverage"
```

Set `matrix`, `readme`, `copilot_instructions` to `""`, or drop entries from
`manifests`, to disable those checks. `required_headings = []` disables the
heading contract.

## Evaluation

```bash
skillkit-evals --list                     # discover cases without launching a model
skillkit-evals B02 --collection .         # one case
skillkit-evals --all --collection .       # every case, resumable
```

`--collection` defaults to the current directory. Also available: `--data-dir`,
`--cases-dir`, `--state-dir`, `--rubric`, `--runs-dir`, `--reports-dir`,
`--shims-root`, `--model`, `--no-local-fallback`, `--skip-review`, and
`--force`.

Runs prefer a local model and fall back to a hosted free model; the chosen model
and the reason are printed and recorded in `report.json` and `metadata.json`.
Each run's `metadata.json` also records the collection's git revision and the
`skillkit` version that produced the evidence.

Case authoring, the runner environment, and grading practice are documented in
[`docs/case-authoring.md`](docs/case-authoring.md),
[`docs/runner-environments.md`](docs/runner-environments.md), and
[`docs/grading-sessions.md`](docs/grading-sessions.md).

## Development

```bash
uv sync --dev
uv run pytest -q
uv run pyright
```

Shim sets under `src/skillkit/evals/runner/data/shims/` ship as package data.
They simulate host tool environments (missing tools, denied installs, a mutmut
stub) and are exercised by `tests/test_eval_runner_shims.py`.
