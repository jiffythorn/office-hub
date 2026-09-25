#!/usr/bin/env bash
# start-apps.sh - run Admidio (:8080) and Flarum (:8081) on PHP's built-in server.
# Office scale (50-200 members): PHP_CLI_SERVER_WORKERS gives enough concurrency.
cd "$(dirname "$0")"

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

# --- 0. dependency check (PHP + extensions, MariaDB, app folders) -------------
if ! "$PY" check_deps.py --section apps --quiet; then
  echo "[apps] dependency check failed - fix the items above, then run again."
  exit 1
fi

command -v php >/dev/null 2>&1 || { echo "PHP not installed. apt install php php-mysql"; exit 1; }
export PHP_CLI_SERVER_WORKERS=8

alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

if [ -d apps/admidio ]; then
  if alive data/admidio.pid; then echo "[apps] Admidio already running"; else
    nohup php -S 0.0.0.0:8080 -t apps/admidio > data/admidio.log 2>&1 &
    echo $! > data/admidio.pid
    echo "[apps] Admidio on http://localhost:8080"
  fi
else
  echo "[apps] apps/admidio missing - run: python3 setup_apps.py"
fi

if [ -d apps/flarum ]; then
  if alive data/flarum.pid; then echo "[apps] Flarum already running"; else
    nohup php -S 0.0.0.0:8081 -t apps/flarum/public > data/flarum.log 2>&1 &
    echo $! > data/flarum.pid
    echo "[apps] Flarum on http://localhost:8081"
  fi
else
  echo "[apps] apps/flarum missing - run: python3 setup_apps.py"
fi
