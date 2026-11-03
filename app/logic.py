from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone


def normalize_name(name: str) -> str:
    """Case/whitespace-insensitive key used to prevent duplicate quiz attempts."""
    value = unicodedata.normalize("NFKC", name or "")
    return re.sub(r"\s+", " ", value).strip().casefold()


def audio_buzz_metrics(duration_ms: int | None, elapsed_ms: int | None, window_ms: int = 500) -> dict:
    """Measure whether a buzz landed before the clip's natural end."""
    if duration_ms is None or elapsed_ms is None or duration_ms < 0 or elapsed_ms < 0:
        return {
            "audio_remaining_at_buzz_ms": None,
            "buzz_before_audio_end": None,
            "buzz_within_window_before_audio_end": None,
        }
    remaining = int(duration_ms) - int(elapsed_ms)
    return {
        "audio_remaining_at_buzz_ms": remaining,
        "buzz_before_audio_end": remaining > 0,
        "buzz_within_window_before_audio_end": 0 <= remaining <= int(window_ms),
    }


def clamp_ms(value, minimum: int = 0, maximum: int = 3_600_000) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def elapsed_ms(start: datetime | None, end: datetime | None = None) -> int | None:
    if start is None:
        return None
    end = end or utc_now()
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return max(0, int((end - start).total_seconds() * 1000))
