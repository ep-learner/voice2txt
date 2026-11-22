from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from lib import PROJECT_ROOT, apply_config, runtime_python

sys.path.insert(0, str(PROJECT_ROOT))


def log(message: str) -> None:
    print(message, flush=True)


def check_resources(settings: dict) -> None:
    required_mb = int(settings["limits"]["required_disk_mb"])
    dep_root = Path(settings["dep_root"])
    dep_root.mkdir(parents=True, exist_ok=True)
    free_mb = shutil.disk_usage(dep_root).free // (1024 * 1024)
    if free_mb < required_mb:
        raise RuntimeError(f"磁盘可用空间不足：当前约 {free_mb} MB，至少需要 {required_mb} MB。请释放空间后重试。")
    log(f"RESOURCE_OK 磁盘可用约 {free_mb} MB。")
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong), ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong), ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong), ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]
        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            total_mb = status.total_physical // (1024 * 1024)
            minimum_mb = int(settings["limits"]["minimum_memory_mb"])
            if total_mb < minimum_mb:
                raise RuntimeError(f"内存不足：{settings['model']['name']} 模型建议至少 {minimum_mb} MB 物理内存，当前约 {total_mb} MB。")
            log(f"RESOURCE_OK 物理内存约 {total_mb} MB。")


def _python_version(python: Path) -> tuple[int, int] | None:
    try:
        result = subprocess.run(
            [str(python), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if result.returncode != 0:
            return None
        major, minor = result.stdout.strip().split(".")[:2]
        return int(major), int(minor)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def find_base_python(settings: dict) -> Path:
    configured = settings["runtime"].get("python", "").strip()
    candidates: list[tuple[str, Path]] = []
    if configured:
        executable = shutil.which(configured)
        candidates.append(("配置指定", Path(executable) if executable else Path(configured)))
    preferred = str(settings["runtime"]["preferred_version"])
    py_launcher = shutil.which("py")
    if py_launcher and os.name == "nt":
        try:
            listing = subprocess.run([py_launcher, "-0p"], capture_output=True, text=True, timeout=15, check=False)
            discovered: list[tuple[str, Path]] = []
            for line in listing.stdout.splitlines():
                match = re.search(r"(3\.\d+)(?:\.\d+)?\s+(.+python(?:\.exe)?)\s*$", line, re.IGNORECASE)
                if match and Path(match.group(2)).is_file():
                    discovered.append((match.group(1), Path(match.group(2))))
            discovered.sort(key=lambda item: (item[0] != preferred, item[0]), reverse=False)
            candidates.extend(discovered)
        except (OSError, subprocess.TimeoutExpired):
            pass
        for version in dict.fromkeys([preferred, "3.13", "3.11", "3.10"]):
            try:
                result = subprocess.run(
                    [py_launcher, f"-{version}", "-c", "import sys; print(sys.executable)"],
                    capture_output=True, text=True, timeout=15, check=False,
                )
                if result.returncode == 0 and result.stdout.strip():
                    candidates.append((version, Path(result.stdout.strip())))
            except (OSError, subprocess.TimeoutExpired):
                pass
    candidates.append(("当前", Path(sys.executable)))
    seen: set[str] = set()
    for source, candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        version = _python_version(candidate)
        if version and version >= (3, 10):
            log(f"RUNTIME_OK 使用 {source} Python {version[0]}.{version[1]}。")
            return candidate.resolve()
    raise RuntimeError("未找到 Python 3.10 或更高版本。请安装 Python 后重新点击“初始化 / 修复”。")


def prepare_runtime(settings: dict) -> Path:
    target_python = runtime_python(settings)
    if _python_version(target_python):
        log("RUNTIME_OK 复用已有运行环境。")
        return target_python
    base_python = find_base_python(settings)
    runtime_dir = Path(settings["paths"]["runtime_dir"])
    runtime_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = runtime_dir.with_name(runtime_dir.name + ".creating")
    if temporary.exists():
        shutil.rmtree(temporary)
    log("RUNTIME_CREATE 正在创建独立运行环境…")
    subprocess.run([str(base_python), "-m", "venv", str(temporary)], check=True)
    temporary_python = temporary / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not _python_version(temporary_python):
        raise RuntimeError("独立运行环境创建后未通过校验，请重新初始化。")
    backup = None
    if runtime_dir.exists():
        backup = runtime_dir.with_name(runtime_dir.name + ".invalid")
        if backup.exists():
            shutil.rmtree(backup)
        os.replace(runtime_dir, backup)
    try:
        os.replace(temporary, runtime_dir)
    except Exception:
        if backup and backup.exists() and not runtime_dir.exists():
            os.replace(backup, runtime_dir)
        raise
    if backup:
        shutil.rmtree(backup, ignore_errors=True)
    return target_python


def installed_version(python: Path, package: str) -> str | None:
    result = subprocess.run(
        [str(python), "-c", "import importlib.metadata as m, sys; print(m.version(sys.argv[1]))", package],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def install_dependencies(settings: dict, python: Path) -> None:
    dependencies = settings["dependencies"]
    pending = [name for name, version in dependencies.items() if installed_version(python, name) != version]
    if not pending:
        log("DEPENDENCIES_OK Python 依赖版本已匹配，跳过安装。")
        return
    requirements = [f"{name}=={dependencies[name]}" for name in pending]
    downloads = settings["downloads"]
    errors = []
    for index_name, index_url in [("官方源", downloads["pypi_index"]), ("镜像源", downloads["pypi_mirror"])]:
        log(f"DEPENDENCIES_DOWNLOAD 正在通过{index_name}安装：{', '.join(pending)}")
        result = subprocess.run([
            str(python), "-m", "pip", "install", "--disable-pip-version-check",
            "--retries", str(downloads["retries"]), "--timeout", str(downloads["timeout_seconds"]),
            "--index-url", index_url, *requirements,
        ], check=False)
        if result.returncode == 0:
            if all(installed_version(python, name) == dependencies[name] for name in dependencies):
                log("DEPENDENCIES_OK Python 依赖安装与版本校验完成。")
                return
        errors.append(f"{index_name}退出码 {result.returncode}")
    raise RuntimeError("Python 依赖安装失败，请检查网络或代理后重试。" + "；".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--venv-ready", action="store_true")
    parser.add_argument("--skip-model", action="store_true")
    parser.add_argument("--skip-smoke", action="store_true")
    args = parser.parse_args()
    try:
        settings = apply_config()
        check_resources(settings)
        python = runtime_python(settings)
        if not args.venv_ready:
            python = prepare_runtime(settings)
            install_dependencies(settings, python)
            command = [str(python), str(Path(__file__).resolve()), "--venv-ready"]
            if args.skip_model:
                command.append("--skip-model")
            if args.skip_smoke:
                command.append("--skip-smoke")
            return subprocess.run(command, cwd=PROJECT_ROOT, check=False).returncode
        if not _python_version(python):
            raise RuntimeError("运行环境无效，请删除损坏环境后重新初始化。")
        if not args.skip_model:
            from server.model_store import ensure_model

            ensure_model(settings, log=log)
        if not args.skip_smoke and settings["smoke_test"]["enabled"]:
            log("SMOKE_START 正在执行真实模型加载与推理冒烟…")
            result = subprocess.run([str(python), str(PROJECT_ROOT / "scripts" / "smoke_test.py")], cwd=PROJECT_ROOT, check=False)
            if result.returncode != 0:
                raise RuntimeError("冒烟测试未通过，请查看日志后点击“初始化 / 修复”。")
        log("SETUP_OK 初始化完成，可以启动服务。")
        return 0
    except subprocess.CalledProcessError as exc:
        log(f"SETUP_ERROR 环境准备命令执行失败（退出码 {exc.returncode}），请重试。")
        return 1
    except Exception as exc:
        log(f"SETUP_ERROR {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
