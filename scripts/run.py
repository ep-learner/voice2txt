from __future__ import annotations

import json
import logging
import os
import socket
import sys
import urllib.request
from logging.handlers import RotatingFileHandler
from pathlib import Path

from lib import PROJECT_ROOT, apply_config, load_generated_settings

sys.path.insert(0, str(PROJECT_ROOT))


def service_is_online(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            pass
    except OSError:
        return False
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=2) as response:
            data = json.load(response)
        return data.get("application") == "voice2txt"
    except Exception:
        return False


def configure_logging(logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logs_dir / "server.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def main() -> int:
    try:
        apply_config()
        settings = load_generated_settings()
        configure_logging(Path(settings["data"]["logs_dir"]))
        os.environ["HF_HOME"] = settings["paths"]["hf_cache_dir"]
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        host = settings["server"]["host"]
        port = int(settings["server"]["port"])
        if service_is_online(host, port):
            print("SERVICE_ONLINE 服务已经在线。")
            return 0
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex((host, port)) == 0:
                print("SERVICE_ERROR 配置的地址已被其他程序占用，请在“高级配置”中更换端口。")
                return 2
        from server.app import create_app

        print(f"SERVICE_READY http://{host}:{port}", flush=True)
        create_app(settings).run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
        return 0
    except Exception as exc:
        logging.getLogger("voice2txt").exception("Service failed")
        print(f"SERVICE_ERROR 服务启动失败：{exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
