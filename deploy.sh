#!/usr/bin/env bash
# Pull the latest code and rebuild only what changed.
#
#   ./deploy.sh              pull, then rebuild frontend and/or backend if their files changed
#   ./deploy.sh --all        pull, then rebuild both regardless
#   ./deploy.sh --frontend   rebuild only the frontend (no pull)
#   ./deploy.sh --backend    rebuild only the backend (no pull)
#
# Frontend: static build in frontend/dist, served directly by nginx
#           (geotech.amsoft.am) — no nginx reload needed.
# Backend:  Docker container "geotech-app" on 127.0.0.1:9080, proxied by
#           nginx (api.geotech.amsoft.am).

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_URL="https://api.geotech.amsoft.am"
IMAGE="geotech-app"
CONTAINER="geotech-app"
PORT=9080

# Runtime data kept on the host so it survives container rebuilds.
TILES_DIR="$REPO_DIR/backend/app/data/real/tiles"
CUSTOM_BLOCKS="$REPO_DIR/backend/app/data/real/custom_blocks.json"
UPLOADED_ANNOTATIONS="$REPO_DIR/backend/app/data/real/uploaded_annotations"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; }

build_frontend() {
    log "Building frontend (VITE_API_URL=$API_URL)"
    cd "$REPO_DIR/frontend"

    if [[ ! -d node_modules ]] || [[ package-lock.json -nt node_modules ]] || [[ package.json -nt node_modules ]]; then
        log "Installing npm dependencies"
        npm ci
    fi

    # Build into a temp dir and swap, so nginx never serves a half-written dist/.
    rm -rf dist.new dist.old
    VITE_API_URL="$API_URL" npm run build -- --outDir dist.new --emptyOutDir
    [[ -d dist ]] && mv dist dist.old
    mv dist.new dist
    rm -rf dist.old
    log "Frontend deployed"
}

build_backend() {
    log "Building backend image"
    cd "$REPO_DIR"
    docker build -t "$IMAGE" .

    [[ -f "$CUSTOM_BLOCKS" ]] || echo '[]' > "$CUSTOM_BLOCKS"
    # Read-write: operators upload new tiles into this dir via /api/site/upload.
    mkdir -p "$TILES_DIR" "$UPLOADED_ANNOTATIONS"
    local mounts=(
        -v "$CUSTOM_BLOCKS:/app/app/data/real/custom_blocks.json"
        -v "$TILES_DIR:/app/app/data/real/tiles"
        -v "$UPLOADED_ANNOTATIONS:/app/app/data/real/uploaded_annotations"
    )
    if ! compgen -G "$TILES_DIR/*.tif*" >/dev/null; then
        err "No tiles in $TILES_DIR — the map will be empty until tiles are uploaded"
    fi

    log "Restarting container"
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    docker run -d --name "$CONTAINER" --restart unless-stopped \
        -p "127.0.0.1:$PORT:$PORT" "${mounts[@]}" "$IMAGE" >/dev/null

    log "Waiting for health check"
    for _ in $(seq 1 30); do
        if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
            log "Backend deployed"
            docker image prune -f >/dev/null
            return 0
        fi
        sleep 1
    done
    err "Backend did not become healthy — last logs:"
    docker logs --tail 30 "$CONTAINER" >&2
    return 1
}

do_frontend=false
do_backend=false

case "${1:-}" in
    --frontend) do_frontend=true ;;
    --backend)  do_backend=true ;;
    --all|"")
        cd "$REPO_DIR"
        old_head=$(git rev-parse HEAD)
        log "Pulling"
        git pull --ff-only
        new_head=$(git rev-parse HEAD)

        if [[ "${1:-}" == "--all" ]]; then
            do_frontend=true
            do_backend=true
        elif [[ "$old_head" == "$new_head" ]]; then
            log "Already up to date — nothing to deploy (use --all to force)"
            exit 0
        else
            changed=$(git diff --name-only "$old_head" "$new_head")
            echo "$changed" | sed 's/^/    /'
            grep -q '^frontend/' <<<"$changed" && do_frontend=true
            grep -qE '^(backend/|Dockerfile$|\.dockerignore$)' <<<"$changed" && do_backend=true
        fi
        ;;
    *)
        sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
        exit 1
        ;;
esac

$do_backend && build_backend
$do_frontend && build_frontend

if ! $do_frontend && ! $do_backend; then
    log "No frontend/backend changes — nothing to rebuild"
fi
