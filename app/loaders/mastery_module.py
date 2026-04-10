"""Loaders for Mastery Module RMV data files."""
import json
import os
from functools import lru_cache

from app.config import settings

_BASE = settings.mastery_module_dir


def _j(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _t(path: str) -> str:
    with open(path) as f:
        return f.read()


@lru_cache(maxsize=None)
def get_product_definition() -> dict:
    return _j(os.path.join(_BASE, "product", "product_definition.json"))


@lru_cache(maxsize=None)
def get_rubric() -> dict:
    return _j(os.path.join(_BASE, "product", "rubric.json"))


@lru_cache(maxsize=None)
def get_interview_template() -> dict:
    return _j(os.path.join(_BASE, "templates", "interview_template.json"))


@lru_cache(maxsize=None)
def get_examiner_system_prompt() -> str:
    return _t(os.path.join(_BASE, "prompts", "examiner_system_prompt.txt"))


@lru_cache(maxsize=None)
def get_scoring_system_prompt() -> str:
    return _t(os.path.join(_BASE, "prompts", "scoring_system_prompt.txt"))


@lru_cache(maxsize=None)
def get_prompt_generation_system_prompt() -> str:
    return _t(os.path.join(_BASE, "prompts", "prompt_generation_system_prompt.txt"))


@lru_cache(maxsize=None)
def get_followup_rules() -> dict:
    return _j(os.path.join(_BASE, "prompts", "followup_rules.json"))


@lru_cache(maxsize=None)
def get_module_record(module_id: str) -> dict:
    return _j(os.path.join(_BASE, "modules", module_id, "module_record.json"))


@lru_cache(maxsize=None)
def get_module_objectives(module_id: str) -> dict:
    return _j(os.path.join(_BASE, "modules", module_id, "module_objectives.json"))


@lru_cache(maxsize=None)
def get_scoring_anchors(module_id: str) -> dict:
    return _j(os.path.join(_BASE, "modules", module_id, "scoring_anchors.json"))


def get_module_content(module_id: str) -> str:
    """Load transcript and curated notes for a module. Returns combined text."""
    module_dir = os.path.join(_BASE, "modules", module_id)
    parts = []

    transcript_path = os.path.join(module_dir, "transcript.txt")
    if os.path.exists(transcript_path):
        parts.append("=== TRANSCRIPT ===\n" + _t(transcript_path))

    notes_path = os.path.join(module_dir, "curated_notes.json")
    if os.path.exists(notes_path):
        notes = _j(notes_path)
        parts.append("=== CURATED NOTES ===\n" + json.dumps(notes, indent=2))

    return "\n\n".join(parts) if parts else ""


def get_active_modules() -> list[dict]:
    modules_dir = os.path.join(_BASE, "modules")
    active = []
    if not os.path.isdir(modules_dir):
        return active
    for module_id in os.listdir(modules_dir):
        record_path = os.path.join(modules_dir, module_id, "module_record.json")
        if os.path.exists(record_path):
            record = _j(record_path)
            if record.get("status") == "active":
                active.append(record)
    return active
