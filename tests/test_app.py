from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["DATABASE_URL"] = f"sqlite:///{ROOT / 'test-run.db'}"
os.environ["QUIZ_CONTENT_DIR"] = str(ROOT / "quiz-repository-template" / "quizzes")
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "test-password"
os.environ["APP_SECRET_KEY"] = "test-secret-not-for-production"
os.environ["COOKIE_SECURE"] = "false"
os.environ["B2_BUCKET_NAME"] = ""

from fastapi.testclient import TestClient
from sqlalchemy import delete
from app.main import app
from app.database import Base, engine, SessionLocal
from app.models import Participant, Attempt, QuestionResult

client = TestClient(app)


def reset_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def register(name, pid):
    r = client.post("/api/participants", json={"name": name, "participant_id": pid})
    assert r.status_code == 200, r.text
    return r.json()


def setup_function():
    reset_database()


def test_health_and_quiz_catalog():
    assert client.get("/health").status_code == 200
    data = client.get("/api/quizzes").json()
    assert any(x["slug"] == "example-quiz" for x in data["quizzes"])


def test_full_attempt_buzz_save_advance_complete_and_lockout():
    pid = "11111111-1111-4111-8111-111111111111"
    register("Alex Smith", pid)
    created = client.post("/api/attempts", json={"participant_id": pid, "quiz_slug": "example-quiz"})
    assert created.status_code == 200, created.text
    attempt = created.json()
    assert attempt["phase"] == "question"
    assert attempt["question"]["id"] == "q1"

    buzz = client.post(f"/api/attempts/{attempt['id']}/buzz", json={
        "participant_id": pid, "question_elapsed_ms": 1800,
        "audio_stop_delay_ms": 333,
    })
    assert buzz.status_code == 200, buzz.text
    assert buzz.json()["audio_stop_delay_ms"] == 333

    saved = client.post(f"/api/attempts/{attempt['id']}/submit", json={
        "participant_id": pid, "answer_text": "Copper", "answer_time_ms": 2300,
        "total_question_time_ms": 4100, "reason": "enter",
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["phase"] == "transition"

    # Idempotent retry after a response was saved.
    retry = client.post(f"/api/attempts/{attempt['id']}/submit", json={
        "participant_id": pid, "answer_text": "Copper", "answer_time_ms": 2300,
        "total_question_time_ms": 4100, "reason": "enter",
    })
    assert retry.status_code == 200, retry.text

    next_question = client.post(f"/api/attempts/{attempt['id']}/advance", json={"participant_id": pid})
    assert next_question.status_code == 200, next_question.text
    assert next_question.json()["question"]["id"] == "q2"

    # Audio is fetched by the server and gated by the current attempt.
    audio = client.get(f"/api/attempts/{attempt['id']}/audio/audio/demo-tone.wav", headers={"X-Participant-ID": pid})
    assert audio.status_code == 200, audio.text
    assert audio.headers["content-type"] in {"audio/wav", "audio/x-wav"}
    assert len(audio.content) > 100

    # Audio end starts the after-audio grace timer; a timeout is then recorded.
    ended = client.post(f"/api/attempts/{attempt['id']}/audio-ended", json={
        "participant_id": pid, "audio_elapsed_ms": 450, "audio_duration_ms": 450,
        "audio_ended": True, "question_elapsed_ms": 9500,
    })
    assert ended.status_code == 200, ended.text
    no_buzz = client.post(f"/api/attempts/{attempt['id']}/no-buzz", json={
        "participant_id": pid, "question_elapsed_ms": 9000, "remaining_ms": 0,
    })
    assert no_buzz.status_code == 200, no_buzz.text
    complete = client.post(f"/api/attempts/{attempt['id']}/advance", json={"participant_id": pid})
    assert complete.status_code == 200, complete.text
    assert complete.json()["status"] == "completed"

    # Same browser/name cannot start the same quiz again.
    locked = client.post("/api/attempts", json={"participant_id": pid, "quiz_slug": "example-quiz"})
    assert locked.status_code == 200
    assert locked.json()["status"] == "completed"

    with SessionLocal() as db:
        results = db.query(QuestionResult).filter_by(attempt_id=attempt["id"]).order_by(QuestionResult.question_index).all()
        assert [x.status for x in results] == ["answered", "no_buzz"]
        assert results[0].answer_text == "Copper"
        assert results[0].answer_time_ms == 2300


def test_duplicate_normalized_name_is_blocked_per_quiz():
    register("Sam  Jones", "22222222-2222-4222-8222-222222222222")
    register(" sam jones ", "33333333-3333-4333-8333-333333333333")
    first = client.post("/api/attempts", json={"participant_id": "22222222-2222-4222-8222-222222222222", "quiz_slug": "example-quiz"})
    assert first.status_code == 200
    second = client.post("/api/attempts", json={"participant_id": "33333333-3333-4333-8333-333333333333", "quiz_slug": "example-quiz"})
    assert second.status_code == 409



def test_buzz_within_half_second_before_audio_end_is_recorded():
    pid = "55555555-5555-4555-8555-555555555555"
    register("Taylor", pid)
    created = client.post("/api/attempts", json={"participant_id": pid, "quiz_slug": "example-quiz"}).json()
    first = client.post(f"/api/attempts/{created['id']}/no-buzz", json={
        "participant_id": pid, "question_elapsed_ms": 20000, "remaining_ms": 0,
    })
    assert first.status_code == 200, first.text
    next_q = client.post(f"/api/attempts/{created['id']}/advance", json={"participant_id": pid}).json()
    assert next_q["question"]["id"] == "q2"
    ended = client.post(f"/api/attempts/{created['id']}/audio-ended", json={
        "participant_id": pid, "audio_elapsed_ms": 10000, "audio_duration_ms": 10000,
        "audio_ended": True, "question_elapsed_ms": 30000,
    })
    assert ended.status_code == 200
    buzz = client.post(f"/api/attempts/{created['id']}/buzz", json={
        "participant_id": pid, "question_elapsed_ms": 30200,
        "remaining_ms": 4800, "audio_elapsed_ms": 9650,
        "audio_duration_ms": 10000, "audio_stop_delay_ms": 444,
    })
    assert buzz.status_code == 200, buzz.text
    with SessionLocal() as db:
        result = db.query(QuestionResult).filter_by(attempt_id=created["id"], question_index=1).one()
        assert result.audio_remaining_at_buzz_ms == 350
        assert result.buzz_before_audio_end is True
        assert result.buzz_within_500ms_before_audio_end is True
        assert result.audio_stop_delay_ms == 444


def test_admin_login_and_results_export():
    pid = "44444444-4444-4444-8444-444444444444"
    register("Casey", pid)
    client.post("/api/attempts", json={"participant_id": pid, "quiz_slug": "example-quiz"})
    bad = client.post("/api/admin/login", json={"username": "admin", "password": "wrong"})
    assert bad.status_code == 401
    good = client.post("/api/admin/login", json={"username": "admin", "password": "test-password"})
    assert good.status_code == 200
    assert client.get("/api/admin/summary").status_code == 200
    export = client.get("/api/admin/export.csv")
    assert export.status_code == 200
    assert "Casey" in export.text


def test_attempt_can_be_resumed_and_name_is_locked_after_start():
    pid = "66666666-6666-4666-8666-666666666666"
    register("Jordan Lee", pid)
    first = client.post("/api/attempts", json={"participant_id": pid, "quiz_slug": "example-quiz"})
    assert first.status_code == 200
    attempt_id = first.json()["id"]

    # The browser can update the latest timer state before it closes.
    heartbeat = client.post(f"/api/attempts/{attempt_id}/heartbeat", json={
        "participant_id": pid, "remaining_ms": 12345, "question_elapsed_ms": 7655,
        "answer_draft": "",
    })
    assert heartbeat.status_code == 200

    # Starting the same quiz with the same browser identity resumes the existing attempt.
    resumed = client.post("/api/attempts", json={"participant_id": pid, "quiz_slug": "example-quiz"})
    assert resumed.status_code == 200
    assert resumed.json()["id"] == attempt_id
    assert resumed.json()["phase_remaining_ms"] == 12345
    assert resumed.json()["question_elapsed_ms"] == 7655

    # Changing a student's name after an attempt exists cannot bypass the name-based lockout.
    rename = client.post("/api/participants", json={"participant_id": pid, "name": "Jordan Lee 2"})
    assert rename.status_code == 409


def test_answer_timeout_is_saved_without_grading():
    pid = "77777777-7777-4777-8777-777777777777"
    register("Morgan", pid)
    attempt = client.post("/api/attempts", json={"participant_id": pid, "quiz_slug": "example-quiz"}).json()
    buzz = client.post(f"/api/attempts/{attempt['id']}/buzz", json={
        "participant_id": pid, "question_elapsed_ms": 1000, "audio_stop_delay_ms": 250,
    })
    assert buzz.status_code == 200
    saved = client.post(f"/api/attempts/{attempt['id']}/submit", json={
        "participant_id": pid, "answer_text": "Partial answer", "answer_time_ms": 8000,
        "total_question_time_ms": 9000, "reason": "timeout",
    })
    assert saved.status_code == 200
    with SessionLocal() as db:
        result = db.query(QuestionResult).filter_by(attempt_id=attempt["id"], question_index=0).one()
        assert result.status == "answer_timeout"
        assert result.answer_text == "Partial answer"
        assert result.answer_time_ms == 8000


def test_admin_attempt_detail_contains_raw_answer_and_buzz_metadata():
    pid = "88888888-8888-4888-8888-888888888888"
    register("Riley", pid)
    attempt = client.post("/api/attempts", json={"participant_id": pid, "quiz_slug": "example-quiz"}).json()
    client.post(f"/api/attempts/{attempt['id']}/buzz", json={
        "participant_id": pid, "question_elapsed_ms": 3210, "audio_stop_delay_ms": 444,
    })
    client.post(f"/api/attempts/{attempt['id']}/submit", json={
        "participant_id": pid, "answer_text": "ungraded response", "answer_time_ms": 1200,
        "total_question_time_ms": 4410, "reason": "enter",
    })
    client.post("/api/admin/login", json={"username": "admin", "password": "test-password"})
    detail = client.get(f"/api/admin/attempts/{attempt['id']}")
    assert detail.status_code == 200
    record = detail.json()["results"][0]
    assert record["answer_text"] == "ungraded response"
    assert record["status"] == "answered"
    assert record["time_to_buzz_ms"] == 3210
    assert record["audio_stop_delay_ms"] == 444
