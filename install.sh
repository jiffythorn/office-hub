#!/usr/bin/env bash
# install.sh - ONE CLICK Linux/macOS setup for the Office Hub blueprint.
# Double-click equivalent: just run  ./install.sh  and follow the prompts.
cd "$(dirname "$0")"

echo "==============================================="
echo "  Office Hub - one-click install (Linux/macOS)"
echo "==============================================="

PY=python3
command -v $PY >/dev/null 2>&1 || { echo "ERROR: python3 required. apt install python3"; exit 1; }

$PY install.py $@
STATUS=$?

if [ $STATUS -eq 0 ]; then
  echo
  read -r -p "Start the hub now? [Y/n] " ans
  if [ "${ans:-Y}" = "Y" ]; then
    chmod +x start-hub.sh stop-hub.sh
    ./start-hub.sh
    sleep 2
    curl -s http://localhost:8090/health && echo
    echo "Open http://localhost:8090  ->  chat UI"
  fi
  echo
  echo "Next: python3 setup_apps.py   # member portal + forum (Admidio/Flarum)"
  echo "      python3 secure_admin.py # set strong admin credentials"
fi
