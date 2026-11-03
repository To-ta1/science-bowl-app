# Fossil Ridge Science Bowl

A small, desktop-first Science Bowl question runner. Students enter a name once per browser, select a quiz, buzz with the space bar, and type an answer. The server records attempts and individual question timing. There is deliberately **no answer key and no automatic grading**.

## What it does

- Remembers the student's name and browser participant ID in local storage.
- Loads quiz manifests and audio from a separate GitHub repository **on the server**. The browser never receives a GitHub token or directly fetches private GitHub files.
- Shows the question prompt throughout the question. With audio, use a short prompt such as `Question 1` if you do not want the full text revealed.
- Supports audio or no audio on every question.
- Supports two hidden pre-buzz timing modes:
  - `after_audio`: students may buzz while the audio plays; the configured grace countdown begins when the audio ends.
  - `from_question_start`: countdown begins when audio playback starts, or immediately when there is no audio.
- A space-bar buzz starts the visible answer countdown immediately. The playing audio is paused 200–500 ms later. The actual client-selected delay is recorded.
- Enter submits the answer immediately; otherwise it submits when the answer timer reaches zero.
- Advances automatically after a short configurable delay.
- Records raw answers, buzzes, timeouts, elapsed times, audio timing, and whether the buzz was within 500 ms of the audio's natural end.
- Prevents a second attempt for the same normalized name and quiz. Names are case/whitespace-insensitive. A participant's name is locked after their first attempt, so changing the display name cannot be used to bypass this lockout.
- Resumes an in-progress attempt in the same browser. The most recent timer/audio/draft state is checkpointed approximately once per second. Timers pause while the browser is closed.
- Has one administrator login at `/admin`, configured with environment variables. The administrator can view attempts, inspect answers and timings, export CSV, and request a B2 backup.
- Stores records in PostgreSQL. B2 receives compressed database snapshots as an additional recovery layer.

## This project is AI Generated.
