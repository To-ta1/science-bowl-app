from __future__ import annotations

import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON
from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    attempts: Mapped[list["Attempt"]] = relationship(back_populates="participant")


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (
        UniqueConstraint("quiz_slug", "normalized_name", name="uq_attempt_quiz_normalized_name"),
        Index("ix_attempts_quiz_status", "quiz_slug", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    quiz_slug: Mapped[str] = mapped_column(String(100), nullable=False)
    quiz_title: Mapped[str] = mapped_column(String(200), nullable=False)
    quiz_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    participant_id: Mapped[str] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), nullable=False, index=True)
    student_name: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="in_progress", nullable=False)
    current_question_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    phase: Mapped[str] = mapped_column(String(20), default="question", nullable=False)
    phase_remaining_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_elapsed_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    question_elapsed_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    audio_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_ended: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    answer_draft: Mapped[str] = mapped_column(Text, default="", nullable=False)
    question_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    participant: Mapped[Participant] = relationship(back_populates="attempts")
    results: Mapped[list["QuestionResult"]] = relationship(back_populates="attempt", cascade="all, delete-orphan")


class QuestionResult(Base):
    __tablename__ = "question_results"
    __table_args__ = (UniqueConstraint("attempt_id", "question_index", name="uq_result_attempt_question"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), nullable=False, index=True)
    question_index: Mapped[int] = mapped_column(Integer, nullable=False)
    question_id: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="buzzed")
    answer_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    question_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    buzzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    time_to_buzz_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    answer_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_question_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_elapsed_at_buzz_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_remaining_at_buzz_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    buzz_before_audio_end: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    buzz_within_500ms_before_audio_end: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    audio_stop_delay_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    keystroke_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    attempt: Mapped[Attempt] = relationship(back_populates="results")
