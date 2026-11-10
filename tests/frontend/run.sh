#!/bin/sh
# Drives the real frontend modules in jsdom against a live, seeded server.
# Needs Node 20+ and Python deps from requirements-dev.txt.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PORT="${PORT:-8766}"

cd "$HERE"
[ -d node_modules ] || npm install --silent
# The modules are plain ES modules; Node needs to be told so for files under static/js.
echo '{"type":"module"}' > "$ROOT/snipesbot/web/static/js/package.json"
trap 'kill $SERVER_PID 2>/dev/null; rm -f "$ROOT/snipesbot/web/static/js/package.json"' EXIT

python "$HERE/serve_seed.py" "$PORT" > /tmp/snipesbot_seed_server.log 2>&1 &
SERVER_PID=$!
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  curl -fs "http://127.0.0.1:$PORT/api/status" >/dev/null 2>&1 && break
  sleep 0.5
done

BASE_URL="http://127.0.0.1:$PORT" node smoke.mjs
