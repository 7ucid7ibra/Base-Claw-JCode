from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from operator_core.config import DEFAULT_MANUAL_UPDATE_REF
from process_utils import hidden_subprocess_kwargs

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OPERATOR_ENTRYPOINT = Path(__file__).resolve().parents[1] / "telegram_operator.py"
LOGGER = logging.getLogger("telegram_operator")


class UpdateLifecycleMixin:
    def _git_command(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            **hidden_subprocess_kwargs(),
        )

    def _is_git_repo(self) -> bool:
        result = self._git_command(["rev-parse", "--is-inside-work-tree"])
        return result.returncode == 0 and result.stdout.strip().lower() == "true"

    def _git_dirty(self) -> bool:
        if not self._is_git_repo():
            return False
        result = self._git_command(["status", "--porcelain", "--untracked-files=normal"])
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip() or "git status failed")
        return bool(result.stdout.strip())

    def _git_commit_all(self, message: str) -> Optional[str]:
        if not self._is_git_repo():
            LOGGER.info("Skipping git checkpoint because this install is not a git repository.")
            return None
        if not self._git_dirty():
            return None
        add_result = self._git_command(["add", "-A"])
        if add_result.returncode != 0:
            raise RuntimeError((add_result.stderr or add_result.stdout).strip() or "git add failed")
        commit_result = self._git_command(["commit", "-m", message])
        if commit_result.returncode != 0:
            raise RuntimeError((commit_result.stderr or commit_result.stdout).strip() or "git commit failed")
        rev_result = self._git_command(["rev-parse", "--short", "HEAD"])
        if rev_result.returncode == 0:
            return rev_result.stdout.strip()
        return None

    def _manual_update_ref(self, requested_ref: Optional[str] = None) -> str:
        ref = (requested_ref or "").strip() or self.config.manual_update_ref.strip() or DEFAULT_MANUAL_UPDATE_REF
        return ref

    def _manual_update_summary(self, requested_ref: Optional[str] = None) -> str:
        remote = self._select_source_update_remote()
        ref = self._manual_update_ref(requested_ref)
        current = self._git_command(["rev-parse", "--short", "HEAD"])
        current_text = current.stdout.strip() if current.returncode == 0 else "unknown"
        dirty = "yes" if self._git_dirty() else "no"
        return (
            "Manual source update.\n"
            f"Remote: {remote or 'not configured'}\n"
            f"Ref: {ref}\n"
            f"Current commit: {current_text}\n"
            f"Local changes: {dirty}\n\n"
            "This will fetch from the configured source mirror and fast-forward only. It will not overwrite local edits."
        )

    def _pull_manual_update(self, requested_ref: Optional[str] = None) -> str:
        if self._git_dirty():
            return "Update blocked: local worktree is not clean."
        remote = self._select_source_update_remote()
        if not remote:
            return (
                "Update blocked: no usable source update remote is configured. "
                "Set TELEGRAM_OPERATOR_SOURCE_UPDATE_REMOTE or add a source mirror remote."
            )
        ref = self._manual_update_ref(requested_ref)
        fetch = self._git_command(["fetch", remote, ref])
        if fetch.returncode != 0:
            return "Update blocked: git fetch failed: " + (fetch.stderr or fetch.stdout).strip()
        target = self._git_command(["rev-parse", "--short", "FETCH_HEAD"])
        target_text = target.stdout.strip() if target.returncode == 0 else ref
        current = self._git_command(["rev-parse", "--short", "HEAD"])
        if current.returncode == 0 and current.stdout.strip() == target_text:
            return f"Already current at {target_text}."
        merge = self._git_command(["merge", "--ff-only", "FETCH_HEAD"])
        if merge.returncode != 0:
            return "Update blocked: git fast-forward failed: " + (merge.stderr or merge.stdout).strip()
        return f"Updated local source to {target_text}. Restart the operator to run the new code."

    def _select_source_update_remote(self) -> Optional[str]:
        preferred = self.config.source_update_remote.strip()
        remotes = self._git_command(["remote"])
        if remotes.returncode != 0:
            return preferred or None
        names = {line.strip() for line in remotes.stdout.splitlines() if line.strip()}
        if preferred and preferred in names:
            return preferred
        if "source-mirror" in names:
            return "source-mirror"
        if "origin" in names:
            return "origin"
        return None

    def _spawn_replacement_operator(self) -> subprocess.Popen:
        command = [sys.executable, str(OPERATOR_ENTRYPOINT)]
        creationflags = 0
        if os.name == "nt":
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=os.name != "nt",
            creationflags=creationflags,
        )

    async def _exit_after_restart(self, delay_seconds: float = 1.5) -> None:
        await asyncio.sleep(delay_seconds)
        LOGGER.info("Exiting old Telegram operator process after self-restart pid=%s", os.getpid())
        os._exit(0)

