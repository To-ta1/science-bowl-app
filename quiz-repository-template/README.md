# Fossil Ridge Science Bowl quiz repository

Create quiz folders inside `quizzes/`. Each folder must contain a `quiz.json` file. Audio files go inside that quiz's `audio/` folder.

The application reads this repository on the server. The browser receives only the quiz list metadata and the question currently being played. Do not put answer keys in this repository; the application deliberately does not grade answers.

## Example layout

```text
quizzes/
  chemistry-round-1/
    quiz.json
    audio/
      q1.mp3
      q2.mp3
  biology-round-1/
    quiz.json
    audio/
      q1.mp3
```

## Example `quiz.json`

```json
{
  "slug": "chemistry-round-1",
  "title": "Chemistry — Round 1",
  "description": "Chemistry practice round.",
  "inter_question_delay_seconds": 2,
  "questions": [
    {
      "id": "q1",
      "prompt": "Question 1",
      "audio": "audio/q1.mp3",
      "pre_buzz": {
        "mode": "after_audio",
        "seconds": 5
      },
      "answer_seconds": 8
    },
    {
      "id": "q2",
      "prompt": "A sample question without audio",
      "audio": null,
      "pre_buzz": {
        "mode": "from_question_start",
        "seconds": 30
      },
      "answer_seconds": 10
    }
  ]
}
```

## Fields

- `slug`: lowercase letters, numbers, and hyphens; must match the folder name.
- `title`: title displayed to students and in admin reports.
- `description`: optional description.
- `inter_question_delay_seconds`: seconds between questions (0–15; default 2).
- `questions`: ordered list of questions.
- `id`: unique identifier inside the quiz.
- `prompt`: text always shown on screen. With audio, this can simply be `Question 1`.
- `audio`: optional path under that quiz's `audio/` directory, or `null`.
- `pre_buzz.mode`: `after_audio` or `from_question_start`.
  - `after_audio`: the hidden pre-buzz grace timer starts when the clip ends; students can still buzz while the audio is playing.
  - `from_question_start`: the hidden timer starts when audio playback starts (or immediately if there is no audio).
- `pre_buzz.seconds`: hidden grace/time window after the relevant start event.
- `answer_seconds`: time allowed to type after buzzing; starts immediately on buzz.

The audio is stopped by the browser 200–500 ms after the buzz. That delay is recorded. For audio questions, reports include the estimated milliseconds remaining in the clip at the moment of the buzz and a flag for buzzes within 500 ms before the clip's natural end.

## Notes

- Use MP3, WAV, or another format supported by the students' browsers. MP3 is recommended for consistency and smaller files.
- Keep audio files below 100 MB each (the GitHub Contents API limit used by the server).
- Do not add correctness fields or answer keys. Answers are collected for manual review only.
- The repository can be private. Configure a GitHub fine-grained personal access token with read-only Contents permission in the web service environment.
