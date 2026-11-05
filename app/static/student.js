(() => {
  const $ = (id) => document.getElementById(id);
  const STORAGE = {
    id: "frsb_participant_id",
    name: "frsb_participant_name",
    attempt: "frsb_active_attempt",
    pendingSubmit: "frsb_pending_submit",
    pendingBuzz: "frsb_pending_buzz",
    draft: "frsb_answer_draft"
  };

  const state = {
    participantId: localStorage.getItem(STORAGE.id) || makeId(),
    name: localStorage.getItem(STORAGE.name) || "",
    attemptId: localStorage.getItem(STORAGE.attempt) || "",
    attempt: null,
    question: null,
    audio: null,
    audioUrl: null,
    audioStarted: false,
    audioEnded: false,
    audioDurationMs: null,
    audioElapsedMs: 0,
    questionClockStarted: false,
    questionClockPerf: 0,
    questionElapsedBase: 0,
    timerDeadline: null,
    timerKind: null,
    timerTick: null,
    heartbeatTick: null,
    answerTimerBase: 0,
    answerKeystrokes: 0,
    answerTimerRemainingMs: 0,
    answerTimerLastPerf: 0,
    answerTimerPaused: false,
    answerTimerPauseUntil: 0,
    timerWarningConfig: null,
    timerWarningKind: null,
    timerWarningFired: new Set(),
    timerWarningTimeout: null,
    warningAudioContext: null,
    phase: "home",
    submitting: false,
    buzzPending: false,
    pendingTimer: null,
    screenIsSmall: false
  };

  function makeId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, c => {
      const r = Math.random() * 16 | 0;
      return (c === "x" ? r : (r & 3 | 8)).toString(16);
    });
  }

  function showOnly(id) {
    ["name-panel", "home-panel", "quiz-panel", "complete-panel"].forEach(x => $(x).classList.add("hidden"));
    if (id) $(id).classList.remove("hidden");
  }

  function clearError() {
    ["home-error", "quiz-error", "name-error"].forEach(id => $(id).textContent = "");
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
    const text = await response.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }

  async function saveParticipant(name) {
    const data = await api("/api/participants", {
      method: "POST", body: JSON.stringify({ participant_id: state.participantId, name })
    });
    state.name = data.name;
    localStorage.setItem(STORAGE.id, state.participantId);
    localStorage.setItem(STORAGE.name, state.name);
  }

  function showNamePanel() {
    showOnly("name-panel");
    $("student-name").value = state.name;
    $("student-name").focus();
  }

  async function renderHome() {
    clearError();
    showOnly("home-panel");
    $("welcome-heading").textContent = `Hello, ${state.name}`;
    $("quiz-list").replaceChildren();
    $("home-error").textContent = "Loading quizzes…";
    try {
      const data = await api(`/api/quizzes?participant_id=${encodeURIComponent(state.participantId)}`);
      $("home-error").textContent = "";
      if (!data.quizzes.length) {
        const p = document.createElement("p");
        p.className = "muted";
        p.textContent = "There are no quizzes available yet.";
        $("quiz-list").append(p);
        return;
      }
      data.quizzes.forEach(quiz => {
        const card = document.createElement("article");
        card.className = "quiz-card";
        const title = document.createElement("h2"); title.textContent = quiz.title;
        const description = document.createElement("p"); description.textContent = quiz.description || `${quiz.question_count} questions`;
        const footer = document.createElement("div"); footer.className = "quiz-card-footer";
        const status = document.createElement("span"); status.className = "status-label";
        const button = document.createElement("button"); button.className = "button primary";
        if (quiz.student_status === "completed") {
          status.textContent = "Completed";
          button.textContent = "Completed"; button.disabled = true; button.className = "button";
        } else if (quiz.student_status === "in_progress") {
          status.textContent = "In progress";
          button.textContent = "Resume quiz";
        } else {
          status.textContent = `${quiz.question_count} questions`;
          button.textContent = "Start quiz";
        }
        button.addEventListener("click", () => openQuiz(quiz, button));
        footer.append(status, button);
        card.append(title, description, footer);
        $("quiz-list").append(card);
      });
    } catch (error) {
      $("home-error").textContent = `Could not load quizzes: ${error.message}`;
    }
  }

  async function openQuiz(quiz, button) {
    clearError();
    let password;
    if (quiz.password_required) {
      password = window.prompt(`Enter the password for \"${quiz.title}\":`);
      if (password === null) return;
    }
    button.disabled = true;
    button.textContent = "Opening…";
    try {
      const data = await api("/api/attempts", {
        method: "POST", body: JSON.stringify({ participant_id: state.participantId, quiz_slug: quiz.slug, password })
      });
      if (data.status === "completed") {
        localStorage.removeItem(STORAGE.attempt);
        await renderHome();
        return;
      }
      state.attemptId = data.id;
      localStorage.setItem(STORAGE.attempt, data.id);
      await loadAttempt(data);
    } catch (error) {
      $("home-error").textContent = error.message;
      button.disabled = false;
      button.textContent = "Try again";
    }
  }

  async function loadAttempt(initial = null) {
    stopRuntime();
    let attempt = initial;
    if (!attempt) {
      attempt = await api(`/api/attempts/${encodeURIComponent(state.attemptId)}?participant_id=${encodeURIComponent(state.participantId)}`);
    }
    state.attempt = attempt;
    if (attempt.status === "completed" || attempt.phase === "complete") {
      finishScreen(); return;
    }
    if (attempt.phase === "transition") {
      const pending = readPending(STORAGE.pendingSubmit);
      if (pending && pending.attempt_id === attempt.id) {
        localStorage.removeItem(STORAGE.pendingSubmit);
        localStorage.removeItem(STORAGE.draft);
      }
      showOnly("quiz-panel");
      renderTransition(attempt.phase_remaining_ms || 0);
      return;
    }
    state.question = attempt.question;
    state.phase = attempt.phase;
    if (attempt.phase === "answer") {
      renderAnswerResume(attempt);
      return;
    }
    renderQuestion(attempt);
  }

  function renderQuestion(attempt) {
    hideTimerWarning();
    showOnly("quiz-panel");
    clearError();
    state.phase = "question";
    state.question = attempt.question;
    state.questionElapsedBase = Number(attempt.question_elapsed_ms || 0);
    state.audioElapsedMs = Number(attempt.audio_elapsed_ms || 0);
    state.audioEnded = !!attempt.audio_ended;
    state.audioStarted = false;
    state.audioDurationMs = attempt.audio_duration_ms || null;
    state.questionClockStarted = false;
    state.timerDeadline = null;
    state.timerKind = null;
    state.attempt = attempt;
    $("quiz-title-label").textContent = attempt.quiz_title;
    $("question-position").textContent = `Question ${attempt.current_question_index + 1} of ${attempt.question_count}`;
    $("question-number").textContent = `Question ${attempt.current_question_index + 1}`;
    $("question-prompt").textContent = state.question.prompt || `Question ${attempt.current_question_index + 1}`;
    $("buzz-area").classList.remove("hidden");
    $("answer-area").classList.add("hidden");
    $("transition-area").classList.add("hidden");
    $("exit-quiz").classList.remove("hidden");
    $("saving-message").textContent = "";

    const audioPath = state.question.audio;
    if (audioPath && !state.audioEnded) {
      $("audio-status").classList.remove("hidden");
      $("audio-status-text").textContent = "Loading question audio…";
      $("play-audio").classList.add("hidden");
      loadQuestionAudio(attempt.id, audioPath, Number(attempt.audio_elapsed_ms || 0), attempt);
    } else {
      $("audio-status").classList.toggle("hidden", !audioPath);
      if (audioPath) $("audio-status-text").textContent = "Audio finished. Listen for the buzzer window.";
      $("play-audio").classList.add("hidden");
      beginQuestionClock();
      if (state.question.pre_buzz.mode === "after_audio") {
        const remaining = attempt.phase_remaining_ms == null ? Number(state.question.pre_buzz.seconds) * 1000 : Number(attempt.phase_remaining_ms);
        startCountdown("prebuzz", remaining);
      } else {
        startCountdown("prebuzz", attempt.phase_remaining_ms == null ? Number(state.question.pre_buzz.seconds) * 1000 : Number(attempt.phase_remaining_ms));
      }
      startHeartbeat();
    }
    const pendingBuzz = readPending(STORAGE.pendingBuzz);
    if (pendingBuzz && pendingBuzz.attempt_id === attempt.id) {
      state.phase = "answer";
      showAnswerArea(Number(state.question.answer_seconds) * 1000, "");
      retryBuzz(pendingBuzz);
    }
  }

  async function loadQuestionAudio(attemptId, audioPath, resumeMs, attempt) {
    try {
      const encodedPath = audioPath.split("/").map(encodeURIComponent).join("/");
      const response = await fetch(`/api/attempts/${encodeURIComponent(attemptId)}/audio/${encodedPath}`, {
        headers: { "X-Participant-ID": state.participantId }, cache: "no-store"
      });
      if (!response.ok) throw new Error(`Audio request failed (${response.status}).`);
      const blob = await response.blob();
      if (state.attemptId !== attemptId || state.phase !== "question") return;
      state.audioUrl = URL.createObjectURL(blob);
      state.audio = new Audio(state.audioUrl);
      state.audio.preload = "auto";
      state.audio.addEventListener("loadedmetadata", () => {
        state.audioDurationMs = Math.round(state.audio.duration * 1000);
        const seekTo = Math.min(resumeMs / 1000, Math.max(0, state.audio.duration - 0.05));
        if (seekTo > 0) state.audio.currentTime = seekTo;
      }, { once: true });
      state.audio.addEventListener("ended", onAudioEnded);
      state.audio.addEventListener("error", () => showAudioPlayButton("Audio could not be loaded. Try again."));
      $("audio-status-text").textContent = "Starting audio…";
      try {
        await state.audio.play();
        audioPlaybackStarted(attempt);
      } catch {
        showAudioPlayButton("Click to start the question audio.");
      }
    } catch (error) {
      showAudioPlayButton(`Could not load audio: ${error.message}`);
    }
  }

  function showAudioPlayButton(message) {
    $("audio-status").classList.remove("hidden");
    $("audio-status-text").textContent = message;
    $("play-audio").textContent = "Play / resume audio";
    $("play-audio").classList.remove("hidden");
  }

  $("play-audio").addEventListener("click", async () => {
    if (!state.audio) {
      // If the server fetch failed, let the student retry the file request as well as playback.
      if (state.attemptId && state.question && state.question.audio) {
        $("play-audio").classList.add("hidden");
        await loadQuestionAudio(state.attemptId, state.question.audio, state.audioElapsedMs || 0, state.attempt);
      }
      return;
    }
    try {
      await state.audio.play();
      $("play-audio").classList.add("hidden");
      audioPlaybackStarted(state.attempt);
    } catch (error) {
      $("quiz-error").textContent = `Audio playback failed: ${error.message}`;
    }
  });

  function audioPlaybackStarted(attempt) {
    if (state.audioStarted) return;
    state.audioStarted = true;
    $("play-audio").classList.add("hidden");
    $("audio-status-text").textContent = "Listen to the question.";
    beginQuestionClock();
    if (state.question.pre_buzz.mode === "from_question_start") {
      const remaining = attempt.phase_remaining_ms == null ? Number(state.question.pre_buzz.seconds) * 1000 : Number(attempt.phase_remaining_ms);
      startCountdown("prebuzz", remaining);
    }
    startHeartbeat();
  }

  function onAudioEnded() {
    state.audioEnded = true;
    state.audioElapsedMs = state.audioDurationMs || state.audioElapsedMs;
    $("audio-status-text").textContent = "Audio finished.";
    if (state.phase !== "question") return;
    const q = state.question;
    const body = {
      participant_id: state.participantId,
      audio_elapsed_ms: state.audioElapsedMs,
      audio_duration_ms: state.audioDurationMs,
      audio_ended: true,
      question_elapsed_ms: getQuestionElapsed()
    };
    if (q.pre_buzz.mode === "after_audio") {
      startCountdown("prebuzz", Number(q.pre_buzz.seconds) * 1000);
    }
    retryAudioEnded(body, q, 0);
  }

  async function retryAudioEnded(body, question, attemptNumber) {
    try {
      const data = await api(`/api/attempts/${encodeURIComponent(state.attemptId)}/audio-ended`, { method: "POST", body: JSON.stringify(body) });
      if (state.phase !== "question") return;
      if (question.pre_buzz.mode === "after_audio" && state.timerKind !== "prebuzz") {
        startCountdown("prebuzz", data.phase_remaining_ms == null ? Number(question.pre_buzz.seconds) * 1000 : data.phase_remaining_ms);
      }
      heartbeatNow();
    } catch (error) {
      $("quiz-error").textContent = `Saving the audio timing… ${error.message}`;
      state.pendingTimer = setTimeout(() => retryAudioEnded(body, question, attemptNumber + 1), Math.min(5000, 750 + attemptNumber * 500));
    }
  }

  function beginQuestionClock() {
    if (!state.questionClockStarted) {
      state.questionClockStarted = true;
      state.questionClockPerf = performance.now();
    }
  }

  function getQuestionElapsed() {
    return state.questionElapsedBase + (state.questionClockStarted ? Math.max(0, performance.now() - state.questionClockPerf) : 0);
  }

  function updateTypingAnswerTimer(now = performance.now()) {
    if (state.timerKind !== "answer" || state.answerTimerLastPerf === 0) return;
    if (state.answerTimerPaused) {
      if (now >= state.answerTimerPauseUntil) {
        state.answerTimerRemainingMs -= Math.max(0, now - state.answerTimerPauseUntil);
        state.answerTimerPaused = false;
        state.answerTimerPauseUntil = 0;
      }
    } else {
      state.answerTimerRemainingMs -= Math.max(0, now - state.answerTimerLastPerf);
    }
    state.answerTimerLastPerf = now;
  }

  function timerWarningsFor(kind) {
    if (kind === "prebuzz") return state.question?.pre_buzz?.warnings || null;
    if (kind === "answer") return state.question?.answer_timer?.warnings || null;
    return null;
  }

  function initializeTimerWarnings(kind, remainingMs) {
    const config = timerWarningsFor(kind);
    state.timerWarningKind = kind;
    state.timerWarningConfig = config && Array.isArray(config.at_seconds) ? config : null;
    state.timerWarningFired = new Set();
    if (!state.timerWarningConfig) return;
    // On resume, don't replay warnings whose thresholds have already passed.
    for (const seconds of state.timerWarningConfig.at_seconds) {
      if (Number(seconds) * 1000 > Number(remainingMs) + 1) {
        state.timerWarningFired.add(Number(seconds));
      }
    }
  }

  function checkTimerWarnings(kind, remainingMs) {
    if (state.timerWarningKind !== kind || !state.timerWarningConfig) return;
    for (const rawSeconds of state.timerWarningConfig.at_seconds) {
      const seconds = Number(rawSeconds);
      if (state.timerWarningFired.has(seconds) || remainingMs > seconds * 1000) continue;
      state.timerWarningFired.add(seconds);
      showTimerWarning(seconds, kind);
      playTimerWarningSound(state.timerWarningConfig.sound, state.timerWarningConfig.volume);
    }
  }

  function showTimerWarning(seconds, kind) {
    const box = $("timer-warning");
    const label = $("timer-warning-text");
    if (!box || !label) return;
    const action = kind === "prebuzz" ? "left to buzz" : "left to answer";
    label.textContent = `${seconds} second${seconds === 1 ? "" : "s"} ${action}`;
    if (state.timerWarningTimeout) clearTimeout(state.timerWarningTimeout);
    box.classList.add("hidden");
    box.classList.remove("show");
    // Force a layout so repeated threshold notices restart the short animation.
    void box.offsetWidth;
    box.classList.remove("hidden");
    box.classList.add("show");
    state.timerWarningTimeout = setTimeout(() => {
      box.classList.remove("show");
      box.classList.add("hidden");
      state.timerWarningTimeout = null;
    }, 1300);
  }

  function hideTimerWarning() {
    const box = $("timer-warning");
    if (state.timerWarningTimeout) clearTimeout(state.timerWarningTimeout);
    state.timerWarningTimeout = null;
    if (box) {
      box.classList.remove("show");
      box.classList.add("hidden");
    }
  }

  function unlockWarningAudio() {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return;
    try {
      if (!state.warningAudioContext) state.warningAudioContext = new AudioContextClass();
      if (state.warningAudioContext.state === "suspended") {
        state.warningAudioContext.resume().catch(() => {});
      }
    } catch { /* The visual warning remains available if Web Audio is unavailable. */ }
  }

  function playTimerWarningSound(sound = "double_beep", volume = 0.8) {
    unlockWarningAudio();
    const context = state.warningAudioContext;
    if (!context) return;
    const patterns = {
      short_beep: [{ hz: 1040, ms: 125 }],
      double_beep: [{ hz: 940, ms: 85 }, { hz: 940, ms: 85, gap: 65 }],
      high_beep: [{ hz: 1320, ms: 130 }],
      low_beep: [{ hz: 620, ms: 155 }],
      triple_beep: [{ hz: 1180, ms: 65 }, { hz: 900, ms: 65, gap: 55 }, { hz: 1180, ms: 65, gap: 55 }]
    };
    const pattern = patterns[sound] || patterns.double_beep;
    const level = Math.max(0, Math.min(1, Number(volume ?? 0.8))) * 0.16;
    if (level <= 0) return;
    const play = () => {
      if (context.state !== "running") return;
      let startAt = context.currentTime + 0.01;
      for (const tone of pattern) {
        const oscillator = context.createOscillator();
        const gain = context.createGain();
        oscillator.type = "triangle";
        oscillator.frequency.setValueAtTime(tone.hz, startAt);
        gain.gain.setValueAtTime(0.0001, startAt);
        gain.gain.exponentialRampToValueAtTime(Math.max(0.001, level), startAt + 0.008);
        gain.gain.setValueAtTime(Math.max(0.001, level), startAt + tone.ms / 1000 - 0.015);
        gain.gain.exponentialRampToValueAtTime(0.0001, startAt + tone.ms / 1000);
        oscillator.connect(gain);
        gain.connect(context.destination);
        oscillator.start(startAt);
        oscillator.stop(startAt + tone.ms / 1000 + 0.01);
        startAt += (tone.ms + (tone.gap || 35)) / 1000;
      }
    };
    if (context.state === "suspended") context.resume().then(play).catch(() => {});
    else play();
  }

  function startTypingAwareCountdown(remainingMs) {
    state.timerKind = "answer";
    state.timerDeadline = null;
    state.answerTimerRemainingMs = Math.max(0, Number(remainingMs || 0));
    state.answerTimerLastPerf = performance.now();
    state.answerTimerPaused = false;
    state.answerTimerPauseUntil = 0;
    let lastDisplay = "";
    const tick = () => {
      updateTypingAnswerTimer();
      const remaining = Math.max(0, state.answerTimerRemainingMs);
      checkTimerWarnings("answer", remaining);
      const display = `${(remaining / 1000).toFixed(1)} s`;
      if (display !== lastDisplay) {
        $("answer-time-label").textContent = display;
        lastDisplay = display;
      }
      if (remaining <= 0) {
        clearCountdown();
        submitCurrentAnswer("timeout");
        return true;
      }
      return false;
    };
    if (tick()) return;
    state.timerTick = setInterval(tick, 40);
  }

  function startCountdown(kind, remainingMs) {
    clearCountdown();
    const initialRemaining = Math.max(0, Number(remainingMs || 0));
    initializeTimerWarnings(kind, initialRemaining);
    if (kind === "answer" && state.question?.answer_timer?.mode === "typing_pause") {
      startTypingAwareCountdown(initialRemaining);
      return;
    }
    state.timerKind = kind;
    state.timerDeadline = performance.now() + initialRemaining;
    let lastAnswerDisplay = "";
    const tick = () => {
      const remaining = Math.max(0, state.timerDeadline - performance.now());
      if (kind === "prebuzz" || kind === "answer") checkTimerWarnings(kind, remaining);
      if (kind === "answer") {
        const display = `${(remaining / 1000).toFixed(1)} s`;
        if (display !== lastAnswerDisplay) {
          $("answer-time-label").textContent = display;
          lastAnswerDisplay = display;
        }
      }
      if (remaining <= 0) {
        clearCountdown();
        if (kind === "prebuzz") expirePreBuzz();
        else if (kind === "answer") submitCurrentAnswer("timeout");
        else if (kind === "transition") advanceToNextQuestion();
        return true;
      }
      return false;
    };
    if (tick()) return;
    state.timerTick = setInterval(tick, 40);
  }

  function clearCountdown() {
    if (state.timerTick) clearInterval(state.timerTick);
    state.timerTick = null;
    state.timerDeadline = null;
    state.timerKind = null;
    state.answerTimerLastPerf = 0;
    state.answerTimerPaused = false;
    state.answerTimerPauseUntil = 0;
  }

  function remainingMs() {
    if (state.timerKind === "answer" && state.question?.answer_timer?.mode === "typing_pause") {
      updateTypingAnswerTimer();
      return Math.max(0, Math.round(state.answerTimerRemainingMs));
    }
    if (state.timerDeadline == null) return null;
    return Math.max(0, Math.round(state.timerDeadline - performance.now()));
  }

  function countAnswerKeystroke(event) {
    if (state.phase !== "answer" || document.activeElement !== $("answer-input")) return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const isBackspace = event.key === "Backspace" || event.key === "Delete";
    const isPrintable = event.key && event.key.length === 1;
    if (!isBackspace && !isPrintable) return;
    state.answerKeystrokes += 1;

    if (state.question?.answer_timer?.mode === "typing_pause") {
      const now = performance.now();
      updateTypingAnswerTimer(now);
      if (isPrintable) {
        const buffer = Number(state.question.answer_timer.typing_buffer_ms ?? 100);
        state.answerTimerPaused = buffer > 0;
        state.answerTimerPauseUntil = buffer > 0 ? now + buffer : 0;
      } else {
        // Backspace/Delete count as keystrokes, but do not pause the timer.
        state.answerTimerPaused = false;
        state.answerTimerPauseUntil = 0;
      }
      state.answerTimerLastPerf = now;
    }
    persistAnswerDraft();
  }

  function startHeartbeat() {
    if (state.heartbeatTick) clearInterval(state.heartbeatTick);
    state.heartbeatTick = setInterval(heartbeatNow, 1000);
    heartbeatNow();
  }

  function heartbeatNow() {
    if (!state.attemptId || !state.attempt || !["question", "answer", "transition"].includes(state.phase)) return;
    const remaining = remainingMs();
    const audioElapsed = state.audio && !state.audioEnded ? Math.round(state.audio.currentTime * 1000) : state.audioElapsedMs;
    const draft = state.phase === "answer" ? $("answer-input").value : "";
    const body = {
      participant_id: state.participantId,
      remaining_ms: remaining,
      audio_elapsed_ms: audioElapsed,
      audio_duration_ms: state.audioDurationMs,
      audio_ended: state.audioEnded,
      question_elapsed_ms: Math.round(getQuestionElapsed()),
      answer_draft: draft,
      keystroke_count: state.phase === "answer" ? state.answerKeystrokes : 0
    };
    api(`/api/attempts/${encodeURIComponent(state.attemptId)}/heartbeat`, { method: "POST", body: JSON.stringify(body) })
      .catch(() => {});
  }

  function stopRuntime() {
    clearCountdown();
    if (state.heartbeatTick) clearInterval(state.heartbeatTick);
    state.heartbeatTick = null;
    if (state.pendingTimer) clearTimeout(state.pendingTimer);
    state.pendingTimer = null;
    if (state.audio) {
      try { state.audio.pause(); } catch {}
      state.audio = null;
    }
    if (state.audioUrl) {
      URL.revokeObjectURL(state.audioUrl);
      state.audioUrl = null;
    }
    state.audioStarted = false;
  }

  function pressSpace() {
    if (state.phase !== "question" || state.buzzPending) return;
    if (state.question.audio && !state.audioStarted) return;
    if (state.timerKind === "prebuzz" && remainingMs() <= 0) return;
    if (state.question.pre_buzz.mode === "after_audio" && state.audioEnded && state.timerKind === "prebuzz" && remainingMs() <= 0) return;
    triggerBuzz();
  }

  async function triggerBuzz() {
    if (state.phase !== "question" || state.buzzPending) return;
    state.buzzPending = true;
    const preBuzzRemaining = remainingMs();
    state.phase = "answer";
    clearCountdown();
    const q = state.question;
    const stopDelay = Math.floor(200 + Math.random() * 301);
    const body = {
      attempt_id: state.attemptId,
      participant_id: state.participantId,
      question_elapsed_ms: Math.round(getQuestionElapsed()),
      remaining_ms: preBuzzRemaining,
      audio_elapsed_ms: state.audio && state.audioStarted ? Math.round(state.audio.currentTime * 1000) : state.audioElapsedMs,
      audio_duration_ms: state.audioDurationMs,
      audio_stop_delay_ms: stopDelay
    };
    const pending = { ...body };
    localStorage.setItem(STORAGE.pendingBuzz, JSON.stringify(pending));
    if (state.audio && !state.audioEnded) {
      setTimeout(() => { try { state.audio && state.audio.pause(); } catch {} }, stopDelay);
    }
    state.answerKeystrokes = 0;
    showAnswerArea(Number(q.answer_seconds) * 1000, "");
    heartbeatNow();
    retryBuzz(pending);
  }

  function showAnswerArea(durationMs, draft, savedKeystrokes = 0) {
    hideTimerWarning();
    state.phase = "answer";
    state.buzzPending = true;
    $("buzz-area").classList.add("hidden");
    $("answer-area").classList.remove("hidden");
    $("transition-area").classList.add("hidden");
    const localDraft = readPending(STORAGE.draft);
    const hasLocalDraft = localDraft && localDraft.attempt_id === state.attemptId;
    const chosenDraft = hasLocalDraft ? localDraft.answer : (draft || "");
    state.answerKeystrokes = hasLocalDraft ? Number(localDraft.keystrokes || 0) : Number(savedKeystrokes || 0);
    $("answer-input").value = chosenDraft || "";
    $("answer-time-label").textContent = "Answer window";
    $("saving-message").textContent = "";
    $("answer-input").focus();
    startCountdown("answer", durationMs);
    startHeartbeat();
  }

  async function retryBuzz(pending) {
    const body = { ...pending };
    delete body.attempt_id;
    try {
      const data = await api(`/api/attempts/${encodeURIComponent(pending.attempt_id)}/buzz`, {
        method: "POST", body: JSON.stringify(body)
      });
      localStorage.removeItem(STORAGE.pendingBuzz);
      state.buzzPending = false;
      // The client's timer started on keydown; do not restart it after the server round trip.
      $("saving-message").textContent = "";
      if (!state.audioEnded && state.audio) {
        // audio stop timer was started locally at buzz; server's value is retained for the report.
      }
      return data;
    } catch (error) {
      $("saving-message").textContent = `Connecting to save your buzz… ${error.message}`;
      state.pendingTimer = setTimeout(() => retryBuzz(pending), 1500);
    }
  }

  function renderAnswerResume(attempt) {
    showOnly("quiz-panel");
    state.phase = "answer";
    state.question = attempt.question;
    state.questionElapsedBase = Number(attempt.question_elapsed_ms || 0);
    state.questionClockStarted = true;
    state.questionClockPerf = performance.now();
    state.audioEnded = true;
    state.audioElapsedMs = attempt.audio_elapsed_ms || 0;
    state.audioDurationMs = attempt.audio_duration_ms || null;
    $("quiz-title-label").textContent = attempt.quiz_title;
    $("question-position").textContent = `Question ${attempt.current_question_index + 1} of ${attempt.question_count}`;
    $("question-number").textContent = `Question ${attempt.current_question_index + 1}`;
    $("question-prompt").textContent = attempt.question.prompt;
    $("audio-status").classList.toggle("hidden", !attempt.question.audio);
    if (attempt.question.audio) $("audio-status-text").textContent = "Audio stopped after your buzz.";
    $("exit-quiz").classList.remove("hidden");
    const total = Number(attempt.question.answer_seconds) * 1000;
    const remaining = attempt.phase_remaining_ms == null ? total : Number(attempt.phase_remaining_ms);
    const draft = attempt.answer_draft || "";
    showAnswerArea(remaining, draft, attempt.keystroke_count || 0);
    const pending = readPending(STORAGE.pendingBuzz);
    if (pending && pending.attempt_id === attempt.id) retryBuzz(pending);
    const pendingSubmit = readPending(STORAGE.pendingSubmit);
    if (pendingSubmit && pendingSubmit.attempt_id === attempt.id) submitPending(pendingSubmit);
  }

  async function submitCurrentAnswer(reason) {
    if (state.submitting || state.phase !== "answer") return;
    state.submitting = true;
    const currentRemaining = remainingMs();
    clearCountdown();
    const answer = $("answer-input").value;
    const question = state.question;
    const duration = Number(question.answer_seconds) * 1000;
    const spent = Math.max(0, duration - (currentRemaining == null ? 0 : currentRemaining));
    const body = {
      attempt_id: state.attemptId,
      participant_id: state.participantId,
      answer_text: answer,
      answer_time_ms: reason === "timeout" ? duration : Math.round(spent),
      total_question_time_ms: Math.round(getQuestionElapsed()),
      keystroke_count: state.answerKeystrokes,
      reason
    };
    localStorage.setItem(STORAGE.pendingSubmit, JSON.stringify(body));
    await submitPending(body);
  }

  async function submitPending(body) {
    if (state.submitting && !readPending(STORAGE.pendingSubmit)) return;
    state.submitting = true;
    $("answer-input").disabled = true;
    $("saving-message").textContent = "Saving your answer…";
    try {
      const requestBody = { ...body }; delete requestBody.attempt_id;
      const data = await api(`/api/attempts/${encodeURIComponent(body.attempt_id)}/submit`, {
        method: "POST", body: JSON.stringify(requestBody)
      });
      localStorage.removeItem(STORAGE.pendingSubmit);
      localStorage.removeItem(STORAGE.draft);
      state.submitting = false;
      $("answer-input").disabled = false;
      state.phase = "transition";
      state.attempt = data;
      renderTransition(data.phase_remaining_ms || 0);
    } catch (error) {
      $("saving-message").textContent = `Answer not yet confirmed by the server. Retrying… ${error.message}`;
      state.pendingTimer = setTimeout(() => submitPending(body), 2000);
    }
  }

  async function expirePreBuzz() {
    if (state.phase !== "question") return;
    state.phase = "saving_no_buzz";
    clearCountdown();
    if (state.audio) state.audio.pause();
    const body = { attempt_id: state.attemptId, participant_id: state.participantId, question_elapsed_ms: Math.round(getQuestionElapsed()), remaining_ms: 0 };
    retryNoBuzz(body);
  }

  async function retryNoBuzz(body) {
    try {
      const requestBody = { ...body }; delete requestBody.attempt_id;
      const data = await api(`/api/attempts/${encodeURIComponent(body.attempt_id)}/no-buzz`, {
        method: "POST", body: JSON.stringify(requestBody)
      });
      state.phase = "transition";
      state.attempt = data;
      renderTransition(data.phase_remaining_ms || 0);
    } catch (error) {
      $("quiz-error").textContent = `Saving the no-buzz record… ${error.message}`;
      state.pendingTimer = setTimeout(() => retryNoBuzz(body), 1500);
    }
  }

  function renderTransition(remaining) {
    hideTimerWarning();
    stopAudioOnly();
    state.phase = "transition";
    $("buzz-area").classList.add("hidden");
    $("answer-area").classList.add("hidden");
    $("audio-status").classList.add("hidden");
    $("play-audio").classList.add("hidden");
    $("transition-area").classList.remove("hidden");
    $("transition-message").textContent = "Next question…";
    startCountdown("transition", Number(remaining || 0));
    startHeartbeat();
  }

  function stopAudioOnly() {
    if (state.audio) { try { state.audio.pause(); } catch {} state.audio = null; }
    if (state.audioUrl) { URL.revokeObjectURL(state.audioUrl); state.audioUrl = null; }
    state.audioStarted = false;
  }

  async function advanceToNextQuestion() {
    if (state.phase !== "transition") return;
    clearCountdown();
    try {
      const data = await api(`/api/attempts/${encodeURIComponent(state.attemptId)}/advance`, {
        method: "POST", body: JSON.stringify({ participant_id: state.participantId })
      });
      state.attempt = data;
      if (data.status === "completed") finishScreen();
      else await loadAttempt(data);
    } catch (error) {
      // If the server committed the advance but the response was lost, reload authoritative state.
      try { await loadAttempt(); }
      catch (reloadError) {
        $("quiz-error").textContent = `Could not load the next question: ${reloadError.message || error.message}`;
        state.pendingTimer = setTimeout(advanceToNextQuestion, 2000);
      }
    }
  }

  function finishScreen() {
    hideTimerWarning();
    stopRuntime();
    state.phase = "complete";
    localStorage.removeItem(STORAGE.attempt);
    localStorage.removeItem(STORAGE.pendingSubmit);
    localStorage.removeItem(STORAGE.pendingBuzz);
    localStorage.removeItem(STORAGE.draft);
    showOnly("complete-panel");
  }

  function readPending(key) {
    try { return JSON.parse(localStorage.getItem(key) || "null"); } catch { return null; }
  }

  function keyHandler(event) {
    if (state.phase === "answer" && document.activeElement === $("answer-input")) {
      if (event.key === "Enter") {
        state.answerKeystrokes += 1;
        persistAnswerDraft();
        event.preventDefault();
        submitCurrentAnswer("enter");
        return;
      }
      countAnswerKeystroke(event);
      return;
    }
    if (event.code === "Space" && state.phase === "question") {
      event.preventDefault();
      pressSpace();
    }
  }

  function finishToHome() {
    stopRuntime();
    state.phase = "home";
    state.attemptId = "";
    localStorage.removeItem(STORAGE.attempt);
    renderHome();
  }

  function persistAnswerDraft() {
    if (state.attemptId) {
      localStorage.setItem(STORAGE.draft, JSON.stringify({
        attempt_id: state.attemptId, answer: $("answer-input").value, keystrokes: state.answerKeystrokes
      }));
    }
  }

  $("answer-input").addEventListener("input", persistAnswerDraft);

  $("name-form").addEventListener("submit", async event => {
    event.preventDefault();
    const name = $("student-name").value.trim();
    if (!name) { $("name-error").textContent = "Please enter your name."; return; }
    try {
      await saveParticipant(name);
      renderHome();
    } catch (error) { $("name-error").textContent = error.message; }
  });
  $("change-name").addEventListener("click", showNamePanel);
  $("exit-quiz").addEventListener("click", () => {
    // The server keeps the in-progress attempt. Returning to the list never resets it.
    stopRuntime(); state.phase = "home"; renderHome();
  });
  $("back-to-quizzes").addEventListener("click", finishToHome);
  document.addEventListener("pointerdown", unlockWarningAudio, { once: true, capture: true });
  document.addEventListener("keydown", unlockWarningAudio, { once: true, capture: true });
  document.addEventListener("keydown", keyHandler);
  window.addEventListener("beforeunload", heartbeatNow);

  async function init() {
    state.screenIsSmall = window.innerWidth < 760;
    if (state.screenIsSmall) {
      $("desktop-warning").classList.remove("hidden");
      showOnly(null);
      return;
    }
    localStorage.setItem(STORAGE.id, state.participantId);
    if (!state.name) { showNamePanel(); return; }
    try {
      await saveParticipant(state.name);
    } catch (error) {
      $("name-error").textContent = error.message;
      showNamePanel(); return;
    }
    if (state.attemptId) {
      try {
        await loadAttempt();
        return;
      } catch (error) {
        localStorage.removeItem(STORAGE.attempt);
        $("home-error").textContent = `The previous attempt could not be resumed: ${error.message}`;
      }
    }
    await renderHome();
  }

  init();
})();
