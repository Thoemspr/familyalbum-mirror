# FamilyAlbum mirror: crawler, site build, rclone upload and the web server in
# one image. compose.yaml runs it twice — `sync` (scheduler) and `web` (Caddy).
FROM caddy:2.11-alpine AS caddy
FROM rclone/rclone:1.75.1 AS rclone

FROM python:3.14-slim

ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.49
ARG SUPERCRONIC_SHA256_amd64=a53ae236602c7338aba3fbaff40bda6300eae3b9fedb8261eb06cfe3724430c1
ARG SUPERCRONIC_SHA256_arm64=02aa0cb229ba09050cba6638059dadb9eedc2276632ea43d6a57a2f8c1629dd5

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg jq tzdata ca-certificates curl util-linux \
 && rm -rf /var/lib/apt/lists/*

RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) sum="$SUPERCRONIC_SHA256_amd64" ;; \
      arm64) sum="$SUPERCRONIC_SHA256_arm64" ;; \
      *) echo "unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}"; \
    echo "$sum  /usr/local/bin/supercronic" | sha256sum -c -; \
    chmod +x /usr/local/bin/supercronic

COPY --from=caddy /usr/bin/caddy /usr/local/bin/caddy
COPY --from=rclone /usr/local/bin/rclone /usr/local/bin/rclone

RUN pip install --no-cache-dir "pillow>=11,<13"

# Unprivileged by default; compose can override the uid:gid so the files on the
# host belong to the right user.
RUN useradd --uid 1000 --user-group --home-dir /tmp --no-create-home album \
 && mkdir -p /album /config/rclone /caddy \
 && chown -R album:album /album /config /caddy \
 # PUID/PGID may be any uid, and a named volume keeps the image's ownership.
 && chmod 1777 /caddy

WORKDIR /app
COPY mitene.py settings.py notify.py sync.py build_site.py ./
COPY site/index.html site/app.js site/style.css ./site/
COPY docker/ ./docker/
RUN chmod +x docker/*.sh

ENV FA_HOME=/album \
    FA_CONFIG=/config/config.json \
    RCLONE_CONFIG=/config/rclone/rclone.conf \
    XDG_DATA_HOME=/caddy/data \
    XDG_CONFIG_HOME=/caddy/config \
    HOME=/tmp \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER album
VOLUME ["/album"]
EXPOSE 80 443
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["scheduler"]
