#!/bin/sh
# One command to run the whole demo: backend (fake AWS, free) + FREE-WILi/camera bridge.
#   ./demo/start_demo.sh [path/to/lpr-backend/backend]
# Open http://localhost:8000/demo/ , press the green button on the FREE-WILi.
# Stop with Ctrl-C. Records live in memory and are lost on exit (no AWS account needed).
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
BACKEND="${1:-$HERE/../../lpr-backend/backend}"
[ -f "$BACKEND/app/main.py" ] || { echo "backend not found at $BACKEND (pass its path as the first argument)"; exit 1; }
VENV="$HERE/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  echo "first run: installing dependencies (a few minutes)..."
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q -r "$BACKEND/requirements-dev.txt" pyserial pyobjc-framework-Vision pyobjc-framework-Quartz
fi
command -v tesseract >/dev/null || echo "note: tesseract not installed (brew install tesseract); only the fallback reader needs it"
pkill -f run_local_demo.py 2>/dev/null || true
pkill -f freewili_bridge.py 2>/dev/null || true
"$VENV/bin/python" "$HERE/run_local_demo.py" "$BACKEND" > "$HERE/api.log" 2>&1 &
API=$!
trap 'kill $API 2>/dev/null; pkill -f freewili_bridge.py 2>/dev/null' EXIT INT TERM
for i in $(seq 1 30); do curl -s localhost:8000/health >/dev/null 2>&1 && break; sleep 1; done
echo "API up. Frontend: http://localhost:8000/demo/"
open http://localhost:8000/demo/ 2>/dev/null || true
cd "$BACKEND"
API_URL=http://localhost:8000 API_KEY=demo-camera-key "$VENV/bin/python" -u bridge/freewili_bridge.py --source demo-cam
