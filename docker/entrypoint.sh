#!/usr/bin/env bash
# Entry point of the image. Modes:
#
#   scheduler        (default) run sync + upload on SYNC_SCHEDULE
#   sync             one run right now: sync, then upload (same as a scheduled run)
#   upload           only the rclone upload
#   web              serve the gallery with Caddy
#   notify-test      send a test notification
#   sync.py ARGS     any of the Python tools with their own flags,
#                    e.g. `sync.py --verify` or `sync.py --album grandma`
#   anything else    executed as-is, e.g. `rclone config`
set -euo pipefail

APP=/app
cd "$APP"

check_writable() {
    local probe="$FA_HOME/.write-test.$$"
    if ! touch "$probe" 2>/dev/null; then
        echo "ERROR: $FA_HOME is not writable for uid $(id -u)." >&2
        echo "       Create the folder on the host before the first start and make it" >&2
        echo "       belong to PUID/PGID from .env (see README, 'Permissions')." >&2
        exit 1
    fi
    rm -f "$probe"
}

case "${1:-scheduler}" in
    scheduler)
        check_writable
        # Fail fast on a broken config.json instead of at 3 a.m.
        python3 -c '
import sys, settings
try:
    settings.load_config()
except settings.ConfigError as exc:
    sys.exit(f"config error: {exc}")' || exit 1
        python3 build_site.py >/dev/null
        schedule=${SYNC_SCHEDULE:-30 3 * * *}
        echo "$schedule $APP/docker/run.sh" > /tmp/crontab
        echo "scheduler: '$schedule' (TZ=${TZ:-UTC})"
        if [[ ${RUN_ON_START:-false} == true ]]; then
            "$APP/docker/run.sh" || true
        fi
        # supercronic never starts a job while the previous one still runs,
        # which matters for the first, very long download.
        exec supercronic -passthrough-logs /tmp/crontab
        ;;
    sync)
        check_writable
        exec "$APP/docker/run.sh"
        ;;
    upload)
        exec "$APP/docker/upload.sh"
        ;;
    web)
        if [[ -z ${FA_AUTH_USER:-} || ${FA_AUTH_HASH:-} != \$2* ]]; then
            echo "ERROR: the gallery needs a login. Set FA_AUTH_USER and FA_AUTH_HASH in .env;" >&2
            echo "       create the hash with:" >&2
            echo "       docker compose run --rm sync caddy hash-password --plaintext 'your-password'" >&2
            echo "       and put it in single quotes: FA_AUTH_HASH='\$2a\$14\$...'" >&2
            exit 1
        fi
        exec caddy run --config "$APP/docker/Caddyfile" --adapter caddyfile
        ;;
    notify-test)
        exec python3 notify.py "FamilyAlbum" "Test notification from the FamilyAlbum mirror."
        ;;
    *.py)
        script=$1; shift
        exec python3 "$APP/$script" "$@"
        ;;
    *)
        exec "$@"
        ;;
esac
