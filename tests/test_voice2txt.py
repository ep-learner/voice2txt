from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import lib as config_lib
from server.app import create_app, safe_output_stem
from server.model_store import model_is_ready
from server.transcriber import FasterWhisperTranscriber


class FakeTranscriber:
    model_state = "loaded"

    def __init__(self, text: str = "第一句。\n第二句。", error: Exception | None = None):
        self.text = text
        self.error = error

    def transcribe(self, _audio_path, progress_callback, phase_callback):
        phase_callback("转写中…")
        progress_callback(0.5)
        if self.error:
            raise self.error
        progress_callback(1.0)
        return self.text


class ConfigTests(unittest.TestCase):
    def test_deep_merge_preserves_unrelated_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            local_path = Path(directory) / "stack.local.yaml"
            local_path.write_text(
                json.dumps({"server": {"port": 9876}, "transcribe": {"beam_size": 2}}),
                encoding="utf-8",
            )
            settings = config_lib.load_settings(local_path=local_path)
        self.assertEqual(settings["server"]["port"], 9876)
        self.assertEqual(settings["server"]["host"], "127.0.0.1")
        self.assertEqual(settings["transcribe"]["beam_size"], 2)
        self.assertEqual(settings["transcribe"]["language"], "zh")
        self.assertEqual(settings["model"]["name"], "small")

    def test_paths_follow_a_relocated_project_root(self):
        with tempfile.TemporaryDirectory() as directory:
            relocated = Path(directory) / "new-location" / "voice2txt"
            settings = config_lib.load_settings(local_path=Path(directory) / "missing.yaml")
            with patch.object(config_lib, "PROJECT_ROOT", relocated):
                resolved = config_lib.resolve_settings(settings)
            self.assertEqual(Path(resolved["dep_root"]), relocated.parent / "voice2txt-deps")
            self.assertEqual(
                Path(resolved["paths"]["runtime_dir"]),
                relocated.parent / "voice2txt-deps" / "runtime",
            )
            self.assertEqual(Path(resolved["data"]["output_dir"]), relocated / "data" / "output")
            self.assertNotIn(str(PROJECT_ROOT), json.dumps(resolved, ensure_ascii=False))

    def test_copied_project_applies_config_at_the_new_location(self):
        with tempfile.TemporaryDirectory() as directory:
            relocated = Path(directory) / "relocated" / "voice2txt"
            for relative in [
                "config/stack.yaml",
                "scripts/lib.py",
                "scripts/apply_config.py",
            ]:
                target = relocated / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PROJECT_ROOT / relative, target)
            result = subprocess.run(
                [sys.executable, str(relocated / "scripts" / "apply_config.py")],
                cwd=relocated,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            generated = json.loads((relocated / "config" / "_env.json").read_text(encoding="utf-8"))
            self.assertEqual(Path(generated["project_root"]), relocated)
            self.assertEqual(Path(generated["dep_root"]), relocated.parent / "voice2txt-deps")
            self.assertEqual(Path(generated["data"]["output_dir"]), relocated / "data" / "output")
            self.assertNotIn(str(PROJECT_ROOT), json.dumps(generated, ensure_ascii=False))

            original_generated = (relocated / "config" / "_env.json").read_bytes()
            local_config = relocated / "config" / "stack.local.yaml"
            local_config.write_text(json.dumps({"paths": {
                "runtime_dir": "upgrades/rehearsal/runtime",
                "model_dir": "upgrades/rehearsal/model",
            }}), encoding="utf-8")
            upgraded = subprocess.run(
                [sys.executable, str(relocated / "scripts" / "apply_config.py")],
                cwd=relocated,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(upgraded.returncode, 0, upgraded.stderr)
            upgraded_settings = json.loads((relocated / "config" / "_env.json").read_text(encoding="utf-8"))
            upgrade_root = relocated.parent / "voice2txt-deps" / "upgrades" / "rehearsal"
            self.assertEqual(Path(upgraded_settings["paths"]["runtime_dir"]), upgrade_root / "runtime")
            self.assertEqual(Path(upgraded_settings["paths"]["model_dir"]), upgrade_root / "model")
            self.assertTrue((relocated.parent / "voice2txt-deps" / "runtime").is_dir())

            local_config.unlink()
            rolled_back = subprocess.run(
                [sys.executable, str(relocated / "scripts" / "apply_config.py")],
                cwd=relocated,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
            self.assertEqual((relocated / "config" / "_env.json").read_bytes(), original_generated)

    def test_local_config_save_is_idempotent_and_backs_up_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            local_path = Path(directory) / "stack.local.yaml"
            with patch.object(config_lib, "LOCAL_CONFIG", local_path):
                first = {"server": {"port": 9001}}
                config_lib.save_local_settings(first)
                original = local_path.read_bytes()
                config_lib.save_local_settings(first)
                self.assertEqual(local_path.read_bytes(), original)
                self.assertFalse(local_path.with_suffix(".yaml.bak").exists())
                config_lib.save_local_settings({"server": {"port": 9002}})
                backup = local_path.with_suffix(".yaml.bak")
                self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), first)
                self.assertEqual(
                    json.loads(local_path.read_text(encoding="utf-8"))["server"]["port"],
                    9002,
                )

    def test_apply_config_is_repeatable_and_marks_generated_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = root / "config" / "_env.json"
            settings = {
                "paths": {"runtime_dir": str(root / "deps" / "runtime")},
                "data": {"output_dir": str(root / "data" / "output")},
            }
            with (
                patch.object(config_lib, "GENERATED_CONFIG", generated),
                patch.object(config_lib, "load_resolved_settings", return_value=settings),
            ):
                config_lib.apply_config()
                first = generated.read_bytes()
                config_lib.apply_config()
                self.assertEqual(generated.read_bytes(), first)
            content = json.loads(generated.read_text(encoding="utf-8"))
            self.assertIn("自动生成，不要手改", content["_notice"])
            self.assertTrue((root / "deps" / "runtime").is_dir())
            self.assertTrue((root / "data" / "output").is_dir())


class TranscriberTests(unittest.TestCase):
    def test_only_segment_text_is_returned_without_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = {
                "paths": {"model_dir": directory},
                "transcribe": {"language": "zh", "vad_filter": True, "beam_size": 5},
            }
            segments = [
                SimpleNamespace(text="  第一段字幕  ", start=0.0, end=1.2),
                SimpleNamespace(text="   ", start=1.2, end=1.5),
                SimpleNamespace(text="第二段字幕", start=1.5, end=2.0),
            ]
            captured = {}

            class FakeModel:
                def transcribe(self, _path, **kwargs):
                    captured.update(kwargs)
                    return iter(segments), SimpleNamespace(duration=2.0)

            transcriber = FasterWhisperTranscriber(settings)
            transcriber._model = FakeModel()
            progress = []
            result = transcriber.transcribe(Path("test.mp3"), progress.append, lambda _phase: None)

        self.assertEqual(result, "第一段字幕\n第二段字幕")
        self.assertNotRegex(result, r"\d{2}:\d{2}:\d{2}")
        self.assertNotIn("0.0", result)
        self.assertEqual(captured, {"language": "zh", "vad_filter": True, "beam_size": 5})
        self.assertEqual(progress[-1], 1.0)

    def test_model_readiness_requires_core_files(self):
        with tempfile.TemporaryDirectory() as directory:
            model_dir = Path(directory)
            self.assertFalse(model_is_ready(model_dir))
            (model_dir / "model.bin").write_bytes(b"model")
            self.assertFalse(model_is_ready(model_dir))
            (model_dir / "config.json").write_text("{}", encoding="utf-8")
            self.assertTrue(model_is_ready(model_dir))


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.input_dir = root / "input"
        self.output_dir = root / "output"
        self.settings = {
            "version": 1,
            "data": {"input_dir": str(self.input_dir), "output_dir": str(self.output_dir)},
            "paths": {"model_dir": str(root / "model")},
            "model": {"name": "small"},
            "limits": {"max_upload_mb": 1, "job_retention_seconds": 3600},
        }
        self.transcriber = FakeTranscriber()
        self.app = create_app(self.settings, transcriber=self.transcriber)
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions["job_manager"].shutdown()
        self.temporary.cleanup()

    def wait_for_job(self, job_id: str) -> dict:
        for _ in range(200):
            response = self.client.get(f"/api/transcriptions/{job_id}")
            payload = response.get_json()
            if payload["status"] in {"succeeded", "failed"}:
                return payload
            time.sleep(0.01)
        self.fail("转写任务未在测试时限内结束")

    def upload(self, filename="会议 记录.mp3", content=b"mp3 test"):
        with io.BytesIO(content) as stream:
            return self.client.post(
                "/api/transcriptions",
                data={"file": (stream, filename)},
                content_type="multipart/form-data",
            )

    def test_successful_mp3_job_saves_plain_utf8_text_and_cleans_upload(self):
        response = self.upload()
        self.assertEqual(response.status_code, 202)
        job_id = response.get_json()["id"]
        result = self.wait_for_job(job_id)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["text"], "第一句。\n第二句。")
        self.assertEqual(result["download_name"], "会议 记录.txt")
        self.assertNotRegex(result["text"], r"\d{2}:\d{2}:\d{2}")
        saved = self.output_dir / "会议 记录.txt"
        self.assertEqual(saved.read_text(encoding="utf-8"), result["text"])
        self.assertEqual(list(self.input_dir.iterdir()), [])

    def test_transcription_failure_is_visible_and_cleans_upload(self):
        self.transcriber.error = RuntimeError("模拟识别失败")
        response = self.upload(filename="broken.mp3")
        job_id = response.get_json()["id"]
        result = self.wait_for_job(job_id)
        self.assertEqual(result["status"], "failed")
        self.assertIn("模拟识别失败", result["error"])
        self.assertFalse((self.output_dir / "broken.txt").exists())
        self.assertEqual(list(self.input_dir.iterdir()), [])

    def test_rejects_missing_and_non_mp3_uploads(self):
        missing = self.client.post("/api/transcriptions", data={}, content_type="multipart/form-data")
        self.assertEqual(missing.status_code, 400)
        wrong_type = self.upload(filename="audio.wav")
        self.assertEqual(wrong_type.status_code, 400)
        self.assertIn("仅支持", wrong_type.get_json()["error"])
        self.assertFalse(self.input_dir.exists() and any(self.input_dir.iterdir()))

    def test_rejects_upload_over_configured_limit(self):
        response = self.upload(content=b"x" * (1024 * 1024 + 1))
        self.assertEqual(response.status_code, 413)
        self.assertIn("1 MB", response.get_json()["error"])

    def test_health_and_unknown_job_responses(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.get_json()["application"], "voice2txt")
        self.assertEqual(health.headers["Cache-Control"], "no-store")
        unknown = self.client.get("/api/transcriptions/not-a-job")
        self.assertEqual(unknown.status_code, 404)

    def test_output_filename_is_sanitized(self):
        self.assertEqual(safe_output_stem("..\\folder/会议:记录.mp3"), "会议_记录")
        self.assertEqual(safe_output_stem("...mp3"), "audio")


if __name__ == "__main__":
    unittest.main()
