from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib import (  # noqa: E402
    LOCAL_CONFIG,
    PROJECT_ROOT as ROOT,
    apply_config,
    load_resolved_settings,
    read_config_file,
    runtime_python,
    save_local_settings,
)

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
CREATE_NEW_PROCESS_GROUP = 0x00000200 if os.name == "nt" else 0


class Launcher:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.settings = None
        self.service_process = None
        self.service_log = None
        self.action_process = None
        self.action_name = None
        self.service_online = False
        self.dependencies_ready = False
        self.status_refreshing = False
        self.closing = False
        self._build_ui()
        self.reload_settings()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.process_events)
        self.root.after(300, self.refresh_status)

    def _build_ui(self) -> None:
        self.root.title("声转文 · 本地转写启动器")
        self.root.geometry("720x700")
        self.root.minsize(640, 620)
        self.root.configure(bg="#f3f6f5")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f6f5")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Title.TLabel", background="#f3f6f5", foreground="#172a3a", font=("Microsoft YaHei UI", 22, "bold"))
        style.configure("Subtitle.TLabel", background="#f3f6f5", foreground="#6d7b83", font=("Microsoft YaHei UI", 10))
        style.configure("CardTitle.TLabel", background="#ffffff", foreground="#172a3a", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Status.TLabel", background="#ffffff", foreground="#52676e", font=("Microsoft YaHei UI", 10))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(13, 11))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(13, 11))
        style.configure("TCheckbutton", background="#f3f6f5")

        container = ttk.Frame(self.root, padding=25)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="声转文", style="Title.TLabel").pack(anchor="w")
        ttk.Label(container, text="MP3 本地转写为纯文本，音频不上传云端", style="Subtitle.TLabel").pack(anchor="w", pady=(4, 20))

        status_card = ttk.Frame(container, style="Card.TFrame", padding=18)
        status_card.pack(fill="x")
        ttk.Label(status_card, text="运行状态", style="CardTitle.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        self.dependency_var = tk.StringVar(value="正在检查…")
        self.model_var = tk.StringVar(value="正在检查…")
        self.service_var = tk.StringVar(value="正在检查…")
        self.selection_var = tk.StringVar(value="当前模型：—")
        for row, (name, variable) in enumerate([
            ("依赖", self.dependency_var), ("模型", self.model_var), ("服务", self.service_var)
        ], start=1):
            ttk.Label(status_card, text=name, style="CardTitle.TLabel").grid(row=row, column=0, sticky="w", pady=4)
            ttk.Label(status_card, textvariable=variable, style="Status.TLabel").grid(row=row, column=1, sticky="w", padx=(20, 0), pady=4)
        ttk.Label(status_card, textvariable=self.selection_var, style="Status.TLabel").grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 0))
        status_card.columnconfigure(1, weight=1)

        actions = ttk.Frame(container)
        actions.pack(fill="x", pady=(18, 12))
        self.setup_button = ttk.Button(actions, text="初始化 / 修复", style="Accent.TButton", command=self.run_setup)
        self.start_button = ttk.Button(actions, text="启动 / 重启服务", command=self.start_service)
        self.open_button = ttk.Button(actions, text="打开转写页面", command=self.open_page)
        self.config_button = ttk.Button(actions, text="高级配置", command=self.open_config)
        self.update_button = ttk.Button(actions, text="检查升级", command=self.check_updates)
        self.setup_button.grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=4)
        self.start_button.grid(row=0, column=1, sticky="ew", padx=8, pady=4)
        self.open_button.grid(row=0, column=2, sticky="ew", padx=(8, 0), pady=4)
        self.config_button.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(0, 8), pady=4)
        self.update_button.grid(row=1, column=2, sticky="ew", padx=(8, 0), pady=4)
        for column in range(3):
            actions.columnconfigure(column, weight=1)

        self.activity = ttk.Progressbar(container, mode="indeterminate")
        self.activity.pack(fill="x", pady=(0, 12))
        ttk.Label(container, text="进度与提示", style="Subtitle.TLabel").pack(anchor="w", pady=(0, 6))
        self.log_text = tk.Text(
            container, height=14, wrap="word", state="disabled", relief="flat",
            bg="#ffffff", fg="#425961", insertbackground="#425961", padx=13, pady=11,
            font=("Microsoft YaHei UI", 9),
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.tag_configure("error", foreground="#a34f3d")
        self.log_text.tag_configure("success", foreground="#147d73")
        bottom = ttk.Frame(container)
        bottom.pack(fill="x", pady=(9, 0))
        ttk.Label(bottom, text="详细日志：data/logs", style="Subtitle.TLabel").pack(side="left")
        ttk.Button(bottom, text="打开日志目录", command=self.open_logs).pack(side="right")
        self.append_log("欢迎使用。首次使用请先点击“初始化 / 修复”。")

    def reload_settings(self) -> None:
        try:
            self.settings = load_resolved_settings()
            model = self.settings["model"]
            self.selection_var.set(f"当前模型：{model['name']} · 设备：{model['device']} · 精度：{model['compute_type']}")
        except Exception as exc:
            self.settings = None
            self.append_log(f"配置读取失败：{exc}", "error")

    def append_log(self, message: str, tag: str = "") -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def set_busy(self, busy: bool) -> None:
        if busy:
            self.activity.start(12)
        else:
            self.activity.stop()
            self.action_name = None
            self.action_process = None
        state = "disabled" if busy else "normal"
        for button in [self.setup_button, self.start_button, self.config_button, self.update_button]:
            button.configure(state=state)
        self.open_button.configure(state="normal" if self.service_online and not busy else "disabled")

    def run_setup(self) -> None:
        self.append_log("开始初始化。首次需要下载依赖和模型，音频不会上传。")
        self.run_action("setup", [sys.executable, str(ROOT / "scripts" / "setup.py")])

    def check_updates(self) -> None:
        self.append_log("正在检查公开版本，只会读取信息，不会自动升级。")
        python = runtime_python(self.settings) if self.settings else None
        executable = python if python and python.exists() else sys.executable
        self.run_action("update", [str(executable), str(ROOT / "scripts" / "check_update.py")])

    def run_action(self, name: str, command: list[str]) -> None:
        if self.action_name:
            return
        self.action_name = name
        self.set_busy(True)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            self.action_process = subprocess.Popen(
                command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=CREATE_NO_WINDOW, env=env,
            )
        except OSError as exc:
            self.append_log(f"无法启动初始化程序：{exc}", "error")
            self.set_busy(False)
            return
        threading.Thread(target=self._read_action_output, daemon=True).start()

    def _read_action_output(self) -> None:
        process = self.action_process
        action = self.action_name
        try:
            for line in process.stdout:
                self.events.put(("log", line.strip()))
            code = process.wait()
        except Exception as exc:
            self.events.put(("log", f"执行中断：{exc}"))
            code = 1
        self.events.put(("action_done", (action, code)))

    def process_events(self) -> None:
        if self.closing:
            return
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "log":
                    tag = "error" if "ERROR" in payload else "success" if any(mark in payload for mark in ["_OK", "SETUP_OK", "SMOKE_OK"]) else ""
                    self.append_log(payload, tag)
                elif event == "action_done":
                    action, code = payload
                    self.set_busy(False)
                    if action == "setup":
                        if code == 0:
                            self.append_log("初始化完成，正在启动本地服务。", "success")
                            self.reload_settings()
                            self.refresh_status(schedule=False)
                            self.root.after(300, self.start_service)
                        else:
                            self.append_log("初始化未完成。请根据最后一行提示处理后再次点击“初始化 / 修复”。", "error")
                    elif action == "update":
                        self.append_log("升级检查完成。" if code == 0 else "部分更新信息不可用，请检查网络后重试。", "success" if code == 0 else "error")
                elif event == "status":
                    self._apply_status(payload)
                elif event == "service_online":
                    self.service_online = True
                    self.service_var.set("服务在线，可以打开转写页面")
                    self.open_button.configure(state="normal")
                    self.set_busy(False)
        except queue.Empty:
            pass
        self.root.after(100, self.process_events)

    def refresh_status(self, schedule: bool = True) -> None:
        if self.closing:
            return
        if not self.status_refreshing and self.settings:
            self.status_refreshing = True
            threading.Thread(target=self._status_worker, daemon=True).start()
        if schedule:
            self.root.after(4000, self.refresh_status)

    def _status_worker(self) -> None:
        settings = self.settings
        result = {"dependencies": False, "model": False, "online": False, "model_state": "not_ready"}
        try:
            python = runtime_python(settings)
            if python.exists():
                code = (
                    "import importlib.metadata as m, json, sys; "
                    "requirements=json.loads(sys.argv[1]); "
                    "sys.exit(0 if all(m.version(n)==v for n,v in requirements.items()) else 1)"
                )
                check = subprocess.run(
                    [str(python), "-c", code, json.dumps(settings["dependencies"])],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=CREATE_NO_WINDOW, timeout=8, check=False,
                )
                result["dependencies"] = check.returncode == 0
            model_dir = Path(settings["paths"]["model_dir"])
            result["model"] = (model_dir / "model.bin").is_file() and (model_dir / "config.json").is_file()
            host = settings["server"]["host"]
            port = int(settings["server"]["port"])
            with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=1) as response:
                health = json.load(response)
            result["online"] = health.get("application") == "voice2txt"
            result["model_state"] = health.get("model_state", "not_ready")
        except Exception:
            pass
        self.events.put(("status", result))

    def _apply_status(self, status: dict) -> None:
        self.status_refreshing = False
        self.dependencies_ready = status["dependencies"]
        self.service_online = status["online"]
        self.dependency_var.set("依赖已就绪" if status["dependencies"] else "依赖未就绪，请点击“初始化 / 修复”")
        if status["model_state"] == "loaded":
            self.model_var.set("模型已加载，可以转写")
        elif status["model"]:
            self.model_var.set("模型已下载，首次转写时需加载")
        else:
            self.model_var.set("模型未就绪，首次使用请初始化")
        self.service_var.set("服务在线" if status["online"] else "服务离线")
        self.start_button.configure(state="normal" if status["dependencies"] and not self.action_name else "disabled")
        self.open_button.configure(state="normal" if status["online"] and not self.action_name else "disabled")

    def start_service(self) -> None:
        if self.action_name:
            return
        if not self.settings:
            messagebox.showerror("配置不可用", "请先点击“高级配置”修复配置。")
            return
        self.reload_settings()
        if not self.settings:
            self.append_log("配置仍不可用，请通过“高级配置”修复后再启动。", "error")
            return
        self.action_name = "service"
        self.set_busy(True)
        self.append_log("正在启动或重启本地服务…")
        if self.service_process and self.service_process.poll() is None:
            self.service_process.terminate()
            try:
                self.service_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.service_process.kill()
        if self.service_log:
            self.service_log.close()
        python = runtime_python(self.settings)
        if not python.exists():
            self.append_log("运行环境不存在，请先点击“初始化 / 修复”。", "error")
            self.set_busy(False)
            return
        log_dir = Path(self.settings["data"]["logs_dir"])
        log_dir.mkdir(parents=True, exist_ok=True)
        self.service_log = open(log_dir / "launcher-service.log", "a", encoding="utf-8")
        try:
            self.service_process = subprocess.Popen(
                [str(python), str(ROOT / "scripts" / "run.py")], cwd=ROOT,
                stdout=self.service_log, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
            )
        except OSError as exc:
            self.append_log(f"服务启动失败：{exc}", "error")
            self.set_busy(False)
            return
        threading.Thread(target=self._wait_for_service, daemon=True).start()

    def _wait_for_service(self) -> None:
        host = self.settings["server"]["host"]
        port = int(self.settings["server"]["port"])
        process = self.service_process
        time.sleep(0.4)
        for _ in range(40):
            try:
                with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=1) as response:
                    if json.load(response).get("application") == "voice2txt":
                        if process and process.poll() is not None:
                            if process.returncode == 0:
                                self.events.put(("log", "SERVICE_REUSED 已有本项目服务在线，已直接复用；如需重启，请先关闭原启动器。"))
                                self.events.put(("service_online", True))
                                return
                            break
                        self.events.put(("service_online", True))
                        return
            except Exception:
                pass
            if process and process.poll() is not None:
                break
            time.sleep(0.25)
        self.events.put(("log", "SERVICE_ERROR 服务未能启动。请查看 data/logs，或检查端口占用后重试。"))
        self.events.put(("action_done", ("service", 1)))

    def open_page(self) -> None:
        if not self.settings or not self.service_online:
            return
        host = self.settings["server"]["host"]
        port = int(self.settings["server"]["port"])
        webbrowser.open(f"http://{host}:{port}/")

    def open_config(self) -> None:
        example = ROOT / "config" / "stack.local.yaml.example"
        try:
            content = LOCAL_CONFIG.read_text(encoding="utf-8-sig") if LOCAL_CONFIG.exists() else example.read_text(encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("配置读取失败", str(exc))
            return
        window = tk.Toplevel(self.root)
        window.title("高级配置 · 本机私有，不会入库")
        window.geometry("650x570")
        window.transient(self.root)
        editor = tk.Text(window, wrap="none", font=("Consolas", 11), padx=12, pady=12)
        editor.pack(fill="both", expand=True, padx=15, pady=(15, 8))
        editor.insert("1.0", content)
        hint = ttk.Label(window, text="使用 JSON 语法。保存会备份旧配置并自动应用；在线服务将重启。", style="Subtitle.TLabel")
        hint.pack(padx=15, anchor="w")

        def save() -> None:
            try:
                data = json.loads(editor.get("1.0", "end-1c"))
                if not isinstance(data, dict):
                    raise ValueError("顶层必须是 JSON 对象")
                save_local_settings(data)
                apply_config()
                self.reload_settings()
                self.append_log("本机配置已保存并应用。", "success")
                window.destroy()
                self.refresh_status(schedule=False)
                if self.service_online:
                    self.start_service()
            except Exception as exc:
                messagebox.showerror("配置未保存", f"请检查格式：{exc}", parent=window)

        ttk.Button(window, text="保存并应用", style="Accent.TButton", command=save).pack(pady=15)

    def open_logs(self) -> None:
        directory = Path(self.settings["data"]["logs_dir"]) if self.settings else ROOT / "data" / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(directory)
        else:
            webbrowser.open(directory.as_uri())

    def on_close(self) -> None:
        if self.closing:
            return
        owns_running_service = self.service_process and self.service_process.poll() is None
        if owns_running_service and not messagebox.askyesno("关闭启动器", "关闭后，由本启动器启动的服务也会停止。确定关闭吗？"):
            return
        self.closing = True
        for process in [self.action_process, self.service_process]:
            if process and process.poll() is None:
                process.terminate()
        if self.service_log:
            self.service_log.close()
        self.root.destroy()


def main() -> None:
    try:
        root = tk.Tk()
        Launcher(root)
        root.mainloop()
    except Exception as exc:
        try:
            messagebox.showerror("启动器无法打开", f"请确认 Python 已包含 Tkinter。原因：{exc}")
        except Exception:
            print(f"启动器无法打开：{exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
