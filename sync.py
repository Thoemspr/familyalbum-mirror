#!/usr/bin/env python3
"""Mirror the FamilyAlbum share albums to local disk.

One run = per album one full metadata crawl (cheap: ~50 s for 1100 items) plus
a download of everything that is not on disk yet. The metadata crawl is always
complete because comments can appear on old photos at any time, so a "new items
only" shortcut would miss them.

Downloads always go through /media_files/<uuid>/download, which redirects to a
freshly signed URL for the *original* file — the signed URLs embedded in the
album page expire within a day and are never reused. Long runs are additionally
split into batches with a fresh session per batch.

Nothing is ever deleted locally: if a media file disappears from the album it
keeps its entry (flagged ``removedAt``) and its file. That is the whole point
of the backup.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageOps

import notify as notifier
from mitene import Album, MiteneError
from settings import DATA, HOME as ROOT, LOG_FILE, META, ConfigError, load_config

EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/heic": "heic",
              "video/mp4": "mp4", "video/quicktime": "mov"}

# Downloads per session: after each batch the follower cookie is renewed and we
# pause briefly. Nothing here depends on a URL staying valid across batches.
BATCH_SIZE = 100
BATCH_PAUSE = 5.0


def log(message: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} {message}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def notify(title: str, body: str, config: dict | None = None) -> None:
    outcome = notifier.send(title, body, (config or {}).get("notifyCmd"))
    if not outcome.startswith("sent"):
        log(f"notify: {outcome}: {title}: {body}")


# --- per-album paths --------------------------------------------------------

def media_dir(album_id: str) -> Path:
    return DATA / album_id / "media"


def thumb_dir(album_id: str) -> Path:
    return DATA / album_id / "thumbs"


def index_file(album_id: str) -> Path:
    return META / f"{album_id}.json"


def load_index(album_id: str) -> dict[str, dict]:
    path = index_file(album_id)
    if not path.exists():
        return {}
    return {item["uuid"]: item for item in json.loads(path.read_text("utf-8"))}


def save_index(album_id: str, index: dict[str, dict]) -> None:
    items = sorted(index.values(), key=lambda x: x["tookAt"], reverse=True)
    META.mkdir(parents=True, exist_ok=True)
    path = index_file(album_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def local_time(took_at: str, tz: ZoneInfo) -> datetime:
    """mitene reports ``tookAt`` in JST; we want the wall clock of the camera."""
    return datetime.fromisoformat(took_at).astimezone(tz)


def target_path(album_id: str, item: dict, tz: ZoneInfo) -> Path:
    when = local_time(item["tookAt"], tz)
    ext = EXTENSIONS.get(item.get("contentType", ""), "bin")
    name = f"{when:%Y-%m-%d_%H%M%S}_{item['uuid'][:8]}.{ext}"
    return media_dir(album_id) / f"{when:%Y}" / name


# --- quality / integrity ----------------------------------------------------

def probe_video(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=120,
    )
    data = json.loads(out.stdout or "{}")
    stream = (data.get("streams") or [{}])[0]
    return {"width": stream.get("width"), "height": stream.get("height"),
            "duration": float((data.get("format") or {}).get("duration") or 0)}


def check_file(item: dict) -> tuple[bool, str]:
    """Is the local copy complete and at the album's full resolution?

    Orientation may be expressed in EXIF rather than in the pixel grid, so
    width/height are compared as an unordered pair.
    """
    if not item.get("file"):
        return False, "not downloaded"
    path = ROOT / item["file"]
    if not path.exists():
        return False, "file missing"
    size = path.stat().st_size
    if size == 0:
        return False, "empty file"
    if item.get("bytes") and size != item["bytes"]:
        return False, f"size changed ({size} != {item['bytes']})"

    expected = {item.get("mediaWidth"), item.get("mediaHeight")}
    try:
        if item["mediaType"] == "photo":
            with Image.open(path) as img:
                img.load()  # full decode: catches truncated JPEGs
                actual = set(img.size)
        else:
            info = probe_video(path)
            actual = {info["width"], info["height"]}
            wanted = (item.get("videoDuration") or 0) / 1000
            if wanted and abs(info["duration"] - wanted) > 1.5:
                return False, f"duration {info['duration']:.1f}s != {wanted:.1f}s"
    except Exception as exc:
        return False, f"undecodable: {exc}"

    if None not in expected and actual != expected:
        return False, f"resolution {sorted(actual)} != {sorted(expected)}"
    return True, "ok"


def make_thumb(album_id: str, item: dict, source: Path, size: int) -> Path | None:
    """640 px WebP preview; for videos a poster frame grabbed with ffmpeg."""
    thumb = thumb_dir(album_id) / f"{item['uuid']}.webp"
    if thumb.exists():
        return thumb
    thumb.parent.mkdir(parents=True, exist_ok=True)
    frame = None
    try:
        if item["mediaType"] == "photo":
            frame = Image.open(source)
        else:
            offset = min(1.0, (item.get("videoDuration") or 0) / 1000 / 10)
            raw = subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", f"{offset:.2f}", "-i", str(source),
                 "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                capture_output=True, timeout=120,
            )
            if raw.returncode != 0 or not raw.stdout:
                log(f"  thumb: ffmpeg failed for {source.name}")
                return None
            import io
            frame = Image.open(io.BytesIO(raw.stdout))
        frame = ImageOps.exif_transpose(frame).convert("RGB")
        frame.thumbnail((size, size), Image.LANCZOS)
        frame.save(thumb, "WEBP", quality=80, method=4)
        return thumb
    except Exception as exc:
        log(f"  thumb failed for {source.name}: {exc}")
        return None
    finally:
        if frame is not None:
            frame.close()


def comments_fingerprint(item: dict) -> list:
    return [[c["id"], c.get("body"), c.get("updatedAt"), c.get("isDeleted")]
            for c in item.get("comments", [])]


# --- download ---------------------------------------------------------------

def download_files(client: Album, album_id: str, index: dict[str, dict],
                   pending: list[dict], tz: ZoneInfo, config: dict,
                   batch_size: int) -> tuple[int, int]:
    """Download ``pending`` in batches, verifying each file before accepting it."""
    downloaded = failed = 0
    total = len(pending)
    pending = sorted(pending, key=lambda x: x["tookAt"])
    for start in range(0, total, batch_size):
        batch = pending[start : start + batch_size]
        if start:
            log(f"  batch {start // batch_size + 1}: renewing session, pausing "
                f"{BATCH_PAUSE:.0f}s")
            time.sleep(BATCH_PAUSE)
            try:
                client.start_session()
            except MiteneError as exc:
                log(f"  session refresh failed: {exc}")
        for number, item in enumerate(batch, start + 1):
            dest = target_path(album_id, item, tz)
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                item["bytes"] = client.download(item["uuid"], dest)
                item["file"] = str(dest.relative_to(ROOT))
                ok, reason = check_file(item)
                if not ok:
                    dest.unlink(missing_ok=True)
                    item.pop("file", None)
                    item.pop("bytes", None)
                    raise MiteneError(f"rejected: {reason}")
                downloaded += 1
                thumb = make_thumb(album_id, item, dest, config.get("thumbSize", 640))
                if thumb:
                    item["thumb"] = str(thumb.relative_to(ROOT))
            except (MiteneError, OSError) as exc:
                failed += 1
                log(f"  FAILED {item['uuid']}: {exc}")
            if number % 25 == 0 or number == total:
                log(f"  {number}/{total} files")
                save_index(album_id, index)
            time.sleep(0.2)
    return downloaded, failed


# --- one album --------------------------------------------------------------

def sync_album(album: dict, config: dict, args: argparse.Namespace) -> dict:
    album_id, name = album["id"], album.get("name", album["id"])
    tz = ZoneInfo(config.get("timezone", "Europe/Zurich"))
    client = Album(album["token"])
    index = load_index(album_id)
    batch_size = args.batch or BATCH_SIZE
    stats = {"id": album_id, "name": name, "new": 0, "comments": 0, "gone": 0,
             "downloaded": 0, "failed": 0}

    log(f"[{album_id}] {name}")
    if args.verify_only:
        if not index:
            log(f"[{album_id}] nothing to verify yet")
            return stats
        log(f"[{album_id}] verifying {len(index)} local file(s)")
    else:
        log(f"[{album_id}] crawling album ({len(index)} items known locally)")
        remote = client.crawl(on_page=lambda p, n, total: (
            log(f"[{album_id}]   page {p}: {total} items") if p % 10 == 0 else None))
        log(f"[{album_id}] crawl done: {len(remote)} items in the album")

        seen: set[str] = set()
        for item in remote:
            uuid = item["uuid"]
            seen.add(uuid)
            existing = index.get(uuid)
            if existing is None:
                item["firstSeen"] = datetime.now(tz).isoformat(timespec="seconds")
                index[uuid] = item
                stats["new"] += 1
            else:
                if comments_fingerprint(existing) != comments_fingerprint(item):
                    stats["comments"] += 1
                existing.update({k: v for k, v in item.items()
                                 if k not in ("firstSeen", "file", "bytes", "thumb")})
                existing.pop("removedAt", None)

        for uuid, item in index.items():
            if uuid not in seen and not item.get("removedAt"):
                item["removedAt"] = datetime.now(tz).isoformat(timespec="seconds")
                stats["gone"] += 1
        if stats["gone"]:
            log(f"[{album_id}] {stats['gone']} item(s) disappeared (kept locally)")
        log(f"[{album_id}] {stats['new']} new item(s), "
            f"{stats['comments']} with changed comments")

    # Anything not on disk, plus (in verify mode) anything that fails its check.
    pending = [i for i in index.values()
               if not (i.get("file") and (ROOT / i["file"]).exists())]
    if args.verify or args.verify_only:
        checked = broken = 0
        for item in index.values():
            if not (item.get("file") and (ROOT / item["file"]).exists()):
                continue
            checked += 1
            ok, reason = check_file(item)
            if not ok:
                broken += 1
                log(f"[{album_id}]   BAD {item.get('file')}: {reason}")
                (ROOT / item["file"]).unlink(missing_ok=True)
                item.pop("file", None)
                item.pop("bytes", None)
                pending.append(item)
            if checked % 250 == 0:
                log(f"[{album_id}]   verified {checked} file(s)")
        log(f"[{album_id}] verified {checked} file(s), {broken} to re-download")

    if args.limit:
        pending = pending[: args.limit]
    if args.no_download:
        log(f"[{album_id}] skipping {len(pending)} download(s) (--no-download)")
    elif pending:
        log(f"[{album_id}] downloading {len(pending)} file(s) in batches of {batch_size}")
        client.start_session()
        stats["downloaded"], stats["failed"] = download_files(
            client, album_id, index, pending, tz, config, batch_size)

    # Backfill thumbnails for anything that has a file but no preview yet.
    missing_thumbs = [i for i in index.values()
                      if i.get("file") and not (i.get("thumb")
                      and (ROOT / i["thumb"]).exists())]
    if missing_thumbs:
        log(f"[{album_id}] generating {len(missing_thumbs)} missing thumbnail(s)")
        for item in missing_thumbs:
            thumb = make_thumb(album_id, item, ROOT / item["file"],
                               config.get("thumbSize", 640))
            if thumb:
                item["thumb"] = str(thumb.relative_to(ROOT))

    save_index(album_id, index)
    total_bytes = sum(i.get("bytes", 0) for i in index.values())
    complete = sum(1 for i in index.values() if i.get("file"))
    stats["items"] = len(index)
    stats["bytes"] = total_bytes
    log(f"[{album_id}] done: {len(index)} items, {complete} local, "
        f"{total_bytes / 1e9:.2f} GB, {stats['downloaded']} downloaded, "
        f"{stats['failed']} failed")
    return stats


# --- the run ----------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        log(f"config error: {exc}")
        if not args.quiet:
            notify("FamilyAlbum-Sync", f"Konfigurationsfehler: {exc}")
        return 2
    albums = config["albums"]
    if args.album:
        albums = [a for a in albums if a["id"] in args.album]
        if not albums:
            log(f"no album matches {args.album}")
            return 2

    results, aborted = [], []
    for album in albums:
        try:
            results.append(sync_album(album, config, args))
        except MiteneError as exc:
            log(f"[{album['id']}] ABORT: {exc}")
            aborted.append((album, exc))

    if not args.no_site:
        import build_site
        build_site.build(config)

    # Push policy: "errors" (default) only reports aborts and failed downloads —
    # a normal night with new photos stays silent. "all" also summarises what
    # arrived; false switches notifications off entirely.
    mode = config.get("notify", "errors")
    if mode and not args.quiet:
        lines = []
        for album, exc in aborted:
            lines.append(f"{album.get('name', album['id'])}: Abbruch – {exc}")
        for stats in results:
            if stats["failed"]:
                lines.append(f"{stats['name']}: {stats['failed']} Download(s) "
                             f"fehlgeschlagen (von {stats['items']} Medien)")
        if mode in (True, "all"):
            for stats in results:
                bits = []
                if stats["new"]:
                    bits.append(f"{stats['new']} neue Medien")
                if stats["comments"]:
                    bits.append(f"{stats['comments']} neue/geänderte Kommentare")
                if stats["gone"]:
                    bits.append(f"{stats['gone']} im Album gelöscht (lokal behalten)")
                if bits:
                    lines.append(f"{stats['name']}: " + ", ".join(bits)
                                 + f" · total {stats['items']} Medien, "
                                   f"{stats['bytes'] / 1e9:.1f} GB")
        if lines:
            notify("FamilyAlbum-Sync", "\n".join(lines), config)

    if aborted:
        return 2
    return 1 if any(s["failed"] for s in results) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--album", action="append", metavar="ID",
                        help="only this album (repeatable)")
    parser.add_argument("--no-download", action="store_true",
                        help="only refresh metadata")
    parser.add_argument("--no-site", action="store_true",
                        help="do not rebuild the static site")
    parser.add_argument("--batch", type=int, metavar="N",
                        help=f"downloads per session (default {BATCH_SIZE})")
    parser.add_argument("--limit", type=int, help="download at most N files per album")
    parser.add_argument("--verify", action="store_true",
                        help="also check every local file and re-fetch bad ones")
    parser.add_argument("--verify-only", action="store_true",
                        help="check local files without crawling the albums")
    parser.add_argument("--quiet", action="store_true", help="no push notification")
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
