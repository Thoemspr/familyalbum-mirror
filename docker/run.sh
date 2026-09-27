#!/usr/bin/env bash
# One scheduled run: mirror all albums, then copy the originals to the rclone
# remote (if RCLONE_REMOTE is set). A lock keeps a manual run and a scheduled
# one from working on the same files at the same time.
set -uo pipefail

exec 9>"$FA_HOME/.sync.lock"
if ! flock -n 9; then
    echo "another sync is still running — skipping this one"
    exit 0
fi

python3 /app/sync.py
status=$?

if [[ -n ${RCLONE_REMOTE:-} ]]; then
    /app/docker/upload.sh || status=$(( status > 0 ? status : 1 ))
fi
exit $status
