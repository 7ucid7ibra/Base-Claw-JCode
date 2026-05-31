from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CliCommand:
    args: list[str]
    display: str


CodexCommand = CliCommand


def _node_wrapped_command(executable: str) -> CliCommand:
    path = Path(executable)
    try:
        first_line = path.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    except (OSError, IndexError):
        first_line = ""
    if path.suffix == ".js" or "node" in first_line:
        node = shutil.which("node")
        for candidate in ("/opt/homebrew/bin/node", "/usr/local/bin/node"):
            if not node and Path(candidate).exists():
                node = candidate
        if node:
            return CliCommand([node, executable], executable)
    return CliCommand([executable], executable)


def _resolve_windows_codex() -> CliCommand:
    cmd = shutil.which("codex.cmd")
    if cmd:
        return CliCommand([cmd], cmd)

    exe = shutil.which("codex.exe")
    if exe:
        return CliCommand([exe], exe)

    ps1 = shutil.which("codex.ps1")
    if ps1:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell:
            return CliCommand(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1],
                ps1,
            )

    bare = shutil.which("codex")
    if bare:
        suffix = Path(bare).suffix.lower()
        if suffix in {".exe", ".cmd", ".bat"}:
            return CliCommand([bare], bare)
        raise RuntimeError(
            f"Codex was found at {bare}, but it is not directly executable on Windows. "
            "Install the Codex CLI so `codex.cmd` or `codex.exe` is available on PATH."
        )

    raise RuntimeError(
        "Codex CLI was not found on PATH. Install Codex, run `codex login`, "
        "then restart the Telegram operator."
    )


def resolve_codex_command() -> CliCommand:
    if sys.platform == "win32":
        return _resolve_windows_codex()

    executable = shutil.which("codex")
    if not executable:
        for candidate in ("/opt/homebrew/bin/codex", "/usr/local/bin/codex"):
            if Path(candidate).exists():
                executable = candidate
                break
    if not executable:
        raise RuntimeError(
            "Codex CLI was not found on PATH. Install Codex, run `codex login`, "
            "then restart the Telegram operator."
        )
    return _node_wrapped_command(executable)


def resolve_named_cli_command(name: str) -> CliCommand:
    executable = shutil.which(name)
    if not executable and sys.platform == "win32":
        for suffix in (".cmd", ".exe", ".bat", ".ps1"):
            executable = shutil.which(f"{name}{suffix}")
            if executable:
                break
    if not executable:
        raise RuntimeError(f"{name} CLI was not found on PATH.")
    return CliCommand([executable], executable)


def resolve_cli_command(provider: str) -> CliCommand:
    provider = provider.strip().lower()
    if provider == "codex":
        return resolve_codex_command()
    if provider in {"jcode", "claude", "gemini"}:
        return resolve_named_cli_command(provider)
    raise RuntimeError(f"Unsupported CLI provider: {provider}")
