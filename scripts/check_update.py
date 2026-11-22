from __future__ import annotations

import importlib.metadata
import json
import urllib.request

from lib import load_resolved_settings, runtime_python


def main() -> int:
    settings = load_resolved_settings()
    timeout = int(settings["downloads"]["timeout_seconds"])
    failed = False
    for package, configured in settings["dependencies"].items():
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            installed = "未安装"
        try:
            url = f"{settings['downloads']['pypi_api'].rstrip('/')}/{package}/json"
            for direct in (False, True):
                try:
                    opener = (
                        urllib.request.build_opener(urllib.request.ProxyHandler({}))
                        if direct else urllib.request.build_opener()
                    )
                    with opener.open(url, timeout=timeout) as response:
                        latest = json.load(response)["info"]["version"]
                    break
                except Exception:
                    if direct:
                        raise
            note = "需按 levelup.md 评估兼容性" if latest != configured else "与公共配置一致"
            print(f"UPDATE_INFO {package}｜当前进程：{installed}｜配置：{configured}｜最新：{latest}｜{note}", flush=True)
        except Exception as exc:
            failed = True
            print(f"UPDATE_ERROR {package} 更新检查失败：{exc}", flush=True)
    if not runtime_python(settings).exists():
        print("UPDATE_INFO 运行环境尚未创建；以上“当前进程”不代表项目环境。", flush=True)
    print("UPDATE_DONE 检查完成。本操作只检查，不会自动覆盖当前环境。", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
