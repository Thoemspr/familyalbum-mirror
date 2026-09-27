#!/usr/bin/env python3
"""Write the data files the static gallery reads.

- ``site/albums.json``          the album switcher: one entry per album
- ``site/album-<id>.json``      album index: totals + one descriptor per month
- ``site/m/<id>-<YYYY-MM>.json``  the media of a single month

The split keeps the first paint small: opening an album fetches a few KB of
month descriptors, and each month's media arrive only when that section comes
into view. A single file for a 1900-item album was ~670 KB, which is a poor
trade on a weak mobile connection.

The gallery itself (index.html / style.css / app.js) is hand-written and lives
in the app's site/; this projects meta/<id>.json into the compact shape the
browser needs. Media paths stay relative to FA_HOME (``data/<id>/...``), which
the web server maps to the data directory. When FA_HOME is not the app
directory (Docker), the static shell is copied next to the generated JSON.
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from settings import META, SITE, STATIC_FILES, STATIC_SITE, load_config

MONTH_DIR = SITE / "m"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    tmp.replace(path)


def entry_of(item: dict, tz: ZoneInfo) -> dict:
    when = datetime.fromisoformat(item["tookAt"]).astimezone(tz)
    entry = {
        "u": item["uuid"],
        "t": when.isoformat(timespec="seconds"),
        "p": item["file"],
        "k": "v" if item["mediaType"] != "photo" else "p",
        "w": item.get("mediaWidth"),
        "h": item.get("mediaHeight"),
    }
    if item.get("thumb"):
        entry["s"] = item["thumb"]
    if item.get("videoDuration"):
        entry["d"] = round(item["videoDuration"] / 1000)
    if item.get("mediaDeviceModel"):
        entry["dev"] = item["mediaDeviceModel"]
    if item.get("removedAt"):
        entry["gone"] = item["removedAt"][:10]
    comments = [
        {
            "n": c.get("user", {}).get("nickname", "?"),
            "b": c.get("body", ""),
            "t": datetime.fromisoformat(c["createdAt"]).astimezone(tz)
            .isoformat(timespec="minutes"),
        }
        for c in item.get("comments", [])
        if not c.get("isDeleted")
    ]
    if comments:
        entry["c"] = comments
    return entry


def build_album(album: dict, tz: ZoneInfo) -> dict:
    album_id = album["id"]
    index_file = META / f"{album_id}.json"
    items = json.loads(index_file.read_text(encoding="utf-8")) if index_file.exists() else []

    by_month: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        if not item.get("file"):
            continue  # not downloaded yet — nothing to show
        entry = entry_of(item, tz)
        by_month[entry["t"][:7]].append(entry)

    months, written = [], set()
    for key in sorted(by_month, reverse=True):
        entries = sorted(by_month[key], key=lambda e: e["t"], reverse=True)
        name = f"{album_id}-{key}.json"
        write_json(MONTH_DIR / name, {"k": key, "media": entries})
        written.add(name)
        months.append({
            "k": key,
            "f": f"m/{name}",
            "n": len(entries),
            "p": sum(1 for e in entries if e["k"] == "p"),
            "v": sum(1 for e in entries if e["k"] == "v"),
            "c": sum(1 for e in entries if e.get("c")),
        })

    # Drop month files that no longer belong to this album (e.g. after a rename
    # of the album id or if a month ended up empty).
    for stale in MONTH_DIR.glob(f"{album_id}-*.json"):
        if stale.name not in written:
            stale.unlink()

    total = sum(m["n"] for m in months)
    summary = {
        "id": album_id,
        "name": album.get("name", album_id),
        "file": f"album-{album_id}.json",
        "count": total,
        "photos": sum(m["p"] for m in months),
        "videos": sum(m["v"] for m in months),
        "comments": sum(len(e.get("c", [])) for entries in by_month.values()
                        for e in entries),
        "bytes": sum(i.get("bytes", 0) for i in items),
        "first": months[-1]["k"] if months else None,
        "last": months[0]["k"] if months else None,
    }
    write_json(SITE / summary["file"], {**summary, "months": months})
    return summary


def copy_static() -> None:
    """Put the gallery shell next to the generated JSON unless it already is."""
    SITE.mkdir(parents=True, exist_ok=True)
    if SITE.resolve() == STATIC_SITE.resolve():
        return
    for name in STATIC_FILES:
        src, dest = STATIC_SITE / name, SITE / name
        if not dest.exists() or dest.read_bytes() != src.read_bytes():
            tmp = dest.with_name(dest.name + ".tmp")
            shutil.copyfile(src, tmp)
            tmp.replace(dest)


def build(config: dict | None = None) -> Path:
    config = config or load_config()
    tz = ZoneInfo(config.get("timezone", "Europe/Zurich"))
    copy_static()
    albums = [build_album(album, tz) for album in config["albums"]]
    manifest = SITE / "albums.json"
    write_json(manifest, {
        "generatedAt": datetime.now(tz).isoformat(timespec="seconds"),
        "albums": albums,
    })
    for album in albums:
        print(f"site: {album['id']}: {album['count']} entries")
    return manifest


if __name__ == "__main__":
    build()
