from __future__ import annotations

import asyncio
import csv
import hmac
import io
import logging
import os
import random
import secrets
import time
import uuid
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.backup import upload_backup
from app.database import SessionLocal, init_db
from app.logic import audio_buzz_metrics, clamp_ms, elapsed_ms, normalize_name, utc_now
from app.models import Attempt, Participant, QuestionResult
from app.quiz_repository import QuizRepository, QuizRepositoryError

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("fossil")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
SITE_TITLE = "Fossil Ridge Science Bowl"
ADMIN_COOKIE = "frsb_admin"
ADMIN_SESSION_MAX_AGE = 12 * 60 * 60
SESSION_SECRET = os.getenv("APP_SECRET_KEY", "local-development-only-change-this").encode("utf-8")
serializer = URLSafeTimedSerializer(SESSION_SECRET, salt="fossil-ridge-admin")
repo = QuizRepository()
BUZZ_AUDIO_STOP_MIN_MS = int(os.getenv("BUZZ_AUDIO_STOP_MIN_MS", "200"))
BUZZ_AUDIO_STOP_MAX_MS = int(os.getenv("BUZZ_AUDIO_STOP_MAX_MS", "500"))
BUZZ_END_WINDOW_MS = int(os.getenv("BUZZ_END_WINDOW_MS", "500"))


class ParticipantIn(BaseModel):
    participant_id: str = Field(min_length=16, max_length=64)
    name: str = Field(min_length=1, max_length=100)


class AttemptIn(BaseModel):
    participant_id: str
    quiz_slug: str


class HeartbeatIn(BaseModel):
    participant_id: str
    remaining_ms: int | None = None
    audio_elapsed_ms: int | None = None
    question_elapsed_ms: int | None = None
    audio_duration_ms: int | None = None
    audio_ended: bool | None = None
    answer_draft: str | None = Field(default=None, max_length=10000)


class BuzzIn(BaseModel):
    participant_id: str
    question_elapsed_ms: int = 0
    remaining_ms: int | None = None
    audio_elapsed_ms: int | None = None
    audio_duration_ms: int | None = None
    audio_stop_delay_ms: int | None = None


class SubmitIn(BaseModel):
    participant_id: str
    answer_text: str = Field(default="", max_length=10000)
    answer_time_ms: int = 0
    total_question_time_ms: int = 0
    reason: str = "enter"


class TimeoutIn(BaseModel):
    participant_id: str
    question_elapsed_ms: int = 0
    remaining_ms: int = 0


class AdvanceIn(BaseModel):
    participant_id: str


class LoginIn(BaseModel):
    username: str
    password: str


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def load_attempt(db: Session, attempt_id: str, participant_id: str, lock: bool = False) -> Attempt:
    stmt = select(Attempt).where(Attempt.id == attempt_id, Attempt.participant_id == participant_id)
    if lock:
        stmt = stmt.with_for_update()
    attempt = db.scalar(stmt)
    if attempt is None:
        raise HTTPException(404, "Attempt not found.")
    return attempt


def current_question(attempt: Attempt) -> dict:
    questions = (attempt.quiz_snapshot or {}).get("questions", [])
    if attempt.current_question_index < 0 or attempt.current_question_index >= len(questions):
        raise HTTPException(409, "There is no active question.")
    return questions[attempt.current_question_index]


def safe_attempt_payload(attempt: Attempt, include_state: bool = True) -> dict:
    payload = {
        "id": attempt.id,
        "quiz_slug": attempt.quiz_slug,
        "quiz_title": attempt.quiz_title,
        "student_name": attempt.student_name,
        "status": attempt.status,
        "current_question_index": attempt.current_question_index,
        "question_count": len((attempt.quiz_snapshot or {}).get("questions", [])),
        "phase": attempt.phase,
        "inter_question_delay_seconds": (attempt.quiz_snapshot or {}).get("inter_question_delay_seconds", 2),
    }
    if include_state and attempt.status == "in_progress":
        payload.update({
            "question": current_question(attempt),
            "phase_remaining_ms": attempt.phase_remaining_ms,
            "audio_elapsed_ms": attempt.audio_elapsed_ms,
            "question_elapsed_ms": attempt.question_elapsed_ms,
            "audio_duration_ms": attempt.audio_duration_ms,
            "audio_ended": attempt.audio_ended,
            "answer_draft": attempt.answer_draft,
        })
    return payload


def participant_or_404(db: Session, participant_id: str) -> Participant:
    person = db.get(Participant, participant_id)
    if not person:
        raise HTTPException(404, "Participant not found. Return to the home page and enter your name again.")
    return person


def start_question_state(attempt: Attempt) -> None:
    question = current_question(attempt)
    pre = question["pre_buzz"]
    has_audio = bool(question.get("audio"))
    if pre["mode"] == "from_question_start" or not has_audio:
        attempt.phase_remaining_ms = int(float(pre["seconds"]) * 1000)
    else:
        attempt.phase_remaining_ms = None
    attempt.phase = "question"
    attempt.audio_elapsed_ms = 0
    attempt.question_elapsed_ms = 0
    attempt.audio_duration_ms = None
    attempt.audio_ended = not has_audio
    attempt.answer_draft = ""
    attempt.question_started_at = utc_now()


def transition_after_result(attempt: Attempt) -> None:
    delay = float((attempt.quiz_snapshot or {}).get("inter_question_delay_seconds", 2))
    attempt.phase = "transition"
    attempt.phase_remaining_ms = int(delay * 1000)
    attempt.answer_draft = ""
    attempt.audio_elapsed_ms = 0
    attempt.question_elapsed_ms = 0
    attempt.audio_duration_ms = None
    attempt.audio_ended = False


def admin_is_logged_in(request: Request) -> bool:
    token = request.cookies.get(ADMIN_COOKIE)
    if not token:
        return False
    try:
        data = serializer.loads(token, max_age=ADMIN_SESSION_MAX_AGE)
        return data.get("user") == os.getenv("ADMIN_USERNAME", "admin")
    except (BadSignature, SignatureExpired, AttributeError):
        return False


def require_admin(request: Request) -> None:
    if not admin_is_logged_in(request):
        raise HTTPException(401, "Administrator login required.")


_backup_change_lock = threading.Lock()
_backup_last_change = 0.0


def mark_backup_dirty() -> None:
    """Record that durable application data changed; thread-safe for sync FastAPI handlers."""
    global _backup_last_change
    with _backup_change_lock:
        _backup_last_change = time.monotonic()


def backup_change_stamp() -> float:
    with _backup_change_lock:
        return _backup_last_change


async def backup_loop():
    # Make one startup snapshot, then only snapshot after the database has been quiet.
    # Poll this in-memory marker, not PostgreSQL, so the loop does not keep Neon awake.
    quiet_period = max(30, int(os.getenv("B2_BACKUP_QUIET_PERIOD_SECONDS", "300")))
    await asyncio.sleep(max(10, int(os.getenv("B2_BACKUP_INITIAL_DELAY_SECONDS", "90"))))
    saved_change_stamp = backup_change_stamp()
    initial_pending = True
    last_attempt = 0.0
    try:
        await asyncio.to_thread(upload_backup)
        initial_pending = False
    except Exception:
        last_attempt = time.monotonic()
        log.exception("Initial B2 backup failed; it will retry. PostgreSQL remains the primary store.")
    while True:
        await asyncio.sleep(10)
        changed_at = backup_change_stamp()
        has_new_data = changed_at > saved_change_stamp
        if not initial_pending and not has_new_data:
            continue
        now = time.monotonic()
        # After startup, defer changed-data snapshots until there have been no writes
        # for the full quiet period. If the initial backup failed, retry it after 60s.
        if has_new_data and now - changed_at < quiet_period:
            continue
        if now - last_attempt < 60:
            continue
        try:
            await asyncio.to_thread(upload_backup)
            initial_pending = False
            # If data changed while the upload was in progress, the newer timestamp
            # remains greater than this value and will trigger another quiet-period pass.
            saved_change_stamp = changed_at
        except Exception:
            last_attempt = time.monotonic()
            log.exception("B2 quiet-period backup failed; it will retry after the retry delay.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("APP_ENV", "development").lower() == "production":
        required = ["DATABASE_URL", "ADMIN_PASSWORD", "APP_SECRET_KEY", "GITHUB_REPOSITORY",
                    "B2_ENDPOINT", "B2_BUCKET_NAME", "B2_KEY_ID", "B2_APPLICATION_KEY"]
        missing = [key for key in required if not os.getenv(key)]
        if missing:
            raise RuntimeError("Missing required production environment variables: " + ", ".join(missing))
    init_db()
    if not os.getenv("B2_BUCKET_NAME"):
        log.warning("B2 backups are not configured. PostgreSQL remains the primary persistent store.")
    task = asyncio.create_task(backup_loop())
    log.info("Fossil Ridge Science Bowl started.")
    try:
        yield
    finally:
        task.cancel()
        try:
            await asyncio.to_thread(upload_backup)
        except Exception:
            log.exception("Final B2 backup attempt failed.")


app = FastAPI(title=SITE_TITLE, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
def health(db: Session = Depends(get_db)):
    # Touch the DB so this checks the real data path, not only the web process.
    db.execute(select(1))
    return {"status": "ok", "service": SITE_TITLE}


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/admin")
def admin_page(request: Request):
    if admin_is_logged_in(request):
        return FileResponse(STATIC_DIR / "admin.html")
    return FileResponse(STATIC_DIR / "admin-login.html")


@app.post("/api/admin/login")
def admin_login(body: LoginIn, request: Request, response: Response):
    expected_user = os.getenv("ADMIN_USERNAME", "admin")
    expected_password = os.getenv("ADMIN_PASSWORD", "")
    if not expected_password:
        raise HTTPException(503, "ADMIN_PASSWORD has not been configured on the server.")
    user_ok = hmac.compare_digest(body.username.encode(), expected_user.encode())
    password_ok = hmac.compare_digest(body.password.encode(), expected_password.encode())
    if not (user_ok and password_ok):
        raise HTTPException(401, "Username or password is incorrect.")
    token = serializer.dumps({"user": expected_user, "issued": int(time.time())})
    response.set_cookie(ADMIN_COOKIE, token, max_age=ADMIN_SESSION_MAX_AGE, httponly=True,
                        secure=(request.url.scheme == "https"), samesite="strict", path="/")
    return {"ok": True}


@app.post("/api/admin/logout")
def admin_logout(response: Response):
    response.delete_cookie(ADMIN_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/admin/me")
def admin_me(request: Request):
    require_admin(request)
    return {"ok": True, "username": os.getenv("ADMIN_USERNAME", "admin")}


@app.post("/api/participants")
def register_participant(body: ParticipantIn, db: Session = Depends(get_db)):
    name = " ".join(body.name.split())
    normalized = normalize_name(name)
    if not normalized:
        raise HTTPException(422, "Enter a name.")
    person = db.get(Participant, body.participant_id)
    if person:
        if name != person.name:
            has_attempt = db.scalar(select(Attempt.id).where(Attempt.participant_id == person.id).limit(1))
            if has_attempt:
                raise HTTPException(409, "Your name is locked after starting a quiz. Contact the administrator if it needs correcting.")
        person.name = name
        person.normalized_name = normalized
        person.updated_at = utc_now()
    else:
        person = Participant(id=body.participant_id, name=name, normalized_name=normalized)
        db.add(person)
    try:
        db.commit()
        mark_backup_dirty()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "That participant ID could not be saved. Try refreshing the page.")
    return {"participant_id": person.id, "name": person.name}


@app.get("/api/quizzes")
def list_quizzes(participant_id: str | None = None, db: Session = Depends(get_db)):
    try:
        quizzes = repo.list_quizzes()
    except QuizRepositoryError as exc:
        raise HTTPException(503, str(exc))
    statuses: dict[str, str] = {}
    if participant_id:
        person = db.get(Participant, participant_id)
        if person:
            attempts = db.scalars(select(Attempt).where(Attempt.normalized_name == normalize_name(person.name))).all()
            statuses = {a.quiz_slug: ("completed" if a.status == "completed" else "in_progress") for a in attempts}
    for quiz in quizzes:
        quiz["student_status"] = statuses.get(quiz["slug"], "not_started")
    return {"quizzes": quizzes}


@app.post("/api/attempts")
def create_attempt(body: AttemptIn, db: Session = Depends(get_db)):
    person = participant_or_404(db, body.participant_id)
    try:
        quiz = repo.get_quiz(body.quiz_slug)
    except QuizRepositoryError as exc:
        raise HTTPException(404, str(exc))
    normalized = normalize_name(person.name)
    existing = db.scalar(select(Attempt).where(Attempt.quiz_slug == quiz["slug"], Attempt.normalized_name == normalized))
    if existing:
        if existing.participant_id != person.id:
            raise HTTPException(409, "A person using this name has already started this quiz. Use your own name or ask the administrator for help.")
        return safe_attempt_payload(existing)
    attempt = Attempt(
        id=str(uuid.uuid4()), quiz_slug=quiz["slug"], quiz_title=quiz["title"], quiz_snapshot=quiz,
        participant_id=person.id, student_name=person.name, normalized_name=normalized,
        status="in_progress", current_question_index=0, phase="question",
        question_started_at=utc_now(),
    )
    db.add(attempt)
    start_question_state(attempt)
    try:
        db.commit()
        mark_backup_dirty()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This quiz has already been started under that name.")
    db.refresh(attempt)
    return safe_attempt_payload(attempt)


@app.get("/api/attempts/{attempt_id}")
def get_attempt(attempt_id: str, participant_id: str, db: Session = Depends(get_db)):
    attempt = get_attempt_row(db, attempt_id, participant_id)
    return safe_attempt_payload(attempt)


def get_attempt_row(db: Session, attempt_id: str, participant_id: str) -> Attempt:
    return load_attempt(db, attempt_id, participant_id)


@app.get("/api/attempts/{attempt_id}/audio/{audio_path:path}")
def attempt_audio(attempt_id: str, audio_path: str, x_participant_id: str = Header(default=""), db: Session = Depends(get_db)):
    attempt = load_attempt(db, attempt_id, x_participant_id)
    if attempt.status != "in_progress":
        raise HTTPException(404, "Audio is unavailable for a completed attempt.")
    question = current_question(attempt)
    if question.get("audio") != audio_path:
        raise HTTPException(404, "Audio is not part of the current question.")
    try:
        allowed_paths = {q["audio"] for q in (attempt.quiz_snapshot or {}).get("questions", []) if q.get("audio")}
        body, content_type = repo.get_audio(
            attempt.quiz_slug, audio_path, allowed_paths=allowed_paths,
            source_ref=(attempt.quiz_snapshot or {}).get("source_ref"),
        )
    except QuizRepositoryError as exc:
        raise HTTPException(404, str(exc))
    return Response(body, media_type=content_type, headers={"Cache-Control": "private, max-age=3600", "Accept-Ranges": "none"})


@app.post("/api/attempts/{attempt_id}/heartbeat")
def heartbeat(attempt_id: str, body: HeartbeatIn, db: Session = Depends(get_db)):
    attempt = load_attempt(db, attempt_id, body.participant_id, lock=True)
    if attempt.status != "in_progress":
        return {"ok": True, "status": attempt.status}
    if body.remaining_ms is not None:
        attempt.phase_remaining_ms = clamp_ms(body.remaining_ms)
    if body.audio_elapsed_ms is not None:
        attempt.audio_elapsed_ms = clamp_ms(body.audio_elapsed_ms, 0, 6 * 60 * 60 * 1000)
    if body.audio_duration_ms is not None:
        attempt.audio_duration_ms = clamp_ms(body.audio_duration_ms, 0, 6 * 60 * 60 * 1000)
    if body.question_elapsed_ms is not None:
        attempt.question_elapsed_ms = clamp_ms(body.question_elapsed_ms, 0, 6 * 60 * 60 * 1000)
    if body.audio_ended is not None:
        attempt.audio_ended = bool(body.audio_ended)
    if body.answer_draft is not None:
        attempt.answer_draft = body.answer_draft
        result = db.scalar(select(QuestionResult).where(
            QuestionResult.attempt_id == attempt.id,
            QuestionResult.question_index == attempt.current_question_index,
        ))
        if result and result.status == "buzzed":
            result.answer_text = body.answer_draft
    attempt.updated_at = utc_now()
    db.commit()
    mark_backup_dirty()
    return {"ok": True}


@app.post("/api/attempts/{attempt_id}/audio-ended")
def audio_ended(attempt_id: str, body: HeartbeatIn, db: Session = Depends(get_db)):
    attempt = load_attempt(db, attempt_id, body.participant_id, lock=True)
    if attempt.status != "in_progress" or attempt.phase != "question":
        return safe_attempt_payload(attempt)
    question = current_question(attempt)
    already_ended = attempt.audio_ended
    attempt.audio_ended = True
    if question["pre_buzz"]["mode"] == "after_audio" and not already_ended:
        attempt.phase_remaining_ms = int(float(question["pre_buzz"]["seconds"]) * 1000)
    if body.audio_elapsed_ms is not None:
        attempt.audio_elapsed_ms = clamp_ms(body.audio_elapsed_ms)
    if body.audio_duration_ms is not None:
        attempt.audio_duration_ms = clamp_ms(body.audio_duration_ms)
    db.commit()
    mark_backup_dirty()
    return {"ok": True, "phase_remaining_ms": attempt.phase_remaining_ms}


@app.post("/api/attempts/{attempt_id}/buzz")
def buzz(attempt_id: str, body: BuzzIn, db: Session = Depends(get_db)):
    attempt = load_attempt(db, attempt_id, body.participant_id, lock=True)
    if attempt.status != "in_progress":
        raise HTTPException(409, "The buzzer is not active.")
    if attempt.phase == "answer":
        existing_result = db.scalar(select(QuestionResult).where(
            QuestionResult.attempt_id == attempt.id,
            QuestionResult.question_index == attempt.current_question_index,
        ))
        if existing_result and existing_result.status == "buzzed":
            question = current_question(attempt)
            return {"ok": True, "answer_seconds": question["answer_seconds"],
                    "audio_stop_delay_ms": existing_result.audio_stop_delay_ms}
    if attempt.phase != "question":
        raise HTTPException(409, "The buzzer is not active.")
    question = current_question(attempt)
    if question["pre_buzz"]["mode"] == "from_question_start" and attempt.phase_remaining_ms is not None and attempt.phase_remaining_ms <= 0:
        raise HTTPException(409, "The buzzer window has ended.")
    if question["pre_buzz"]["mode"] == "after_audio" and attempt.audio_ended and attempt.phase_remaining_ms is not None and attempt.phase_remaining_ms <= 0:
        raise HTTPException(409, "The buzzer window has ended.")
    if body.remaining_ms is not None and body.remaining_ms <= 0:
        raise HTTPException(409, "The buzzer window has ended.")
    now = utc_now()
    audio_elapsed = clamp_ms(body.audio_elapsed_ms) if body.audio_elapsed_ms is not None else None
    duration = clamp_ms(body.audio_duration_ms) if body.audio_duration_ms is not None else attempt.audio_duration_ms
    metrics = audio_buzz_metrics(duration, audio_elapsed, BUZZ_END_WINDOW_MS)
    requested_delay = body.audio_stop_delay_ms if body.audio_stop_delay_ms is not None else random.randint(BUZZ_AUDIO_STOP_MIN_MS, BUZZ_AUDIO_STOP_MAX_MS)
    stop_delay = max(BUZZ_AUDIO_STOP_MIN_MS, min(BUZZ_AUDIO_STOP_MAX_MS, int(requested_delay)))
    result = db.scalar(select(QuestionResult).where(
        QuestionResult.attempt_id == attempt.id,
        QuestionResult.question_index == attempt.current_question_index,
    ))
    if result is None:
        result = QuestionResult(
            attempt_id=attempt.id, question_index=attempt.current_question_index,
            question_id=question["id"], prompt=question["prompt"], status="buzzed",
            answer_text="", question_started_at=attempt.question_started_at,
        )
        db.add(result)
    result.status = "buzzed"
    result.buzzed_at = now
    result.time_to_buzz_ms = clamp_ms(body.question_elapsed_ms, 0, 6 * 60 * 60 * 1000)
    attempt.question_elapsed_ms = result.time_to_buzz_ms
    result.audio_duration_ms = duration
    result.audio_elapsed_at_buzz_ms = audio_elapsed
    result.audio_remaining_at_buzz_ms = metrics["audio_remaining_at_buzz_ms"]
    result.buzz_before_audio_end = metrics["buzz_before_audio_end"]
    result.buzz_within_500ms_before_audio_end = metrics["buzz_within_window_before_audio_end"]
    result.audio_stop_delay_ms = stop_delay
    attempt.phase = "answer"
    attempt.phase_remaining_ms = int(float(question["answer_seconds"]) * 1000)
    attempt.answer_draft = ""
    attempt.audio_elapsed_ms = audio_elapsed or 0
    attempt.audio_duration_ms = duration
    attempt.updated_at = now
    db.commit()
    mark_backup_dirty()
    return {"ok": True, "answer_seconds": question["answer_seconds"], "audio_stop_delay_ms": stop_delay}


@app.post("/api/attempts/{attempt_id}/submit")
def submit_answer(attempt_id: str, body: SubmitIn, db: Session = Depends(get_db)):
    attempt = load_attempt(db, attempt_id, body.participant_id, lock=True)
    if attempt.status != "in_progress":
        raise HTTPException(409, "This attempt is no longer active.")
    if attempt.phase != "answer":
        # Idempotent retry after a successful submission/transition.
        prior = db.scalar(select(QuestionResult).where(
            QuestionResult.attempt_id == attempt.id,
            QuestionResult.question_index == attempt.current_question_index,
        ))
        if attempt.phase == "transition" and prior and prior.status in {"answered", "answer_timeout"}:
            return safe_attempt_payload(attempt)
        raise HTTPException(409, "There is no answer window open.")
    question = current_question(attempt)
    result = db.scalar(select(QuestionResult).where(
        QuestionResult.attempt_id == attempt.id,
        QuestionResult.question_index == attempt.current_question_index,
    ))
    if result is None or result.status != "buzzed":
        raise HTTPException(409, "The buzz was not recorded. Please contact the administrator.")
    now = utc_now()
    answer = body.answer_text
    reason = "answer_timeout" if body.reason == "timeout" else "answered"
    result.status = reason
    result.answer_text = answer
    result.submitted_at = now
    result.answer_time_ms = clamp_ms(body.answer_time_ms, 0, int(float(question["answer_seconds"]) * 1000) + 2000)
    result.total_question_time_ms = clamp_ms(body.total_question_time_ms, 0, 6 * 60 * 60 * 1000)
    attempt.answer_draft = answer
    transition_after_result(attempt)
    attempt.updated_at = now
    db.commit()
    mark_backup_dirty()
    return safe_attempt_payload(attempt)


@app.post("/api/attempts/{attempt_id}/no-buzz")
def no_buzz(attempt_id: str, body: TimeoutIn, db: Session = Depends(get_db)):
    attempt = load_attempt(db, attempt_id, body.participant_id, lock=True)
    if attempt.status != "in_progress":
        raise HTTPException(409, "Attempt is not active.")
    if attempt.phase == "transition":
        prior = db.scalar(select(QuestionResult).where(
            QuestionResult.attempt_id == attempt.id,
            QuestionResult.question_index == attempt.current_question_index,
        ))
        if prior and prior.status == "no_buzz":
            return safe_attempt_payload(attempt)
    if attempt.phase != "question":
        raise HTTPException(409, "The pre-buzz window is not active.")
    if body.remaining_ms > 0:
        raise HTTPException(409, "The pre-buzz window has not expired yet.")
    question = current_question(attempt)
    if question["pre_buzz"]["mode"] == "after_audio" and question.get("audio") and not attempt.audio_ended:
        raise HTTPException(409, "The audio has not finished, so the pre-buzz window has not expired.")
    now = utc_now()
    result = db.scalar(select(QuestionResult).where(
        QuestionResult.attempt_id == attempt.id,
        QuestionResult.question_index == attempt.current_question_index,
    ))
    if result is None:
        result = QuestionResult(
            attempt_id=attempt.id, question_index=attempt.current_question_index,
            question_id=question["id"], prompt=question["prompt"], status="no_buzz",
            answer_text="", question_started_at=attempt.question_started_at,
        )
        db.add(result)
    else:
        raise HTTPException(409, "A buzz has already been recorded for this question.")
    result.status = "no_buzz"
    result.total_question_time_ms = clamp_ms(body.question_elapsed_ms, 0, 6 * 60 * 60 * 1000)
    attempt.question_elapsed_ms = result.total_question_time_ms
    result.submitted_at = now
    transition_after_result(attempt)
    db.commit()
    mark_backup_dirty()
    return safe_attempt_payload(attempt)


@app.post("/api/attempts/{attempt_id}/advance")
def advance(attempt_id: str, body: AdvanceIn, db: Session = Depends(get_db)):
    attempt = load_attempt(db, attempt_id, body.participant_id, lock=True)
    if attempt.status != "in_progress" or attempt.phase != "transition":
        raise HTTPException(409, "The attempt is not between questions.")
    question_count = len((attempt.quiz_snapshot or {}).get("questions", []))
    if attempt.current_question_index + 1 >= question_count:
        attempt.status = "completed"
        attempt.phase = "complete"
        attempt.phase_remaining_ms = 0
        attempt.completed_at = utc_now()
        attempt.updated_at = utc_now()
        db.commit()
        mark_backup_dirty()
        return safe_attempt_payload(attempt, include_state=False)
    attempt.current_question_index += 1
    start_question_state(attempt)
    attempt.updated_at = utc_now()
    db.commit()
    mark_backup_dirty()
    return safe_attempt_payload(attempt)


def _admin_attempt_query(db: Session, quiz_slug: str | None = None, status: str | None = None):
    stmt = select(Attempt).order_by(Attempt.created_at.desc())
    if quiz_slug:
        stmt = stmt.where(Attempt.quiz_slug == quiz_slug)
    if status:
        stmt = stmt.where(Attempt.status == status)
    return db.scalars(stmt).all()


@app.get("/api/admin/summary")
def admin_summary(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    attempts = db.scalars(select(Attempt)).all()
    return {
        "participants": len(db.scalars(select(Participant.id)).all()),
        "attempts": len(attempts),
        "completed": sum(a.status == "completed" for a in attempts),
        "in_progress": sum(a.status == "in_progress" for a in attempts),
        "answers": len(db.scalars(select(QuestionResult.id)).all()),
    }


@app.get("/api/admin/attempts")
def admin_attempts(request: Request, quiz_slug: str | None = None, status: str | None = None, db: Session = Depends(get_db)):
    require_admin(request)
    attempts = _admin_attempt_query(db, quiz_slug, status)
    return {"attempts": [
        {"id": a.id, "quiz_slug": a.quiz_slug, "quiz_title": a.quiz_title,
         "student_name": a.student_name, "status": a.status,
         "question_count": len((a.quiz_snapshot or {}).get("questions", [])),
         "current_question": a.current_question_index + 1 if a.status == "in_progress" else None,
         "created_at": a.created_at.isoformat(),
         "completed_at": a.completed_at.isoformat() if a.completed_at else None}
        for a in attempts
    ]}


@app.get("/api/admin/attempts/{attempt_id}")
def admin_attempt_detail(attempt_id: str, request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    attempt = db.get(Attempt, attempt_id)
    if not attempt:
        raise HTTPException(404, "Attempt not found.")
    results = db.scalars(select(QuestionResult).where(QuestionResult.attempt_id == attempt.id).order_by(QuestionResult.question_index)).all()
    return {
        "attempt": {"id": attempt.id, "quiz_title": attempt.quiz_title, "quiz_slug": attempt.quiz_slug,
                    "student_name": attempt.student_name, "status": attempt.status,
                    "created_at": attempt.created_at.isoformat(),
                    "completed_at": attempt.completed_at.isoformat() if attempt.completed_at else None},
        "results": [
            {"question_number": r.question_index + 1, "question_id": r.question_id, "prompt": r.prompt,
             "status": r.status, "answer_text": r.answer_text,
             "time_to_buzz_ms": r.time_to_buzz_ms, "answer_time_ms": r.answer_time_ms,
             "total_question_time_ms": r.total_question_time_ms,
             "audio_duration_ms": r.audio_duration_ms,
             "audio_elapsed_at_buzz_ms": r.audio_elapsed_at_buzz_ms,
             "audio_remaining_at_buzz_ms": r.audio_remaining_at_buzz_ms,
             "buzz_before_audio_end": r.buzz_before_audio_end,
             "buzz_within_500ms_before_audio_end": r.buzz_within_500ms_before_audio_end,
             "audio_stop_delay_ms": r.audio_stop_delay_ms,
             "buzzed_at": r.buzzed_at.isoformat() if r.buzzed_at else None,
             "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None}
            for r in results
        ],
    }


@app.get("/api/admin/export.csv")
def admin_export(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "student_name", "quiz_title", "quiz_slug", "attempt_status", "question_number", "question_id",
        "prompt", "question_status", "answer_text", "time_to_buzz_ms", "answer_time_ms",
        "total_question_time_ms", "audio_duration_ms", "audio_elapsed_at_buzz_ms",
        "audio_remaining_at_buzz_ms", "buzz_before_audio_end", "buzz_within_500ms_before_audio_end",
        "audio_stop_delay_ms", "buzzed_at", "submitted_at", "attempt_created_at", "attempt_completed_at",
    ])
    attempts = _admin_attempt_query(db)
    for a in attempts:
        results = db.scalars(select(QuestionResult).where(QuestionResult.attempt_id == a.id).order_by(QuestionResult.question_index)).all()
        if not results:
            writer.writerow([a.student_name, a.quiz_title, a.quiz_slug, a.status, "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", a.created_at.isoformat(), a.completed_at.isoformat() if a.completed_at else ""])
        for r in results:
            writer.writerow([
                a.student_name, a.quiz_title, a.quiz_slug, a.status, r.question_index + 1, r.question_id,
                r.prompt, r.status, r.answer_text, r.time_to_buzz_ms, r.answer_time_ms,
                r.total_question_time_ms, r.audio_duration_ms, r.audio_elapsed_at_buzz_ms,
                r.audio_remaining_at_buzz_ms, r.buzz_before_audio_end, r.buzz_within_500ms_before_audio_end,
                r.audio_stop_delay_ms, r.buzzed_at.isoformat() if r.buzzed_at else "",
                r.submitted_at.isoformat() if r.submitted_at else "", a.created_at.isoformat(),
                a.completed_at.isoformat() if a.completed_at else "",
            ])
    data = "\ufeff" + output.getvalue()
    return Response(data, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=fossil-science-bowl-results.csv"})


@app.post("/api/admin/backup-now")
def admin_backup_now(request: Request):
    require_admin(request)
    try:
        uploaded = upload_backup(force=True)
    except Exception as exc:
        log.exception("Manual B2 backup failed.")
        raise HTTPException(502, f"B2 backup failed: {exc}")
    if not uploaded:
        raise HTTPException(503, "B2 is not configured. Set the B2 environment variables first.")
    return {"ok": True, "message": "Database backup uploaded to B2."}
