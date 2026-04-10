"""
Scorer module — produces a structured result JSON from a completed session transcript.

Calls Claude with the full transcript, rubric, and scoring anchors.
Returns a dict conforming to result_schema.json.
"""

from __future__ import annotations

import json
import re

import anthropic

from app.case_loader import get_case_detail, get_rubric, get_scoring_anchors, get_scoring_system_prompt
from app.config import settings

_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


def _format_transcript(turns: list[dict]) -> str:
    """
    Converts a list of turn dicts into a readable transcript string.
    Each turn dict: {phase_id, prompt_id, is_followup, prompt_text, response_text}
    """
    current_phase = None
    lines = []

    for turn in turns:
        if turn["phase_id"] != current_phase:
            current_phase = turn["phase_id"]
            label = current_phase.replace("_", " ").title()
            lines += [f"\n=== Phase: {label} ===\n"]

        role = "EXAMINER (follow-up)" if turn["is_followup"] else "EXAMINER"
        lines.append(f"{role}: {turn['prompt_text']}")
        lines.append(f"CANDIDATE: {turn['response_text'] or '[no response recorded]'}")
        lines.append("")

    return "\n".join(lines)


def _strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` wrappers if present."""
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*([\s\S]*?)```$", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


async def score_session(
    attempt_id: str,
    candidate_id: str,
    case_id: str,
    turns: list[dict],
    duration_minutes: float,
) -> dict:
    """
    Scores a completed session and returns a result dict conforming to result_schema.json.

    turns: list of dicts with keys phase_id, prompt_id, is_followup, prompt_text, response_text
    """
    scoring_prompt = get_scoring_system_prompt()
    rubric = get_rubric()
    anchors = get_scoring_anchors(case_id)
    case_detail = get_case_detail(case_id)
    transcript = _format_transcript(turns)

    user_message = (
        f"## Case\n"
        f"case_id: {case_detail['case_id']}\n"
        f"title: {case_detail['title']}\n\n"
        f"## Rubric\n"
        f"{json.dumps(rubric, indent=2)}\n\n"
        f"## Scoring Anchors\n"
        f"{json.dumps(anchors, indent=2)}\n\n"
        f"## Full Session Transcript\n"
        f"{transcript}\n\n"
        f"## Required Output\n"
        f"Produce the complete result JSON.\n"
        f'attempt_id: "{attempt_id}"\n'
        f'candidate_id: "{candidate_id}"\n'
        f'case_id: "{case_id}"\n'
        f"duration_minutes: {duration_minutes}\n\n"
        f"Output valid JSON only. No commentary outside the JSON object."
    )

    response = await _client.messages.create(
        model=settings.claude_model,
        max_tokens=2000,
        system=scoring_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = _strip_markdown_fences(response.content[0].text)
    return json.loads(raw)
