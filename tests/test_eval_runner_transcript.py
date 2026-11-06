"""Tests for skillkit.evals.runner.transcript event parsing."""

import json
from pathlib import Path

from skillkit.evals.runner import transcript

SESSION = "ses_01ABC"


def ev(obj: dict) -> str:
    return json.dumps({"sessionID": SESSION, **obj})


def skill_event(name: str, status: str = "completed") -> str:
    return ev(
        {
            "type": "tool_use",
            "part": {
                "type": "tool",
                "tool": "skill",
                "state": {
                    "status": status,
                    "input": {"id": name},
                    "output": "loaded",
                },
            },
        }
    )


def tool_event(tool: str, input_payload: dict) -> str:
    return ev(
        {
            "type": "tool_use",
            "part": {
                "tool": tool,
                "state": {"status": "completed", "input": input_payload},
            },
        }
    )


def text_event(text: str) -> str:
    return ev({"type": "text", "part": {"text": text}})


def standard_lines() -> list[str]:
    return [
        ev({"type": "step_start", "part": {}}),
        skill_event("debugging-failure-triage"),
        tool_event("read", {"filePath": "/run/workspace/pipeline.sh"}),
        tool_event("bash", {"command": "bash pipeline.sh"}),
        text_event("The exporter error goes to stderr, so $? is 0."),
        ev({"type": "step_finish", "part": {}}),
    ]


def test_parse_events_extracts_session_id() -> None:
    facts = transcript.parse_events(standard_lines())

    assert facts.session_id == SESSION


def test_parse_events_collects_completed_skill_reads() -> None:
    lines = standard_lines() + [skill_event("nope", status="error")]

    facts = transcript.parse_events(lines)

    assert facts.skills_read == ("debugging-failure-triage",)
    assert set(facts.skills_dirs_seen) == {
        "debugging-failure-triage",
        "nope",
    }


def test_parse_events_collects_file_paths_touched() -> None:
    facts = transcript.parse_events(standard_lines())

    assert "/run/workspace/pipeline.sh" in facts.paths_touched


def test_parse_events_survives_malformed_lines() -> None:
    lines = ["{not json", *standard_lines(), ""]
    facts = transcript.parse_events(lines)

    assert facts.session_id == SESSION
    assert facts.malformed_lines == 1


def test_render_conversation_log_orders_tools_before_text() -> None:
    log = transcript.render_conversation_log(transcript.parse_events(standard_lines()))

    assert "skill:debugging-failure-triage" in log
    assert "read:" in log
    assert "$? is 0" in log
    assert log.index("skill:debugging-failure-triage") < log.index("$? is 0")


LIVE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "live_b02_events.jsonl"


def test_parses_real_live_b02_event_sample() -> None:
    # Sample captured 2026-10-11 from a real `opencode run --format json` run.
    # Live events identify a skill via state.input.id, not a directory path.
    lines = LIVE_FIXTURE.read_text(encoding="utf-8").splitlines()

    facts = transcript.parse_events(lines)

    assert facts.session_id.startswith("ses_")
    assert facts.malformed_lines == 0
    assert "debugging-failure-triage" in facts.skills_read
    assert "pipeline.sh" in " ".join(facts.paths_touched)


def test_runner_turn_events_become_user_entries() -> None:
    lines = [
        json.dumps({"type": "runner_turn", "turn": 1, "user": "do the thing"}),
        json.dumps(
            {
                "sessionID": "ses_x",
                "type": "text",
                "part": {"type": "text", "text": "I need access, is that ok?"},
            }
        ),
        json.dumps({"type": "runner_turn", "turn": 2, "user": "Approved, continue."}),
        json.dumps(
            {
                "sessionID": "ses_x",
                "type": "text",
                "part": {"type": "text", "text": "Done."},
            }
        ),
    ]

    facts = transcript.parse_events(lines)

    assert ("user", "do the thing") in facts.entries
    assert ("user", "Approved, continue.") in facts.entries
    assert facts.user_turns == ("do the thing", "Approved, continue.")
    log = transcript.render_conversation_log(facts)
    assert "user:do the thing" in log
    assert "user:Approved, continue." in log


def test_agent_awaits_user_only_on_trailing_question() -> None:
    asking = transcript.parse_events(
        [
            json.dumps({"type": "runner_turn", "turn": 1, "user": "go"}),
            json.dumps(
                {
                    "sessionID": "s",
                    "type": "text",
                    "part": {"type": "text", "text": "Ok to edit?"},
                }
            ),
        ]
    )
    assert transcript.agent_awaits_user(asking) is True

    stating = transcript.parse_events(
        [
            json.dumps({"type": "runner_turn", "turn": 1, "user": "go"}),
            json.dumps(
                {
                    "sessionID": "s",
                    "type": "text",
                    "part": {"type": "text", "text": "All done."},
                }
            ),
        ]
    )
    assert transcript.agent_awaits_user(stating) is False

    quoted_question = transcript.parse_events(
        [
            json.dumps({"type": "runner_turn", "turn": 1, "user": "go"}),
            json.dumps(
                {
                    "sessionID": "s",
                    "type": "text",
                    "part": {"type": "text", "text": 'Shall I run it?"\n'},
                }
            ),
        ]
    )
    assert transcript.agent_awaits_user(quoted_question) is True

    no_text = transcript.parse_events([json.dumps({"type": "runner_turn", "turn": 1, "user": "go"})])
    assert transcript.agent_awaits_user(no_text) is False
