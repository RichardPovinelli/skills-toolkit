# Adopt Python 3.15 frozen and enum data structures

Status: In progress

## Goal

Use Python features newer than 3.11 where they reduce mutable and stringly-typed
state in this collection of repositories, without changing observable behavior.

Two groups of work were identified:

1. **Blocked:** `frozendict` (PEP 814, builtin in Python 3.15) for module-level
   lookup tables and frozen dataclass mapping fields.
2. **Unblocked:** `copy.replace()` (3.13) for frozen dataclass updates, and
   `enum.StrEnum` (3.11) for automated verdicts that are currently bare strings.

## Context

- Every repository targets Python 3.15: `requires-python = ">=3.15"` in
  `skills-toolkit`, `dev`, and `writing`; `">=3.15,<3.16"` in
  `devcontainer-configs`; Pyright `pythonVersion: "3.15"` plus a
  `#!/usr/bin/env python3.15` hook in `dotfiles`.
- A local 3.15.2 interpreter is available through `uv`, so `frozendict` and
  `copy.replace` exist at runtime.
- **Blocking evidence:** Pyright 1.1.414 reports `reportCallIssue` for both
  `frozendict({"a": 1})` and `frozendict(a=1)`. Its bundled typeshed declares the
  constructor overloads on `__new__` while `__init__` takes no arguments, so any
  positional or keyword construction is rejected. `frozendict.fromkeys(...)`
  type-checks but does not cover the literal-table use cases.
- `frozendict` is also not a `dict` subclass, though it is a `collections.abc.Mapping`,
  supports `dict(x)`, `**x`, `json.dumps`, and compares equal to a plain `dict`.

## Constraints and contracts

- Public JSON artifacts (`report.json`, `prechecks.json`, `turns.json`,
  `metadata.json`) must keep their current string values.
- Report, review, and CLI behavior must be unchanged: exit codes, batch
  summaries, and skip/force semantics.
- Pyright must stay clean in `standard` mode for `src` and `tests`.
- Do not modify eval fixtures or provisioned workspace state.

## Non-goals

- No `frozendict` adoption until Pyright's bundled typeshed supports construction.
- No change to rubric scores, case specs, or evidence artifacts.
- No conversion of working buffers, JSON/TOML payloads, or `deep_merge` output.
- No `Effect` StrEnum conversion in `dotfiles` in this pass (see risks).

## Plan

- [x] 1. Replace `dataclasses.replace` with `copy.replace` in the orchestrator.
      Verify: `uv run pytest -q tests/test_eval_runner_orchestrator.py`.
      Expected: the default-model override test still passes.
- [x] 2. Add an `Outcome` `StrEnum` to `runner/types.py` and use it for
      `RunResult.outcome`, `BatchEntry.outcome`, and `CompletedRun.outcome`.
      Verify: `uv run pyright` and `uv run pytest -q`.
      Expected: no type errors; report JSON still contains `"PASS"`.
- [x] 3. Tighten on-disk report parsing: an unrecognized `outcome` value is not a
      completed run.
      Verify: focused batch discovery test.
      Expected: a report with an unknown verdict is ignored like a malformed one.
- [x] 4. Re-run the full toolkit suite and confirm CLI output is unchanged.
      Verify: `uv run pytest -q` and `uv run skillkit-evals --list`.
      Expected: all tests pass and the listing count is unchanged.

### Deferred

- [ ] 5. Adopt `frozendict` for `dotfiles` lookup tables, `Check.params`,
      `_RULES`, and `CapabilityBundle.fragment` once Pyright accepts construction.
      Verify: `uv run pyright` in each repository.
      Expected: literal tables become immutable and hashable where values allow.
- [ ] 6. Convert `dotfiles` `Effect` from `Literal["allow","ask","deny"]` to a
      `StrEnum`, converting at parse boundaries in `codec`, `opencode`,
      `starlark`, and the adapters, then updating strict-mode test call sites.
      Verify: `uv run pyright` (strict) and `uv run --with pytest pytest tests/python -q`.
      Expected: identical policy behavior with a single runtime vocabulary.

## Progress

### Step 1
Status: Complete. `orchestrator.run_case` now uses `copy.replace` for the
default-model override; the focused orchestrator tests passed.

### Step 2
Status: Complete. `Outcome` is defined in `runner/types.py` and used by
`RunResult`, `BatchEntry`, and `CompletedRun`. Pyright reported 0 errors. The
report and aggregate JSON still contain plain strings, proven by the existing
`report["outcome"] == "PASS"` and summary assertions.

### Step 3
Status: Complete. `_parse_outcome` returns `None` for unknown verdict strings, and
`test_discover_ignores_reports_with_unknown_outcome` covers it.

### Step 4
Status: Complete. `uv run pytest -q` reported 256 passed. `uv run skillkit-evals
--list` still enumerated the dev collection up to `B57` with exit code 0.

## Decisions and discoveries

- `copy.replace` supports frozen dataclasses but **not** `frozendict`, so it is
  not a substitute for frozen-mapping updates.
- `frozendict` immutability is shallow: a mapping holding a list is unhashable.
- `Effect` stays a `Literal` for now. `dotfiles` runs Pyright in `strict` mode
  and constructs `Rule(effect=...)` from parsed JSON, Starlark, and adapter
  payloads across roughly forty modules and tests. The conversion needs explicit
  boundary parsing and is a separate, larger change.

## Risks and open questions

- Pyright's typeshed may change in a patch release; re-check construction
  support before starting step 5.
- Any future `StrEnum` in `dotfiles` must keep JSON output identical, because
  native host configuration is compared byte-for-byte in tests.
