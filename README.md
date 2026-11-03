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

### Important identity limitation

There are no student login accounts. The participant ID is stored in browser local storage and the name is used for the per-quiz lockout. This prevents ordinary duplicate attempts, but it cannot prove a student's real identity: a student can use a different name or another browser/device. A student should use the same desktop browser to resume an unfinished attempt. If the browser's local data is deleted, the student may not be able to resume with the original participant ID.

### Important recovery limitation

Each submitted answer is committed to PostgreSQL immediately. The current answer draft and timer state are checkpointed about once per second. This is designed to be resilient, but no internet application can guarantee literally zero loss if a device closes between keystrokes/checkpoints, a network is unavailable, or the database provider has an outage. Keep the B2 backup enabled and periodically download CSV exports after important sessions.

---

## Project layout

```text
fossil-ridge-science-bowl/
  app/                         FastAPI backend and static website
  quiz-repository-template/    Starting content for the separate quiz repo
  scripts/restore_from_b2.py   Disaster-recovery restore script
  tests/                       Automated API/logic tests
  render.yaml                  Render Blueprint configuration
  requirements.txt
```

You will create **two GitHub repositories**:

1. An application repository containing this project (the repository Render deploys).
2. A separate quiz-content repository containing the contents of `quiz-repository-template/` (the repository the server reads to obtain quizzes/audio).

The quiz-content repository can be private.

---

# Part A — Create the application GitHub repository

1. Sign in to GitHub and create a new repository, for example `fossil-ridge-science-bowl-app`.
2. You can make this repository private or public. Private is preferable because it includes your deployment structure and admin application code, although it contains no production secrets.
3. Extract the application ZIP and upload **the contents of the `fossil_ridge_science_bowl` folder** to the root of the new repository. The root should directly contain `app/`, `scripts/`, `tests/`, `requirements.txt`, and `render.yaml`.
4. Do not upload `.env` files containing real passwords or tokens. Only `.env.example` is included.
5. Commit to the `main` branch.

# Part B — Create the separate quiz-content repository

1. Create a second GitHub repository, for example `fossil-ridge-science-bowl-quizzes`.
2. Set it to **Private**.
3. From the application project, open `quiz-repository-template/`.
4. Upload the **contents inside that folder** to the root of the new quiz repository. In particular, `quizzes/` must be at the repository root. Do not nest it inside another `quiz-repository-template/` folder.
5. The sample `example-quiz` is only a format demonstration. Replace it with your real quizzes or delete it after adding your own.

The expected quiz repository layout is:

```text
fossil-ridge-science-bowl-quizzes/
  README.md
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

## Create a GitHub read-only token

The Render server needs permission to read the private quiz repository.

1. In GitHub, open **Settings → Developer settings → Personal access tokens → Fine-grained tokens**.
2. Create a token, with repository access limited to `fossil-ridge-science-bowl-quizzes`.
3. Under repository permissions, grant **Contents: Read-only**. No write permission is needed.
4. Create the token and copy it once. Add it to Render as `GITHUB_TOKEN` in Part E below. Do not put it in either repository.

## Add or edit quizzes

Create a folder under `quizzes/`. The folder name is the quiz slug, using lowercase letters, numbers, and hyphens. Each folder needs `quiz.json`.

Example:

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

Field meanings:

- `slug`: must match the folder name.
- `title`: quiz title shown to students/admin.
- `description`: optional catalogue description.
- `inter_question_delay_seconds`: 0–15 seconds; defaults to 2.
- `questions`: ordered array of question objects.
- `id`: unique question ID within this quiz.
- `prompt`: always-visible text. For audio questions it can simply be `Question 1`.
- `audio`: path under that quiz's `audio/` folder, or `null`.
- `pre_buzz.mode`: `after_audio` or `from_question_start`.
- `pre_buzz.seconds`: hidden time allowance. For `after_audio`, this is the grace period after the clip ends. For `from_question_start`, this is the entire buzzer window beginning when the clip starts (or immediately if there is no clip).
- `answer_seconds`: time to type after a buzz, 1–300 seconds.

Do **not** include answer keys, correctness fields, scoring information, or student data. The application deliberately does not read or store grading keys. Unknown question fields are discarded by validation.

Audio guidance:

- MP3 is recommended for broad browser compatibility. WAV is also supported by many browsers.
- Keep each audio file below 100 MB; around 15 MB total audio per quiz is a reasonable target for this use case.
- Audio paths must start with `audio/`, be relative, and cannot contain `..`.
- A quiz can mix questions with and without audio.
- The server caches up to 90 MiB of audio in memory. Your estimate of 5 quizzes × 15 MB = 75 MB fits within this cache.
- A new attempt snapshots its quiz manifest and GitHub commit reference. If you edit a quiz while someone is taking it, their in-progress attempt continues to use the snapshot it started with. New attempts use the latest cached/published version (cache is normally 120 seconds).

After editing GitHub, allow up to `QUIZ_CACHE_SECONDS` (default 120) for the app's quiz catalogue/cache to refresh. A Render redeploy is not required for ordinary quiz edits.

---

# Part C — Create a Neon PostgreSQL database

Render Free services have an ephemeral filesystem and lose local files when a service restarts, redeploys, or spins down. Do not use SQLite as the production database. Use Neon PostgreSQL instead.

1. Create a free project at [Neon](https://neon.com/).
2. Choose a region reasonably close to your Render service region.
3. Open the project's **Connect** panel and copy the connection string for the database/user you created.
4. Prefer Neon's pooled connection string (usually the hostname contains `-pooler`). It should start with `postgresql://` and include `sslmode=require`.
5. Store the whole connection string as the Render `DATABASE_URL` value. Keep its password private.

Neon's current Free plan lists 1 GB storage and 5 GB public network transfer per project per month. Your database records are small text/timing records; question audio is not stored in Neon. Monitor Neon's usage dashboard during testing.

---

# Part D — Create the Render service

1. Sign in to [Render](https://render.com/).
2. Choose **New → Web Service**.
3. Connect your application GitHub repository (`fossil-ridge-science-bowl-app`).
4. Set branch to `main`.
5. Runtime: **Python**.
6. Region: choose one close to the Neon database.
7. Instance type: **Free**.
8. Build command:

   ```bash
   pip install -r requirements.txt
   ```

9. Start command:

   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers
   ```

10. Health check path: `/health`.
11. Create the service. Render will initially give it a temporary `*.onrender.com` URL.

You can also use the included `render.yaml` as a Blueprint. If you create the service manually, use the build/start/health values above.

## Add environment variables

Open **Render → your new service → Environment**. Add these variables. Do not commit real values to GitHub.

| Key | Value |
|---|---|
| `APP_ENV` | `production` |
| `DATABASE_URL` | The full Neon pooled connection string |
| `ADMIN_USERNAME` | `admin` (or choose a different username) |
| `ADMIN_PASSWORD` | A password you choose; do not reuse your school/email password |
| `APP_SECRET_KEY` | A newly generated long random secret; instructions below |
| `GITHUB_REPOSITORY` | `YOUR-GITHUB-USERNAME/fossil-ridge-science-bowl-quizzes` |
| `GITHUB_BRANCH` | `main` |
| `GITHUB_QUIZZES_PATH` | `quizzes` |
| `GITHUB_TOKEN` | Your fine-grained GitHub read-only token |
| `QUIZ_CACHE_SECONDS` | `120` |
| `AUDIO_CACHE_MAX_BYTES` | `94371840` (90 MiB) |
| `BUZZ_AUDIO_STOP_MIN_MS` | `200` |
| `BUZZ_AUDIO_STOP_MAX_MS` | `500` |
| `BUZZ_END_WINDOW_MS` | `500` |
| `B2_ENDPOINT` | Copy the exact S3 endpoint shown in your existing Backblaze B2 bucket details |
| `B2_REGION` | Your bucket region, e.g. `us-west-004`; use the actual bucket region |
| `B2_BUCKET_NAME` | `science-bowl-backups` |
| `B2_KEY_ID` | Existing B2 application key ID with access to this bucket |
| `B2_APPLICATION_KEY` | Matching B2 application key secret |
| `B2_PREFIX` | `fossil-science-bowl` |
| `B2_BACKUP_INITIAL_DELAY_SECONDS` | `90` |
| `B2_BACKUP_QUIET_PERIOD_SECONDS` | `300` |

For `APP_SECRET_KEY`, generate a new value on your computer with Python:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Copy the output into Render. Do not reuse the sample value from `.env.example`.

For B2, use a **private bucket**. If you want to use a separate B2 application key, restrict it to the `science-bowl-backups` bucket and the `fossil-science-bowl/` prefix. It needs permission to list/read/write files for backups and disaster recovery. The backup contains names and raw student answers, so keep the bucket private and its key secret.

The application creates/updates its tables on startup. Once the environment variables are present, save them and deploy. Check Render's deploy logs for `Fossil Ridge Science Bowl started.` and open `/health`; it should return JSON with status `ok`.

---

# Part E — Test the temporary Render URL before changing the custom domain

Do not move your custom domain until the temporary Render address is working.

1. Open the temporary Render service URL on a desktop computer with a physical keyboard.
2. Enter a test name.
3. Start the sample `Example Quiz — replace this`.
4. Test question 1 (no audio): buzz with Space, type a response, press Enter.
5. Test question 2 (short demo audio): confirm audio plays, buzz during playback, and confirm it stops after a short delay.
6. Test an unbuzzed question by waiting for the hidden deadline; confirm it advances automatically. The sample currently has two questions, so add a third temporary test question if you want to test no-buzz on an audio question independently.
7. Close/reopen the same browser during an unfinished attempt; confirm it resumes at the same question and keeps the saved draft/timer state. The most recent heartbeat is approximately one second old at worst.
8. Finish the quiz. Return to the list and confirm it is marked completed and cannot be started again under the same name.
9. Open `/admin`, sign in, inspect the attempt, and download the CSV.
10. In Render logs, confirm that the B2 backup loop is running and successfully uploads a snapshot after new records are created.

The app is designed for desktop and requires a physical keyboard. It intentionally does not provide mobile/touchscreen buzzer support.

---

# Part F — Move your existing custom domain to the new service

Your current public domain is `quizdock.is-cool.dev`. It is attached to the old QuizDock service now. Render generally allows a custom domain to be attached to only one service at a time, so move it only after the new service passes the temporary-URL tests.

1. In the **old** Render QuizDock service, open **Settings → Custom Domains** and remove `quizdock.is-cool.dev` from that old service.
2. In the **new Fossil Ridge Science Bowl** Render service, open **Settings → Custom Domains → Add Custom Domain** and enter:

   ```text
   quizdock.is-cool.dev
   ```

3. Open your Open Domains DNS manager for `quizdock.is-cool.dev`.
4. Edit the existing CNAME record so its target is the **new service's** `YOUR-NEW-SERVICE.onrender.com` hostname. Do not put `https://` or a path in the target.
5. Keep TTL at Auto/3600 if that is the available setting. Keep any Cloudflare proxy set to DNS-only/off while Render verifies the domain.
6. Return to Render and click **Verify** if prompted. Wait for the managed HTTPS certificate to be issued.
7. Open `https://quizdock.is-cool.dev` and repeat the student/admin tests.
8. Keep the old Render service until you have verified everything and exported any old QuizDock results you still need. This new application starts with a fresh database; it does not import old QuizDock quiz banks, Kinde users, or historical results.

Do not disable the new service's `onrender.com` subdomain during initial testing. You can decide later whether to disable it.

---

# How students use it

1. Open `https://quizdock.is-cool.dev` on a desktop browser.
2. Enter their name on the first visit. It is remembered in that browser.
3. Select a quiz and click Start.
4. Listen/read the question. The question text is always visible.
5. Press **Space** to buzz.
6. Type the response and press **Enter**, or let the answer timer expire.
7. The next question begins automatically after the configured delay.
8. Close/reopen in the same browser to resume an unfinished attempt.

After a quiz is completed, the same normalized name cannot start it again. If a student made a spelling mistake in their name after starting a quiz, ask the administrator to review it; names are locked after the first attempt to prevent a simple rename from bypassing the one-attempt rule.

## What gets recorded

For each question where the student buzzes or times out, the application records the question ID/prompt, outcome (`answered`, `answer_timeout`, or `no_buzz`), raw answer text, time to buzz, answer time, total question time, audio duration and position at buzz, whether the buzz was before the audio end, whether it was within 500 ms of the natural audio end, and the selected 200–500 ms audio stop delay.

The 500 ms flag is a measurement only; it does not affect whether the answer is accepted. No answer is graded and no correctness value is stored.

---

# Administrator results and exports

1. Go to `https://quizdock.is-cool.dev/admin`.
2. Sign in using `ADMIN_USERNAME` and `ADMIN_PASSWORD` from Render.
3. The dashboard shows participants, attempts, completed attempts, in-progress attempts, and question records.
4. Filter by quiz or attempt status.
5. Select **View answers** to inspect each question's raw answer and timing information.
6. Select **Download all results (CSV)** to export the result table for spreadsheets.
7. Select **Back up now** to force an immediate B2 backup.

The admin password is intentionally simple environment-variable authentication, as requested. Keep it private. Anyone who learns it can view student names and answers.

---

# Backups and disaster recovery

- PostgreSQL is the primary store. Every answer submission is committed immediately.
- The service takes one startup snapshot, then tracks successful database writes in memory. Once the database has had no writes for 5 minutes, it uploads a compressed JSON snapshot to B2. The quiet-period timer resets with each new write. The background check polls an in-memory timestamp, not PostgreSQL, so it does not repeatedly wake the Neon database just to compare hashes.
- B2 objects are stored under `fossil-science-bowl/latest.json.gz` and `fossil-science-bowl/previous.json.gz` in the `science-bowl-backups` bucket.
- A best-effort backup is attempted when the Render process shuts down. Render may terminate a Free service without allowing a final shutdown hook to finish, so do not rely on that alone.
- A service restart/deploy causes one startup snapshot upload, even if the database did not change. The service also attempts a best-effort final snapshot during graceful shutdown.

### Restore from B2

Restoring is a destructive operation: it **replaces all participants, attempts, and question records currently in the configured PostgreSQL database** with the contents of the selected backup. Only do this during recovery, and verify `DATABASE_URL` carefully first.

1. Clone/download the application repository onto a trusted computer.
2. Install the app requirements locally: `pip install -r requirements.txt`.
3. Set environment variables in that terminal for the target Neon `DATABASE_URL` and B2 settings (`B2_ENDPOINT`, `B2_REGION`, `B2_BUCKET_NAME`, `B2_KEY_ID`, `B2_APPLICATION_KEY`, and `B2_PREFIX`).
4. From the project root, run:

   ```bash
   python scripts/restore_from_b2.py --confirm
   ```

5. To restore `previous.json.gz` instead of the latest backup, run:

   ```bash
   python scripts/restore_from_b2.py --key previous.json.gz --confirm
   ```

Do not run this command just to test whether a backup exists; it overwrites current data.

---

# Local development and tests

The production service uses PostgreSQL. Automated tests use SQLite locally and the included sample quiz/audio.

On Windows PowerShell, from the project root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r dev-requirements.txt
$env:DATABASE_URL = "sqlite:///./fossil_science_bowl_local.db"
$env:QUIZ_CONTENT_DIR = "quiz-repository-template/quizzes"
$env:ADMIN_USERNAME = "admin"
$env:ADMIN_PASSWORD = "local-test-password"
$env:APP_SECRET_KEY = "local-development-secret-change-this"
uvicorn app.main:app --reload
```

Then open `http://127.0.0.1:8000`. The local mode uses the sample quiz directory and does not need GitHub/B2.

Run automated tests in another terminal from the project root:

```powershell
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "."
pytest -q tests
node --check app/static/student.js
node --check app/static/admin.js
python -m py_compile app\*.py scripts\restore_from_b2.py
```

---

# Bandwidth estimate for your testing group

Your estimate is 20 students × 5 quizzes × 15 MB of audio per quiz:

```text
20 × 5 × 15 MB = 1,500 MB ≈ 1.5 GB
```

That is the approximate Render-to-browser audio transfer if each student downloads all five quizzes' audio once. It excludes page/API traffic, repeat attempts, reloads, and retries. It is below Render Hobby's 5 GB monthly included outbound bandwidth, but leaves roughly 3.5 GB for all other traffic. Render counts traffic sent from your service to students and service-initiated traffic to external services as outbound bandwidth. Monitor the workspace usage while testing.

Neon has a separate 5 GB public network transfer allowance on its current Free plan. The app's database records are comparatively small; the audio is served by Render from the server-side GitHub cache and is not stored in Neon.

A few operational caveats:

- Render Free services spin down after 15 minutes without inbound traffic and may take around a minute to wake up. Avoid starting a timed quiz immediately after a long idle period; first load the site and let it wake fully.
- Render Free's filesystem is ephemeral. The application does not depend on it for persistent records; PostgreSQL and B2 do that.
- Render's Hobby workspace includes 5 GB monthly outbound bandwidth. If the allowance is exhausted and there is no payment method, Render suspends free services until the next monthly period. Monitor usage during the trial.
- The first browser to fetch each audio file causes the server to fetch it from GitHub; the server caches up to 90 MiB. Each student still receives the audio from Render, so the 1.5 GB estimate remains the right order of magnitude for student playback traffic.

## Current limitations

- No mobile/touchscreen buzzer support; desktop keyboard required.
- No student accounts or identity verification. Name-based lockout can be bypassed by using another name or clearing/changing browser storage; it is intended to prevent casual repeat attempts, not determined cheating.
- Timer checkpoints and answer drafts are saved about once per second; an immediate device failure between checkpoints could lose the last second of an in-progress draft/timing state. Completed answer submissions are committed immediately.
- No grading, answer key, scores, or correctness flags.
- Quiz content changes are cached briefly; new attempts use the latest version once the cache refreshes. Existing attempts use their original quiz snapshot.
- Render Free is appropriate for testing/hobby use, not a guaranteed-uptime competition system. If this becomes a high-stakes event, use a paid always-on application service and verify database/backups before the event.
