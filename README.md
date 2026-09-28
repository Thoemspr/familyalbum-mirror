# FamilyAlbum mirror

A self-hosted backup of **FamilyAlbum** (みてね / mitene.us) share albums, with
a private web gallery and an optional offsite copy to OneDrive (or any other
storage rclone supports).

If the FamilyAlbum service or a share link ever goes away, everything is still
yours: the original photos and videos in full quality, their timestamps and
all the comments.

- **Crawler**: mirrors any number of albums every night: originals, metadata
  and comments. Nothing is ever deleted locally.
- **Gallery**: fast on phones, grouped by month, with a timeline scrubber,
  photo/video filters, a lightbox with swipe and comments, and a switch between
  albums. Protected by a password. The interface is in German.
- **Offsite copy** (optional): copies the originals to an rclone remote as
  plain, browsable folders, one per album.
- **Notifications** (optional): a Telegram message when something fails.

Everything runs in Docker as two containers built from the same image.

## What you need

- A machine with Docker and Docker Compose that is always on, such as a NAS, a
  Raspberry Pi 4/5, a mini PC or a small server (amd64 or arm64).
- The **share link** of each album. In the FamilyAlbum app, open the album and
  go to *Settings → Invite family → Share link*. It looks like
  `https://mitene.us/f/AbCdEf123`.
- Disk space for the originals, roughly 2.5 GB per 1000 photos and videos.

> ⚠️ A share link is a key to the whole album: anyone who has it can see every
> photo. Treat `config.json` like a password.

## Setup

```bash
mkdir familyalbum && cd familyalbum
curl -fsSLO https://raw.githubusercontent.com/Thoemspr/familyalbum-mirror/main/compose.yaml
curl -fsSL -o .env https://raw.githubusercontent.com/Thoemspr/familyalbum-mirror/main/.env.example
curl -fsSL -o config.json https://raw.githubusercontent.com/Thoemspr/familyalbum-mirror/main/config.example.json
mkdir -p album rclone
chmod 600 config.json .env
```

**1. Albums → `config.json`**

```json
{
  "albums": [
    { "id": "family",  "name": "Family",            "token": "https://mitene.us/f/AbCdEf123" },
    { "id": "grandma", "name": "Grandma & Grandpa", "token": "https://mitene.us/f/XyZ987", "password": "only-if-the-link-asks-for-one" }
  ]
}
```

- `id` becomes a folder and URL name: use lowercase letters, digits and dashes,
  and don't change it later.
- `name` is what the gallery shows.
- `token` is either the full share link or just the part after `/f/`.
- `password` (optional) is only needed if the share link asks for a password.
- **Add as many albums as you like.** A new entry is picked up by the next
  run; nothing else needs to change. To fetch a new album right away:
  `docker compose run --rm sync sync.py --album grandma`.

**2. Settings → `.env`**

Every setting is explained in the file. The important ones:

| Variable | Default | Purpose |
|---|---|---|
| `PUID` / `PGID` | `1000` | Owner of the files in `./album` (`id -u`, `id -g`) |
| `FA_DATA_DIR` | `./album` | Where photos, index and gallery are stored |
| `TZ` | `Europe/Zurich` | Time zone for photo dates and the schedule |
| `SYNC_SCHEDULE` | `30 3 * * *` | When to sync (cron syntax) |
| `RUN_ON_START` | `true` | Also sync whenever the container starts |
| `FA_AUTH_USER` / `FA_AUTH_HASH` | – | Gallery login (**required**, see below) |
| `FA_SITE_ADDRESS` | `:80` | `:80` behind a reverse proxy, or your domain for automatic HTTPS |
| `FA_HTTP_PORT` / `FA_HTTPS_PORT` | `8080` / `8443` | Ports on the host |
| `FA_NOTIFY` | `errors` | `errors`, `all` (also new photos/comments) or `off` |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | – | Telegram notifications |
| `RCLONE_REMOTE` | – | Offsite copy target, e.g. `onedrive:Pictures/FamilyAlbum` |
| `RCLONE_BWLIMIT` | `10M` | Upload bandwidth limit (`off` for none) |

**3. Gallery password**

```bash
docker compose run --rm sync caddy hash-password --plaintext 'choose-a-password'
```

Put the output into `.env` **in single quotes** (the hash contains `$` signs):

```bash
FA_AUTH_USER=family
FA_AUTH_HASH='$2a$14$....'
```

**4. Start**

```bash
docker compose up -d
docker compose logs -f sync
```

The first run downloads everything. That can take hours for a large album,
because downloads are deliberately slow and sequential. If it is interrupted,
the next run continues where it stopped. The gallery is at
`http://<your-host>:8080` and fills up as photos arrive.

## Reaching the gallery from outside

Choose one of these:

- **Your own reverse proxy** (Nginx Proxy Manager, Traefik, Caddy, Synology's
  reverse proxy, …): keep `FA_SITE_ADDRESS=:80` and point the proxy at
  `http://<host>:8080`.
- **Directly with automatic HTTPS**: set `FA_SITE_ADDRESS=photos.example.com`,
  `FA_HTTP_PORT=80` and `FA_HTTPS_PORT=443`, point the domain's DNS at your IP,
  and forward ports 80 and 443 on your router. Caddy fetches the certificate
  itself.
- **Only at home or over a VPN** (Tailscale, WireGuard): nothing else to do.

The gallery always asks for the password and tells search engines not to index
it.

## Offsite copy to OneDrive (or elsewhere)

The originals can be copied to any storage
[rclone supports](https://rclone.org/overview/): OneDrive, Google Drive,
Dropbox, a NAS via SFTP/SMB, S3 and more. Each album goes into its own folder
(`<remote>/<album name>/<year>/…`) as normal files you can browse in the
provider's photo view.

It is a **copy, never a sync**: photos deleted from the album or locally stay on
the remote. Thumbnails are not uploaded.

One-time setup for OneDrive:

1. On a computer with a browser, [install rclone](https://rclone.org/install/)
   and run `rclone authorize "onedrive"`. Log in, then copy the token it prints.
2. On the server, run:

   ```bash
   docker compose run --rm sync rclone config
   ```

   Choose `n` (new remote), name it `onedrive` and pick *Microsoft OneDrive*.
   Answer **No** to "Use web browser to automatically authenticate?", then
   paste the token from step 1. The configuration is saved in
   `./rclone/rclone.conf`.
3. In `.env`, set `RCLONE_REMOTE=onedrive:Pictures/FamilyAlbum`, then run
   `docker compose up -d`.

The upload runs after every sync. To start it by hand:
`docker compose run --rm sync upload`.

## Telegram notifications

1. Message [@BotFather](https://t.me/BotFather) and create a bot with
   `/newbot`. Copy the token.
2. Send your new bot any message, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the number in
   `"chat":{"id":…}`.
3. Put both into `.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) and run
   `docker compose up -d`.
4. Test it: `docker compose run --rm sync notify-test`.

By default you only hear from it when something goes wrong. Set
`FA_NOTIFY=all` for a short summary of new photos and comments after each run.

## Everyday commands

```bash
docker compose run --rm sync sync                  # sync + upload right now
docker compose run --rm sync sync.py --album family  # one album only
docker compose run --rm sync sync.py --verify      # check every file, re-fetch broken ones
docker compose run --rm sync sync.py --no-download # refresh comments only
docker compose run --rm sync upload                # offsite copy only
docker compose logs -f sync                        # what is it doing?
docker compose pull && docker compose up -d        # update
```

## Where things are

Everything lives in `./album` (or `FA_DATA_DIR`):

| Path | Contents |
|---|---|
| `data/<id>/media/<year>/` | Originals, named `YYYY-MM-DD_HHMMSS_<id>.<ext>` in local time |
| `data/<id>/thumbs/` | Previews for the gallery (can be regenerated) |
| `meta/<id>.json` | Full index per album including all comments: **the important part** |
| `site/` | The gallery: app and generated data (can be regenerated) |
| `logs/sync.log` | Log of every run |

To back up the mirror, copy `data/*/media` and `meta/`. Everything else can be
rebuilt.

## How it works

FamilyAlbum has no public API. The share page embeds its data as a
`gon.media = {…}` object, 25 media per page with their comments. Originals are
fetched through `/f/<token>/media_files/<uuid>/download`, which signs a fresh
download URL on every request, so a download run lasting several hours never
depends on an expired link. If a share link is password-protected, the crawler first submits the
album's `password` to the link's login form and reuses the session cookie.

- **Every run crawls each album completely.** Comments can appear on
  years-old photos, so fetching only new pages would miss them. Only files
  missing on disk are downloaded.
- **Nothing is ever deleted locally.** If a photo disappears from the album,
  it keeps its file and is marked "im Album gelöscht" in the gallery.
- **Original quality is verified, not assumed.**
  - The download must come from the `original/` bucket; resized versions are
    rejected.
  - The byte count must match what the server announced.
  - Every file is decoded and its size compared with the album's metadata, and
    for videos also the duration (`ffprobe`).
  - `--verify` repeats these checks over the whole archive.
- **The crawler is polite on purpose:** strictly sequential, 0.4 s between
  pages and 0.2 s between downloads, with a fresh session every 100 files.
  Please keep it that way: it is someone else's service, and we want share
  links to keep working.
- **The gallery loads by month.** The album index is under 1 KB, and a month's
  media are fetched only when that section scrolls into view, so even albums
  with thousands of photos open instantly on a phone.

### Running without Docker

The crawler has no Docker dependency: Python 3.11+, `pip install pillow`,
`ffmpeg` on the `PATH`.

```bash
cp config.example.json config.json   # add your albums
python3 sync.py                      # data/, meta/, site/ appear next to the code
```

Serve `site/` at `/` and `data/` at `/data/` with any web server; see
`docker/Caddyfile`. `FA_HOME` moves the data somewhere else. Besides Telegram,
notifications can go to any script taking `<title> <message>`: set
`"notifyCmd": "/path/to/script"` in `config.json` (or `NOTIFY_CMD`).

## Known limitations

- **Password-protected share links** need the link's password as
  `"password"` in `config.json`. A wrong password stops the crawl with a
  clear error.
- The gallery interface is in German.
- This is an unofficial tool with no connection to FamilyAlbum / MIXI. It
  only reads what your share link already shows you.

## License

MIT
