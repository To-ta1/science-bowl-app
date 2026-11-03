from __future__ import annotations

import argparse
import gzip
import json
import os
from datetime import datetime

import boto3
from sqlalchemy import delete

from app.database import SessionLocal, init_db
from app.models import Attempt, Participant, QuestionResult


def parse_dt(value):
    return datetime.fromisoformat(value) if value else None


def main():
    parser = argparse.ArgumentParser(description="Restore the Fossil Ridge Science Bowl database from a B2 snapshot.")
    parser.add_argument("--confirm", action="store_true", help="Required: replaces all current application data with the B2 snapshot.")
    parser.add_argument("--key", default="latest.json.gz", help="B2 object key relative to B2_PREFIX (default: latest.json.gz).")
    args = parser.parse_args()
    if not args.confirm:
        parser.error("This operation replaces all current participants, attempts, and results. Re-run with --confirm after verifying the target DATABASE_URL.")

    required = ["DATABASE_URL", "B2_ENDPOINT", "B2_BUCKET_NAME", "B2_KEY_ID", "B2_APPLICATION_KEY"]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        parser.error("Missing environment variables: " + ", ".join(missing))
    prefix = os.getenv("B2_PREFIX", "fossil-science-bowl").strip("/")
    key = f"{prefix}/{args.key}" if prefix else args.key
    client = boto3.client(
        "s3", endpoint_url=os.environ["B2_ENDPOINT"],
        aws_access_key_id=os.environ["B2_KEY_ID"],
        aws_secret_access_key=os.environ["B2_APPLICATION_KEY"],
        region_name=os.getenv("B2_REGION", "us-west-004"),
    )
    payload = client.get_object(Bucket=os.environ["B2_BUCKET_NAME"], Key=key)["Body"].read()
    data = json.loads(gzip.decompress(payload).decode("utf-8"))
    if data.get("format_version") != 1:
        raise SystemExit("Unsupported backup format.")

    init_db()
    with SessionLocal.begin() as db:
        db.execute(delete(QuestionResult))
        db.execute(delete(Attempt))
        db.execute(delete(Participant))
        for row in data["participants"]:
            db.add(Participant(
                id=row["id"], name=row["name"], normalized_name=row["normalized_name"],
                created_at=parse_dt(row["created_at"]), updated_at=parse_dt(row["updated_at"]),
            ))
        for row in data["attempts"]:
            db.add(Attempt(
                id=row["id"], quiz_slug=row["quiz_slug"], quiz_title=row["quiz_title"], quiz_snapshot=row["quiz_snapshot"],
                participant_id=row["participant_id"], student_name=row["student_name"], normalized_name=row["normalized_name"],
                status=row["status"], current_question_index=row["current_question_index"], phase=row["phase"],
                phase_remaining_ms=row["phase_remaining_ms"], audio_elapsed_ms=row["audio_elapsed_ms"],
                question_elapsed_ms=row.get("question_elapsed_ms", 0), audio_duration_ms=row["audio_duration_ms"],
                audio_ended=row["audio_ended"], answer_draft=row["answer_draft"],
                question_started_at=parse_dt(row["question_started_at"]), completed_at=parse_dt(row["completed_at"]),
                created_at=parse_dt(row["created_at"]), updated_at=parse_dt(row["updated_at"]),
            ))
        for row in data["question_results"]:
            db.add(QuestionResult(
                id=row["id"], attempt_id=row["attempt_id"], question_index=row["question_index"],
                question_id=row["question_id"], prompt=row["prompt"], status=row["status"], answer_text=row["answer_text"],
                question_started_at=parse_dt(row["question_started_at"]), buzzed_at=parse_dt(row["buzzed_at"]),
                submitted_at=parse_dt(row["submitted_at"]), time_to_buzz_ms=row["time_to_buzz_ms"],
                answer_time_ms=row["answer_time_ms"], total_question_time_ms=row["total_question_time_ms"],
                audio_duration_ms=row["audio_duration_ms"], audio_elapsed_at_buzz_ms=row["audio_elapsed_at_buzz_ms"],
                audio_remaining_at_buzz_ms=row["audio_remaining_at_buzz_ms"],
                buzz_before_audio_end=row["buzz_before_audio_end"],
                buzz_within_500ms_before_audio_end=row["buzz_within_500ms_before_audio_end"],
                audio_stop_delay_ms=row["audio_stop_delay_ms"], created_at=parse_dt(row["created_at"]),
            ))
    print(f"Restored {len(data['participants'])} participants, {len(data['attempts'])} attempts, and {len(data['question_results'])} question records from {key}.")


if __name__ == "__main__":
    main()
