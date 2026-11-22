from __future__ import annotations

import argparse
import array
import json
import math
import sys
import wave
from pathlib import Path

from lib import PROJECT_ROOT, apply_config

sys.path.insert(0, str(PROJECT_ROOT))

from server.transcriber import FasterWhisperTranscriber


def make_test_audio(path: Path) -> None:
    sample_rate = 16_000
    duration_seconds = 1
    samples = array.array("h", (
        int(8_000 * math.sin(2 * math.pi * 440 * index / sample_rate))
        for index in range(sample_rate * duration_seconds)
    ))
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(samples.tobytes())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, help="可选：使用已有音频做真实内容验证，不删除原文件")
    args = parser.parse_args()
    settings = apply_config()
    generated_audio = args.audio is None
    audio = Path(settings["data"]["input_dir"]) / "smoke-input.wav" if generated_audio else args.audio.expanduser().resolve()
    text_path = Path(settings["data"]["output_dir"]) / "smoke-result.txt"
    metadata_path = Path(settings["data"]["output_dir"]) / "smoke-result.json"
    try:
        if generated_audio:
            make_test_audio(audio)
        elif not audio.is_file():
            raise RuntimeError(f"冒烟音频不存在：{audio}")
        transcriber = FasterWhisperTranscriber(settings)
        text = transcriber.transcribe(
            audio,
            lambda value: print(f"SMOKE_PROGRESS {value:.0%}", flush=True),
            lambda value: print(f"SMOKE_PHASE {value}", flush=True),
        )
        text_path.write_text(text, encoding="utf-8")
        metadata = {
            "status": "ok",
            "engine": "faster-whisper",
            "model": settings["model"]["name"],
            "text_characters": len(text),
            "utf8_readable": text_path.read_text(encoding="utf-8") == text,
        }
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        saved_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if saved_metadata["status"] != "ok" or not saved_metadata["utf8_readable"] or metadata_path.stat().st_size == 0:
            raise RuntimeError("冒烟产物校验失败。")
        print(f"SMOKE_OK 真实模型加载与推理完成，输出 {len(text)} 个字符。", flush=True)
        return 0
    except Exception as exc:
        print(f"SMOKE_ERROR {exc}", flush=True)
        return 1
    finally:
        if generated_audio:
            audio.unlink(missing_ok=True)
        text_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
