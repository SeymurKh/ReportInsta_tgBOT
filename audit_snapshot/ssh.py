"""Shared SSH connection for server-side audit and deploy helpers.

Credentials are resolved at connect time, in this order:
1. ``SERVER_SSH_KEY`` — path to a private key,
2. the project default key ``~/.ssh/reportinsta_ed25519``,
3. ``SERVER_SSH_PASSWORD``.

No secret ever lives in the repository. Host and user can be overridden with
``SERVER_SSH_HOST`` / ``SERVER_SSH_USER``.
"""
import os
from pathlib import Path

import paramiko

HOST = os.getenv("SERVER_SSH_HOST", "49.13.20.118")
USER = os.getenv("SERVER_SSH_USER", "root")
DEFAULT_KEY = Path.home() / ".ssh" / "reportinsta_ed25519"


def _resolve_key() -> str | None:
    configured = os.getenv("SERVER_SSH_KEY")
    candidate = Path(configured).expanduser() if configured else DEFAULT_KEY
    return str(candidate) if candidate.exists() else None


def connect(timeout: int = 30) -> paramiko.SSHClient:
    """Open an SSH client using the best available credential."""
    key_filename = _resolve_key()
    password = os.getenv("SERVER_SSH_PASSWORD") or None
    if key_filename is None and password is None:
        raise RuntimeError(
            "No SSH credentials: set SERVER_SSH_KEY or SERVER_SSH_PASSWORD "
            f"(or create {DEFAULT_KEY})"
        )
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST,
        username=USER,
        password=password,
        key_filename=key_filename,
        look_for_keys=key_filename is None,
        timeout=timeout,
    )
    return client
