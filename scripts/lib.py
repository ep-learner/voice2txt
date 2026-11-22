from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
PUBLIC_CONFIG = CONFIG_DIR / "stack.yaml"
LOCAL_CONFIG = CONFIG_DIR / "stack.local.yaml"
GENERATED_CONFIG = CONFIG_DIR / "_env.json"


def read_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"配置文件格式不正确：{path.name}。请使用合法 JSON 语法。{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"配置文件顶层必须是对象：{path.name}")
    return data


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_settings(local_path: Path | None = None) -> dict[str, Any]:
    public = read_config_file(PUBLIC_CONFIG)
    local = read_config_file(local_path or LOCAL_CONFIG)
    return deep_merge(public, local)


def _resolve(base: Path, value: str) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def resolve_settings(settings: dict[str, Any]) -> dict[str, Any]:
    resolved = copy.deepcopy(settings)
    dep_root = _resolve(PROJECT_ROOT, settings["dep_root"])
    resolved["project_root"] = str(PROJECT_ROOT)
    resolved["dep_root"] = str(dep_root)
    resolved["paths"] = {
        key: str(_resolve(dep_root, value))
        for key, value in settings["paths"].items()
    }
    resolved["data"] = {
        key: str(_resolve(PROJECT_ROOT, value))
        for key, value in settings["data"].items()
    }
    return resolved


def load_resolved_settings() -> dict[str, Any]:
    return resolve_settings(load_settings())


def load_generated_settings() -> dict[str, Any]:
    if not GENERATED_CONFIG.exists():
        return load_resolved_settings()
    return read_config_file(GENERATED_CONFIG)


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def save_local_settings(data: dict[str, Any]) -> None:
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if LOCAL_CONFIG.exists():
        if LOCAL_CONFIG.read_text(encoding="utf-8-sig") == serialized:
            return
        backup = LOCAL_CONFIG.with_suffix(LOCAL_CONFIG.suffix + ".bak")
        shutil.copy2(LOCAL_CONFIG, backup)
    atomic_write_json(LOCAL_CONFIG, data)


def apply_config() -> dict[str, Any]:
    settings = load_resolved_settings()
    generated = {"_notice": "自动生成，不要手改。来源：config/stack.yaml + config/stack.local.yaml", **settings}
    for directory in [*settings["paths"].values(), *settings["data"].values()]:
        Path(directory).mkdir(parents=True, exist_ok=True)
    atomic_write_json(GENERATED_CONFIG, generated)
    return generated


def runtime_python(settings: dict[str, Any] | None = None) -> Path:
    config = settings or load_resolved_settings()
    return Path(config["paths"]["runtime_dir"]) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
