from __future__ import annotations

import json
import logging
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from harnesses.cli import resolve_codex_command
from process_utils import agent_subprocess_env, hidden_subprocess_kwargs, terminate_process_tree

APP_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = APP_DIR.parent
CODEX_FINAL_MESSAGE_GRACE_SECONDS = 8.0
LOGGER = logging.getLogger("telegram_operator")


def friendly_codex_error(detail: str, exit_code: int) -> str:
    cleaned = (detail or "").strip()
    lower = cleaned.lower()
    if any(token in lower for token in ("not authenticated", "login", "log in", "unauthorized", "api key", "authentication")):
        return (
            "Codex CLI appears to be unauthenticated. Run `codex login` in a local terminal, "
            "confirm Codex works, then restart the Telegram operator."
        )
    if "not recognized" in lower or "not found" in lower:
        return "Codex CLI was not found on PATH. Install Codex and restart the Telegram operator."
    return cleaned or f"Codex exited with code {exit_code}"


class CodexBridge:
    def __init__(
        self,
        workdir: Path,
        model: str,
        timeout_seconds: int,
        safety_mode: str = "safe",
        access_scope: str = "workspace",
        allowed_paths: Optional[list[Path]] = None,
        action_mode: str = "full",
    ):
        self.workdir = workdir
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds
        self.safety_mode = safety_mode
        self.access_scope = access_scope
        self.allowed_paths = allowed_paths or []
        self.action_mode = action_mode

    @property
    def execution_dir(self) -> Path:
        if self.access_scope == "code":
            return PROJECT_ROOT
        return self.workdir

    def writable_dirs(self) -> list[Path]:
        if self.access_scope == "full":
            return []
        dirs = [self.execution_dir]
        if self.access_scope == "code" and self.workdir != PROJECT_ROOT:
            dirs.append(self.workdir)
        dirs.extend(self.allowed_paths)
        unique = []
        seen = set()
        for path in dirs:
            key = str(path.resolve())
            if key not in seen:
                unique.append(path)
                seen.add(key)
        return unique

    def _base_command(self, proposal_mode: bool = False) -> list[str]:
        cmd = [
            *resolve_codex_command().args,
            "exec",
            "--skip-git-repo-check",
            "--json",
            "-C",
            str(self.execution_dir),
        ]
        if proposal_mode:
            cmd.extend(["--sandbox", "read-only", "--ephemeral"])
        elif self.access_scope == "full" and self.action_mode == "full":
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        elif self.action_mode == "read":
            cmd.extend(["--sandbox", "read-only"])
        else:
            cmd.extend(["--sandbox", "workspace-write"])
            for path in self.writable_dirs()[1:]:
                cmd.extend(["--add-dir", str(path)])
        if self.model:
            cmd.extend(["-m", self.model])
        return cmd

    @staticmethod
    def _summarize_shell_command(command: str) -> str:
        command_lower = command.lower()
        if "ssh " in command_lower:
            return "running an SSH command"
        if command_lower.strip().startswith("git ") or " git " in command_lower:
            return "running a git command"
        if "python" in command_lower:
            return "running a Python command"
        if "powershell" in command_lower or command_lower.strip().startswith("$"):
            return "running a PowerShell command"
        if "npm " in command_lower or "node " in command_lower:
            return "running a Node command"
        if "curl" in command_lower or "invoke-restmethod" in command_lower:
            return "calling a local or remote service"
        return "running a shell command"

    @staticmethod
    def _short_status_text(text: str, limit: int = 140) -> str:
        line = " ".join((text or "").strip().split())
        if not line:
            return ""
        for marker in (". ", "! ", "? "):
            if marker in line:
                line = line.split(marker, 1)[0] + marker.strip()
                break
        if len(line) > limit:
            line = line[: limit - 3].rstrip() + "..."
        return line

    def _status_from_codex_event(self, raw_line: str) -> Optional[str]:
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            return None

        event_type = event.get("type", "")
        if event_type == "thread.started":
            return "starting a Codex session"

        if event_type == "event_msg":
            payload = event.get("payload", {})
            payload_type = payload.get("type")
            if payload_type == "task_started":
                return "starting the agent turn"
            if payload_type == "agent_message":
                phase = payload.get("phase")
                if phase == "commentary":
                    status = self._short_status_text(str(payload.get("message") or ""))
                    return status or None
                if phase == "final_answer":
                    return "preparing the final reply"
            if payload_type == "task_complete":
                return "finishing up"

        if event_type == "response_item":
            payload = event.get("payload", {})
            payload_type = payload.get("type")
            if payload_type == "function_call":
                if payload.get("name") == "shell_command":
                    try:
                        arguments = json.loads(str(payload.get("arguments") or "{}"))
                    except json.JSONDecodeError:
                        arguments = {}
                    return self._summarize_shell_command(str(arguments.get("command") or ""))
                return f"using tool {payload.get('name')}"
            if payload_type == "function_call_output":
                return "checking command output"
            if payload_type == "reasoning":
                return "thinking through the next step"

        if event_type == "item.completed":
            item = event.get("item", {})
            item_type = item.get("type")
            if item_type == "command_execution":
                return "checking command output"
            if item_type == "agent_message":
                status = self._short_status_text(str(item.get("text") or item.get("message") or ""))
                return status or None

        return None

    @staticmethod
    def _event_indicates_more_work(raw_line: str) -> bool:
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            return False
        if event.get("type") != "response_item":
            return False
        payload = event.get("payload", {})
        return payload.get("type") in {"function_call", "function_call_output"}

    def _record_codex_event(
        self,
        raw_line: str,
        *,
        stderr_chunks: list[str],
    ) -> tuple[str, str, bool, bool]:
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            return "", "", False, False

        event_type = event.get("type", "")
        session_id = ""
        agent_message = ""
        completed = False
        final_answer = False

        if event_type == "thread.started":
            session_id = str(event.get("thread_id", "") or "")

        if event_type == "item.completed":
            item = event.get("item", {})
            item_type = item.get("type")
            if item_type == "agent_message":
                agent_message = str(item.get("text") or item.get("message") or "")
            if item_type == "command_execution" and item.get("exit_code") not in (None, 0):
                stderr_chunks.append(str(item.get("aggregated_output", "")))

        if event_type == "event_msg":
            payload = event.get("payload", {})
            payload_type = payload.get("type")
            if payload_type == "agent_message" and payload.get("phase") == "final_answer":
                agent_message = str(payload.get("message") or "")
                final_answer = True
            if payload_type == "task_complete":
                agent_message = str(payload.get("last_agent_message") or "")
                completed = True
                final_answer = True

        if event_type in {"task_complete", "turn.completed"}:
            agent_message = str(event.get("last_agent_message") or event.get("message") or "")
            completed = True
            final_answer = True

        return session_id, agent_message, completed, final_answer

    @staticmethod
    def _stream_reader(
        stream: Any,
        stream_name: str,
        events: "queue.Queue[tuple[str, Optional[str]]]",
    ) -> None:
        try:
            for line in iter(stream.readline, ""):
                events.put((stream_name, line))
        finally:
            events.put((stream_name, None))

    def _run(
        self,
        cmd: list[str],
        prompt: str,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> tuple[str, str]:
        process: Optional[subprocess.Popen[str]] = None
        stderr_chunks: list[str] = []
        last_agent_message = ""
        last_final_message = ""
        session_id = ""
        completion_seen = False
        final_message_seen_at: Optional[float] = None
        stdout_closed = False
        stderr_closed = False

        try:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(self.execution_dir),
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=agent_subprocess_env(),
                **agent_subprocess_kwargs(),
            )

            assert process.stdin is not None
            assert process.stdout is not None
            assert process.stderr is not None
            process.stdin.write(prompt)
            process.stdin.close()

            events: queue.Queue[tuple[str, Optional[str]]] = queue.Queue()
            stdout_thread = threading.Thread(
                target=self._stream_reader,
                args=(process.stdout, "stdout", events),
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=self._stream_reader,
                args=(process.stderr, "stderr", events),
                daemon=True,
            )
            stdout_thread.start()
            stderr_thread.start()

            deadline = time.monotonic() + self.timeout_seconds
            while True:
                if completion_seen and last_final_message:
                    terminate_process_tree(process)
                    return session_id, last_final_message

                if (
                    final_message_seen_at is not None
                    and last_final_message
                    and time.monotonic() - final_message_seen_at >= CODEX_FINAL_MESSAGE_GRACE_SECONDS
                    and process.poll() is None
                ):
                    LOGGER.warning(
                        "Codex produced a final message but did not exit after %.1fs; recovering reply pid=%s",
                        CODEX_FINAL_MESSAGE_GRACE_SECONDS,
                        process.pid,
                    )
                    terminate_process_tree(process)
                    return session_id, last_final_message

                if process.poll() is not None and stdout_closed and stderr_closed:
                    break

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    terminate_process_tree(process)
                    if last_final_message:
                        LOGGER.warning(
                            "Codex timed out after %ss, but a final message was recovered pid=%s",
                            self.timeout_seconds,
                            process.pid,
                        )
                        return session_id, last_final_message
                    raise RuntimeError(f"Codex timed out after {self.timeout_seconds} seconds")

                try:
                    stream_name, line = events.get(timeout=min(0.25, max(0.05, remaining)))
                except queue.Empty:
                    continue

                if line is None:
                    if stream_name == "stdout":
                        stdout_closed = True
                    else:
                        stderr_closed = True
                    continue

                if stream_name == "stderr":
                    if line.strip():
                        stderr_chunks.append(line)
                    continue

                raw_line = line.strip()
                if not raw_line:
                    continue
                if status_callback:
                    status = self._status_from_codex_event(raw_line)
                    if status:
                        status_callback(status)
                new_session_id, agent_message, completed, final_answer = self._record_codex_event(
                    raw_line,
                    stderr_chunks=stderr_chunks,
                )
                if new_session_id:
                    session_id = new_session_id
                if agent_message:
                    last_agent_message = agent_message
                    if final_answer:
                        last_final_message = agent_message
                        final_message_seen_at = time.monotonic()
                if completed:
                    if not last_final_message and last_agent_message:
                        last_final_message = last_agent_message
                    completion_seen = True

        except BrokenPipeError as exc:
            raise RuntimeError("Codex process closed before it accepted the prompt") from exc
        finally:
            if process is not None and process.poll() is None and not (completion_seen and last_final_message):
                terminate_process_tree(process)

        assert process is not None
        return_code = process.returncode if process.returncode is not None else 1
        if return_code != 0 and not last_agent_message:
            detail = "".join(stderr_chunks).strip()
            raise RuntimeError(friendly_codex_error(detail, return_code))
        if not last_agent_message:
            raise RuntimeError("Codex returned no final agent message")
        return session_id, last_final_message or last_agent_message

    def send(
        self,
        prompt: str,
        session_id: Optional[str],
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> tuple[str, str]:
        if session_id:
            cmd = self._base_command() + ["resume", session_id, "-"]
        else:
            cmd = self._base_command() + ["-"]
        return self._run(cmd, prompt, status_callback=status_callback)

    def propose(self, prompt: str) -> str:
        cmd = self._base_command(proposal_mode=True) + ["-"]
        _session_id, proposal = self._run(cmd, prompt)
        return proposal


class GenericCliBridge:
    def __init__(self, provider: str, workdir: Path, command_template: str, timeout_seconds: int):
        self.provider = provider
        self.workdir = workdir
        self.command_template = command_template.strip()
        self.timeout_seconds = timeout_seconds

    def send(self, prompt: str, session_id: Optional[str]) -> tuple[str, str]:
        if not self.command_template:
            raise RuntimeError(
                f"No command template configured for provider '{self.provider}'. "
                "Set TELEGRAM_OPERATOR_AGENT_COMMAND in .env.telegram-operator."
            )

        prompt_path = None
        try:
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt", encoding="utf-8") as handle:
                handle.write(prompt)
                prompt_path = Path(handle.name)

            command = self.command_template.format(
                prompt_file=str(prompt_path),
                workdir=str(self.workdir),
                session_id=session_id or "",
            )
            env = agent_subprocess_env()
            env["TELEGRAM_OPERATOR_PROVIDER"] = self.provider
            env["TELEGRAM_OPERATOR_WORKDIR"] = str(self.workdir)
            env["TELEGRAM_OPERATOR_SESSION_ID"] = session_id or ""
            env["TELEGRAM_OPERATOR_PROMPT_FILE"] = str(prompt_path)
            try:
                process = subprocess.run(
                    command,
                    input=prompt,
                    shell=True,
                    text=True,
                    capture_output=True,
                    cwd=str(self.workdir),
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    timeout=self.timeout_seconds,
                    **hidden_subprocess_kwargs(),
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"{self.provider} timed out after {self.timeout_seconds} seconds") from exc
        finally:
            if prompt_path and prompt_path.exists():
                prompt_path.unlink(missing_ok=True)

        output = process.stdout.strip()
        detail = process.stderr.strip()
        if process.returncode != 0 and not output:
            raise RuntimeError(detail or f"{self.provider} exited with code {process.returncode}")
        if not output:
            raise RuntimeError(f"{self.provider} returned no stdout reply")
        return session_id or f"{self.provider}:stateless", output


class LocalCliBridge:
    def __init__(
        self,
        provider: str,
        workdir: Path,
        timeout_seconds: int,
        model: str = "",
        jcode_provider_profile: str = "",
        jcode_provider_id: str = "",
        jcode_api_key: str = "",
        jcode_base_url: str = "",
        action_mode: str = "full",
    ):
        self.provider = provider
        self.workdir = workdir
        self.timeout_seconds = timeout_seconds
        self.model = model.strip()
        self.jcode_provider_profile = jcode_provider_profile.strip()
        self.jcode_provider_id = jcode_provider_id.strip()
        self.jcode_api_key = jcode_api_key.strip()
        self.jcode_base_url = jcode_base_url.strip().rstrip("/")
        self.action_mode = action_mode.strip().lower()

    def _command(self, prompt: str, session_id: Optional[str]) -> tuple[list[str], Optional[str]]:
        if self.provider == "claude":
            cmd = ["claude", "-p", "--dangerously-skip-permissions", "--output-format", "text"]
            if self.model and self.model != "default":
                cmd.extend(["--model", self.model])
            if session_id:
                cmd.append("--continue")
            return cmd, prompt
        if self.provider == "gemini":
            cmd = ["gemini", "--prompt", "", "--skip-trust", "--output-format", "text"]
            if self.model and self.model != "default":
                cmd.extend(["--model", self.model])
            approval_mode = "plan" if self.action_mode == "read" else "default" if self.action_mode == "approve" else "yolo"
            cmd.extend(["--approval-mode", approval_mode])
            if session_id:
                cmd.extend(["--resume", "latest"])
            return cmd, prompt
        if self.provider == "jcode":
            self._ensure_jcode_api_key()
            profile = self.jcode_provider_profile or self._ensure_jcode_local_profile()
            cmd = [
                self._jcode_executable(),
                "--quiet",
                "--no-update",
                "--no-selfdev",
            ]
            if profile:
                cmd.extend(["--provider-profile", profile])
            elif self.jcode_provider_id:
                cmd.extend(["--provider", self.jcode_provider_id])
            if self.model:
                cmd.extend(["--model", self.model])
            if session_id and not session_id.startswith("jcode:latest"):
                cmd.extend(["--resume", session_id])
            cmd.extend(["run", "--json", prompt])
            return cmd, None
        raise RuntimeError(f"Unsupported provider: {self.provider}")

    def _jcode_executable(self) -> str:
        for name in ("jcode.exe", "jcode"):
            path = shutil.which(name)
            if path:
                return path
        raise RuntimeError("Could not find jcode on PATH")

    def _ensure_jcode_api_key(self) -> None:
        if not self.jcode_api_key or self.jcode_provider_id in {"", "lmstudio", "ollama"}:
            return
        subprocess.run(
            [
                self._jcode_executable(),
                "login",
                "--provider",
                self.jcode_provider_id,
                "--api-key",
                self.jcode_api_key,
                "--no-validate",
                "--quiet",
            ],
            text=True,
            capture_output=True,
            cwd=str(self.workdir),
            timeout=30,
            **hidden_subprocess_kwargs(),
        )

    def _ensure_jcode_local_profile(self) -> str:
        if self.jcode_provider_id not in {"lmstudio", "ollama"} or not self.jcode_base_url or not self.model:
            return ""
        profile = f"baseclaw-{self.jcode_provider_id}"
        result = subprocess.run(
            [
                self._jcode_executable(),
                "provider",
                "add",
                profile,
                "--base-url",
                self.jcode_base_url,
                "--model",
                self.model,
                "--no-api-key",
                "--auth",
                "none",
                "--overwrite",
                "--quiet",
            ],
            text=True,
            capture_output=True,
            cwd=str(self.workdir),
            timeout=30,
            **hidden_subprocess_kwargs(),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            LOGGER.warning("Failed to configure JCode local profile provider=%s base_url=%s error=%s", self.jcode_provider_id, self.jcode_base_url, detail)
            return ""
        return profile

    def _parse_jcode_output(self, output: str, fallback_session_id: Optional[str]) -> tuple[str, str]:
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return fallback_session_id or "jcode:latest", output
        if not isinstance(payload, dict):
            return fallback_session_id or "jcode:latest", output
        reply = str(payload.get("text") or "").strip()
        if not reply:
            reply = "I received an empty response from jcode. Please try that once more; I will keep the reply conversational instead of sending raw harness JSON."
        session_id = str(payload.get("session_id") or fallback_session_id or "jcode:latest")
        return session_id, reply

    def send(self, prompt: str, session_id: Optional[str]) -> tuple[str, str]:
        cmd, stdin_text = self._command(prompt, session_id)
        env = agent_subprocess_env()
        if self.provider == "jcode":
            env.setdefault("JCODE_NO_TELEMETRY", "1")
            if self.jcode_base_url:
                env["BASECLAW_JCODE_BASE_URL"] = self.jcode_base_url
                env["OPENAI_BASE_URL"] = self.jcode_base_url
                env["LM_STUDIO_BASE_URL"] = self.jcode_base_url
                if self.jcode_provider_id == "ollama":
                    env["OLLAMA_HOST"] = self.jcode_base_url.removesuffix("/v1")
        try:
            process = subprocess.run(
                cmd,
                input=stdin_text,
                text=True,
                capture_output=True,
                cwd=str(self.workdir),
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=self.timeout_seconds,
                **hidden_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{self.provider} timed out after {self.timeout_seconds} seconds") from exc
        output = process.stdout.strip()
        detail = process.stderr.strip()
        if process.returncode != 0 and not output:
            raise RuntimeError(detail or f"{self.provider} exited with code {process.returncode}")
        if not output:
            raise RuntimeError(f"{self.provider} returned no stdout reply")
        if self.provider == "jcode":
            return self._parse_jcode_output(output, session_id)
        return session_id or f"{self.provider}:latest", output


def build_agent_bridge(config):
    provider = config.agent_provider.strip().lower() or "codex"
    if provider == "codex":
        return CodexBridge(
            config.workdir,
            config.codex_model,
            config.agent_timeout_seconds,
            config.safety_mode,
            config.access_scope,
            config.allowed_paths,
            config.action_mode,
        )
    if provider in {"claude", "gemini", "jcode"} and not config.agent_command.strip():
        return LocalCliBridge(
            provider,
            config.workdir,
            config.agent_timeout_seconds,
            config.codex_model,
            config.jcode_provider_profile,
            config.jcode_provider_id,
            config.jcode_api_key,
            config.jcode_base_url,
            config.action_mode,
        )
    return GenericCliBridge(provider, config.workdir, config.agent_command, config.agent_timeout_seconds)

