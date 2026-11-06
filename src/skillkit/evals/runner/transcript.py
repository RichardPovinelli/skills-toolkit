"""Parse opencode JSONL events into run facts and a human-readable log (ADR 0005).

``events.jsonl`` is the evidence of record; everything here is derived and can be
regenerated from it. Malformed lines are counted, never fatal, so a partial
crash still yields an analyzable transcript.
"""

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class RunFacts:
    """Derived facts from a run's event stream."""

    session_id: str = ""
    skills_read: tuple[str, ...] = ()
    skills_dirs_seen: tuple[str, ...] = ()
    paths_touched: tuple[str, ...] = ()
    malformed_lines: int = 0
    entries: tuple[tuple, ...] = ()

    @property
    def user_turns(self) -> tuple[str, ...]:
        return tuple(entry[1] for entry in self.entries if entry[0] == "user")


def parse_events(lines: list[str]) -> RunFacts:
    session_id = ""
    skills_read: list[str] = []
    skills_seen: list[str] = []
    paths: list[str] = []
    entries: list[tuple] = []
    malformed = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not session_id:
            session_id = event.get("sessionID") or ""
        part = event.get("part") or {}
        event_type = event.get("type")
        if event_type == "runner_turn":
            # Synthetic line written by the runner to mark each scripted turn.
            entries.append(("user", event.get("user") or ""))
        elif event_type == "tool_use":
            entries.extend(_consume_tool_use(part, skills_read, skills_seen, paths))
        elif event_type == "text":
            text = part.get("text") or ""
            if text:
                entries.append(("text", text))

    return RunFacts(
        session_id=session_id,
        skills_read=tuple(skills_read),
        skills_dirs_seen=tuple(skills_seen),
        paths_touched=tuple(paths),
        malformed_lines=malformed,
        entries=tuple(entries),
    )


def _consume_tool_use(
    part: dict,
    skills_read: list[str],
    skills_seen: list[str],
    paths: list[str],
) -> list[tuple]:
    tool = part.get("tool") or "unknown"
    state = part.get("state") or {}
    if tool == "skill":
        # Live `opencode run --format json` events carry the skill id in the
        # tool input, e.g. {"id": "debugging-failure-triage"}.
        name = (state.get("input") or {}).get("id") or "unknown"
        if name not in skills_seen:
            skills_seen.append(name)
        if state.get("status") == "completed" and name not in skills_read:
            skills_read.append(name)
        return [("skill", name, state.get("status") or "")]
    tool_input = state.get("input") or {}
    file_path = tool_input.get("filePath") or tool_input.get("path")
    if file_path and file_path not in paths:
        paths.append(file_path)
    return [(tool, tool_input)]


_AWAIT_TRAILING = '"\')]}"'


def agent_awaits_user(facts: RunFacts) -> bool:
    """Heuristic: the agent's latest turn ends in a question, so it awaits a reply."""
    for entry in reversed(facts.entries):
        if entry[0] == "user":
            return False
        if entry[0] == "text":
            text = entry[1].rstrip()
            while text and text[-1] in _AWAIT_TRAILING:
                text = text[:-1].rstrip()
            return text.endswith("?")
    return False


def render_conversation_log(facts: RunFacts) -> str:
    lines: list[str] = []
    for entry in facts.entries:
        if entry[0] == "user":
            lines.append(f"user:{entry[1]}")
        elif entry[0] == "skill":
            _, name, status = entry
            lines.append(f"skill:{name} ({status})" if status else f"skill:{name}")
        elif entry[0] == "text":
            lines.append(f"assistant:{entry[1]}")
        else:
            tool, tool_input = entry
            lines.append(f"{tool}:{json.dumps(tool_input, sort_keys=True)}")
    return "\n".join(lines) + ("\n" if lines else "")
