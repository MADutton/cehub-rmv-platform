"""
Examiner module — follow-up decision logic for all three product types.

Assigned Case: checks against pre-defined triggers from prompts.json.
Case-Based / Mastery Module: evaluates against general trigger rules and
generates follow-up text if warranted.
"""
from __future__ import annotations

import json

import openai

from app.config import settings

_client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

_TRIGGER_SYSTEM = (
    "You are evaluating a candidate response in a structured clinical oral examination. "
    "Decide whether the response meets a follow-up trigger condition. "
    "Respond with valid JSON only. No commentary."
)

_GENERAL_SYSTEM = (
    "You are a neutral structured examiner in a clinical oral assessment. "
    "Decide whether a follow-up question is warranted and, if so, generate the follow-up text. "
    "Respond with valid JSON only. No commentary."
)


async def decide_followup_assigned_case(
    current_primary_prompt: dict,
    candidate_response: str,
    available_followups: list[dict],
) -> dict | None:
    """
    Assigned Case: checks response against pre-defined follow-up triggers.
    Returns a follow-up dict or None.
    """
    if not available_followups:
        return None

    trigger_lines = "\n".join(
        f"- {fu['followup_id']}: {fu['trigger']}" for fu in available_followups
    )

    user_message = (
        f'Primary prompt: "{current_primary_prompt["text"]}"\n\n'
        f'Candidate response: "{candidate_response}"\n\n'
        f"Follow-up triggers:\n{trigger_lines}\n\n"
        f"Does the response meet one of these triggers?\n"
        f'{{"issue_followup": true, "followup_id": "matching_id"}}\n'
        f"OR\n"
        f'{{"issue_followup": false}}'
    )

    try:
        resp = await _client.chat.completions.create(
            model=settings.openai_model,
            max_tokens=80,
            messages=[
                {"role": "system", "content": _TRIGGER_SYSTEM},
                {"role": "user", "content": user_message},
            ],
        )
        result = json.loads(resp.choices[0].message.content.strip())
    except Exception:
        return None

    if result.get("issue_followup") and result.get("followup_id"):
        matched_id = result["followup_id"]
        return next((fu for fu in available_followups if fu["followup_id"] == matched_id), None)

    return None


async def decide_followup_generated(
    product_type: str,
    current_prompt: dict,
    candidate_response: str,
    source_context: str,
    followup_rules: dict,
    followup_number: int,
) -> dict | None:
    """
    Case-Based / Mastery Module: evaluates response against general trigger rules
    and generates follow-up text if warranted.
    Returns a follow-up dict {followup_id, text, phase_id} or None.
    """
    trigger_labels = "\n".join(
        f"- {t['trigger_id']}: {t['description']}"
        for t in followup_rules.get("permitted_trigger_types", [])
    )

    user_message = (
        f'Current prompt: "{current_prompt["text"]}"\n\n'
        f'Response: "{candidate_response}"\n\n'
        f"General follow-up trigger conditions:\n{trigger_labels}\n\n"
        f"Does the response meet one of these conditions?\n\n"
        f"If yes, generate a single focused follow-up question grounded in the source material. "
        f"If no, do not issue a follow-up.\n\n"
        f"Respond with JSON only:\n"
        f'{{"issue_followup": true, "followup_text": "the follow-up question", "trigger_id": "matched_trigger_id"}}\n'
        f"OR\n"
        f'{{"issue_followup": false}}'
    )

    try:
        resp = await _client.chat.completions.create(
            model=settings.openai_model,
            max_tokens=200,
            messages=[
                {"role": "system", "content": _GENERAL_SYSTEM},
                {"role": "user", "content": user_message},
            ],
        )
        result = json.loads(resp.choices[0].message.content.strip())
    except Exception:
        return None

    if result.get("issue_followup") and result.get("followup_text"):
        prompt_id = current_prompt["prompt_id"]
        return {
            "followup_id": f"{prompt_id}_fu{followup_number}",
            "phase_id": current_prompt["phase_id"],
            "text": result["followup_text"],
        }

    return None


def format_opening_assigned_case(case_stem: dict) -> str:
    lines = [
        "Welcome to the Assigned Case RMV assessment. I will present you with a standardized "
        "clinical case and ask a series of structured questions. Please respond as you would in "
        "a specialist oral examination. I will not confirm or deny whether your answers are "
        "correct during the session.",
        "",
        f"**Case: {case_stem['title']}**",
        "",
        f"**Signalment:** {case_stem['signalment']}",
        f"**Presenting complaint:** {case_stem['presenting_complaint']}",
        "",
        "**History:",
    ]
    for item in case_stem["history"]:
        lines.append(f"- {item}")
    lines += ["", "**Physical examination:**"]
    for item in case_stem["physical_exam"]:
        lines.append(f"- {item}")
    lines += ["", "**Diagnostics available:**"]
    for item in case_stem["diagnostics_available"]:
        lines.append(f"- {item}")
    lines += ["", "Please review the case above and confirm when you are ready to begin."]
    return "\n".join(lines)


def format_opening_case_based(submission_title: str, species: str) -> str:
    return (
        "Welcome to the Case-Based RMV assessment. I will ask you a series of structured "
        "questions about your submitted case. Please respond as you would in a specialist "
        "oral examination. Be as specific as possible. I will not confirm or deny whether "
        "your answers are correct during the session.\n\n"
        f"**Submitted case:** {submission_title} ({species})\n\n"
        "Please confirm you are ready to begin."
    )


def format_opening_mastery_module(module_title: str) -> str:
    return (
        "Welcome to the Mastery Module RMV assessment. I will ask you a series of structured "
        "questions about the module you just completed. Please respond as you would in a "
        "specialist-level oral examination. I will not confirm or deny whether your answers "
        "are correct during the session.\n\n"
        f"**Module:** {module_title}\n\n"
        "Please confirm you are ready to begin."
    )
