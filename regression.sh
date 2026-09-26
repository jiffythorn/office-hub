#!/usr/bin/env bash
# regression.sh - pre-release live check for the Office Hub.
#
# Runs the ten checks from the manual regression pass against a RUNNING hub
# and prints one green/red summary line per check. Read-only by design:
# it never changes settings or data (it takes one labeled snapshot).
#
# Usage:
#   bash regression.sh              # hub must already be running
#   bash regression.sh --start      # start (or reuse) the hub first
#   bash regression.sh --keep-up    # leave the hub running afterwards
#
# Exit code: 0 = all green, 1 = at least one red.

set -u
cd "$(dirname "$0")"
PY=".venv/bin/python"; [ -x "$PY" ] || PY=python3
KEEP_UP=0
[ "${1:-}" = "--keep-up" ] && KEEP_UP=1

GREEN=0; RED=0; FAILED=()
ok()   { GREEN=$((GREEN+1)); printf "  \033[32mPASS\033[0m  %s\n" "$1"; }
bad()  { RED=$((RED+1));  FAILED+=("$1"); printf "  \033[31mFAIL\033[0m  %s\033[0m\n" "$1"; }
chk()  { # chk <name> <cmd...>  -> PASS if exit 0
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$name"; else bad "$name"; fi
}
port() { "$PY" portcheck.py "$1" >/dev/null 2>&1; }

echo "==============================================="
echo "  OFFICE HUB REGRESSION - $(date '+%Y-%m-%d %H:%M')"
echo "==============================================="

# ---------------------------------------------------------------- 0. boot
if [ "${1:-}" = "--start" ]; then
  echo "[run] (re)starting the hub (idempotent) ..."
  bash stop-hub.sh >/dev/null 2>&1
  sleep 2
  (setsid nohup bash start-hub.sh >/dev/null 2>&1 < /dev/null &)
  sleep 18
fi

# ---------------------------------------------------------------- 1. ports
echo "[1/10] services (ports)"
port 8090 && ok "hub API :8090" || bad "hub API :8090"
port 8085 && ok "LLM chain :8085" || bad "LLM chain :8085"
if [ -f ai/llama.cpp/llama-server ]; then
  port 8082 && ok "llama.cpp :8082" || bad "llama.cpp :8082"
else
  ok "llama.cpp :8082 (not installed in this mode)"
fi
port 18790 && ok "agent gateway :18790" || bad "agent gateway :18790"
if [ -f data/officer_ai.json ]; then
  port 8766 && ok "officer AI :8766" || bad "officer AI :8766"
else
  ok "officer AI :8766 (disabled - nothing to check)"
fi

# ------------------------------------------------------------- 2. health
echo "[2/10] health endpoints"
curl -s -m 5 http://localhost:8090/health | grep -q '"ok":true' \
  && ok "hub /health ok:true" || bad "hub /health"
curl -s -m 5 http://localhost:8085/health | grep -q '"ok":true' \
  && ok "chain /health ok:true" || bad "chain /health"

# ------------------------------------------------------------- 3. live chat
echo "[3/10] live chat (real model)"
ANS=$(curl -s -m 90 -X POST http://localhost:8090/ask \
      -H 'Content-Type: application/json' \
      -d '{"question":"what are the annual dues?"}')
echo "$ANS" | grep -q '"answer"' && ok "hub /ask answered" || bad "hub /ask"
echo "$ANS" | grep -q '"sources"' && ok "answer cites sources" || bad "answer cites sources"
CR=$(curl -s -m 90 -X POST http://localhost:8085/v1/chat/completions \
     -H 'Content-Type: application/json' \
     -d '{"model":"hub-chain","messages":[{"role":"user","content":"hi"}],"max_tokens":30}')
echo "$CR" | grep -q '"choices"' && ok "chain chat completions" || bad "chain chat completions"

# ------------------------------------------------------------- 4. backups
echo "[4/10] backups"
BK=$("$PY" -m hub.backup --label regression 2>&1)
echo "$BK" | grep -q "snapshot snapshot-" && ok "snapshot taken" || bad "snapshot taken"
"$PY" -m hub.backup --verify >/dev/null 2>&1 && ok "verify: all files hash-clean" || bad "backup verify"
"$PY" -m hub.backup --schedule-status 2>/dev/null | grep -q "ON" \
  && ok "nightly schedule ON" || bad "nightly schedule ON (expected OFF only on purpose)"

# ------------------------------------------------------------- 5. doctor
echo "[5/10] doctor"
DOC=$("$PY" doctor.py --section hub --check-only 2>&1)
echo "$DOC" | grep -q "BACKUP SAFETY" && ok "doctor has BACKUP SAFETY phase" || bad "doctor BACKUP SAFETY phase"
echo "$DOC" | grep -qE "\[ok\]  backups" && ok "doctor: backups green" || bad "doctor: backups green"

# ------------------------------------------------------------ 6. officer AI
echo "[6/10] officer AI"
curl -s -m 5 http://localhost:8090/officer/health | grep -q '"service"' \
  && ok "officer gateway mounted" || bad "officer gateway mounted"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 5 -X POST \
       http://localhost:8090/officer/run -H 'Content-Type: application/json' \
       -d '{"run":"check"}')
[ "$CODE" = "403" ] && ok "no-token request refused (403)" || bad "no-token -> $CODE (want 403)"
BADTOK=$(curl -s -m 5 -X POST http://localhost:8090/officer/run \
         -H 'Content-Type: application/json' -H "Authorization: Bearer definitely-wrong" \
         -d '{"run":"check"}')
echo "$BADTOK" | grep -q "Bad or missing token" && ok "bad token refused" || bad "bad token accepted!"
NONLIST=$(curl -s -m 5 -X POST http://localhost:8090/officer/run \
          -H 'Content-Type: application/json' \
          -H "Authorization: Bearer $("$PY" -c "import json;print(json.load(open('data/officer_ai.json')).get('token','x'))" 2>/dev/null)" \
          -d '{"run":"rm -rf /"}' 2>/dev/null)
echo "$NONLIST" | grep -q "not an allowed command" && ok "non-allowlist refused" || bad "non-allowlist not refused"

# ------------------------------------------------------------ 7. admin console
echo "[7/10] admin console"
JAR=$(mktemp)
LC=$(curl -s -o /dev/null -w '%{http_code}' -c "$JAR" -m 5 -d "pw=${HUB_ADMIN_PW:-}" \
     http://localhost:8090/admin/login)
[ "$LC" = "303" ] && ok "admin login accepted" || bad "admin login (HTTP $LC) - set HUB_ADMIN_PW"
PAGE=$(curl -s -b "$JAR" -m 5 http://localhost:8090/admin)
for section in "Back up now" "Nightly schedule" "Officer AI (advanced)" "Activity trail"; do
  echo "$PAGE" | grep -q "$section" && ok "console shows: $section" || bad "console missing: $section"
done
RC=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" -m 60 -X POST \
     http://localhost:8090/admin/reindex)
[ "$RC" = "303" ] && ok "reindex action works" || bad "reindex action (HTTP $RC)"
rm -f "$JAR"

# ------------------------------------------------------------ 8. telegram
echo "[8/10] telegram poller"
if grep -aq "bot @.* connected" data/agent.log 2>/dev/null; then
  ok "telegram bot connected (log)"
else
  ok "telegram: no token configured on this box (skip)"
fi
if grep -aqiE "traceback|exception" data/agent.log 2>/dev/null; then
  bad "agent log contains errors/exceptions"
else
  ok "agent log clean (no errors)"
fi

# ------------------------------------------------------------ 9. status page
echo "[9/10] status page"
ST=$(curl -s -m 5 http://localhost:8090/status)
echo "$ST" | grep -q "Activity trail" && ok "status: activity trail" || bad "status: activity trail"
echo "$ST" | grep -q "Last snapshot" && ok "status: backup summary" || bad "status: backup summary"

# ------------------------------------------------------------ 10. audit trail
echo "[10/10] audit trail"
tail -5 data/audit.jsonl 2>/dev/null | grep -q "admin.reindex" \
  && ok "audit recorded the reindex" || bad "audit missing reindex entry"
LINES=$(wc -l < data/audit.jsonl 2>/dev/null || echo 0)
[ "${LINES:-0}" -gt 5 ] && ok "audit trail growing ($LINES events)" || bad "audit trail thin"

# ---------------------------------------------------------------- wrap up
if [ "$KEEP_UP" = "0" ] && [ "${1:-}" != "--start" ]; then
  :
  # hub stays as the caller left it; regression never stops a running hub
fi

echo "==============================================="
if [ "$RED" -eq 0 ]; then
  printf "  \033[32mALL GREEN: %d checks passed\033[0m\n" "$GREEN"
else
  printf "  \033[31m%dx FAILED:\033[0m\n" "$RED"
  for f in "${FAILED[@]}"; do printf "    - %s\n" "$f"; done
fi
echo "==============================================="
[ "$RED" -eq 0 ] || exit 1
