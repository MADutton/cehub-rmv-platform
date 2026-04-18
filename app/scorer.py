"""
Scorer module — produces structured result JSON for all three product types.
"""
from __future__ import annotations

import json
import re

import openai

from app.config import settings
from app.loaders import assigned_case as ac_loader
from app.loaders import case_based as cb_loader
from app.loaders import mastery_module as mm_loader

_client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

_CONFIDENCE_INSTRUCTIONS = """
## Confidence and Review Fields
In addition to domain scores, your JSON output must include:
- "scoring_confidence": "high" | "medium" | "low"
    high   — transcript is complete and unambiguous; scores are well-supported
    medium — minor gaps or ambiguities; scores are defensible but uncertain in 1-2 domains
    low    — significant gaps, very short responses, or transcript is insufficient to score reliably
- "confidence_rationale": "" (empty string if high; brief explanation if medium or low)
- "safety_flags": [] or a list of strings describing any specific unsafe practice recommendations
  detected in the candidate's responses (e.g. "recommended NSAID + corticosteroid without
  contraindication awareness", "recommended laser over confirmed neoplasm")
- "review_required": true | false
    Set true if ANY of the following: scoring_confidence is "low", any domain scored 0,
    safety_flags is non-empty.
"""


def _strip_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*([\s\S]*?)```$", text, re.DOTALL)
    return match.group(1).strip() if match else text


def _format_transcript(turns: list[dict]) -> str:
    current_phase = None
    lines = []
    for turn in turns:
        if turn["phase_id"] != current_phase:
            current_phase = turn["phase_id"]
            lines += [f"\n=== Phase: {current_phase.replace('_', ' ').title()} ===\n"]
        role = "EXAMINER (follow-up)" if turn["is_followup"] else "EXAMINER"
        lines.append(f"{role}: {turn['prompt_text']}")
        lines.append(f"CANDIDATE: {turn['response_text'] or '[no response recorded]'}")
        latency = turn.get("response_latency_seconds")
        if latency is not None:
            lines.append(f"[response latency: {latency}s]")
        lines.append("")
    return "\n".join(lines)


async def score_assigned_case(
    attempt_id: str, candidate_id: str, case_id: str,
    turns: list[dict], duration_minutes: float,
) -> dict:
    scoring_prompt = ac_loader.get_scoring_system_prompt()
    rubric = ac_loader.get_rubric()
    anchors = ac_loader.get_scoring_anchors(case_id)
    case_detail = ac_loader.get_case_detail(case_id)

    user_message = (
        f"## Case\ncase_id: {case_detail['case_id']}\ntitle: {case_detail['title']}\n\n"
        f"## Rubric\n{json.dumps(rubric, indent=2)}\n\n"
        f"## Scoring Anchors\n{json.dumps(anchors, indent=2)}\n\n"
        f"## Full Session Transcript\n{_format_transcript(turns)}\n\n"
        f"## Required Output\n"
        f'attempt_id: "{attempt_id}", candidate_id: "{candidate_id}", '
        f'case_id: "{case_id}", duration_minutes: {duration_minutes}\n'
        f"{_CONFIDENCE_INSTRUCTIONS}\n"
        f"Output valid JSON only."
    )

    response = await _client.chat.completions.create(
        model=settings.openai_model, max_tokens=2000,
        messages=[
            {"role": "system", "content": scoring_prompt},
            {"role": "user", "content": user_message},
        ],
    )
    return json.loads(_strip_fences(response.choices[0].message.content))


async def score_case_based(
    attempt_id: str, candidate_id: str, submission_id: str,
    submission_text: str, turns: list[dict], duration_minutes: float,
) -> dict:
    scoring_prompt = cb_loader.get_scoring_system_prompt()
    rubric = cb_loader.get_rubric()

    user_message = (
        f"## Candidate Submission\n{submission_text}\n\n"
        f"## Rubric\n{json.dumps(rubric, indent=2)}\n\n"
        f"## Full Session Transcript\n{_format_transcript(turns)}\n\n"
        f"## Required Output\n"
        f'attempt_id: "{attempt_id}", candidate_id: "{candidate_id}", '
        f'submission_id: "{submission_id}", duration_minutes: {duration_minutes}\n'
        f"{_CONFIDENCE_INSTRUCTIONS}\n"
        f"Output valid JSON only."
    )

    response = await _client.chat.completions.create(
        model=settings.openai_model, max_tokens=2000,
        messages=[
            {"role": "system", "content": scoring_prompt},
            {"role": "user", "content": user_message},
        ],
    )
    return json.loads(_strip_fences(response.choices[0].message.content))


async def score_mastery_module(
    attempt_id: str, learner_id: str, module_id: str,
    attempt_number: int, turns: list[dict], duration_minutes: float,
) -> dict:
    scoring_prompt = mm_loader.get_scoring_system_prompt()
    rubric = mm_loader.get_rubric()
    objectives = mm_loader.get_module_objectives(module_id)
    anchors = mm_loader.get_scoring_anchors(module_id)

    user_message = (
        f"## Module\nmodule_id: {module_id}\n"
        f"title: {mm_loader.get_module_record(module_id)['module_title']}\n\n"
        f"## Learning Objectives\n{json.dumps(objectives, indent=2)}\n\n"
        f"## Scoring Anchors\n{json.dumps(anchors, indent=2)}\n\n"
        f"## Rubric\n{json.dumps(rubric, indent=2)}\n\n"
        f"## Full Session Transcript\n{_format_transcript(turns)}\n\n"
        f"## Required Output\n"
        f'attempt_id: "{attempt_id}", learner_id: "{learner_id}", '
        f'module_id: "{module_id}", attempt_number: {attempt_number}, '
        f"duration_minutes: {duration_minutes}\n"
        f"{_CONFIDENCE_INSTRUCTIONS}\n"
        f"Output valid JSON only."
    )

    response = await _client.chat.completions.create(
        model=settings.openai_model, max_tokens=2000,
        messages=[
            {"role": "system", "content": scoring_prompt},
            {"role": "user", "content": user_message},
        ],
    )
    return json.loads(_strip_fences(response.choices[0].message.content))
