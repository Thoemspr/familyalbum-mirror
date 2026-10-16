"""Where the mirror lives and how it is configured — shared by sync and site build.

Everything is rooted at ``FA_HOME`` (default: this directory, so a plain
checkout keeps working in place). Below it:

- ``config.json``   albums + tokens (``FA_CONFIG`` points elsewhere if needed)
- ``data/<id>/``    originals and thumbnails
- ``meta/<id>.json`` the authoritative local index
- ``site/``         the gallery: static shell + generated JSON
- ``logs/``         sync log

Paths stored in ``meta/`` and served to the browser are relative to FA_HOME
(``data/<id>/media/...``), so the whole tree can be moved or mounted anywhere.

A few simple settings can be overridden from the environment, which is what
the Docker image uses; the album list stays in config.json because it holds
the share tokens and does not fit into a flat variable.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
HOME = Path(os.environ.get("FA_HOME") or APP_DIR).resolve()
CONFIG_FILE = Path(os.environ.get("FA_CONFIG") or HOME / "config.json")
DATA = HOME / "data"
META = HOME / "meta"
SITE = HOME / "site"
LOG_FILE = HOME / "logs" / "sync.log"

# The hand-written gallery files. When FA_HOME is elsewhere (Docker), the site
# build copies them next to the generated JSON.
STATIC_SITE = APP_DIR / "site"
STATIC_FILES = ("index.html", "app.js", "style.css")

# Album ids end up in file names and URLs.
ALBUM_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
SHARE_LINK = re.compile(r"/f/([^/?#\s]+)")


class ConfigError(Exception):
    pass


def _notify_mode(value: str):
    value = value.strip().lower()
    if value in ("", "0", "false", "off", "no", "none"):
        return False
    if value in ("1", "true", "on", "yes"):
        return "all"
    return value


def load_config() -> dict:
    """Read config.json, apply environment overrides and validate the albums."""
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{CONFIG_FILE} not found — copy config.example.json") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{CONFIG_FILE} is not valid JSON: {exc}") from None

    if os.environ.get("FA_TIMEZONE"):
        config["timezone"] = os.environ["FA_TIMEZONE"]
    if os.environ.get("FA_THUMB_SIZE"):
        config["thumbSize"] = int(os.environ["FA_THUMB_SIZE"])
    if "FA_NOTIFY" in os.environ:
        config["notify"] = _notify_mode(os.environ["FA_NOTIFY"])

    albums = config.get("albums")
    if not isinstance(albums, list) or not albums:
        raise ConfigError("config.json needs a non-empty \"albums\" list")
    seen = set()
    for number, album in enumerate(albums, 1):
        where = f"album #{number}"
        if not isinstance(album, dict):
            raise ConfigError(f"{where} must be an object")
        album_id = album.get("id")
        if not isinstance(album_id, str) or not ALBUM_ID.match(album_id):
            raise ConfigError(f"{where}: \"id\" must be lowercase letters, digits "
                              f"and dashes (got {album_id!r})")
        if album_id in seen:
            raise ConfigError(f"{where}: id {album_id!r} is used twice")
        seen.add(album_id)
        if not str(album.get("name", "")).strip():
            raise ConfigError(f"album {album_id!r}: \"name\" is empty")
        token = str(album.get("token", "")).strip()
        # Accept the whole share link as well: https://mitene.us/f/<token>
        match = SHARE_LINK.search(token)
        if match:
            token = match.group(1)
        if not token or "/" in token:
            raise ConfigError(f"album {album_id!r}: \"token\" is missing or not a "
                              f"share link token")
        album["token"] = token
        password = album.get("password")
        if password is not None and not isinstance(password, str):
            raise ConfigError(f"album {album_id!r}: \"password\" must be a string")
    return config
