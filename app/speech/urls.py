from __future__ import annotations

import re
import shutil
import subprocess
from urllib.parse import urlsplit, urlunsplit

from process_utils import hidden_subprocess_kwargs

DEFAULT_LOCAL_SPEECH_URL = "http://127.0.0.1:8766"


def normalize_speech_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url and "://" not in url:
        url = "http://" + url
    if url:
        parts = urlsplit(url)
        host_part = parts.netloc.rsplit("@", 1)[-1]
        has_port = ":" in host_part and not host_part.endswith("]")
        if parts.netloc and not has_port:
            url = urlunsplit((parts.scheme or "http", f"{parts.netloc}:8766", parts.path, "", ""))
    return url


def is_local_host(host: str) -> bool:
    return host.strip().lower() in {"", "127.0.0.1", "localhost", "0.0.0.0", "::1"}


def is_local_speech_url(url: str) -> bool:
    normalized = normalize_speech_url(url)
    if not normalized:
        return False
    return is_local_host(urlsplit(normalized).hostname or "")


def tailscale_speech_urls() -> list[str]:
    executable = shutil.which("tailscale") or shutil.which("tailscale.exe")
    if not executable:
        return []
    try:
        result = subprocess.run(
            [executable, "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=4,
            encoding="utf-8",
            errors="replace",
            **hidden_subprocess_kwargs(),
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    urls = []
    for line in result.stdout.splitlines():
        ip = line.strip()
        if re.fullmatch(r"100(?:\.\d{1,3}){3}", ip):
            urls.append(f"http://{ip}:8766")
    return urls


def unique_urls(urls: list[str]) -> list[str]:
    unique = []
    seen = set()
    for url in urls:
        normalized = normalize_speech_url(url)
        if normalized and normalized not in seen:
            unique.append(normalized)
            seen.add(normalized)
    return unique


def build_speech_urls(remote_url: str, local_fallback: bool = True) -> list[str]:
    urls = []
    remote_url = normalize_speech_url(remote_url)
    if remote_url:
        urls.append(remote_url)
    if local_fallback and not is_local_speech_url(remote_url):
        urls.append(DEFAULT_LOCAL_SPEECH_URL)
        urls.extend(tailscale_speech_urls())
    return unique_urls(urls)
