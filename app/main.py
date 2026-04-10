from __future__ import annotations

import random
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.case_loader import (
    get_active_cases,
    get_case_detail,
    get_case_stem,
    get_exam_product,
    get_primary_prompts,
)
from app.database import AsyncSessionLocal, get_db, init_db
from app.examiner import decide_followup, format_opening
from app.models import (
    PromptResponse,
    ResultRecord,
    ResultResponse,
    SessionRecord,
    SessionStateResponse,
    StartSessionRequest,
    StartSessionResponse,
    SubmitResponseRequest,
    TurnRecord,
)
from app.scorer import score_session


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Assigned Case RMV",
    description="Standardized, rubric-driven AI oral examination for veterinary specialists.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Background scoring task
# ---------------------------------------------------------------------------

async def _score_session_background(session_id: str) -> None:
    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                select(SessionRecord)
                .options(selectinload(SessionRecord.turns))
                .where(SessionRecord.id == session_id)
            )
            session = result.scalar_one_or_none()
            if not session or session.status != "complete":
                return

            completed_at = session.completed_at or datetime.now(timezone.utc)
            created_at = session.created_at
            duration = (completed_at - created_at).total_seconds() / 60

            turns_data = [
                {
                    "phase_id": t.phase_id,
                    "prompt_id": t.prompt_id,
                    "is_followup": t.is_followup,
                    "prompt_text": t.prompt_text,
                    "response_text": t.response_text,
                }
                for t in session.turns
            ]

            result_data = await score_session(
                attempt_id=session_id,
                candidate_id=session.candidate_id,
                case_id=session.case_id,
                turns=turns_data,
                duration_minutes=round(duration, 1),
            )

            record = ResultRecord(session_id=session_id, result_data=result_data)
            db.add(record)
            session.status = "scored"
            await db.commit()

        except Exception as exc:
            print(f"[scorer] Error scoring session {session_id}: {exc}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/cases")
async def list_cases():
    return {"cases": get_active_cases()}


@app.post("/sessions", response_model=StartSessionResponse, status_code=201)
async def start_session(
    body: StartSessionRequest,
    db: AsyncSession = Depends(get_db),
):
    # Resolve case
    if body.case_id:
        try:
            get_case_detail(body.case_id)
        except FileNotFoundError:
            raise HTTPException(404, f"Case '{body.case_id}' not found")
        case_id = body.case_id
    else:
        active = get_active_cases()
        if not active:
            raise HTTPException(500, "No active cases available")
        case_id = random.choice(active)["case_id"]

    primary_prompts = get_primary_prompts(case_id)
    if not primary_prompts:
        raise HTTPException(500, f"No prompts defined for case '{case_id}'")

    first_prompt = primary_prompts[0]
    case_stem = get_case_stem(case_id)

    session = SessionRecord(
        candidate_id=body.candidate_id,
        case_id=case_id,
        status="active",
        state={
            "primary_prompt_index": 0,
            "followups_used_count": 0,
            "used_followup_ids": [],
            "total_prompts_issued": 1,
            "awaiting_response_for": first_prompt["prompt_id"],
        },
    )
    db.add(session)

    turn = TurnRecord(
        session_id=session.id,
        turn_number=1,
        phase_id=first_prompt["phase_id"],
        prompt_id=first_prompt["prompt_id"],
        is_followup=False,
        prompt_text=first_prompt["text"],
    )
    db.add(turn)
    await db.commit()

    return StartSessionResponse(
        session_id=session.id,
        case_id=case_id,
        opening_message=format_opening(case_stem),
        case_stem=case_stem,
        first_prompt=first_prompt["text"],
        phase=first_prompt["phase_id"],
    )


@app.get("/sessions/{session_id}", response_model=SessionStateResponse)
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SessionRecord).where(SessionRecord.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Session not found")

    state = session.state
    current_phase = None
    if session.status == "active":
        primary_prompts = get_primary_prompts(session.case_id)
        idx = state.get("primary_prompt_index", 0)
        if idx < len(primary_prompts):
            current_phase = primary_prompts[idx]["phase_id"]

    return SessionStateResponse(
        session_id=session.id,
        candidate_id=session.candidate_id,
        case_id=session.case_id,
        status=session.status,
        current_phase=current_phase,
        total_prompts_issued=state.get("total_prompts_issued", 0),
        created_at=session.created_at,
    )


@app.post("/sessions/{session_id}/respond", response_model=PromptResponse)
async def respond(
    session_id: str,
    body: SubmitResponseRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SessionRecord)
        .options(selectinload(SessionRecord.turns))
        .where(SessionRecord.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Session not found")
    if session.status != "active":
        raise HTTPException(400, f"Session is '{session.status}', not active")

    state = session.state
    awaiting_id = state["awaiting_response_for"]

    # Record the response on the awaiting turn
    current_turn = next(
        (t for t in session.turns if t.prompt_id == awaiting_id and t.response_text is None),
        None,
    )
    if not current_turn:
        raise HTTPException(500, "No open turn found for current prompt")

    current_turn.response_text = body.response

    # Determine next action
    product = get_exam_product()
    max_followups = product.get("max_followups_per_prompt", 2)
    max_total = product.get("max_total_prompts", 10)

    primary_prompts = get_primary_prompts(session.case_id)
    primary_index = state["primary_prompt_index"]
    current_primary = primary_prompts[primary_index]
    followups_used = state["followups_used_count"]
    used_ids = state["used_followup_ids"]
    total_issued = state["total_prompts_issued"]

    # Filter to unused follow-ups for the current primary prompt
    all_followups = current_primary.get("followups", [])
    available_followups = [fu for fu in all_followups if fu["followup_id"] not in used_ids]

    next_followup = None
    if followups_used < max_followups and total_issued < max_total and available_followups:
        next_followup = await decide_followup(
            current_primary_prompt=current_primary,
            candidate_response=body.response,
            followups_used_in_phase=followups_used,
            total_prompts_issued=total_issued,
            available_followups=available_followups,
        )

    if next_followup:
        new_total = total_issued + 1
        session.state = {
            **state,
            "followups_used_count": followups_used + 1,
            "used_followup_ids": used_ids + [next_followup["followup_id"]],
            "total_prompts_issued": new_total,
            "awaiting_response_for": next_followup["followup_id"],
        }
        db.add(TurnRecord(
            session_id=session_id,
            turn_number=new_total,
            phase_id=current_primary["phase_id"],
            prompt_id=next_followup["followup_id"],
            is_followup=True,
            prompt_text=next_followup["text"],
        ))
        await db.commit()
        return PromptResponse(
            done=False,
            next_prompt=next_followup["text"],
            phase=current_primary["phase_id"],
            prompt_id=next_followup["followup_id"],
            is_followup=True,
        )

    # Advance to next primary prompt
    next_index = primary_index + 1

    if next_index >= len(primary_prompts):
        # All phases complete — close session and trigger scoring
        session.status = "complete"
        session.completed_at = datetime.now(timezone.utc)
        session.state = {**state, "primary_prompt_index": primary_index}
        await db.commit()

        background_tasks.add_task(_score_session_background, session_id)

        closing = (
            "That concludes the examination. Thank you for your participation. "
            "Your responses will be reviewed and scored. "
            "You will receive your results through the standard reporting process."
        )
        return PromptResponse(done=True, next_prompt=closing, phase=None, prompt_id=None, is_followup=False)

    # Issue next primary prompt
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
        session_id=session_id,
        turn_number=new_total,
        phase_id=next_primary["phase_id"],
        prompt_id=next_primary["prompt_id"],
        is_followup=False,
        prompt_text=next_primary["text"],
    ))
    await db.commit()

    return PromptResponse(
        done=False,
        next_prompt=next_primary["text"],
        phase=next_primary["phase_id"],
        prompt_id=next_primary["prompt_id"],
        is_followup=False,
    )


@app.get("/sessions/{session_id}/result", response_model=ResultResponse)
async def get_result(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SessionRecord)
        .options(selectinload(SessionRecord.result))
        .where(SessionRecord.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Session not found")
    if session.status == "active":
        raise HTTPException(400, "Session is still active")
    if session.status == "complete":
        return ResultResponse(session_id=session_id, status="scoring", result=None)
    if session.result is None:
        raise HTTPException(500, "Session marked scored but result record missing")

    return ResultResponse(
        session_id=session_id,
        status="scored",
        result=session.result.result_data,
    )
