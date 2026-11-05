import pytest

from app.quiz_repository import QuizRepository, QuizRepositoryError


def _manifest(pre_buzz=None, answer_timer=None):
    return {
        "title": "Timer warnings",
        "questions": [{
            "id": "q1",
            "prompt": "Question 1",
            "pre_buzz": pre_buzz or {"mode": "from_question_start", "seconds": 20},
            "answer_seconds": 8,
            "answer_timer": answer_timer or {"mode": "countdown", "typing_buffer_ms": 100},
        }],
    }


def test_timer_warnings_are_normalized_for_prebuzz_and_answer_timer():
    quiz = QuizRepository.validate_quiz(_manifest(
        pre_buzz={
            "mode": "from_question_start",
            "seconds": 20,
            "warnings": {"at_seconds": [5, 10, 2], "sound": "double_beep", "volume": 0.7},
        },
        answer_timer={
            "mode": "typing_pause",
            "typing_buffer_ms": 100,
            "warnings": {"at_seconds": [4, 2], "sound": "triple_beep", "volume": 1},
        },
    ), "timer-warnings")

    question = quiz["questions"][0]
    assert question["pre_buzz"]["warnings"] == {
        "at_seconds": [10.0, 5.0, 2.0], "sound": "double_beep", "volume": 0.7
    }
    assert question["answer_timer"]["warnings"] == {
        "at_seconds": [4.0, 2.0], "sound": "triple_beep", "volume": 1.0
    }


def test_timer_warnings_default_to_disabled():
    question = QuizRepository.validate_quiz(_manifest(), "timer-warnings")["questions"][0]
    assert "warnings" not in question["pre_buzz"]
    assert "warnings" not in question["answer_timer"]


@pytest.mark.parametrize("warnings", [
    {"at_seconds": [0]},
    {"at_seconds": [-1]},
    {"at_seconds": [21]},  # exceeds the 20-second pre-buzz timer
    {"at_seconds": [5, 5]},
    {"at_seconds": "5"},
    {"at_seconds": [5], "sound": "siren"},
    {"at_seconds": [5], "volume": 1.1},
    {"at_seconds": [5], "volume": -0.1},
])
def test_invalid_prebuzz_warning_settings_are_rejected(warnings):
    with pytest.raises(QuizRepositoryError):
        QuizRepository.validate_quiz(_manifest(
            pre_buzz={"mode": "from_question_start", "seconds": 20, "warnings": warnings}
        ), "bad-timer-warnings")


def test_warning_threshold_cannot_exceed_answer_timer_duration():
    with pytest.raises(QuizRepositoryError):
        QuizRepository.validate_quiz(_manifest(
            answer_timer={"mode": "countdown", "warnings": {"at_seconds": [9]}}
        ), "bad-answer-warnings")
