import json
import os
from functools import lru_cache

from app.config import settings


def _load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _load_text(path: str) -> str:
    with open(path) as f:
        return f.read()


@lru_cache(maxsize=None)
def get_exam_product() -> dict:
    return _load_json(os.path.join(settings.product_dir, "exam_product.json"))


@lru_cache(maxsize=None)
def get_rubric() -> dict:
    return _load_json(os.path.join(settings.product_dir, "rubric.json"))


@lru_cache(maxsize=None)
def get_case_bank() -> dict:
    return _load_json(os.path.join(settings.product_dir, "case_bank.json"))


@lru_cache(maxsize=None)
def get_examiner_system_prompt() -> str:
    return _load_text(os.path.join(settings.prompts_dir, "examiner_system_prompt.txt"))


@lru_cache(maxsize=None)
def get_scoring_system_prompt() -> str:
    return _load_text(os.path.join(settings.prompts_dir, "scoring_system_prompt.txt"))


@lru_cache(maxsize=None)
def get_followup_rules() -> dict:
    return _load_json(os.path.join(settings.prompts_dir, "followup_rules.json"))


@lru_cache(maxsize=None)
def get_case_detail(case_id: str) -> dict:
    return _load_json(os.path.join(settings.cases_dir, case_id, "case_detail.json"))


@lru_cache(maxsize=None)
def get_case_prompts(case_id: str) -> dict:
    return _load_json(os.path.join(settings.cases_dir, case_id, "prompts.json"))


@lru_cache(maxsize=None)
def get_scoring_anchors(case_id: str) -> dict:
    return _load_json(os.path.join(settings.cases_dir, case_id, "scoring_anchors.json"))


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


def get_primary_prompts(case_id: str) -> list[dict]:
    return [
        p for p in get_case_prompts(case_id)["prompt_sequence"]
        if p["prompt_type"] == "primary"
    ]
