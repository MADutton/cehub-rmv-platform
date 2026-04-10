from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pydantic import BaseModel

from app.database import Base

PRODUCT_TYPES = ("assigned_case", "case_based", "mastery_module")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------

class SubmissionRecord(Base):
    """Stores candidate-uploaded case submissions for Case-Based RMV."""
    __tablename__ = "submissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    candidate_id: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)  # pdf | docx | txt
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    sessions: Mapped[list[SessionRecord]] = relationship(
        "SessionRecord", back_populates="submission", foreign_keys="SessionRecord.submission_id"
    )


class SessionRecord(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    product_type: Mapped[str] = mapped_column(String(30), nullable=False)
    participant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    content_id: Mapped[str] = mapped_column(String(255), nullable=False)
    submission_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("submissions.id"), nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="active")
    prompts: Mapped[list] = mapped_column(JSON, nullable=False)
    state: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    turns: Mapped[list[TurnRecord]] = relationship(
        "TurnRecord", back_populates="session", order_by="TurnRecord.turn_number"
    )
    result: Mapped[ResultRecord | None] = relationship(
        "ResultRecord", back_populates="session", uselist=False
    )
    submission: Mapped[SubmissionRecord | None] = relationship(
        "SubmissionRecord", back_populates="sessions", foreign_keys=[submission_id]
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
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), unique=True, nullable=False)
    result_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    session: Mapped[SessionRecord] = relationship("SessionRecord", back_populates="result")


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class UploadSubmissionResponse(BaseModel):
    submission_id: str
    candidate_id: str
    original_filename: str
    file_type: str
    char_count: int


class StartSessionRequest(BaseModel):
    product_type: str
    participant_id: str
    case_id: str | None = None
    submission_id: str | None = None
    module_id: str | None = None
    attempt_number: int = 1


class StartSessionResponse(BaseModel):
    session_id: str
    product_type: str
    content_id: str
    opening_message: str
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
    product_type: str
    participant_id: str
    content_id: str
    status: str
    current_phase: str | None
    total_prompts_issued: int
    attempt_number: int
    created_at: datetime


class ResultResponse(BaseModel):
    session_id: str
    product_type: str
    status: str
    result: dict[str, Any] | None = None
