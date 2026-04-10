"""
Generates prompt sets for Case-Based and Mastery Module RMV using OpenAI.
Assigned Case RMV uses pre-written prompts and does not use this module.
"""
from __future__ import annotations

import json
import re

import openai

from app.config import settings
from app.loaders import case_based as cb_loader
from app.loaders import mastery_module as mm_loader

_client = openai.AsyncOpenAI(api_key=settings.openai_api_key)


def _strip_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*([\s\S]*?)```$", text, re.DOTALL)
    return match.group(1).strip() if match else text


async def generate_prompts_for_submission(submission_text: str) -> list[dict]:
    """
    Generates 4 primary prompts from a candidate's submitted case text.
    Returns a list of prompt dicts conforming to the prompt sequence schema.
    """
    system_prompt = cb_loader.get_prompt_generation_system_prompt()
    template = cb_loader.get_interview_template()
    rubric = cb_loader.get_rubric()

    user_message = (
        f"## Interview Template\n"
        f"{json.dumps(template, indent=2)}\n\n"
        f"## Rubric Domains\n"
        f"{json.dumps([d['domain_id'] for d in rubric['domains']], indent=2)}\n\n"
        f"## Candidate Submission\n"
        f"{submission_text}\n\n"
        f"Generate exactly 4 primary prompts specific to this submission."
    )

    response = await _client.chat.completions.create(
        model=settings.openai_model,
        max_tokens=1000,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )

    raw = _strip_fences(response.choices[0].message.content)
    return json.loads(raw)


async def generate_prompts_for_module(module_id: str) -> list[dict]:
    """
    Generates 4 primary prompts from a module's content and objectives.
    Returns a list of prompt dicts conforming to the prompt sequence schema.
    """
    system_prompt = mm_loader.get_prompt_generation_system_prompt()
    template = mm_loader.get_interview_template()
    objectives = mm_loader.get_module_objectives(module_id)
    rubric = mm_loader.get_rubric()
    content = mm_loader.get_module_content(module_id)

    if not content:
        raise ValueError(f"No module content found for module_id='{module_id}'")

    user_message = (
        f"## Module Title\n"
        f"{mm_loader.get_module_record(module_id)['module_title']}\n\n"
        f"## Learning Objectives\n"
        f"{json.dumps(objectives['learning_objectives'], indent=2)}\n\n"
        f"## High Priority Objectives\n"
        f"{json.dumps(objectives['high_priority_objectives'], indent=2)}\n\n"
        f"## Interview Template\n"
        f"{json.dumps(template, indent=2)}\n\n"
        f"## Rubric Domains\n"
        f"{json.dumps([d['domain_id'] for d in rubric['domains']], indent=2)}\n\n"
        f"## Module Content\n"
        f"{content}\n\n"
        f"Generate exactly 4 primary prompts specific to this module."
    )

    response = await _client.chat.completions.create(
        model=settings.openai_model,
        max_tokens=1000,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )

    raw = _strip_fences(response.choices[0].message.content)
    return json.loads(raw)
