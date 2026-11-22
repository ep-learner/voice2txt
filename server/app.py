from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, jsonify, request

from server.transcriber import FasterWhisperTranscriber


def safe_output_stem(filename: str) -> str:
    basename = filename.replace("\\", "/").split("/")[-1]
    stem = Path(basename).stem
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .")
    return (cleaned or "audio")[:120]


class JobManager:
    def __init__(self, settings: dict, transcriber=None):
        self.settings = settings
        self.transcriber = transcriber or FasterWhisperTranscriber(settings)
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="transcription")
        self._logger = logging.getLogger("voice2txt")

    def submit(self, file_storage) -> dict:
        original_name = file_storage.filename or ""
        if not original_name.lower().endswith(".mp3"):
            raise ValueError("仅支持 .mp3 文件。")
        self._prune_jobs()
        job_id = uuid.uuid4().hex
        input_dir = Path(self.settings["data"]["input_dir"])
        input_dir.mkdir(parents=True, exist_ok=True)
        audio_path = input_dir / f"{job_id}.mp3"
        file_storage.save(audio_path)
        job = {
            "id": job_id,
            "status": "queued",
            "phase": "等待转写…",
            "progress": 0.0,
            "elapsed_seconds": 0.0,
            "estimated_seconds": None,
            "text": "",
            "error": "",
            "filename": original_name.replace("\\", "/").split("/")[-1],
            "download_name": safe_output_stem(original_name) + ".txt",
            "created_at": time.time(),
            "started_at": None,
        }
        with self._lock:
            self._jobs[job_id] = job
        try:
            self._executor.submit(self._run, job_id, audio_path)
        except Exception:
            audio_path.unlink(missing_ok=True)
            with self._lock:
                self._jobs.pop(job_id, None)
            raise
        return self.get(job_id)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return self._public_job(job)

    def _public_job(self, job: dict) -> dict:
        result = {key: value for key, value in job.items() if key not in {"created_at", "started_at"}}
        if job["status"] == "running" and job["started_at"] is not None:
            elapsed = max(0.0, time.monotonic() - job["started_at"])
            result["elapsed_seconds"] = round(elapsed, 1)
            progress = job["progress"]
            result["estimated_seconds"] = round(max(0.0, elapsed / progress - elapsed), 1) if progress >= 0.02 else None
        return result

    def _update(self, job_id: str, **changes) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(changes)

    def _run(self, job_id: str, audio_path: Path) -> None:
        started = time.monotonic()
        self._update(job_id, status="running", phase="准备转写…", started_at=started)
        try:
            text = self.transcriber.transcribe(
                audio_path,
                lambda value: self._update(job_id, progress=round(value, 4), phase="转写中…"),
                lambda value: self._update(job_id, phase=value),
            )
            with self._lock:
                job = self._jobs[job_id]
                output_path = Path(self.settings["data"]["output_dir"]) / job["download_name"]
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = output_path.with_name(f".{job_id}.tmp")
            try:
                temporary.write_text(text, encoding="utf-8")
                os.replace(temporary, output_path)
            finally:
                temporary.unlink(missing_ok=True)
            self._update(
                job_id,
                status="succeeded",
                phase="转写完成",
                progress=1.0,
                elapsed_seconds=round(time.monotonic() - started, 1),
                estimated_seconds=0.0,
                text=text,
            )
        except Exception as exc:
            self._logger.exception("Transcription job %s failed", job_id)
            self._update(
                job_id,
                status="failed",
                phase="转写失败",
                elapsed_seconds=round(time.monotonic() - started, 1),
                estimated_seconds=None,
                error=str(exc),
            )
        finally:
            audio_path.unlink(missing_ok=True)

    def _prune_jobs(self) -> None:
        cutoff = time.time() - float(self.settings["limits"]["job_retention_seconds"])
        with self._lock:
            expired = [
                job_id for job_id, job in self._jobs.items()
                if job["created_at"] < cutoff and job["status"] in {"succeeded", "failed"}
            ]
            for job_id in expired:
                self._jobs.pop(job_id, None)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)


def create_app(settings: dict, transcriber=None) -> Flask:
    web_dir = Path(__file__).resolve().parent.parent / "web"
    app = Flask(__name__, static_folder=str(web_dir), static_url_path="")
    app.config["MAX_CONTENT_LENGTH"] = int(settings["limits"]["max_upload_mb"]) * 1024 * 1024
    manager = JobManager(settings, transcriber=transcriber)
    app.extensions["job_manager"] = manager

    @app.after_request
    def no_store(response):
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/health")
    def health():
        return jsonify({
            "application": "voice2txt",
            "status": "ok",
            "version": settings["version"],
            "model": settings["model"]["name"],
            "model_state": manager.transcriber.model_state,
            "max_upload_mb": settings["limits"]["max_upload_mb"],
        })

    @app.post("/api/transcriptions")
    def create_transcription():
        uploaded = request.files.get("file")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "请先选择 MP3 文件。"}), 400
        try:
            job = manager.submit(uploaded)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        finally:
            uploaded.close()
        return jsonify(job), 202

    @app.get("/api/transcriptions/<job_id>")
    def get_transcription(job_id: str):
        job = manager.get(job_id)
        if job is None:
            return jsonify({"error": "任务不存在或已过期。"}), 404
        return jsonify(job)

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify({"error": f"文件超过 {settings['limits']['max_upload_mb']} MB 上限。"}), 413

    @app.errorhandler(500)
    def internal_error(error):
        logging.getLogger("voice2txt").error("Unhandled request error: %s", error)
        return jsonify({"error": "本地服务处理失败，请查看启动器提示后重试。"}), 500

    return app
