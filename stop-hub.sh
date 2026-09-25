#!/usr/bin/env bash
# stop-hub.sh - stops the whole blueprint: glue API, local AI, agent, Admidio, Flarum.
# (MariaDB is left running on purpose - it's a system service; stop it with
#  sudo systemctl stop mariadb if you really need to.)
cd "$(dirname "$0")"
for f in data/hub.pid data/chain.pid data/llama.pid data/agent.pid data/admidio.pid data/flarum.pid; do
  if [ -f "$f" ]; then
    PID=$(cat "$f")
    kill "$PID" 2>/dev/null && echo "[hub] stopped pid $PID ($f)" || rm -f "$f"
    rm -f "$f"
  fi
done
echo "[hub] stopped."
