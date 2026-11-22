from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from server.model_store import ensure_model, model_is_ready

ProgressCallback = Callable[[float], None]
PhaseCallback = Callable[[str], None]


class TranscriptionError(RuntimeError):
    pass


class FasterWhisperTranscriber:
    def __init__(self, settings: dict):
        self.settings = settings
        self._model = None
        self._model_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._state = "ready" if model_is_ready(Path(settings["paths"]["model_dir"])) else "not_ready"
        self._logger = logging.getLogger("voice2txt")

    @property
    def model_state(self) -> str:
        with self._state_lock:
            if self._model is not None:
                return "loaded"
            return self._state

    def _set_state(self, state: str) -> None:
        with self._state_lock:
            self._state = state

    def _load_model(self, phase_callback: PhaseCallback):
        with self._model_lock:
            if self._model is not None:
                return self._model
            try:
                if not model_is_ready(Path(self.settings["paths"]["model_dir"])):
                    self._set_state("downloading")
                    phase_callback("首次使用需下载模型，请稍候")
                    ensure_model(self.settings, log=self._logger.info)
                self._set_state("loading")
                phase_callback("正在加载模型，请稍候")
                from faster_whisper import WhisperModel

                model_config = self.settings["model"]
                self._model = WhisperModel(
                    str(Path(self.settings["paths"]["model_dir"])),
                    device=model_config["device"],
                    compute_type=model_config["compute_type"],
                )
                self._set_state("ready")
                return self._model
            except Exception as exc:
                self._set_state("error")
                raise TranscriptionError(f"模型准备失败，请回到启动器点击“初始化 / 修复”。原因：{exc}") from exc

    def transcribe(
        self,
        audio_path: Path,
        progress_callback: ProgressCallback,
        phase_callback: PhaseCallback,
    ) -> str:
        model = self._load_model(phase_callback)
        phase_callback("转写中…")
        options = self.settings["transcribe"]
        try:
            segments, info = model.transcribe(
                str(audio_path),
                language=options["language"],
                vad_filter=options["vad_filter"],
                beam_size=options["beam_size"],
            )
            duration = float(getattr(info, "duration", 0) or 0)
            lines: list[str] = []
            for segment in segments:
                text = segment.text.strip()
                if text:
                    lines.append(text)
                if duration > 0:
                    progress_callback(min(0.99, max(0.0, float(segment.end) / duration)))
            progress_callback(1.0)
            return "\n".join(lines)
        except Exception as exc:
            raise TranscriptionError(f"转写失败，请确认音频可正常播放且为标准 MP3。原因：{exc}") from exc
