from __future__ import annotations

import io
import os
import random
import statistics
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal, get_db, init_db
from app.examiner import (
    decide_followup_assigned_case,
    decide_followup_generated,
    format_opening_assigned_case,
    format_opening_case_based,
    format_opening_mastery_module,
)
from app.generators.prompt_generator import (
    generate_prompts_for_module,
    generate_prompts_for_submission,
)
from app.loaders import assigned_case as ac_loader
from app.loaders import case_based as cb_loader
from app.loaders import mastery_module as mm_loader
from app.models import (
    PromptResponse,
    ResultRecord,
    ResultResponse,
    SessionRecord,
    SessionStateResponse,
    StartSessionRequest,
    StartSessionResponse,
    SubmissionRecord,
    SubmitResponseRequest,
    TurnRecord,
    UploadSubmissionResponse,
)
from app.scorer import score_assigned_case, score_case_based, score_mastery_module

_KNOWN_DOMAINS = [
    "core_concept_understanding",
    "clinical_application",
    "prioritization_decision_making",
    "justification",
    "boundaries_uncertainty",
    "mastery_depth",
]


def _extract_text(filename: str, content: bytes) -> str:
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext == "txt":
        return content.decode("utf-8", errors="replace")
    if ext == "pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(content))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as e:
            raise HTTPException(422, f"PDF extraction failed: {e}")
    if ext in ("doc", "docx"):
        try:
            import docx
            doc = docx.Document(io.BytesIO(content))
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception as e:
            raise HTTPException(422, f"DOCX extraction failed: {e}")
    raise HTTPException(422, f"Unsupported file type: .{ext}")


async def _score_background(session_id: str) -> None:
    async with AsyncSessionLocal() as db:
        try:
            res = await db.execute(
                select(SessionRecord)
                .options(selectinload(SessionRecord.turns), selectinload(SessionRecord.submission))
                .where(SessionRecord.id == session_id)
            )
            session = res.scalar_one_or_none()
            if not session or session.status != "complete":
                return

            completed_at = session.completed_at or datetime.now(timezone.utc)
            duration = (completed_at - session.created_at).total_seconds() / 60
            turns_data = [
                {
                    "phase_id": t.phase_id,
                    "prompt_id": t.prompt_id,
                    "is_followup": t.is_followup,
                    "prompt_text": t.prompt_text,
                    "response_text": t.response_text,
                    "response_latency_seconds": (
                        round((t.response_submitted_at - t.prompt_delivered_at).total_seconds(), 1)
                        if t.response_submitted_at and t.prompt_delivered_at else None
                    ),
                }
                for t in session.turns
            ]

            if session.product_type == "assigned_case":
                result_data = await score_assigned_case(
                    session_id, session.participant_id, session.content_id,
                    turns_data, round(duration, 1),
                )
            elif session.product_type == "case_based":
                submission_text = session.submission.extracted_text if session.submission else ""
                result_data = await score_case_based(
                    session_id, session.participant_id, session.content_id,
                    submission_text, turns_data, round(duration, 1),
                )
            else:
                result_data = await score_mastery_module(
                    session_id, session.participant_id, session.content_id,
                    session.attempt_number, turns_data, round(duration, 1),
                )

            db.add(ResultRecord(session_id=session_id, result_data=result_data))
            session.status = "scored"
            await db.commit()

        except Exception as exc:
            print(f"[scorer] Error scoring session {session_id}: {exc}")


def _primary_prompts(session: SessionRecord) -> list[dict]:
    return [p for p in session.prompts if p.get("prompt_type") == "primary"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="CEHub RMV Platform",
    description="Unified AI assessment engine for Assigned Case, Case-Based, and Mastery Module RMV.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------------------
# Static front-end
# ---------------------------------------------------------------------------
# Serves the CEHub RMV launcher SPA at / and /static/* so a Thinkific lesson
# can embed a single iframe URL against the unified API.

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def _root_index() -> FileResponse:
        return FileResponse(os.path.join(_STATIC_DIR, "index.html"))


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/cases")
async def list_cases():
    return {"cases": ac_loader.get_active_cases()}


@app.get("/modules")
async def list_modules():
    return {"modules": mm_loader.get_active_modules()}


@app.post("/submissions", response_model=UploadSubmissionResponse, status_code=201)
async def upload_submission(
    candidate_id: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    filename = file.filename or "submission"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"
    if ext not in ("pdf", "doc", "docx", "txt"):
        raise HTTPException(422, f"Unsupported file type: .{ext}")

    extracted = _extract_text(filename, content)
    if not extracted.strip():
        raise HTTPException(422, "Could not extract text from the uploaded file.")

    record = SubmissionRecord(
        candidate_id=candidate_id,
        original_filename=filename,
        file_type=ext,
        extracted_text=extracted,
    )
    db.add(record)
    await db.commit()
    return UploadSubmissionResponse(
        submission_id=record.id,
        candidate_id=candidate_id,
        original_filename=filename,
        file_type=ext,
        char_count=len(extracted),
    )


@app.post("/sessions", response_model=StartSessionResponse, status_code=201)
async def start_session(body: StartSessionRequest, db: AsyncSession = Depends(get_db)):
    pt = body.product_type
    if pt not in ("assigned_case", "case_based", "mastery_module"):
        raise HTTPException(400, f"Invalid product_type: '{pt}'")

    if pt == "assigned_case":
        case_id = body.case_id
        if not case_id:
            active = ac_loader.get_active_cases()
            if not active:
                raise HTTPException(500, "No active cases available")
            case_id = random.choice(active)["case_id"]
        try:
            case_stem = ac_loader.get_case_stem(case_id)
        except FileNotFoundError:
            raise HTTPException(404, f"Case '{case_id}' not found")
        prompts = ac_loader.get_case_prompts(case_id)
        content_id = case_id
        opening = format_opening_assigned_case(case_stem)
        submission_id = None

    elif pt == "case_based":
        if not body.submission_id:
            raise HTTPException(400, "submission_id is required for case_based")
        res = await db.execute(
            select(SubmissionRecord).where(SubmissionRecord.id == body.submission_id)
        )
        submission = res.scalar_one_or_none()
        if not submission:
            raise HTTPException(404, f"Submission '{body.submission_id}' not found")
        prompts = await generate_prompts_for_submission(submission.extracted_text)
        content_id = body.submission_id
        submission_id = body.submission_id
        opening = format_opening_case_based(submission.original_filename, "")

    else:
        if not body.module_id:
            raise HTTPException(400, "module_id is required for mastery_module")
        try:
            record = mm_loader.get_module_record(body.module_id)
        except FileNotFoundError:
            raise HTTPException(404, f"Module '{body.module_id}' not found")
        static_prompts = mm_loader.get_module_prompts(body.module_id)
        prompts = static_prompts if static_prompts is not None else await generate_prompts_for_module(body.module_id)
        content_id = body.module_id
        submission_id = None
        opening = format_opening_mastery_module(record["module_title"])

    first_prompt = next((p for p in prompts if p["prompt_type"] == "primary"), None)
    if not first_prompt:
        raise HTTPException(500, "No primary prompts in generated set")

    now = datetime.now(timezone.utc)
    session = SessionRecord(
        product_type=pt,
        participant_id=body.participant_id,
        content_id=content_id,
        submission_id=submission_id,
        attempt_number=body.attempt_number,
        prompts=prompts,
        metadata=body.metadata,
        state={
            "primary_prompt_index": 0,
            "followups_used_count": 0,
            "used_followup_ids": [],
            "total_prompts_issued": 1,
            "awaiting_response_for": first_prompt["prompt_id"],
        },
    )
    db.add(session)
    await db.flush()
    db.add(TurnRecord(
        session_id=session.id,
        turn_number=1,
        phase_id=first_prompt["phase_id"],
        prompt_id=first_prompt["prompt_id"],
        is_followup=False,
        prompt_text=first_prompt["text"],
        prompt_delivered_at=now,
    ))
    await db.commit()

    return StartSessionResponse(
        session_id=session.id,
        product_type=pt,
        content_id=content_id,
        opening_message=opening,
        first_prompt=first_prompt["text"],
        phase=first_prompt["phase_id"],
    )


@app.post("/sessions/{session_id}/respond", response_model=PromptResponse)
async def respond(
    session_id: str,
    body: SubmitResponseRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(SessionRecord)
        .options(selectinload(SessionRecord.turns))
        .where(SessionRecord.id == session_id)
    )
    session = res.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Session not found")
    if session.status != "active":
        raise HTTPException(400, f"Session is '{session.status}', not active")

    state = session.state
    awaiting_id = state["awaiting_response_for"]

    current_turn = next(
        (t for t in session.turns if t.prompt_id == awaiting_id and t.response_text is None),
        None,
    )
    if not current_turn:
        raise HTTPException(500, "No open turn found for current prompt")
    current_turn.response_text = body.response
    current_turn.response_submitted_at = datetime.now(timezone.utc)

    if session.product_type == "assigned_case":
        product = ac_loader.get_exam_product()
    elif session.product_type == "case_based":
        product = cb_loader.get_product_definition()
    else:
        product = mm_loader.get_product_definition()

    max_followups = product.get("max_followups_per_prompt", 2)
    max_total = product.get("max_total_prompts", 10)

    primary_prompts = _primary_prompts(session)
    primary_index = state["primary_prompt_index"]
    current_primary = primary_prompts[primary_index]
    followups_used = state["followups_used_count"]
    used_ids = state["used_followup_ids"]
    total_issued = state["total_prompts_issued"]

    next_followup = None
    if followups_used < max_followups and total_issued < max_total:
        if session.product_type == "assigned_case":
            all_fus = current_primary.get("followups", [])
            available = [f for f in all_fus if f["followup_id"] not in used_ids]
            if available:
                next_followup = await decide_followup_assigned_case(
                    current_primary, body.response, available
                )
        elif session.product_type == "case_based":
            fu_dict = await decide_followup_generated(
                session.product_type, current_primary, body.response,
                "", cb_loader.get_followup_rules(), followups_used + 1,
            )
            if fu_dict:
                next_followup = fu_dict
        else:
            all_fus = current_primary.get("followups", [])
            if all_fus:
                available = [f for f in all_fus if f["followup_id"] not in used_ids]
                if available:
                    next_followup = await decide_followup_assigned_case(
                        current_primary, body.response, available
                    )
            else:
                fu_dict = await decide_followup_generated(
                    session.product_type, current_primary, body.response,
                    "", mm_loader.get_followup_rules(), followups_used + 1,
                )
                if fu_dict:
                    next_followup = fu_dict

    now = datetime.now(timezone.utc)

    if next_followup:
        fu_id = next_followup.get("followup_id")
        fu_text = next_followup.get("text") or next_followup.get("followup_text", "")
        new_total = total_issued + 1
        session.state = {
            **state,
            "followups_used_count": followups_used + 1,
            "used_followup_ids": used_ids + [fu_id],
            "total_prompts_issued": new_total,
            "awaiting_response_for": fu_id,
        }
        db.add(TurnRecord(
            session_id=session_id, turn_number=new_total,
            phase_id=current_primary["phase_id"], prompt_id=fu_id,
            is_followup=True, prompt_text=fu_text,
            prompt_delivered_at=now,
        ))
        await db.commit()
        return PromptResponse(done=False, next_prompt=fu_text,
            phase=current_primary["phase_id"],
            prompt_id=fu_id, is_followup=True)

    next_index = primary_index + 1
    if next_index >= len(primary_prompts):
        session.status = "complete"
        session.completed_at = datetime.now(timezone.utc)
        session.state = {**state, "primary_prompt_index": primary_index}
        await db.commit()
        background_tasks.add_task(_score_background, session_id)
        closing = (
            "That concludes the assessment. Thank you for your participation. "
            "Your responses will be reviewed and scored. "
            "You will receive your results through the standard reporting process."
        )
        return PromptResponse(done=True, next_prompt=closing,
            phase=None, prompt_id=None, is_followup=False)

    next_primary = primary_prompts[next_index]
    new_total = total_issued + 1
    session.state = {
        "primary_prompt_index": next_index,
        "followups_used_count": 0,
        "used_followup_ids": [],
        "total_prompts_issued": new_total,
        "awaiting_response_for": next_primary["prompt_id"],
    }
    db.add(TurnRecord(
        session_id=session_id, turn_number=new_total,
        phase_id=next_primary["phase_id"], prompt_id=next_primary["prompt_id"],
        is_followup=False, prompt_text=next_primary["text"],
        prompt_delivered_at=now,
    ))
    await db.commit()
    return PromptResponse(done=False, next_prompt=next_primary["text"],
        phase=next_primary["phase_id"],
        prompt_id=next_primary["prompt_id"], is_followup=False)


@app.get("/sessions/{session_id}", response_model=SessionStateResponse)
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(SessionRecord).where(SessionRecord.id == session_id))
    session = res.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Session not found")
    state = session.state
    current_phase = None
    if session.status == "active":
        primaries = _primary_prompts(session)
        idx = state.get("primary_prompt_index", 0)
        if idx < len(primaries):
            current_phase = primaries[idx]["phase_id"]
    return SessionStateResponse(
        session_id=session.id, product_type=session.product_type,
        participant_id=session.participant_id, content_id=session.content_id,
        status=session.status, current_phase=current_phase,
        total_prompts_issued=state.get("total_prompts_issued", 0),
        attempt_number=session.attempt_number, created_at=session.created_at,
    )


@app.get("/sessions/{session_id}/result", response_model=ResultResponse)
async def get_result(session_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(SessionRecord)
        .options(selectinload(SessionRecord.result))
        .where(SessionRecord.id == session_id)
    )
    session = res.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Session not found")
    if session.status == "active":
        raise HTTPException(400, "Session is still active")
    if session.status == "complete":
        return ResultResponse(session_id=session_id, product_type=session.product_type,
            status="scoring", result=None)
    if session.result is None:
        raise HTTPException(500, "Session marked scored but result record missing")
    return ResultResponse(session_id=session_id, product_type=session.product_type,
        status="scored", result=session.result.result_data)


# ---------------------------------------------------------------------------
# Analytics endpoint
# ---------------------------------------------------------------------------
# Aggregates domain scores, outcomes, confidence flags, and response latency
# across scored sessions. Designed to feed reliability analysis, G-studies,
# and AI-scoring validation workflows.

def _safe_stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "std": None, "min": None, "max": None}
    n = len(values)
    mean = sum(values) / n
    std = statistics.stdev(values) if n > 1 else 0.0
    return {"n": n, "mean": round(mean, 3), "std": round(std, 3),
            "min": round(min(values), 3), "max": round(max(values), 3)}


def _extract_domain_scores(result_data: dict) -> dict[str, float]:
    """Extract the 6 known domain scores from result_data regardless of nesting."""
    # Try common top-level keys first
    for key in ("domains", "domain_scores", "scores", "section_scores"):
        candidate = result_data.get(key)
        if isinstance(candidate, dict):
            found = {d: candidate[d] for d in _KNOWN_DOMAINS if d in candidate}
            if found:
                return found
    # Fall back: look for domain names directly at the top level
    return {d: result_data[d] for d in _KNOWN_DOMAINS if d in result_data}


@app.get("/analytics")
async def get_analytics(
    product_type: str | None = Query(default=None),
    content_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(SessionRecord, ResultRecord)
        .join(ResultRecord, ResultRecord.session_id == SessionRecord.id)
        .options(selectinload(SessionRecord.turns))
        .where(SessionRecord.status == "scored")
    )
    if product_type:
        stmt = stmt.where(SessionRecord.product_type == product_type)
    if content_id:
        stmt = stmt.where(SessionRecord.content_id == content_id)

    rows = (await db.execute(stmt)).all()

    # Accumulate per content_id
    buckets: dict[str, dict] = defaultdict(lambda: {
        "n": 0,
        "domain_scores": defaultdict(list),
        "weighted_pcts": [],
        "outcomes": defaultdict(int),
        "review_required_count": 0,
        "low_confidence_count": 0,
        "latencies_seconds": [],
    })

    for session, result in rows:
        rd = result.result_data
        b = buckets[session.content_id]
        b["n"] += 1

        for domain, score in _extract_domain_scores(rd).items():
            if isinstance(score, (int, float)):
                b["domain_scores"][domain].append(float(score))

        for pct_key in ("weighted_pct", "total_pct", "final_pct", "weighted_final_pct"):
            val = rd.get(pct_key)
            if isinstance(val, (int, float)):
                b["weighted_pcts"].append(float(val))
                break

        outcome = rd.get("outcome") or rd.get("result") or "unknown"
        b["outcomes"][str(outcome)] += 1

        if rd.get("review_required"):
            b["review_required_count"] += 1
        if rd.get("scoring_confidence") in ("low", "medium"):
            b["low_confidence_count"] += 1

        for turn in session.turns:
            if turn.response_submitted_at and turn.prompt_delivered_at:
                latency = (turn.response_submitted_at - turn.prompt_delivered_at).total_seconds()
                b["latencies_seconds"].append(latency)

    summary = {}
    for cid, b in buckets.items():
        summary[cid] = {
            "n_sessions": b["n"],
            "domain_stats": {
                domain: _safe_stats(scores)
                for domain, scores in b["domain_scores"].items()
            },
            "weighted_pct_stats": _safe_stats(b["weighted_pcts"]),
            "outcomes": dict(b["outcomes"]),
            "review_required_count": b["review_required_count"],
            "low_or_medium_confidence_count": b["low_confidence_count"],
            "response_latency_stats": _safe_stats(b["latencies_seconds"]),
        }

    return {
        "total_sessions": sum(b["n"] for b in buckets.values()),
        "filters": {"product_type": product_type, "content_id": content_id},
        "by_content_id": summary,
    }
