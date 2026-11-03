from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Attempt, Participant, QuestionResult

log = logging.getLogger("fossil.backup")
_last_hash: str | None = None


def _dt(value):
    return value.isoformat() if value else None


def build_snapshot() -> tuple[bytes, bytes]:
    with SessionLocal() as db:
        participants = db.scalars(select(Participant).order_by(Participant.created_at)).all()
        attempts = db.scalars(select(Attempt).order_by(Attempt.created_at)).all()
        results = db.scalars(select(QuestionResult).order_by(QuestionResult.created_at)).all()
        snapshot: dict[str, Any] = {
            "format_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "participants": [
                {"id": x.id, "name": x.name, "normalized_name": x.normalized_name,
                 "created_at": _dt(x.created_at), "updated_at": _dt(x.updated_at)}
                for x in participants
            ],
            "attempts": [
                {"id": x.id, "quiz_slug": x.quiz_slug, "quiz_title": x.quiz_title,
                 "quiz_snapshot": x.quiz_snapshot, "participant_id": x.participant_id,
                 "student_name": x.student_name, "normalized_name": x.normalized_name,
                 "status": x.status, "current_question_index": x.current_question_index,
                 "phase": x.phase, "phase_remaining_ms": x.phase_remaining_ms,
                 "audio_elapsed_ms": x.audio_elapsed_ms, "question_elapsed_ms": x.question_elapsed_ms, "audio_duration_ms": x.audio_duration_ms,
                 "audio_ended": x.audio_ended, "answer_draft": x.answer_draft,
                 "question_started_at": _dt(x.question_started_at), "completed_at": _dt(x.completed_at),
                 "created_at": _dt(x.created_at), "updated_at": _dt(x.updated_at)}
                for x in attempts
            ],
            "question_results": [
                {"id": x.id, "attempt_id": x.attempt_id, "question_index": x.question_index,
                 "question_id": x.question_id, "prompt": x.prompt, "status": x.status,
                 "answer_text": x.answer_text, "question_started_at": _dt(x.question_started_at),
                 "buzzed_at": _dt(x.buzzed_at), "submitted_at": _dt(x.submitted_at),
                 "time_to_buzz_ms": x.time_to_buzz_ms, "answer_time_ms": x.answer_time_ms,
                 "total_question_time_ms": x.total_question_time_ms, "audio_duration_ms": x.audio_duration_ms,
                 "audio_elapsed_at_buzz_ms": x.audio_elapsed_at_buzz_ms,
                 "audio_remaining_at_buzz_ms": x.audio_remaining_at_buzz_ms,
                 "buzz_before_audio_end": x.buzz_before_audio_end,
                 "buzz_within_500ms_before_audio_end": x.buzz_within_500ms_before_audio_end,
                 "audio_stop_delay_ms": x.audio_stop_delay_ms, "created_at": _dt(x.created_at)}
                for x in results
            ],
        }
    # Omit timestamp from the fingerprint so identical data is not uploaded repeatedly.
    fingerprint_data = dict(snapshot)
    fingerprint_data.pop("created_at", None)
    canonical = json.dumps(fingerprint_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), compresslevel=6, mtime=0)
    return canonical, compressed


def b2_client():
    endpoint = os.getenv("B2_ENDPOINT", "").strip()
    key_id = os.getenv("B2_KEY_ID", "").strip()
    app_key = os.getenv("B2_APPLICATION_KEY", "").strip()
    if not endpoint or not key_id or not app_key:
        return None
    return boto3.client(
        "s3", endpoint_url=endpoint,
        aws_access_key_id=key_id, aws_secret_access_key=app_key,
        region_name=os.getenv("B2_REGION", "us-west-004").strip(),
    )


def upload_backup(force: bool = False) -> bool:
    global _last_hash
    bucket = os.getenv("B2_BUCKET_NAME", "").strip()
    client = b2_client()
    if not bucket or client is None:
        return False
    canonical, compressed = build_snapshot()
    digest = hashlib.sha256(canonical).hexdigest()
    if not force and digest == _last_hash:
        log.info("B2 backup skipped: database contents have not changed.")
        return False
    prefix = os.getenv("B2_PREFIX", "fossil-science-bowl").strip("/")
    latest_key = f"{prefix}/latest.json.gz" if prefix else "latest.json.gz"
    previous_key = f"{prefix}/previous.json.gz" if prefix else "previous.json.gz"
    try:
        client.head_object(Bucket=bucket, Key=latest_key)
        client.copy_object(Bucket=bucket, Key=previous_key, CopySource={"Bucket": bucket, "Key": latest_key})
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code not in {"404", "NoSuchKey", "NotFound"}:
            raise
    client.put_object(
        Bucket=bucket, Key=latest_key, Body=compressed,
        ContentType="application/gzip", CacheControl="no-store",
        Metadata={"sha256": digest, "format": "fossil-science-bowl-db-backup-v1"},
    )
    _last_hash = digest
    log.info("B2 backup uploaded: %s (%s bytes compressed).", latest_key, len(compressed))
    return True
