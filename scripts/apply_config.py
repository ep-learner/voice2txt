from __future__ import annotations

import json

from lib import apply_config


def main() -> int:
    try:
        settings = apply_config()
        print(json.dumps({
            "status": "ok",
            "dep_root": settings["dep_root"],
            "model_dir": settings["paths"]["model_dir"],
            "server": settings["server"],
        }, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"配置生成失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
