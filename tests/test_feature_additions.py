from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.database import Base, SessionLocal, engine, init_db
from app.main import app, repo
from app.models import Attempt, QuestionResult
from app.quiz_repository import QuizRepository, QuizRepositoryError

client = TestClient(app)


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    repo._catalog_cache = None
    repo._quiz_cache.clear()


def test_quiz_manifest_accepts_typing_pause_and_password():
    manifest = {
        "title": "Protected quiz",
        "password": "test-pass",
        "questions": [{
            "prompt": "Question 1",
            "answer_seconds": 12,
            "answer_timer": {"mode": "typing_pause", "typing_buffer_ms": 100},
        }],
    }
    quiz = QuizRepository.validate_quiz(manifest, "protected-quiz")
    assert quiz["password"] == "test-pass"
    assert quiz["questions"][0]["answer_timer"] == {
        "mode": "typing_pause", "typing_buffer_ms": 100
    }


def test_quiz_manifest_rejects_invalid_answer_timer_mode():
    manifest = {
        "title": "Invalid timer",
        "questions": [{"prompt": "Question 1", "answer_timer": {"mode": "pause-forever"}}],
    }
    with pytest.raises(QuizRepositoryError):
        QuizRepository.validate_quiz(manifest, "invalid-timer")


def test_password_gate_does_not_expose_password_and_keystrokes_are_saved(monkeypatch):
    original_get_quiz = repo.get_quiz

    def protected_quiz(slug, refresh=False):
        quiz = copy.deepcopy(original_get_quiz(slug, refresh=refresh))
        quiz["password"] = "test-pass"
        return quiz

    monkeypatch.setattr(repo, "get_quiz", protected_quiz)
    repo._catalog_cache = None

    participant_id = "77777777-7777-4777-8777-777777777777"
    registered = client.post("/api/participants", json={
        "participant_id": participant_id, "name": "Feature Student"
    })
    assert registered.status_code == 200

    catalog = client.get(f"/api/quizzes?participant_id={participant_id}")
    quiz_item = next(q for q in catalog.json()["quizzes"] if q["slug"] == "example-quiz")
    assert quiz_item["password_required"] is True
    assert "password" not in quiz_item

    wrong = client.post("/api/attempts", json={
        "participant_id": participant_id, "quiz_slug": "example-quiz", "password": "wrong"
    })
    assert wrong.status_code == 403

    started = client.post("/api/attempts", json={
        "participant_id": participant_id, "quiz_slug": "example-quiz", "password": "test-pass"
    })
    assert started.status_code == 200, started.text
    attempt_id = started.json()["id"]
    assert "password" not in started.json()["question"]

    buzz = client.post(f"/api/attempts/{attempt_id}/buzz", json={
        "participant_id": participant_id
    })
    assert buzz.status_code == 200, buzz.text

    heartbeat = client.post(f"/api/attempts/{attempt_id}/heartbeat", json={
        "participant_id": participant_id,
        "answer_draft": "Au",
        "keystroke_count": 7,
        "remaining_ms": 5000,
    })
    assert heartbeat.status_code == 200

    resumed = client.get(
        f"/api/attempts/{attempt_id}?participant_id={participant_id}"
    )
    assert resumed.json()["keystroke_count"] == 7

    submitted = client.post(f"/api/attempts/{attempt_id}/submit", json={
        "participant_id": participant_id,
        "answer_text": "Au",
        "answer_time_ms": 1000,
        "total_question_time_ms": 2000,
        "keystroke_count": 8,
        "reason": "enter",
    })
    assert submitted.status_code == 200, submitted.text

    with SessionLocal() as db:
        result = db.query(QuestionResult).filter_by(
            attempt_id=attempt_id, question_index=0
        ).one()
        saved_attempt = db.get(Attempt, attempt_id)
        assert result.keystroke_count == 8
        assert "password" not in saved_attempt.quiz_snapshot

    login = client.post("/api/admin/login", json={
        "username": "admin", "password": "test-password"
    })
    assert login.status_code == 200
    detail = client.get(f"/api/admin/attempts/{attempt_id}")
    assert detail.json()["results"][0]["keystroke_count"] == 8
    export = client.get("/api/admin/export.csv")
    assert "keystroke_count" in export.text.splitlines()[0]
    assert ",8," in export.text


def test_existing_database_gets_keystroke_column_migration():
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE question_results DROP COLUMN keystroke_count"))
    assert "keystroke_count" not in {
        column["name"] for column in inspect(engine).get_columns("question_results")
    }
    init_db()
    assert "keystroke_count" in {
        column["name"] for column in inspect(engine).get_columns("question_results")
    }
