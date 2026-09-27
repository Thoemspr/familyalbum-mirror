#!/usr/bin/env python3
"""Push notifications for sync and upload.

Channel, first match wins:

1. Telegram, if ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID`` are set.
2. A command called as ``<command> <title> <message>``: ``NOTIFY_CMD`` from
   the environment, or ``"notifyCmd"`` in config.json (passed in by sync.py).
3. Nothing — the message is only written to the log.

Usage from a shell script: ``python3 notify.py "Title" "Message"``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request

TELEGRAM_LIMIT = 3900  # Telegram rejects messages over 4096 characters


def send(title: str, body: str, command: str | None = None) -> str:
    """Deliver a message; return a short description of what happened.

    Never raises: a failed notification must not break a sync.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    command = (os.environ.get("NOTIFY_CMD") or command or "").strip()
    try:
        if token and chat:
            text = f"[{title}]\n{body}"
            if len(text) > TELEGRAM_LIMIT:
                text = text[: TELEGRAM_LIMIT - 1] + "…"
            data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
            with urllib.request.urlopen(
                    f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=30):
                pass
            return "sent via Telegram"
        if command and shutil.which(command):
            subprocess.run([command, title, body], timeout=30, check=False)
            return f"sent via {command}"
        return "no notification channel configured"
    except Exception as exc:  # noqa: BLE001 — see docstring
        # The exception text of urllib can contain the request URL, and with it
        # the bot token — report the type only.
        return f"notification failed ({type(exc).__name__})"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: notify.py TITLE MESSAGE")
    print(f"notify: {send(sys.argv[1], sys.argv[2])}")
