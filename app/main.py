from __future__ import annotations

import io
import os
import random
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile
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
                    "phase_id": t.phase_id, "prompt_id": t.prompt_id,
                    "is_followup": t.is_followup, "prompt_text": t.prompt_text,
                    "response_text": t.response_text,
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
        prompts = await generate_prompts_for_module(body.module_id)
        content_id = body.module_id
        submission_id = None
        opening = format_opening_mastery_module(record["module_title"])

    first_prompt = next((p for p in prompts if p["prompt_type"] == "primary"), None)
    if not first_prompt:
        raise HTTPException(500, "No primary prompts in generated set")

    session = SessionRecord(
        product_type=pt,
        participant_id=body.participant_id,
        content_id=content_id,
        submission_id=submission_id,
        attempt_number=body.attempt_number,
        prompts=prompts,
        state={
            "primary_prompt_index": 0,
            "followups_used_count": 0,
            "used_followup_ids": [],
            "total_prompts_issued": 1,
            "awaiting_response_for": first_prompt["prompt_id"],
        },
    )
    db.add(session)
    # Flush so SessionRecord.id (a Python-side column default) is populated
    # before we reference it on the child TurnRecord. Without this, session.id
    # is None at TurnRecord construction time and the child INSERT fails with
    # NotNullViolationError on turns.session_id.
    await db.flush()
    db.add(TurnRecord(
        session_id=session.id,
        turn_number=1,
        phase_id=first_prompt["phase_id"],
        prompt_id=first_prompt["prompt_id"],
        is_followup=False,
        prompt_text=first_prompt["text"],
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
        else:
            followup_rules = (
                cb_loader.get_followup_rules()
                if session.product_type == "case_based"
                else mm_loader.get_followup_rules()
            )
            fu_dict = await decide_followup_generated(
                session.product_type, current_primary, body.response,
                "", followup_rules, followups_used + 1,
            )
            if fu_dict:
                next_followup = fu_dict

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
