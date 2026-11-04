# Runner Environments

For evaluation operation and evidence review, see the [eval developer manual](README.md). Case TOML fields and authoring guidance are in [Case authoring and spec format](case-authoring.md).

## Stored State

A case may set `state = "<name>"` to copy `runner/state/<name>/` into the workspace root before the run. Case inline files may overwrite files at the same paths. State-tree repositories are unpacked before the run's `git init`, allowing scenarios to begin with committed project artifacts without modifying this checkout.

State trees can contain project directories such as `requirements/`. The pre-run leak guard permits workspace top-level names created by declared state provisioning or skill exposure, while failing closed on other names such as `plans/`, `projects/`, `design/`, and `reports/`. Protected eval material must not be placed inside a state tree. Transcript checks flag reads outside the workspace and references to protected eval documents or `evals/` paths.

## Isolation Boundaries

Isolation is best-effort detective containment, not an operating-system security sandbox. Each run gets a workspace and synthetic `HOME` outside the checkout, a `git init` anchor, and pinned `PWD` and `TMPDIR`. This keeps the agent project and runtime scratch anchored inside the run workspace. The synthetic home also hides global skill directories.

The workspace contains only the case fixtures, inline files, exposed skills, and any declared state tree or skill subtree. Skills are exposed through symlinks under `.opencode/skills/`. The rubric, `cases.md`, ownership matrix, `plans/`, `projects/`, and previous runs are withheld by construction and checked by pre-run and post-run guards.

## Skill routing instruction

Full-catalog runs receive one generic instruction in
`<home>/.config/opencode/AGENTS.md`. It tells the agent to load the skill whose
description owns the task through the host's skill tool before acting, to load
only the primary owner and required companions, and to avoid inventing a skill
when none matches. It names no skill or case and contains no graded material.
It lives outside the workspace, so it does not appear in workspace diffs or
artifacts. Full-catalog cases receive it by default; a restricted-exposure case
opts in with `skill_routing = true` in its spec (B08 does this).

## Simulated Tool Environments

Cases that test tool-presence behavior can declare `shims = "<set>"`. The runner stages `runner/shims/<set>/` under the run directory and constructs a closed PATH for the agent.

- The staged shim set is placed in `<run-dir>/shim-env/<set>` so listing PATH does not reveal graded material.
- Commands listed in the set's `exclude.txt` are removed from the mirrored host PATH. Excluded commands are absent rather than shadowed by host installations.
- Stubs such as `apt-get`, `uv`, `pwsh`, and `powershell.exe` return explicitly `[SIMULATED]` outcomes and log invocations under `<run-dir>/shim-state/`. Simulated outcomes are evidence, not claims about the host.
- `sudo` either runs commands against the shims or fails closed, depending on the case; system package operations are not performed.
- `$SHIM_BIN` is a run-writable PATH directory where install stubs can materialize a simulated tool. `EVAL_PYTHON` lets stubs invoke the real Python interpreter when needed.
- Shimmed runs record the shim set and a `simulated-tool-env` deviation in `metadata.json`.

Host fidelity remains approximate. Headless opencode with a local router or the hosted fallback does not reproduce every interactive host environment; interpret the run evidence accordingly and grade behavior against the rubric.

The synthetic `HOME` and `XDG_DATA_HOME` hide the interactive OpenCode credential database. The free `opencode/deepseek-v4.1-flash-free` fallback uses OpenCode's built-in provider and works without an API key in this environment. Free-model access is time-limited and may be rate-limited; a rejected request is a model-availability failure, not a credential problem. Model choice and the fallback reason are recorded in `report.json` and `metadata.json`.

## Office-file fixtures (B34 and B35)

B34 uses a real two-page PDF and the real local Qwen vision endpoint; it has no
vision-response shim. B35 uses real DOCX, XLSX, and PPTX files inside a safe ZIP,
plus a separate ZIP with deliberately unsafe member paths and harmless marker
payloads. Never extract the unsafe ZIP while validating fixtures. The runner's
detective containment is not an OS sandbox; use an appropriately isolated host
for behavioral archive-handling runs.

The committed binary inputs can be rebuilt from the repo root using the
existing Office environment (Pillow, Office libraries, LibreOffice, and
Liberation Sans):

```bash
office-python validation/build_office_fixtures.py
```

The builder creates only `fixtures/b34-inspection.pdf`,
`fixtures/b35-delivery.zip`, and `fixtures/b35-supplement.zip`. It does not run
the agent or call the vision endpoint. Temporary source files and conversion
profiles are discarded. Rebuilding may change binary metadata; validate
semantic contents rather than expecting byte-identical regenerated files.
Only the generated inputs are staged; the builder, fixture answers, rubric,
and prior results must stay outside evaluated workspaces.

For B34, preflight `pdfinfo`, `pdftotext`, `pdftoppm`, `markitdown`, and the local
vision model. For B35, preflight `office-python` imports, `markitdown`, and
LibreOffice. Specs declare those tools as setup facts; the runner does not
install them. Missing tools or an unreachable real model must be recorded as
an environment limitation rather than claimed as successful processing.

Existing mechanical checks cover owner reads, input preservation, artifact
existence, and catalog/isolation rules. Content correctness, archive member
validation order, canary reads/disclosure, and vision-request evidence remain
rubric judgments; an automated PASS does not establish those facts. Use the
observable fixture checks in the withheld rubric before grading these cases.
