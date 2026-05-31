from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from typing import Any


LOGGER = logging.getLogger("telegram_operator")


def hidden_subprocess_kwargs() -> dict:
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def agent_subprocess_kwargs() -> dict:
    kwargs = hidden_subprocess_kwargs()
    if sys.platform == "win32":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return kwargs


def agent_subprocess_env() -> dict[str, str]:
    """Run coding agents without inheriting BaseClaw's Telegram bot credentials."""
    env = os.environ.copy()
    blocked_keys = {
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_ALLOWED_CHAT_IDS",
        "TELEGRAM_ALLOWED_CHAT_ID",
        "TELEGRAM_CHAT_ID",
    }
    for key in list(env):
        if key in blocked_keys:
            env.pop(key, None)
    return env


def terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        taskkill = shutil.which("taskkill")
        if taskkill:
            subprocess.run(
                [taskkill, "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                **hidden_subprocess_kwargs(),
            )
            try:
                process.wait(timeout=5)
            except Exception:
                pass
            return
    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
        except Exception:
            LOGGER.warning("Failed to kill process pid=%s", process.pid, exc_info=True)

