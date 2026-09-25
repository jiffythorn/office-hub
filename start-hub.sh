#!/usr/bin/env bash
# start-hub.sh - ONE SCRIPT brings up the entire blueprint, in the right order:
#   0. dependency gate (Python, venv, packages, AI stack for this privacy mode)
#   1. MariaDB         (databases for the portal + forum)
#   2. Admidio :8080 + Flarum :8081   (PHP; skipped with --hub-only)
#   3. llama.cpp       (only when privacy_mode = local)
#   4. AI glue API :8090
# Safe to run twice (pidfiles + port checks). --hub-only skips MariaDB/apps.

set -u
cd "$(dirname "$0")"

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"
mkdir -p data

HUB_ONLY=0
[ "${1:-}" = "--hub-only" ] && HUB_ONLY=1

alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
port_open() { "$PY" portcheck.py "$1" 2>/dev/null; }

# --- 0. dependency gate -------------------------------------------------------
MODE="$($PY -c "import json;print(json.load(open('config.json'))['privacy_mode'])" 2>/dev/null || echo retrieval_only)"
if ! "$PY" check_deps.py --section hub --quiet; then
  echo "[hub] dependency check failed - fix the items above (or run: python3 doctor.py)."
  exit 1
fi

# --- 1. MariaDB (best effort - the hub itself runs fine without it) -----------
if [ "$HUB_ONLY" -eq 0 ]; then
  if port_open 3306; then
    echo "[hub] MariaDB is up (:3306)"
  elif command -v systemctl >/dev/null 2>&1; then
    echo "[hub] starting MariaDB service ..."
    sudo systemctl start mariadb 2>/dev/null \
      || echo "[hub] WARNING: could not start MariaDB (needs sudo). Portal/forum will not work until it runs. Try: sudo systemctl start mariadb"
  else
    echo "[hub] WARNING: MariaDB not detected on :3306 - start it manually for the portal/forum."
  fi
fi

# --- 2. Admidio + Flarum (best effort) ----------------------------------------
if [ "$HUB_ONLY" -eq 0 ]; then
  if [ -d apps/admidio ] && command -v php >/dev/null 2>&1; then
    if port_open 8080; then echo "[apps] Admidio already running (:8080)"; else
      export PHP_CLI_SERVER_WORKERS=8
      nohup php -S 0.0.0.0:8080 -t apps/admidio > data/admidio.log 2>&1 &
      echo $! > data/admidio.pid
      echo "[apps] Admidio on http://localhost:8080"
    fi
  else
    echo "[apps] Admidio skipped (needs apps/admidio + php) - run: python3 setup_apps.py"
  fi
  if [ -d apps/flarum ] && command -v php >/dev/null 2>&1; then
    if port_open 8081; then echo "[apps] Flarum already running (:8081)"; else
      export PHP_CLI_SERVER_WORKERS=8
      nohup php -S 0.0.0.0:8081 -t apps/flarum/public > data/flarum.log 2>&1 &
      echo $! > data/flarum.pid
      echo "[apps] Flarum on http://localhost:8081"
    fi
  else
    echo "[apps] Flarum skipped (needs apps/flarum + php) - run: python3 setup_apps.py"
  fi
fi

# --- 3. local AI engine (optional) ---------------------------------------------
if [ "$MODE" = "local" ] && [ -f "ai/llama.cpp/llama-server" ]; then
  if alive data/llama.pid; then
    echo "[hub] local AI already running (pid $(cat data/llama.pid))"
  else
    read -r CTX THREADS MODELPORT HOST <<<"$($PY -c "import json;c=json.load(open('config.json'))['local_ai'];print(c['ctx'],c['threads'],c['port'],c['host'])")"
    MODEL="$($PY -c "import json;print(json.load(open('config.json'))['local_ai']['model_path'])")"
    nohup "ai/llama.cpp/llama-server" -m "$MODEL" \
      --host "$HOST" --port "$MODELPORT" --ctx-size "$CTX" --threads "$THREADS" \
      > data/llama.log 2>&1 &
    echo $! > data/llama.pid
    echo "[hub] local AI starting on :$MODELPORT (log: data/llama.log)"
  fi
fi

# --- 4. glue API ----------------------------------------------------------------
if alive data/hub.pid; then
  echo "[hub] AI API already running (pid $(cat data/hub.pid))"
else
  nohup "$PY" -m uvicorn hub.server:app --host 0.0.0.0 --port 8090 > data/hub.log 2>&1 &
  echo $! > data/hub.pid
  echo "[hub] AI API starting on http://localhost:8090  (log: data/hub.log)"
fi

echo "[hub] up."
[ "$HUB_ONLY" -eq 1 ] && exit 0
echo "  AI assistant : http://localhost:8090"
echo "  Member portal: http://localhost:8080   (Admidio)"
echo "  Forum / chat : http://localhost:8081   (Flarum)"
