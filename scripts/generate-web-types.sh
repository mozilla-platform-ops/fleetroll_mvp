#!/usr/bin/env bash
set -euo pipefail

# Generate web/src/lib/types.generated.ts from the FastAPI OpenAPI schema.
# Run after any backend schema change. Pre-commit warns if types are stale.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="$REPO_ROOT/web/src/lib/types.generated.ts"

# Find a free port
PORT=$(python3 -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()")

BACKEND_PID=""

cleanup() {
    if [ -n "$BACKEND_PID" ]; then
        kill "$BACKEND_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo "Starting backend on port $PORT..."
uv run fleetroll web --port "$PORT" &
BACKEND_PID=$!

# Wait for the server to be ready
for _ in $(seq 1 20); do
    if curl -sf "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

echo "Fetching OpenAPI schema..."
OPENAPI_JSON=$(curl -sf "http://127.0.0.1:${PORT}/openapi.json")

echo "Generating TypeScript types..."
echo "$OPENAPI_JSON" | \
    pnpm --dir "$REPO_ROOT/web" exec openapi-typescript /dev/stdin \
    --output "$OUTPUT"

echo "Done: $OUTPUT"
