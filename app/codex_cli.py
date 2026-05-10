from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CodexCommand:
    args: list[str]
    display: str


def resolve_codex_command() -> CodexCommand:
    if sys.platform == "win32":
        cmd = shutil.which("codex.cmd")
        if cmd:
            return CodexCommand([cmd], cmd)

        exe = shutil.which("codex.exe")
        if exe:
            return CodexCommand([exe], exe)

        ps1 = shutil.which("codex.ps1")
        if ps1:
            powershell = shutil.which("powershell.exe") or shutil.which("powershell")
            if powershell:
                return CodexCommand(
                    [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1],
                    ps1,
                )

        bare = shutil.which("codex")
        if bare:
            suffix = Path(bare).suffix.lower()
            if suffix in {".exe", ".cmd", ".bat"}:
                return CodexCommand([bare], bare)
            raise RuntimeError(
                f"Codex was found at {bare}, but it is not directly executable on Windows. "
                "Install the Codex CLI so `codex.cmd` or `codex.exe` is available on PATH."
            )

        raise RuntimeError(
            "Codex CLI was not found on PATH. Install Codex, run `codex login`, "
            "then restart the Telegram operator."
        )

    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError(
            "Codex CLI was not found on PATH. Install Codex, run `codex login`, "
            "then restart the Telegram operator."
        )
    return CodexCommand([executable], executable)

