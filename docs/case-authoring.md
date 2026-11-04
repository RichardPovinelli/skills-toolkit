# Case Authoring and Spec Format

For evaluation operation, run instructions, batching, and evidence review, see the [eval developer manual](README.md). For the collection overview and installation, see the [top-level README](../../README.md).

## Sources of Truth

Case prompts and setup facts belong in [`cases.md`](cases.md). Case IDs are immutable. Use the withheld [rubric](rubric.md) and the [skill ownership matrix](../../skill-trigger-ownership-matrix.md) to record the expected owner and grading intent; do not preload those materials into an evaluated agent's context.

## TOML Specs

Case specs live in `validation/cases/*.toml` under the collection root. The runner accepts these top-level keys:

- `id`, `prompt`, `model`, and `timeout_s` define the case identity, task, model, and turn timeout. Omitting `model` means local-first with an OpenCode Go fallback; setting `model` pins the case and disables the fallback for it. Pin cases that need a specific capability, such as B34's local vision model.
- `session_context` stages a prior-conversation preamble for an otherwise blank session.
- `canned_replies` provides scripted follow-ups for multi-turn cases.
- `state` selects a stored workspace tree; see [Runner environments](runner-environments.md).
- `skills_tree` selects a subtree of `skills/` to copy into the workspace (`.` exposes the whole tree).
- `skill_routing` opts a case into the generic harness skill-routing instruction even when it uses a restricted `skill_names` exposure. Full-catalog cases receive the instruction by default.
- `[exposure]` selects `full_catalog` or a `skill_names` allowlist.
- `[[fixture]]` and `[[inline_file]]` add files; source and destination paths must remain workspace-relative.
- `[[check]]` declares mechanical checks.

Supported check types are `owner_read_any_of`, `owner_read_all_of`,
`no_skill_outside_catalog`, `workspace_unchanged`, `paths_unchanged`,
`paths_changed`, `no_forbidden_reads`, `artifacts_exist`,
`artifacts_any_exist`, and `workspace_scope`. The owner checks require at least
one or every listed skill to be read. Path checks compare named
workspace-relative paths with the run diff; artifact checks require every listed
path or at least one listed path to exist in the finished workspace.
`workspace_scope` requires at least one file matching its `require_any` glob
patterns, rejects files matching its `forbid` patterns, and permits each
`protected_top_level` name only when every file under it matches `require_any`.

When a case declares `canned_replies`, the runner continues the same opencode session (`-s <sessionID>`) after each turn. It supplies a reply only when the agent's last text ends with a question. Otherwise the conversation ends; unused replies are recorded in `turns.json` as `replies_unused`. The file also records `turn_count` and `replies_used`. Per-turn file diffs are recorded against the previous turn's snapshot.

Keep the setup, prompt, fixture, and expected mechanical checks aligned. Run the case in a fresh disposable workspace and inspect its evidence; automated checks do not replace human rubric grading.
