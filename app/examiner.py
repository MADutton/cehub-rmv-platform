"""
Examiner module — handles follow-up decision logic via Claude.

The application controls the phase/prompt sequence deterministically.
Claude's only job here is to decide whether a candidate response triggers
one of the predefined follow-up conditions for the current prompt.
"""

from __future__ import annotations

import json

import anthropic

from app.config import settings

_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

_DECISION_SYSTEM = (
    "You are evaluating a candidate response in a structured clinical oral examination. "
    "Your only task is to decide whether the response meets one of the listed follow-up "
    "trigger conditions. Respond with valid JSON only. No commentary."
)


async def decide_followup(
    current_primary_prompt: dict,
    candidate_response: str,
    followups_used_in_phase: int,
    total_prompts_issued: int,
    available_followups: list[dict],
) -> dict | None:
    """
    Returns a follow-up dict from available_followups if one is warranted,
    or None if the examiner should advance to the next phase.

    Hard caps are enforced by the caller; this function trusts that
    available_followups is already filtered for used IDs.
    """
    if not available_followups:
        return None

    trigger_lines = "\n".join(
        f"- {fu['followup_id']}: {fu['trigger']}" for fu in available_followups
    )

    user_message = (
        f"Primary prompt given to candidate:\n"
        f'"{current_primary_prompt["text"]}"\n\n'
        f"Candidate response:\n"
        f'"{candidate_response}"\n\n'
        f"Available follow-up triggers:\n"
        f"{trigger_lines}\n\n"
        f"Does the candidate's response meet one of these trigger conditions?\n\n"
        f"Respond with JSON only:\n"
        f'{{"issue_followup": true, "followup_id": "matching_id"}}\n'
        f"OR\n"
        f'{{"issue_followup": false}}'
    )

    try:
        response = await _client.messages.create(
            model=settings.claude_model,
            max_tokens=80,
            system=_DECISION_SYSTEM,
            messages=[{"role": "user", "content": user_message}],
        )
        text = response.content[0].text.strip()
        result = json.loads(text)
    except Exception:
        return None

    if result.get("issue_followup") and result.get("followup_id"):
        matched_id = result["followup_id"]
        return next(
            (fu for fu in available_followups if fu["followup_id"] == matched_id), None
        )

    return None


def format_opening(case_stem: dict) -> str:
    """Formats the case stem into the opening message shown to the candidate."""
    lines = [
        "Welcome to the Assigned Case RMV assessment. I will present you with a "
        "standardized clinical case and ask a series of structured questions. "
        "Please respond as you would in a specialist oral examination. "
        "Take your time, think through your answers, and respond as completely as you can. "
        "I will not confirm or deny whether your answers are correct during the session.",
        "",
        f"**Case: {case_stem['title']}**",
        "",
        f"**Signalment:** {case_stem['signalment']}",
        f"**Presenting complaint:** {case_stem['presenting_complaint']}",
        "",
        "**History:**",
    ]
    for item in case_stem["history"]:
        lines.append(f"- {item}")

    lines += ["", "**Physical examination:**"]
    for item in case_stem["physical_exam"]:
        lines.append(f"- {item}")

    lines += ["", "**Diagnostics available:**"]
    for item in case_stem["diagnostics_available"]:
        lines.append(f"- {item}")

    lines += ["", "Please review the case above. When you are ready, I will begin the examination."]
    return "\n".join(lines)
