"""Client for a FamilyAlbum (mitene.us) follower share link.

The share page is server-rendered Rails with the album page embedded as a
``gon.media = {...}`` assignment; there is no public JSON API. Everything we
need is in there: one page holds 25 media records with their comments, and
``/f/<token>/media_files/<uuid>/download`` redirects to the original file on
CloudFront (signed, short-lived).
"""

from __future__ import annotations

import http.cookiejar
import json
import re
import time
import urllib.error
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

    # -- metadata ----------------------------------------------------------
    def page(self, number: int) -> dict:
        """One album page: ``{hasNext, hasPrev, currentPage, mediaFiles[]}``."""
        with self._open(f"{BASE}/f/{self.token}?page={number}") as resp:
            html = resp.read().decode("utf-8", "replace")
        match = GON_MEDIA.search(html)
        if not match:
            if "password" in html and "/password" in html:
                raise MiteneError(
                    "no media on page — the share link now asks for a password"
                )
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

        The cookie is good for two weeks, but a long initial run is cheap to
        make robust: call this between batches and no run can ever outlive its
        own session.
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
