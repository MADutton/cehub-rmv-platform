"""Loaders for Assigned Case RMV data files."""
import json
import os
from functools import lru_cache

from app.config import settings

_BASE = settings.assigned_case_dir


def _j(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _t(path: str) -> str:
    with open(path) as f:
        return f.read()


@lru_cache(maxsize=None)
def get_exam_product() -> dict:
    return _j(os.path.join(_BASE, "product", "exam_product.json"))


@lru_cache(maxsize=None)
def get_rubric() -> dict:
    return _j(os.path.join(_BASE, "product", "rubric.json"))


@lru_cache(maxsize=None)
def get_case_bank() -> dict:
    return _j(os.path.join(_BASE, "product", "case_bank.json"))


@lru_cache(maxsize=None)
def get_examiner_system_prompt() -> str:
    return _t(os.path.join(_BASE, "prompts", "examiner_system_prompt.txt"))


@lru_cache(maxsize=None)
def get_scoring_system_prompt() -> str:
    return _t(os.path.join(_BASE, "prompts", "scoring_system_prompt.txt"))


@lru_cache(maxsize=None)
def get_case_detail(case_id: str) -> dict:
    return _j(os.path.join(_BASE, "cases", case_id, "case_detail.json"))


@lru_cache(maxsize=None)
def get_case_prompts(case_id: str) -> list[dict]:
    data = _j(os.path.join(_BASE, "cases", case_id, "prompts.json"))
    return data["prompt_sequence"]


@lru_cache(maxsize=None)
def get_scoring_anchors(case_id: str) -> dict:
    return _j(os.path.join(_BASE, "cases", case_id, "scoring_anchors.json"))


def get_active_cases() -> list[dict]:
    return [c for c in get_case_bank()["cases"] if c.get("active", True)]


def get_case_stem(case_id: str) -> dict:
    d = get_case_detail(case_id)
    return {
        "case_id": d["case_id"],
        "title": d["title"],
        "species": d["species"],
        "signalment": d["signalment"],
        "presenting_complaint": d["presenting_complaint"],
        "history": d["history"],
        "physical_exam": d["physical_exam"],
        "diagnostics_available": d["diagnostics_available"],
    }
