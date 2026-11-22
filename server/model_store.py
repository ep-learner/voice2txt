from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Callable


class ModelDownloadError(RuntimeError):
    pass


def model_is_ready(model_dir: Path) -> bool:
    return (model_dir / "model.bin").is_file() and (model_dir / "config.json").is_file()


def ensure_model(settings: dict, log: Callable[[str], None] = print) -> Path:
    model_dir = Path(settings["paths"]["model_dir"])
    if model_is_ready(model_dir):
        log("MODEL_READY 模型已就绪，复用本机文件。")
        return model_dir

    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    from huggingface_hub import snapshot_download

    download_root = Path(settings["paths"]["download_dir"])
    download_root.mkdir(parents=True, exist_ok=True)
    temporary = download_root / f"{model_dir.name}.download"
    model_dir.parent.mkdir(parents=True, exist_ok=True)
    model = settings["model"]
    downloads = settings["downloads"]
    endpoints = [downloads["hf_endpoint"], downloads["hf_mirror"]]
    attempts = max(1, int(downloads["retries"]))
    errors: list[str] = []

    os.environ["HF_HOME"] = settings["paths"]["hf_cache_dir"]
    proxy_keys = ("NO_PROXY", "no_proxy")
    original_proxy_values = {key: os.environ.get(key) for key in proxy_keys}

    def restore_proxy_settings() -> None:
        for key, value in original_proxy_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    try:
        for endpoint in endpoints:
            for attempt in range(1, attempts + 1):
                direct_fallback = attempt > 1
                restore_proxy_settings()
                if direct_fallback:
                    for key in proxy_keys:
                        os.environ[key] = "*"
                network_mode = "直连回退" if direct_fallback else "默认网络"
                try:
                    log(f"MODEL_DOWNLOAD 正在准备模型：{endpoint}，{network_mode}，第 {attempt} 次尝试。")
                    snapshot_download(
                        repo_id=model["repo_id"],
                        revision=model["revision"],
                        endpoint=endpoint,
                        local_dir=temporary,
                        cache_dir=settings["paths"]["hf_cache_dir"],
                        max_workers=4,
                    )
                    if not model_is_ready(temporary):
                        raise ModelDownloadError("下载结束但模型文件不完整。")
                    backup = None
                    if model_dir.exists():
                        backup = model_dir.with_name(f"{model_dir.name}.broken-{int(time.time())}")
                        os.replace(model_dir, backup)
                    try:
                        os.replace(temporary, model_dir)
                    except Exception:
                        if backup and backup.exists() and not model_dir.exists():
                            os.replace(backup, model_dir)
                        raise
                    if backup:
                        shutil.rmtree(backup, ignore_errors=True)
                    log("MODEL_READY 模型下载与校验完成。")
                    return model_dir
                except Exception as exc:
                    errors.append(f"{endpoint}（{network_mode}）: {exc}")
                    log(f"MODEL_RETRY 本次模型下载未完成：{exc}")
    finally:
        restore_proxy_settings()
    raise ModelDownloadError("模型下载失败。请检查网络、代理和磁盘后点击“初始化 / 修复”。" + " | ".join(errors[-2:]))
