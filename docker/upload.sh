#!/usr/bin/env bash
# Copy the originals to any rclone remote (OneDrive, Google Drive, a NAS, ...)
# as plain, browsable files: <RCLONE_REMOTE>/<album name>/<year>/<file>.
#
# `rclone copy`, never `sync`: a photo removed from the album upstream, or a
# broken local mirror, must not delete the offsite copy. Thumbnails stay
# behind — UUID-named, regenerable and useless in a photo app.
#
# Settings (environment):
#   RCLONE_REMOTE      target, e.g. "onedrive:Pictures/FamilyAlbum"   (required)
#   RCLONE_CONFIG      rclone.conf, default /config/rclone/rclone.conf
#   RCLONE_BWLIMIT     bandwidth limit, default 10M ("off" for none)
#   RCLONE_TRANSFERS   parallel transfers, default 4
#   RCLONE_TPSLIMIT    API calls per second, default 10 (keeps OneDrive from throttling)
#   RCLONE_EXTRA_ARGS  anything else to pass to rclone copy
set -uo pipefail

REMOTE=${RCLONE_REMOTE:?RCLONE_REMOTE is not set}
REMOTE=${REMOTE%/}
cd /app  # settings.py lives here

log() { printf '%s upload: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
notify() { python3 /app/notify.py "FamilyAlbum-Upload" "$1" || true; }

# A named remote ("onedrive:...") must exist in rclone.conf. Until the one-off
# `rclone config` has happened, skip quietly instead of alarming every night.
# Connection strings (":local:/path", ":sftp,host=...:") need no config.
if [[ $REMOTE != :* ]]; then
    remote_name=${REMOTE%%:*}
    if ! rclone listremotes 2>/dev/null | grep -qx "$remote_name:"; then
        log "rclone remote '$remote_name' is not configured yet — skipping."
        log "Set it up with: docker compose run --rm sync rclone config"
        exit 0
    fi
    if ! rclone lsd "$remote_name:" >/dev/null 2>&1; then
        log "ERROR: rclone remote '$remote_name' is configured but not reachable"
        notify "Foto-Upload FEHLGESCHLAGEN: rclone-Remote '$remote_name' nicht erreichbar."
        exit 1
    fi
fi

failures=0
uploaded=0
extra=()
[[ -n ${RCLONE_EXTRA_ARGS:-} ]] && read -r -a extra <<< "$RCLONE_EXTRA_ARGS"

while IFS=$'\t' read -r id name; do
    # The album name becomes a folder: no path separators, no remote syntax.
    folder=$(printf '%s' "$name" | tr '/\\:' '---' | sed 's/^[. ]*//; s/[. ]*$//')
    [[ -n $folder ]] || folder=$id
    src="$FA_HOME/data/$id/media"
    if [[ ! -d $src ]]; then
        log "album '$name': nothing downloaded yet, skipping"
        continue
    fi

    log "album '$name': $src -> $REMOTE/$folder"
    if rclone copy "$src" "$REMOTE/$folder" \
        --exclude '*.tmp' --exclude '*.part' \
        --transfers "${RCLONE_TRANSFERS:-4}" --checkers 8 \
        --tpslimit "${RCLONE_TPSLIMIT:-10}" --bwlimit "${RCLONE_BWLIMIT:-10M}" \
        --stats 5m --stats-one-line --stats-log-level NOTICE \
        "${extra[@]}"; then
        count=$(rclone size "$REMOTE/$folder" --json 2>/dev/null | jq -r '.count // "?"')
        log "album '$name': $count files on the remote"
        uploaded=$((uploaded + 1))
    else
        log "album '$name': upload FAILED"
        failures=$((failures + 1))
    fi
done < <(python3 -c '
import settings
for a in settings.load_config()["albums"]:
    print(a["id"], a["name"].replace("\t", " ").replace("\n", " "), sep="\t")
')

if (( failures > 0 )); then
    notify "Foto-Upload mit $failures Fehler(n), $uploaded Album(s) ok."
    exit 1
fi
log "finished: $uploaded album(s)"
