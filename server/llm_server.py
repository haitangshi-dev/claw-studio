# -*- coding: utf-8 -*-
"""llama-server 子进程管理：启动 / 健康轮询 / 停止 / 换模型重启。

所有命令行参数都由 config.defaults + 模型 profile 映射而来（零硬编码）。
要点（来自 2026-09 实测教训）：
  - ngl 必须传确切大数（999），绝不用 -1（差 4.3 倍速度）；
  - KV 量化 -ctk/-ctv 是 2GB 显存跑 32K 上下文的必要条件；
  - --reasoning auto + --reasoning-format deepseek：思考走 reasoning_content。
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from pathlib import Path

import winproc as _winproc  # 子进程统一不弹控制台窗口（Windows GUI 宿主必需）


class LlamaServer:
    def __init__(self, cfg: dict, project_root: Path):
        engine = cfg["engine"]
        self.exe = engine["server_exe"]
        self.host = engine["host"]
        self.port = int(engine["port"])
        self.startup_timeout = int(engine.get("startup_timeout", 150))
        self.log_file = project_root / engine.get("log_file", "logs/llama-server.log")
        self.defaults = dict(cfg.get("defaults", {}))
        self._proc: subprocess.Popen | None = None

    # ---------- 基础 ----------
    @property
    def base(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _get(self, path: str, timeout: float = 3.0):
        try:
            with urllib.request.urlopen(self.base + path, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            return None

    def is_running(self) -> bool:
        return self._get("/health") is not None

    def current_model_path(self) -> str | None:
        props = self._get("/props")
        return props.get("model_path") if props else None

    # ---------- 参数 ----------
    def merged_params(self, profile: dict | None) -> dict:
        """defaults → profile 覆盖（去掉说明性键）。"""
        params = dict(self.defaults)
        for k, v in (profile or {}).items():
            if k in ("path", "note"):
                continue
            params[k] = v
        return params

    def build_args(self, model_path: str, params: dict) -> list[str]:
        args = [
            self.exe,
            "-m", model_path,
            "--host", self.host,
            "--port", str(self.port),
            "-c", str(params.get("ctx", 32768)),
        ]
        if params.get("mmproj"):
            args += ["--mmproj", str(params["mmproj"])]  # 视觉投影器（VL 模型）
        if params.get("kv_k"):
            args += ["-ctk", str(params["kv_k"])]
        if params.get("kv_v"):
            args += ["-ctv", str(params["kv_v"])]
        args += ["-ngl", str(params.get("ngl", 999))]
        if params.get("flash_attn"):
            args += ["-fa", "on"]
        if params.get("temperature") is not None:
            args += ["--temp", str(params["temperature"])]
        if params.get("repeat_penalty") is not None:
            args += ["--repeat-penalty", str(params["repeat_penalty"])]
        if params.get("repeat_last_n") is not None:
            args += ["--repeat-last-n", str(params["repeat_last_n"])]
        if params.get("reasoning"):
            args += ["--reasoning", str(params["reasoning"])]
        args += ["--reasoning-format", str(params.get("reasoning_format", "deepseek"))]
        args += ["-np", "1"]
        return args

    # ---------- 生命周期 ----------
    def start(self, model_path: str, params: dict) -> None:
        if self.is_running():
            self.stop()
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        log = open(self.log_file, "a", encoding="utf-8", errors="replace")
        log.write(f"\n===== spawn {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        args = self.build_args(model_path, params)
        self._proc = _winproc.popen(
            args, stdout=log, stderr=subprocess.STDOUT,
        )
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            if self.is_running():
                return
            if self._proc.poll() is not None:
                raise RuntimeError(
                    f"llama-server 启动即退出（code={self._proc.returncode}），日志: {self.log_file}"
                )
            time.sleep(1.5)
        raise TimeoutError(f"llama-server 启动超时（{self.startup_timeout}s），日志: {self.log_file}")

    def _pid_on_port(self) -> int | None:
        """通过 netstat 找占用本端口的 llama-server 进程（句柄失效时的兜底）。"""
        try:
            out = _winproc.run(["netstat", "-ano"], capture_output=True).stdout
            text = out.decode("utf-8", "replace")
            for line in text.splitlines():
                if f":{self.port}" in line and "LISTENING" in line:
                    pid = int(line.split()[-1])
                    tl = _winproc.run(
                        ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True
                    ).stdout.decode("utf-8", "replace")
                    if "llama-server" in tl:
                        return pid
        except Exception:
            pass
        return None

    def stop(self) -> bool:
        killed = False
        if self._proc and self._proc.poll() is None:
            _winproc.run(
                ["taskkill", "/F", "/T", "/PID", str(self._proc.pid)], capture_output=True
            )
            killed = True
        if self.is_running():
            pid = self._pid_on_port()
            if pid:
                _winproc.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
                killed = True
        for _ in range(20):
            if not self.is_running():
                break
            time.sleep(0.5)
        self._proc = None
        return killed

    def ensure(self, model_path: str, params: dict) -> dict:
        """保证服务在跑且加载的是目标模型；不一致则重启。"""
        if self.is_running():
            cur = self.current_model_path()
            if cur and Path(cur).resolve() == Path(model_path).resolve():
                return {"action": "reused", "model_path": cur}
            self.stop()
            self.start(model_path, params)
            return {"action": "switched", "model_path": model_path}
        self.start(model_path, params)
        return {"action": "started", "model_path": model_path}

    def status(self) -> dict:
        running = self.is_running()
        return {
            "running": running,
            "base": self.base,
            "model_path": self.current_model_path() if running else None,
        }
