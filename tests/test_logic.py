from app.logic import audio_buzz_metrics, clamp_ms, normalize_name
from app.quiz_repository import QuizRepository, QuizRepositoryError


def test_normalize_name_collapses_spaces_and_case():
    assert normalize_name("  Alex   Smith ") == "alex smith"
    assert normalize_name("ＡＬＥＸ Smith") == "alex smith"


def test_audio_buzz_flags_within_half_second_before_end():
    result = audio_buzz_metrics(10_000, 9_650, 500)
    assert result["audio_remaining_at_buzz_ms"] == 350
    assert result["buzz_before_audio_end"] is True
    assert result["buzz_within_window_before_audio_end"] is True


def test_audio_buzz_flags_early_and_after_end_separately():
    early = audio_buzz_metrics(10_000, 8_000, 500)
    after = audio_buzz_metrics(10_000, 10_100, 500)
    assert early["buzz_before_audio_end"] is True
    assert early["buzz_within_window_before_audio_end"] is False
    assert after["buzz_before_audio_end"] is False
    assert after["buzz_within_window_before_audio_end"] is False


def test_missing_audio_metrics_are_null():
    assert audio_buzz_metrics(None, None) == {
        "audio_remaining_at_buzz_ms": None,
        "buzz_before_audio_end": None,
        "buzz_within_window_before_audio_end": None,
    }


def test_clamp_ms_rejects_bad_and_limits_extremes():
    assert clamp_ms("bad") == 0
    assert clamp_ms(-5) == 0
    assert clamp_ms(9_000_000) == 3_600_000


def test_quiz_validation_rejects_path_traversal():
    raw = {
        "title": "Bad quiz",
        "questions": [{"id": "q1", "prompt": "Bad", "audio": "audio/../../secret.mp3"}],
    }
    try:
        QuizRepository.validate_quiz(raw, "bad-quiz")
    except QuizRepositoryError as exc:
        assert "relative path" in str(exc)
    else:
        raise AssertionError("Unsafe audio path was accepted")


def test_quiz_validation_sets_defaults():
    raw = {"title": "Defaults", "questions": [{"prompt": "Question 1"}]}
    quiz = QuizRepository.validate_quiz(raw, "defaults")
    q = quiz["questions"][0]
    assert q["id"] == "q1"
    assert q["pre_buzz"] == {"mode": "from_question_start", "seconds": 5.0}
    assert q["answer_seconds"] == 8.0
