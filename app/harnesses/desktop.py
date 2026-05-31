from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from harnesses.cli import resolve_codex_command
from process_utils import hidden_subprocess_kwargs


def build_host_url(host: str, port: str, path: str = "") -> str:
    host = (host or "").strip() or "127.0.0.1"
    port = (port or "").strip()
    if host.startswith(("http://", "https://")):
        base = host.rstrip("/")
    else:
        base = f"http://{host}"
    if port and ":" not in base.rsplit("/", 1)[-1]:
        base = f"{base}:{port}"
    return base.rstrip("/") + path


def require_executable(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"Could not find {name} on PATH")
    return path


def allowed_write_dirs(values: dict[str, str], execution_dir: Path, default_workspace: Path) -> list[Path]:
    paths = [execution_dir]
    workdir = Path(values.get("TELEGRAM_OPERATOR_WORKDIR") or default_workspace).resolve()
    if workdir != execution_dir:
        paths.append(workdir)
    for part in (values.get("TELEGRAM_OPERATOR_ALLOWED_PATHS") or "").split(";"):
        value = part.strip()
        if value:
            paths.append(Path(value).expanduser().resolve())
    unique = []
    seen = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def jcode_base_url(values: dict[str, str]) -> str:
    provider = values.get("TELEGRAM_OPERATOR_MODEL_PROVIDER", "").strip().lower()
    if provider == "ollama":
        host = values.get("TELEGRAM_OPERATOR_REMOTE_HOST", "").strip() or "127.0.0.1"
        port = values.get("TELEGRAM_OPERATOR_LLM_PORT", "").strip() or "11434"
        return build_host_url(host, port, "/v1")
    return values.get("TELEGRAM_OPERATOR_LM_STUDIO_BASE_URL", "").strip().rstrip("/")


def ensure_jcode_api_key(values: dict[str, str], default_workspace: Path) -> None:
    api_key = values.get("TELEGRAM_OPERATOR_JCODE_API_KEY", "").strip()
    jcode_provider = values.get("TELEGRAM_OPERATOR_MODEL_PROVIDER", "").strip()
    if not api_key or jcode_provider in {"", "lmstudio", "ollama"}:
        return
    subprocess.run(
        [
            require_executable("jcode"),
            "login",
            "--provider",
            jcode_provider,
            "--api-key",
            api_key,
            "--no-validate",
            "--quiet",
        ],
        cwd=values.get("TELEGRAM_OPERATOR_WORKDIR") or str(default_workspace),
        text=True,
        capture_output=True,
        timeout=30,
        **hidden_subprocess_kwargs(),
    )


def ensure_jcode_local_profile(values: dict[str, str], default_workspace: Path) -> str:
    provider = values.get("TELEGRAM_OPERATOR_MODEL_PROVIDER", "").strip().lower()
    model = values.get("TELEGRAM_OPERATOR_CODEX_MODEL", "").strip()
    base_url = jcode_base_url(values)
    if provider not in {"lmstudio", "ollama"} or not model or not base_url:
        return ""
    profile = f"baseclaw-{provider}"
    result = subprocess.run(
        [
            require_executable("jcode"),
            "provider",
            "add",
            profile,
            "--base-url",
            base_url,
            "--model",
            model,
            "--no-api-key",
            "--auth",
            "none",
            "--overwrite",
            "--quiet",
        ],
        cwd=values.get("TELEGRAM_OPERATOR_WORKDIR") or str(default_workspace),
        text=True,
        capture_output=True,
        timeout=30,
        **hidden_subprocess_kwargs(),
    )
    return profile if result.returncode == 0 else ""


def build_desktop_agent_command(
    provider: str,
    prompt: str,
    session_id: str | None,
    values: dict[str, str],
    *,
    base_dir: Path,
    default_workspace: Path,
) -> tuple[list[str], str | None]:
    if provider == "jcode":
        ensure_jcode_api_key(values, default_workspace)
        cmd = [
            require_executable("jcode"),
            "--quiet",
            "--no-update",
            "--no-selfdev",
        ]
        profile = values.get("TELEGRAM_OPERATOR_JCODE_PROVIDER_PROFILE", "").strip()
        jcode_provider = values.get("TELEGRAM_OPERATOR_MODEL_PROVIDER", "").strip()
        model = values.get("TELEGRAM_OPERATOR_CODEX_MODEL", "").strip()
        if not profile:
            profile = ensure_jcode_local_profile(values, default_workspace)
        if profile:
            cmd.extend(["--provider-profile", profile])
        elif jcode_provider:
            cmd.extend(["--provider", jcode_provider])
        if model:
            cmd.extend(["--model", model])
        if session_id and not session_id.startswith("jcode:latest"):
            cmd.extend(["--resume", session_id])
        cmd.extend(["run", "--json", prompt])
        return cmd, None

    if provider == "claude":
        cmd = [require_executable("claude"), "-p", "--dangerously-skip-permissions", "--output-format", "text"]
        model = values.get("TELEGRAM_OPERATOR_CODEX_MODEL", "").strip()
        if model and model != "default":
            cmd.extend(["--model", model])
        if session_id:
            cmd.append("--continue")
        return cmd, prompt

    if provider == "gemini":
        cmd = [require_executable("gemini"), "--prompt", "", "--skip-trust", "--output-format", "text"]
        model = values.get("TELEGRAM_OPERATOR_CODEX_MODEL", "").strip()
        if model and model != "default":
            cmd.extend(["--model", model])
        action_mode = values.get("TELEGRAM_OPERATOR_ACTION_MODE", "full").strip().lower()
        approval_mode = "plan" if action_mode == "read" else "default" if action_mode == "approve" else "yolo"
        cmd.extend(["--approval-mode", approval_mode])
        if session_id:
            cmd.extend(["--resume", "latest"])
        return cmd, prompt

    if provider == "codex":
        codex = resolve_codex_command()
        workdir = Path(values.get("TELEGRAM_OPERATOR_WORKDIR") or default_workspace).resolve()
        access_scope = values.get("TELEGRAM_OPERATOR_ACCESS_SCOPE", "workspace").strip().lower()
        action_mode = values.get("TELEGRAM_OPERATOR_ACTION_MODE", "full").strip().lower()
        execution_dir = base_dir if access_scope == "code" else workdir
        cmd = [
            *codex.args,
            "exec",
            "--skip-git-repo-check",
            "-C",
            str(execution_dir),
        ]
        if access_scope == "full" and action_mode == "full":
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            cmd.extend(["--sandbox", "read-only" if action_mode == "read" else "workspace-write"])
            if action_mode != "read":
                add_dirs = allowed_write_dirs(values, execution_dir, default_workspace)
                for path in add_dirs[1:]:
                    cmd.extend(["--add-dir", str(path)])
        model = values.get("TELEGRAM_OPERATOR_CODEX_MODEL", "").strip()
        if model and model != "default":
            cmd.extend(["--model", model])
        cmd.append("-")
        return cmd, prompt

    raise RuntimeError(f"Desktop chat currently supports jcode, codex, claude, and gemini, not {provider}.")
