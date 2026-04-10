from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pydantic import BaseModel

from app.database import Base


# ---------------------------------------------------------------------------
# SQLAlchemy ORM models
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class SessionRecord(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    candidate_id: Mapped[str] = mapped_column(String(255), nullable=False)
    case_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | complete | scored
    state: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    turns: Mapped[list[TurnRecord]] = relationship(
        "TurnRecord", back_populates="session", order_by="TurnRecord.turn_number"
    )
    result: Mapped[ResultRecord | None] = relationship(
        "ResultRecord", back_populates="session", uselist=False
    )


class TurnRecord(Base):
    __tablename__ = "turns"
    __table_args__ = (Index("ix_turns_session_id", "session_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), nullable=False)
    turn_number: Mapped[int] = mapped_column(Integer, nullable=False)
    phase_id: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_id: Mapped[str] = mapped_column(String(100), nullable=False)
    is_followup: Mapped[bool] = mapped_column(Boolean, default=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    session: Mapped[SessionRecord] = relationship("SessionRecord", back_populates="turns")


class ResultRecord(Base):
    __tablename__ = "results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id"), unique=True, nullable=False
    )
    result_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    session: Mapped[SessionRecord] = relationship("SessionRecord", back_populates="result")


# ---------------------------------------------------------------------------
# Pydantic request / response schemas
# ---------------------------------------------------------------------------

class StartSessionRequest(BaseModel):
    candidate_id: str
    case_id: str | None = None


class StartSessionResponse(BaseModel):
    session_id: str
    case_id: str
    opening_message: str
    case_stem: dict[str, Any]
    first_prompt: str
    phase: str


class SubmitResponseRequest(BaseModel):
    response: str


class PromptResponse(BaseModel):
    done: bool
    next_prompt: str | None
    phase: str | None
    prompt_id: str | None
    is_followup: bool


class SessionStateResponse(BaseModel):
    session_id: str
    candidate_id: str
    case_id: str
    status: str
    current_phase: str | None
    total_prompts_issued: int
    created_at: datetime


class ResultResponse(BaseModel):
    session_id: str
    status: str
    result: dict[str, Any] | None = None
