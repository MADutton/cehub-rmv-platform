"""Loaders for Case-Based RMV data files."""
import json
import os
from functools import lru_cache

from app.config import settings

_BASE = settings.case_based_dir


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
