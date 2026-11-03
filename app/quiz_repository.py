from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


class QuizRepositoryError(RuntimeError):
    pass


class QuizRepository:
    """Reads quiz manifests and audio on the server, never directly in the browser."""

    def __init__(self) -> None:
        self.repo = os.getenv("GITHUB_REPOSITORY", "").strip()
        self.branch = os.getenv("GITHUB_BRANCH", "main").strip()
        self.root = os.getenv("GITHUB_QUIZZES_PATH", "quizzes").strip().strip("/")
        self.token = os.getenv("GITHUB_TOKEN", "").strip()
        local = os.getenv("QUIZ_CONTENT_DIR", "").strip()
        self.local_root = Path(local).resolve() if local else None
        self.cache_seconds = max(0, int(os.getenv("QUIZ_CACHE_SECONDS", "120")))
        self._quiz_cache: dict[str, tuple[float, dict]] = {}
        self._catalog_cache: tuple[float, list[dict]] | None = None
        self._ref_cache: tuple[float, str] | None = None
        self._audio_cache: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
        self._audio_cache_size = 0
        self._audio_cache_limit = int(os.getenv("AUDIO_CACHE_MAX_BYTES", str(90 * 1024 * 1024)))
        self._lock = threading.RLock()
        self._http = httpx.Client(timeout=httpx.Timeout(40.0, connect=10.0), follow_redirects=True)

    def _headers(self, raw: bool = False) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Fossil-Ridge-Science-Bowl/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _api_url(self, path: str, ref: str | None = None) -> str:
        from urllib.parse import quote
        safe_path = "/".join(quote(part, safe="") for part in path.strip("/").split("/"))
        selected_ref = ref or self.branch
        return f"https://api.github.com/repos/{self.repo}/contents/{safe_path}?ref={quote(selected_ref, safe='')}"

    def current_ref(self) -> str | None:
        if self.local_root:
            return None
        with self._lock:
            if self._ref_cache and self._ref_cache[0] > time.monotonic():
                return self._ref_cache[1]
        if not self.repo or "/" not in self.repo:
            raise QuizRepositoryError("Set GITHUB_REPOSITORY to OWNER/REPOSITORY.")
        response = self._http.get(
            f"https://api.github.com/repos/{self.repo}/commits/{self.branch}",
            headers=self._headers(raw=False),
        )
        if response.status_code == 403:
            raise QuizRepositoryError("GitHub denied access while resolving the quiz repository branch.")
        response.raise_for_status()
        sha = response.json().get("sha")
        if not sha:
            raise QuizRepositoryError("GitHub did not return a commit SHA for the quiz repository.")
        with self._lock:
            self._ref_cache = (time.monotonic() + self.cache_seconds, sha)
        return sha

    def _github_json(self, path: str, ref: str | None = None) -> Any:
        if not self.repo or "/" not in self.repo:
            raise QuizRepositoryError("Set GITHUB_REPOSITORY to OWNER/REPOSITORY.")
        response = self._http.get(self._api_url(path, ref=ref), headers=self._headers(raw=False))
        if response.status_code == 404:
            raise QuizRepositoryError(f"GitHub path not found: {path}")
        if response.status_code == 403:
            raise QuizRepositoryError("GitHub denied access. Check the fine-grained token's Contents: Read permission and repository selection.")
        response.raise_for_status()
        return response.json()

    def _github_raw(self, path: str, ref: str | None = None) -> bytes:
        if not self.repo or "/" not in self.repo:
            raise QuizRepositoryError("Set GITHUB_REPOSITORY to OWNER/REPOSITORY.")
        response = self._http.get(self._api_url(path, ref=ref), headers=self._headers(raw=True))
        if response.status_code == 404:
            raise QuizRepositoryError(f"GitHub file not found: {path}")
        if response.status_code == 403:
            raise QuizRepositoryError("GitHub denied access. Check the fine-grained token's Contents: Read permission and repository selection.")
        response.raise_for_status()
        if len(response.content) > 100 * 1024 * 1024:
            raise QuizRepositoryError("A quiz asset exceeds GitHub's 100 MB contents API limit.")
        return response.content

    @staticmethod
    def validate_slug(slug: str) -> str:
        if not SLUG_RE.fullmatch(slug or ""):
            raise QuizRepositoryError("Invalid quiz identifier.")
        return slug

    @staticmethod
    def validate_quiz(data: Any, folder_slug: str) -> dict:
        if not isinstance(data, dict):
            raise QuizRepositoryError(f"{folder_slug}/quiz.json must contain a JSON object.")
        slug = QuizRepository.validate_slug(str(data.get("slug") or folder_slug))
        title = str(data.get("title") or "").strip()
        if not title or len(title) > 200:
            raise QuizRepositoryError(f"{folder_slug}: title is required and must be 200 characters or fewer.")
        description = str(data.get("description") or "").strip()
        questions = data.get("questions")
        if not isinstance(questions, list) or not questions:
            raise QuizRepositoryError(f"{folder_slug}: questions must be a non-empty array.")
        if len(questions) > 300:
            raise QuizRepositoryError(f"{folder_slug}: maximum 300 questions per quiz.")
        delay = data.get("inter_question_delay_seconds", 2)
        try:
            delay = float(delay)
        except (TypeError, ValueError):
            raise QuizRepositoryError(f"{folder_slug}: inter_question_delay_seconds must be numeric.")
        if not 0 <= delay <= 15:
            raise QuizRepositoryError(f"{folder_slug}: inter_question_delay_seconds must be between 0 and 15.")

        normalized_questions = []
        seen_ids = set()
        for index, item in enumerate(questions):
            if not isinstance(item, dict):
                raise QuizRepositoryError(f"{folder_slug}: question {index + 1} must be an object.")
            qid = str(item.get("id") or f"q{index + 1}").strip()
            if not qid or len(qid) > 100 or qid in seen_ids:
                raise QuizRepositoryError(f"{folder_slug}: question {index + 1} has a missing, long, or duplicate id.")
            seen_ids.add(qid)
            prompt = str(item.get("prompt") or f"Question {index + 1}").strip()
            if len(prompt) > 2000:
                raise QuizRepositoryError(f"{folder_slug}: prompt for {qid} is too long.")

            audio = item.get("audio")
            if audio is not None:
                audio = str(audio).strip()
                p = PurePosixPath(audio)
                if not audio or p.is_absolute() or ".." in p.parts or "\\" in audio or not audio.lower().startswith("audio/"):
                    raise QuizRepositoryError(f"{folder_slug}: audio for {qid} must be a relative path inside audio/.")

            pre = item.get("pre_buzz", {})
            if not isinstance(pre, dict):
                raise QuizRepositoryError(f"{folder_slug}: pre_buzz for {qid} must be an object.")
            mode = str(pre.get("mode", "after_audio" if audio else "from_question_start"))
            if mode not in {"after_audio", "from_question_start"}:
                raise QuizRepositoryError(f"{folder_slug}: pre_buzz.mode for {qid} must be after_audio or from_question_start.")
            seconds = pre.get("seconds", 5)
            try:
                seconds = float(seconds)
            except (TypeError, ValueError):
                raise QuizRepositoryError(f"{folder_slug}: pre_buzz.seconds for {qid} must be numeric.")
            if not 0 <= seconds <= 3600:
                raise QuizRepositoryError(f"{folder_slug}: pre_buzz.seconds for {qid} must be between 0 and 3600.")

            answer_seconds = item.get("answer_seconds", 8)
            try:
                answer_seconds = float(answer_seconds)
            except (TypeError, ValueError):
                raise QuizRepositoryError(f"{folder_slug}: answer_seconds for {qid} must be numeric.")
            if not 1 <= answer_seconds <= 300:
                raise QuizRepositoryError(f"{folder_slug}: answer_seconds for {qid} must be between 1 and 300.")

            normalized_questions.append({
                "id": qid,
                "prompt": prompt,
                "audio": audio,
                "pre_buzz": {"mode": mode, "seconds": seconds},
                "answer_seconds": answer_seconds,
            })

        return {
            "slug": slug,
            "title": title,
            "description": description,
            "inter_question_delay_seconds": delay,
            "questions": normalized_questions,
        }

    def _load_local_quiz(self, slug: str) -> dict:
        assert self.local_root is not None
        folder = (self.local_root / slug).resolve()
        if self.local_root not in folder.parents:
            raise QuizRepositoryError("Invalid quiz path.")
        manifest = folder / "quiz.json"
        if not manifest.is_file():
            raise QuizRepositoryError(f"Missing {slug}/quiz.json in local quiz directory.")
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise QuizRepositoryError(f"Invalid JSON in {slug}/quiz.json: {exc}") from exc
        return self.validate_quiz(data, slug)

    def _load_github_quiz(self, slug: str, source_ref: str) -> dict:
        raw = self._github_raw(f"{self.root}/{slug}/quiz.json", ref=source_ref)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QuizRepositoryError(f"Invalid JSON in {slug}/quiz.json: {exc}") from exc
        quiz = self.validate_quiz(data, slug)
        quiz["source_ref"] = source_ref
        return quiz

    def get_quiz(self, slug: str, refresh: bool = False) -> dict:
        slug = self.validate_slug(slug)
        with self._lock:
            cached = self._quiz_cache.get(slug)
            if cached and not refresh and cached[0] > time.monotonic():
                return cached[1]
        if self.local_root:
            quiz = self._load_local_quiz(slug)
            quiz["source_ref"] = None
        else:
            quiz = self._load_github_quiz(slug, self.current_ref())
        with self._lock:
            self._quiz_cache[slug] = (time.monotonic() + self.cache_seconds, quiz)
        return quiz

    def list_quizzes(self, refresh: bool = False) -> list[dict]:
        with self._lock:
            if self._catalog_cache and not refresh and self._catalog_cache[0] > time.monotonic():
                return self._catalog_cache[1]
        if self.local_root:
            entries = [p.name for p in self.local_root.iterdir() if p.is_dir()]
        else:
            entries = [x["name"] for x in self._github_json(self.root) if x.get("type") == "dir"]
        result = []
        errors = []
        for slug in sorted(entries):
            try:
                quiz = self.get_quiz(slug, refresh=refresh)
                result.append({
                    "slug": quiz["slug"],
                    "title": quiz["title"],
                    "description": quiz["description"],
                    "question_count": len(quiz["questions"]),
                    "inter_question_delay_seconds": quiz["inter_question_delay_seconds"],
                })
            except Exception as exc:
                errors.append(f"{slug}: {exc}")
        # Preserve partial success. If every quiz failed, surface the reason rather than silently showing an empty list.
        if not result and errors:
            raise QuizRepositoryError("No valid quizzes were found. " + " | ".join(errors[:3]))
        with self._lock:
            self._catalog_cache = (time.monotonic() + self.cache_seconds, result)
        return result

    def get_audio(self, slug: str, relative_path: str, allowed_paths: set[str] | None = None, source_ref: str | None = None) -> tuple[bytes, str]:
        slug = self.validate_slug(slug)
        rel = PurePosixPath(relative_path)
        if rel.is_absolute() or ".." in rel.parts or "\\" in relative_path or not str(rel).lower().startswith("audio/"):
            raise QuizRepositoryError("Invalid audio path.")
        if allowed_paths is None:
            quiz = self.get_quiz(slug)
            allowed_paths = {q["audio"] for q in quiz["questions"] if q["audio"]}
        if relative_path not in allowed_paths:
            raise QuizRepositoryError("This audio file is not referenced by the quiz.")
        key = f"{slug}/{source_ref or 'local'}/{relative_path}"
        with self._lock:
            if key in self._audio_cache:
                body, content_type = self._audio_cache.pop(key)
                self._audio_cache[key] = (body, content_type)
                return body, content_type
        if self.local_root:
            path = (self.local_root / slug / relative_path).resolve()
            if self.local_root not in path.parents or not path.is_file():
                raise QuizRepositoryError("Audio file not found in local quiz repository.")
            body = path.read_bytes()
        else:
            body = self._github_raw(f"{self.root}/{slug}/{relative_path}", ref=source_ref)
        content_type = mimetypes.guess_type(relative_path)[0] or "application/octet-stream"
        with self._lock:
            if len(body) <= self._audio_cache_limit:
                while self._audio_cache and self._audio_cache_size + len(body) > self._audio_cache_limit:
                    _, (old_body, _) = self._audio_cache.popitem(last=False)
                    self._audio_cache_size -= len(old_body)
                self._audio_cache[key] = (body, content_type)
                self._audio_cache_size += len(body)
        return body, content_type
