"""Client for a FamilyAlbum (mitene.us) follower share link.

The share page is server-rendered Rails with the album page embedded as a
``gon.media = {...}`` assignment; there is no public JSON API. Everything we
need is in there: one page holds 25 media records with their comments, and
``/f/<token>/media_files/<uuid>/download`` redirects to the original file on
CloudFront (signed, short-lived).

Local patch: password-protected share links. If the share page redirects to
``/f/<token>/login``, the login form is filled with the album's ``password``
from config.json and the session cookie is reused for pages and downloads.
"""

from __future__ import annotations

import html as html_lib
import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://mitene.us"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
GON_MEDIA = re.compile(r"gon\.media\s*=\s*(\{)")

# The download endpoint must redirect into the "original" bucket. The same file
# also exists as large/medium/small/smartphone renditions, and silently mirroring
# one of those would defeat the purpose of the backup.
ORIGINAL_MARKER = "/media/uploads/original/"

# Fields that are signed CloudFront URLs: they expire within a day and would
# only create noise in the stored metadata.
VOLATILE_FIELDS = ("expiringUrl", "expiringThumbUrl", "expiringVideoUrl", "signature")

# --- login form parsing (patch) ---------------------------------------------
FORM_RE = re.compile(r"<form\b[^>]*>.*?</form>", re.S | re.I)
FORM_TAG_RE = re.compile(r"<form\b[^>]*>", re.I)
INPUT_RE = re.compile(r"<input\b[^>]*>", re.I)
ATTR_RE = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
SHARE_LINK = re.compile(r"/f/([^/?#\s]+)")


class MiteneError(RuntimeError):
    pass


def _extract_json_object(text: str, start: int) -> dict:
    """Return the JSON object that starts at ``text[start] == '{'``."""
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
        elif c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise MiteneError("unterminated gon.media object")


def _attrs(tag: str) -> dict:
    return {
        k.lower(): html_lib.unescape(v1 if v1 or not v2 else v2)
        for k, v1, v2 in ATTR_RE.findall(tag)
    }


def _album_password(token: str) -> str | None:
    """Password for this share token from config.json (key "password")."""
    path = os.environ.get("FA_CONFIG") or os.path.join(
        os.environ.get("FA_HOME") or os.path.dirname(os.path.abspath(__file__)),
        "config.json",
    )
    try:
        with open(path, encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, ValueError):
        return None
    for album in config.get("albums", []):
        raw = str(album.get("token", "")).strip()
        match = SHARE_LINK.search(raw)
        if (match.group(1) if match else raw) == token:
            password = album.get("password")
            return str(password) if password else None
    return None


def _is_login_page(url: str, html: str) -> bool:
    path = urllib.parse.urlparse(url).path.rstrip("/")
    if path.endswith("/login"):
        return True
    return not GON_MEDIA.search(html) and bool(
        re.search(r"""type\s*=\s*["']password["']""", html, re.I)
    )


class Album:
    def __init__(self, token: str, delay: float = 0.4, retries: int = 3):
        self.token = token
        self.delay = delay
        self.retries = retries
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )
        self.opener.addheaders = [("User-Agent", USER_AGENT)]

    # -- low level ---------------------------------------------------------

    def _open(self, url: str):
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                return self.opener.open(url, timeout=120)
            except (urllib.error.URLError, TimeoutError) as exc:
                last = exc
                status = getattr(exc, "code", None)
                if status in (401, 403, 404):
                    raise MiteneError(f"HTTP {status} for {url}") from exc
                time.sleep(2 * (attempt + 1))
        raise MiteneError(f"giving up on {url}: {last}")

    def _get(self, url: str) -> tuple[str, str]:
        with self._open(url) as resp:
            return resp.geturl(), resp.read().decode("utf-8", "replace")

    # -- login (patch) -----------------------------------------------------

    def login(self) -> None:
        """Submit the share link's password form; the cookie jar keeps the session."""
        password = _album_password(self.token)
        if not password:
            raise MiteneError(
                "the share link asks for a password: add \"password\" to this "
                "album in config.json"
            )
        login_url = f"{BASE}/f/{self.token}/login"
        final, page = self._get(login_url)

        form = next(
            (f for f in FORM_RE.findall(page)
             if re.search(r"""type\s*=\s*["']password["']""", f, re.I)),
            None,
        )
        if form is None:
            raise MiteneError(f"no password form found on {final}")

        form_tag = _attrs(FORM_TAG_RE.match(form).group(0))
        action = urllib.parse.urljoin(final, form_tag.get("action") or final)

        fields: list[tuple[str, str]] = []
        for tag in INPUT_RE.findall(form):
            a = _attrs(tag)
            name = a.get("name")
            kind = a.get("type", "text").lower()
            if not name or kind in ("submit", "button", "image", "file"):
                continue
            if kind == "password":
                fields.append((name, password))
            elif kind in ("checkbox", "radio"):
                if re.search(r"\bchecked\b", tag, re.I):
                    fields.append((name, a.get("value", "on")))
            else:
                fields.append((name, a.get("value", "")))

        request = urllib.request.Request(
            action,
            data=urllib.parse.urlencode(fields).encode(),
            headers={"Referer": final, "Origin": BASE,
                     "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=60) as resp:
                after_url = resp.geturl()
                after = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 422:
                raise MiteneError(
                    "login failed (HTTP 422): wrong password?") from exc
            raise MiteneError(f"login failed: HTTP {exc.code}") from exc
        if _is_login_page(after_url, after):
            raise MiteneError("login failed: wrong password?")
        time.sleep(self.delay)

    # -- metadata ----------------------------------------------------------

    def page(self, number: int) -> dict:
        """One album page: ``{hasNext, hasPrev, currentPage, mediaFiles[]}``."""
        url = f"{BASE}/f/{self.token}?page={number}"
        final, html = self._get(url)
        if _is_login_page(final, html):
            self.login()
            final, html = self._get(url)
        match = GON_MEDIA.search(html)
        if not match:
            if _is_login_page(final, html):
                raise MiteneError("still on the login page after logging in")
            raise MiteneError(f"no gon.media on page {number}")
        return _extract_json_object(html, match.start(1))

    def crawl(self, max_pages: int = 10000, on_page=None) -> list[dict]:
        """All media records, newest first, with the expiring URLs stripped."""
        media: list[dict] = []
        number = 1
        while number <= max_pages:
            page = self.page(number)
            files = page.get("mediaFiles", [])
            for item in files:
                for field in VOLATILE_FIELDS:
                    item.pop(field, None)
            media.extend(files)
            if on_page:
                on_page(number, len(files), len(media))
            if not page.get("hasNext"):
                break
            number += 1
            time.sleep(self.delay)
        return media

    # -- binaries ----------------------------------------------------------

    def start_session(self) -> None:
        """(Re)bootstrap the follower session cookie by loading page 1.

        With a password-protected link, page() logs in again automatically.
        """
        self.cookies.clear()
        self.page(1)

    def download(self, uuid: str, dest) -> int:
        """Fetch the original (unresized, EXIF intact) file to ``dest``.

        The signed CloudFront URLs embedded in the album page expire within a
        day, so they are never reused: this endpoint mints a fresh signature on
        every call, which is what makes a long initial download safe.
        """
        url = f"{BASE}/f/{self.token}/media_files/{uuid}/download"
        tmp = dest.with_suffix(dest.suffix + ".part")
        with self._open(url) as resp:
            final = resp.geturl()
            if ORIGINAL_MARKER not in final:
                raise MiteneError(
                    f"{uuid}: download did not resolve to the original file "
                    f"({final.split('?')[0]})"
                )
            expected = int(resp.headers.get("Content-Length") or 0)
            with open(tmp, "wb") as fh:
                size = 0
                while chunk := resp.read(1 << 20):
                    fh.write(chunk)
                    size += len(chunk)
        if expected and size != expected:
            tmp.unlink(missing_ok=True)
            raise MiteneError(f"{uuid}: truncated ({size} of {expected} bytes)")
        tmp.rename(dest)
        return size
